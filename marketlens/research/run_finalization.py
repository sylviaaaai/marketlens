"""Explicit offline Run closure over the existing schema and immutable evidence.

Callers must hold the collector's single_writer lock. No Adapter, cache, media
store or database initialization is constructed here. SQLite BEGIN IMMEDIATE
also excludes active database writers while validation and closure take place.
"""
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from marketlens.adapters.xiaohongshu_mcp import utc_now
from .access_cache import atomic_write
from .database import assert_integrity


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def intent_path(data_dir, run):
    return Path(data_dir) / "logs" / f"finalization_{run}.json"


def _exclusive(path, value):
    """Publish a complete fsynced intent atomically, without replacing a record."""
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encode(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reviewer(conn, data_dir):
    from .baseline_collector import BaselineCollector
    obj = object.__new__(BaselineCollector)
    obj.conn, obj.data_dir = conn, Path(data_dir)
    return obj


def _fingerprint(conn, run):
    """Hash this Run's collection evidence, allowing later datasets/new Runs."""
    h = hashlib.sha256()
    for row in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"):
        h.update(encode(tuple(row)))
    attempts = "SELECT a.attempt_id FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=?"
    snapshots = f"SELECT snapshot_id FROM post_snapshots WHERE collection_attempt_id IN ({attempts})"
    hits = "SELECT h.post_id FROM search_hits h JOIN search_requests q USING(search_request_id) WHERE q.run_id=?"
    filters = {
        "research_projects": "project_id IN (SELECT p.project_id FROM collection_plans p JOIN collection_runs r USING(plan_id) WHERE r.run_id=?)",
        "collection_plans": "plan_id IN (SELECT plan_id FROM collection_runs WHERE run_id=?)",
        "plan_keywords": "plan_id IN (SELECT plan_id FROM collection_runs WHERE run_id=?)",
        "plan_sort_methods": "plan_id IN (SELECT plan_id FROM collection_runs WHERE run_id=?)",
        "collection_runs": "run_id=?", "search_requests": "run_id=?", "collection_tasks": "run_id=?",
        "collection_attempts": f"attempt_id IN ({attempts})", "raw_artifacts": f"attempt_id IN ({attempts})",
        "search_hits": "search_request_id IN (SELECT search_request_id FROM search_requests WHERE run_id=?)",
        "posts": f"post_id IN ({hits})", "post_snapshots": f"collection_attempt_id IN ({attempts})",
        "post_media": f"snapshot_id IN ({snapshots})", "comment_observations": f"collection_attempt_id IN ({attempts})",
        "comments": f"comment_id IN (SELECT comment_id FROM comment_observations WHERE collection_attempt_id IN ({attempts}))"}
    for table, condition in sorted(filters.items()):
        for row in conn.execute(f'SELECT * FROM "{table}" WHERE {condition} ORDER BY rowid', (run,)):
            value = dict(row)
            if table == "collection_runs" and value["run_id"] == run:
                for key in ("status", "finished_at", "error_summary"):
                    value.pop(key)
            h.update(encode([table, value]))
    return h.hexdigest()


def _safe_note(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 2000
            or re.search(r"(?i)bearer\s|(?:token|cookie|password|authorization|secret)\s*[:=]", value)):
        raise ValueError("Finalization requires a safe explicit human reason")


def _validate(conn, data_dir, run, decision, original_checkpoint):
    if not isinstance(decision, dict) or set(decision) != {"version", "database_path", "run_id", "reason", "checkpoint_sha256", "permanent_skips"}:
        raise ValueError("Unsupported finalization decision fields")
    if json.loads(original_checkpoint)["run_id"] != run:
        raise ValueError("Archived Checkpoint belongs to another Run")
    inspector = _reviewer(conn, data_dir)
    row = conn.execute("SELECT r.*,p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE run_id=?", (run,)).fetchone()
    if row is None or json.loads(row["comment_config_json"]).get("pilot", True):
        raise ValueError("Finalization requires an existing formal Run")
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    identity = str(Path(database).resolve()) if database else ":memory:"
    if (decision.get("version") != 1 or decision.get("run_id") != run
            or decision.get("database_path") != identity
            or decision.get("checkpoint_sha256") != sha(original_checkpoint)):
        raise ValueError("Finalization decision database/Run/Checkpoint binding mismatch")
    _safe_note(decision.get("reason"))
    phase = inspector.phase(run)
    if not phase["search_complete"]:
        raise ValueError("Search plan is incomplete")
    if conn.execute("SELECT 1 FROM search_requests WHERE run_id=? AND finished_at IS NULL LIMIT 1", (run,)).fetchone():
        raise ValueError("Unfinished search request")
    tasks = conn.execute("SELECT * FROM collection_tasks WHERE run_id=? ORDER BY sequence_no", (run,)).fetchall()
    if any(t["status"] in ("pending", "running") or not t["finished_at"] for t in tasks):
        raise ValueError("Unfinished task; no local finalization allowed")
    if conn.execute("SELECT 1 FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND (a.status='running' OR a.finished_at IS NULL) LIMIT 1", (run,)).fetchone():
        raise ValueError("Unfinished Attempt; no local finalization allowed")
    expected = {r[0] for r in conn.execute("SELECT DISTINCT h.post_id FROM search_hits h JOIN search_requests q USING(search_request_id) WHERE q.run_id=? AND h.card_note_type='normal'", (run,))}
    details = [t for t in tasks if t["task_type"] == "feed_detail"]
    if len(details) != len(expected) or {t["post_id"] for t in details} != expected:
        raise ValueError("Detail task identities do not cover the actual candidates")
    skips = decision.get("permanent_skips")
    skip_fields = {"task_id", "post_id", "attempt_id", "classification", "no_refill", "review_sha256", "evidence_sha256", "note"}
    if (not isinstance(skips, list) or any(not isinstance(s, dict) or set(s) != skip_fields for s in skips)
            or len({s["task_id"] for s in skips}) != len(skips)):
        raise ValueError("Explicit unique permanent skip decisions required")
    skip_map = {s["task_id"]: s for s in skips}
    used, dispositions = set(), []
    evidence_files = {}
    def protect(path, expected_sha=None):
        path = Path(path).resolve()
        actual = sha(path.read_bytes())
        if expected_sha is not None and actual != expected_sha:
            raise ValueError("Referenced evidence hash changed")
        evidence_files[str(path)] = actual
    # All Attempt raw files are rechecked, including failed history.
    for a in conn.execute("SELECT a.attempt_id,count(f.raw_artifact_id) n FROM collection_attempts a JOIN collection_tasks t USING(task_id) LEFT JOIN raw_artifacts f USING(attempt_id) WHERE t.run_id=? GROUP BY a.attempt_id", (run,)):
        if a["n"] != 1:
            raise ValueError("Attempt lacks unique preserved raw evidence")
    for artifact in conn.execute("SELECT f.file_path,f.sha256 FROM raw_artifacts f JOIN collection_attempts a USING(attempt_id) JOIN collection_tasks t USING(task_id) WHERE t.run_id=?", (run,)):
        protect(artifact["file_path"], artifact["sha256"])
    for t in tasks:
        attempts = conn.execute("SELECT * FROM collection_attempts WHERE task_id=? ORDER BY attempt_no", (t["task_id"],)).fetchall()
        success = [a for a in attempts if a["status"] == "succeeded"]
        if t["task_type"] == "search":
            if t["status"] != "succeeded" or len(success) != 1 or attempts[-1]["status"] != "succeeded":
                raise ValueError("Search task has no final successful Attempt")
            continue
        entry = inspector._pending_entry(t)
        pending = inspector._pending_state(t)
        if t["status"] == "succeeded":
            if len(success) != 1 or attempts[-1]["status"] != "succeeded" or pending == "pending_review":
                raise ValueError("Successful task has unresolved history/review")
            snaps = conn.execute("SELECT post_id,collection_attempt_id FROM post_snapshots WHERE collection_attempt_id IN (SELECT attempt_id FROM collection_attempts WHERE task_id=?)", (t["task_id"],)).fetchall()
            if len(snaps) != 1 or tuple(snaps[0]) != (t["post_id"], success[0]["attempt_id"]):
                raise ValueError("Successful detail lacks its bound snapshot")
            disposition = "succeeded"
        elif t["status"] == "cancelled":
            if not inspector._source_deleted_record(t):
                raise ValueError("Cancelled detail lacks source_deleted evidence")
            disposition = "source_deleted"
        elif t["status"] == "partial" and pending == "pending_review" and not success:
            s = skip_map.get(t["task_id"])
            if not s:
                raise ValueError("Unresolved manual review requires explicit permanent skip")
            path = Path(data_dir) / "logs" / f"pending_review_{t['task_id']}_{attempts[-1]['attempt_id']}.json"
            if (s.get("post_id") != t["post_id"] or s.get("attempt_id") != attempts[-1]["attempt_id"]
                    or s.get("classification") != "banned_or_restricted" or s.get("no_refill") is not True
                    or s.get("review_sha256") != sha(path.read_bytes())
                    or s.get("evidence_sha256") != entry[1]["decision"]["evidence_sha256"]):
                raise ValueError("Permanent skip identity/evidence mismatch")
            _safe_note(s.get("note"))
            used.add(t["task_id"])
            disposition = "banned_or_restricted"
        else:
            raise ValueError("Task has no supported final disposition")
        dispositions.append({"task_id": t["task_id"], "post_id": t["post_id"], "disposition": disposition})
    if used != set(skip_map):
        raise ValueError("Unused/stale permanent skip decisions")
    # Preserve and validate every review sidecar and its referenced evidence.
    task_ids = {t["task_id"] for t in tasks}
    for pattern in ("pending_review_*.json", "detail_review_*.json", "detail_unavailable_*.json"):
        for path in (Path(data_dir) / "logs").glob(pattern):
            record = json.loads(path.read_text(encoding="utf-8"))
            d = record.get("decision", record)
            binding = d.get("binding", {})
            if binding.get("run_id") != run:
                continue
            if binding.get("task_id") not in task_ids:
                raise ValueError("Orphan manual review decision")
            if path.name.startswith("detail_review_"):
                attempt = conn.execute("SELECT a.*,t.post_id FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE attempt_id=? AND t.run_id=?", (binding.get("attempt_id"), run)).fetchone()
                if (attempt is None or attempt["task_id"] != binding.get("task_id")
                        or attempt["post_id"] != binding.get("post_id")
                        or d.get("allowed_next_attempt_no") != attempt["attempt_no"] + 1
                        or not conn.execute("SELECT 1 FROM collection_attempts WHERE task_id=? AND attempt_no=? AND finished_at IS NOT NULL",
                                            (attempt["task_id"], d["allowed_next_attempt_no"])).fetchone()):
                    raise ValueError("Unconsumed or mismatched timeout review")
            protect(path)
            if "evidence_path" in d:
                protect(d["evidence_path"], d["evidence_sha256"])
    counts = {k: sum(d["disposition"] == k for d in dispositions)
              for k in ("succeeded", "source_deleted", "banned_or_restricted")}
    counts.update(candidates=len(details), failed=0, skipped=counts["source_deleted"] + counts["banned_or_restricted"],
                  pending_review=0, search_succeeded=phase["search_succeeded"],
                  historical_failed_attempts=conn.execute("SELECT count(*) FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND a.status='failed'", (run,)).fetchone()[0])
    assert_integrity(conn)
    return {"counts": counts, "dispositions": dispositions, "evidence_files": evidence_files,
            "database_fingerprint": _fingerprint(conn, run),
            "status": "partial" if counts["skipped"] else "completed"}


def _checkpoint(intent, digest):
    result = json.loads(intent["original_checkpoint"])
    result.update(run_id=intent["run_id"], stage="completed", search_complete=True,
                  execution_status="PARTIAL" if intent["status"] == "partial" else "SUCCESS",
                  reason="LocalFinalization;all_tasks_disposed;no_refill", run_sealed=True,
                  updated_at=intent["finished_at"], finished_at=intent["finished_at"],
                  finalization_sha256=digest, final_counts=intent["counts"])
    return encode(result)


def read_finalization(conn, data_dir, run):
    """Read-only state of a durable intent; invalid evidence is never hidden."""
    path = intent_path(data_dir, run)
    if not path.exists():
        row = conn.execute("SELECT error_summary FROM collection_runs WHERE run_id=?", (run,)).fetchone()
        if row and (row[0] or "").startswith("local_finalization_v1:"):
            raise ValueError("Database finalization anchor lacks its immutable intent")
        return None
    raw = path.read_bytes()
    intent = json.loads(raw)
    if intent.get("version") != 1 or intent.get("run_id") != run:
        raise ValueError("Invalid finalization intent")
    checked = _validate(conn, data_dir, run, intent["decision"], intent["original_checkpoint"].encode("utf-8"))
    for key in checked:
        if checked[key] != intent[key]:
            raise ValueError("Finalization evidence/database changed: " + key)
    row = conn.execute("SELECT status,finished_at,error_summary FROM collection_runs WHERE run_id=?", (run,)).fetchone()
    digest = sha(raw)
    sealed = row["status"] == intent["status"] and row["finished_at"] == intent["finished_at"] and row["error_summary"] == "local_finalization_v1:" + digest
    if not sealed and dict(row) != intent["previous_run"]:
        raise ValueError("Finalization Run state mismatch")
    checkpoint = (Path(data_dir) / "logs" / f"checkpoint_{run}.json").read_bytes()
    synchronized = checkpoint == _checkpoint(intent, digest)
    if checkpoint != intent["original_checkpoint"].encode("utf-8") and not synchronized:
        raise ValueError("Finalization Checkpoint changed unexpectedly")
    return {"intent": intent, "sha256": digest, "sealed": sealed,
            "checkpoint_synchronized": synchronized, "recovery_required": not (sealed and synchronized)}


def finalize_run(conn, data_dir, run, decision, *, check_only=False, fault=lambda stage: None):
    """Validate then close locally. Repeating the SAME decision repairs file gaps.

    Read-only checks acquire a read transaction; execution uses BEGIN IMMEDIATE.
    The caller must additionally hold the OS collector lock for execution.
    """
    data_dir = Path(data_dir)
    path = intent_path(data_dir, run)
    cp = data_dir / "logs" / f"checkpoint_{run}.json"
    if check_only:
        conn.execute("BEGIN")
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        state = read_finalization(conn, data_dir, run)
        if state:
            intent = state["intent"]
            if intent["decision"] != decision:
                raise ValueError("Finalization decision is immutable; repeat the original decision")
        else:
            row = conn.execute("SELECT status,finished_at,error_summary FROM collection_runs WHERE run_id=?", (run,)).fetchone()
            if row is None or row["status"] != "running" or row["finished_at"] is not None:
                raise ValueError("Run is not open or was closed without local finalization")
            original = cp.read_bytes()
            checked = _validate(conn, data_dir, run, decision, original)
            intent = {"version": 1, "run_id": run, "decision": decision,
                      "original_checkpoint": original.decode("utf-8"), "previous_run": dict(row),
                      "finished_at": utc_now(), **checked}
        if check_only:
            conn.rollback()
            return {"result": "FINALIZATION_READY" if not state else "FINALIZATION_RECORDED",
                    "counts": intent["counts"], "target_status": intent["status"],
                    "run_sealed": bool(state and state["sealed"]),
                    "recovery_required": bool(state and state["recovery_required"])}
        if not state:
            _exclusive(path, intent)
        fault("intent_written")
        digest = sha(path.read_bytes())
        if not state or not state["sealed"]:
            conn.execute("UPDATE collection_runs SET status=?,finished_at=?,error_summary=? WHERE run_id=? AND status='running'",
                         (intent["status"], intent["finished_at"], "local_finalization_v1:" + digest, run))
        assert_integrity(conn)
        fault("before_commit")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    fault("after_commit")
    expected = _checkpoint(intent, digest)
    current = cp.read_bytes()
    if current != expected:
        if current != intent["original_checkpoint"].encode("utf-8"):
            raise ValueError("Checkpoint conflict; explicit repair required")
        fault("before_checkpoint")
        atomic_write(cp, expected)
    fault("after_checkpoint")
    final = read_finalization(conn, data_dir, run)
    if final["recovery_required"]:
        raise ValueError("Finalization requires recovery")
    return {"result": "ALREADY_FINALIZED" if state and not state["recovery_required"] else "FINALIZED",
            "run_id": run, "status": intent["status"], "finished_at": intent["finished_at"],
            "counts": intent["counts"], "checkpoint_synchronized": True,
            "finalization_sha256": digest}
