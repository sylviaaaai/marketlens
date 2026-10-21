"""Offline Dataset selection v1, publication and independent verification.

Authority is always read through mode=ro. Simulation writes only a distinct
SQLite copy, whose Run evidence must match the authority exactly. No decisions
are rewritten and no authentication, collector or adapter is instantiated.

Manifest publication precedes the SQLite commit. An orphan published manifest
is an immutable freeze intent: retry may adopt precisely the same intent, never
replace it. Frozen datasets with missing/damaged manifests fail verification.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .database import connect, assert_integrity
from .media_store import MediaStore, run_project
from .run_finalization import read_finalization, _fingerprint, _reviewer


RULE = "dataset_selection_v1"
FORMAT = "marketlens_dataset_manifest_v1"


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise ValueError("Dataset name/version must be safe nonempty identifiers")
    return value


def _rules(cutoff):
    try:
        parsed = datetime.strptime(cutoff, "%Y-%m-%dT%H:%M:%S.%fZ")
        if parsed.isoformat(timespec="milliseconds") + "Z" != cutoff:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("Cutoff must be canonical UTC milliseconds") from None
    return {"rule_version": RULE, "observation_cutoff_at": cutoff,
            "candidates": "all_source_run_hits_from_succeeded_or_partial_attempts",
            "snapshots": "latest_normal_by_observed_at_then_snapshot_id_in_source_run",
            "comments": "latest_stable_identity_by_observed_at_then_observation_id;all_anonymous",
            "quality": "retain_blank_text_null_metrics_and_original_parent_context"}


def _open_read(database):
    conn = connect(Path(database), readonly=True)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def _query(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _row_hash(row):
    return _sha(_encode(dict(row)))


def _file(path, role):
    path = Path(path).resolve()
    # Only hashes and locators, never evidence contents or authentication caches.
    if "private_access" in path.parts:
        raise ValueError("Authentication cache is not a Dataset artifact")
    data = path.read_bytes()
    return {"path": str(path), "role": role, "bytes": len(data), "sha256": _sha(data)}


def _preview(conn, database, *, project_id, run_id, data_dir, dataset_name, version,
             observation_cutoff_at):
    if type(project_id) is not int or type(run_id) is not int:
        raise ValueError("Explicit integer Project/Run IDs required")
    run_project(conn, run_id, project_id)
    rules = _rules(observation_cutoff_at)
    dataset_name, version = _identifier(dataset_name), _identifier(version)
    root = Path(data_dir).resolve()
    run = conn.execute("SELECT * FROM collection_runs WHERE run_id=?", (run_id,)).fetchone()
    if run["status"] not in ("completed", "partial", "failed", "cancelled") or not run["finished_at"]:
        raise ValueError("Dataset source Run must be terminal with finished_at")
    if observation_cutoff_at > run["finished_at"]:
        raise ValueError("Dataset cutoff exceeds source Run finish")
    if conn.execute("""SELECT 1 FROM collection_tasks t LEFT JOIN collection_attempts a USING(task_id)
        WHERE t.run_id=? AND (t.status IN ('pending','running') OR a.status='running'
        OR (a.attempt_id IS NOT NULL AND a.finished_at IS NULL)) LIMIT 1""", (run_id,)).fetchone():
        raise ValueError("Unfinished source collection activity")
    final = read_finalization(conn, root, run_id)
    dispositions, files = {}, {}
    if final:
        if not final["sealed"] or not final["checkpoint_synchronized"] or final["recovery_required"]:
            raise ValueError("Source finalization is not synchronized")
        dispositions = {d["post_id"]: dict(d) for d in final["intent"]["dispositions"]}
        for path, digest in final["intent"]["evidence_files"].items():
            f = _file(path, "source_evidence")
            if f["sha256"] != digest:
                raise ValueError("Source review/Raw evidence changed")
            files[f["path"]] = f
        for path in (root / "logs" / f"finalization_{run_id}.json", root / "logs" / f"checkpoint_{run_id}.json"):
            f = _file(path, "finalization" if path.name.startswith("finalization") else "checkpoint")
            files[f["path"]] = f
    candidates = _query(conn, """SELECT DISTINCT p.* FROM posts p JOIN search_hits h USING(post_id)
        JOIN search_requests q USING(search_request_id) JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
        WHERE q.run_id=? AND a.status IN ('succeeded','partial') ORDER BY p.post_id""", (run_id,))
    snapshots = _query(conn, """SELECT s.* FROM post_snapshots s
        JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id JOIN collection_tasks t USING(task_id)
        WHERE t.run_id=? AND a.status IN ('succeeded','partial') AND s.note_type='normal'
        AND s.observed_at<=? ORDER BY s.observed_at,s.snapshot_id""", (run_id, observation_cutoff_at))
    latest = {s["post_id"]: s for s in snapshots}
    tasks = _query(conn, "SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", (run_id,))
    if not final:
        # Legacy terminal Runs can have multiple observations. Restricted skips
        # still require the existing explicit local-finalization evidence.
        reviewer = _reviewer(conn, root)
        for t in tasks:
            if t["status"] == "cancelled" and reviewer._source_deleted_record(t):
                dispositions[t["post_id"]] = {"task_id": t["task_id"], "post_id": t["post_id"], "disposition": "source_deleted"}
                path = root / "logs" / f"detail_unavailable_{t['task_id']}.json"
                record = json.loads(path.read_text(encoding="utf-8"))["decision"]
                for path2 in (path, record["evidence_path"]):
                    f = _file(path2, "source_evidence")
                    files[f["path"]] = f
            elif t["status"] not in ("succeeded", "partial") or t["post_id"] not in latest:
                raise ValueError("Unsupported/unresolved source disposition")
    raw_rows = _query(conn, """SELECT f.* FROM raw_artifacts f JOIN collection_attempts a USING(attempt_id)
        JOIN collection_tasks t USING(task_id) WHERE t.run_id=? ORDER BY f.raw_artifact_id""", (run_id,))
    if conn.execute("""SELECT 1 FROM collection_attempts a JOIN collection_tasks t USING(task_id)
        LEFT JOIN raw_artifacts f USING(attempt_id) WHERE t.run_id=? GROUP BY a.attempt_id
        HAVING count(f.raw_artifact_id)<>1 LIMIT 1""", (run_id,)).fetchone():
        raise ValueError("Source Attempt lacks unique Raw evidence")
    for r in raw_rows:
        f = _file(r["file_path"], "raw")
        if f["sha256"] != r["sha256"] or (r["file_size_bytes"] is not None and f["bytes"] != r["file_size_bytes"]):
            raise ValueError("Raw evidence integrity mismatch")
        files[f["path"]] = f
    posts, selected = [], {}
    for p in candidates:
        post = p["post_id"]
        s = latest.get(post)
        d = dispositions.get(post)
        if s:
            status = "included"
            selected[post] = s
        elif d and d["disposition"] in ("source_deleted", "banned_or_restricted"):
            status = "detail_unavailable"
            if d["disposition"] == "source_deleted":
                review_path = root / "logs" / f"detail_unavailable_{d['task_id']}.json"
            else:
                skip = next(s for s in final["intent"]["decision"]["permanent_skips"] if s["task_id"] == d["task_id"])
                review_path = root / "logs" / f"pending_review_{d['task_id']}_{skip['attempt_id']}.json"
            review_raw = review_path.read_bytes()
            decision = json.loads(review_raw)["decision"]
            binding = decision["binding"]
            if (binding["run_id"], binding["task_id"], binding["post_id"]) != (run_id, d["task_id"], post):
                raise ValueError("Disposition review identity mismatch")
            d = {**d, "review_path": str(review_path), "review_sha256": _sha(review_raw),
                 "evidence_path": decision["evidence_path"], "evidence_sha256": decision["evidence_sha256"],
                 "finalization_sha256": final["sha256"] if final else None, "no_refill": True}
        elif conn.execute("""SELECT 1 FROM search_hits h JOIN search_requests q USING(search_request_id)
            JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id WHERE q.run_id=? AND h.post_id=?
            AND h.card_note_type='video' AND a.finished_at<=? LIMIT 1""", (run_id, post, observation_cutoff_at)).fetchone():
            status = "type_mismatch"
        else:
            raise ValueError("Candidate lacks a supported detail disposition at cutoff")
        posts.append({"post_id": post, "snapshot_id": s["snapshot_id"] if s else None,
                      "inclusion_status": status, "post_sha256": _row_hash(p),
                      "snapshot_sha256": _row_hash(s) if s else None,
                      "disposition": d if status == "detail_unavailable" else None})
    all_obs = _query(conn, """SELECT o.*,a.status AS attempt_status FROM comment_observations o
        JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id JOIN collection_tasks t USING(task_id)
        WHERE t.run_id=? AND o.observed_at<=? ORDER BY o.observed_at,o.observation_id""", (run_id, observation_cutoff_at))
    candidate_ids = {p["post_id"] for p in candidates}
    by_id, stable, anonymous = {}, {}, []
    for o in all_obs:
        if o["post_id"] not in candidate_ids or o["attempt_status"] not in ("succeeded", "partial"):
            raise ValueError("Comment source falls outside eligible candidates/Attempts")
        by_id[o["observation_id"]] = o
        if o["comment_id"] is None:
            anonymous.append(o)
        else:
            stable[o["comment_id"]] = o
    chosen = sorted([*stable.values(), *anonymous], key=lambda o: o["observation_id"])
    chosen_ids = {o["observation_id"] for o in chosen}
    comments = []
    for o in chosen:
        identity = conn.execute("SELECT * FROM comments WHERE comment_id=?", (o["comment_id"],)).fetchone()
        parent = by_id.get(o["parent_observation_id"])
        parent_selected = None
        if o["comment_level"] == 2:
            if not parent or parent["post_id"] != o["post_id"] or parent["comment_level"] != 1 or parent["collection_attempt_id"] != o["collection_attempt_id"]:
                raise ValueError("Original parent Observation cannot be traced")
            parent_selected = stable.get(parent["comment_id"]) if parent["comment_id"] is not None else parent
            if not parent_selected or parent_selected["observation_id"] not in chosen_ids or parent_selected["post_id"] != o["post_id"] or parent_selected["comment_level"] != 1:
                raise ValueError("Dataset parent identity cannot be traced")
        row = {k: v for k, v in o.items() if k != "attempt_status"}
        comments.append({"post_id": o["post_id"], "comment_id": o["comment_id"], "observation_id": o["observation_id"],
                         "comment_level": o["comment_level"], "observation_sha256": _row_hash(row),
                         "identity_sha256": _row_hash(identity) if identity else None,
                         "parent_observation_id": o["parent_observation_id"],
                         "dataset_parent_observation_id": parent_selected["observation_id"] if parent_selected else None})
    if any(o["post_id"] not in selected for o in chosen):
        raise ValueError("Comments on non-included candidate need explicit design review")
    config = json.loads(conn.execute("""SELECT p.comment_config_json FROM collection_runs r
        JOIN collection_plans p USING(plan_id) WHERE r.run_id=?""", (run_id,)).fetchone()[0])
    store = MediaStore(conn, root, directory=config.get("media_directory", "media"))
    snapshot_ids = {s["snapshot_id"] for s in selected.values()}
    media = []
    events, _, tail = store.read(run_id)
    media_events = {e["post_media_id"]: e for e in events}
    media_rows = _query(conn, """SELECT t.run_id,s.post_id,m.snapshot_id,m.media_id AS post_media_id,
        m.position,m.source_url FROM post_media m JOIN post_snapshots s USING(snapshot_id)
        JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id JOIN collection_tasks t USING(task_id)
        WHERE t.run_id=? ORDER BY m.media_id""", (run_id,))
    for m in media_rows:
        if m["snapshot_id"] not in snapshot_ids:
            continue
        event = media_events.get(m["post_media_id"], {})
        if tail or not event or event.get("status") != "success" or not store.bound(event, m) or not store.verify(event):
            raise ValueError("Selected media is missing, corrupt or unverified")
        media.append({**{k: m[k] for k in ("post_id", "snapshot_id", "post_media_id")},
                      **{k: event[k] for k in ("relative_path", "sha256", "bytes", "format", "width", "height")}})
    def blank(v):
        return v is None or (isinstance(v, str) and not v.strip())
    quality = {"empty_titles": sum(blank(s["title"]) for s in selected.values()),
               "empty_comment_content": sum(blank(o["content"]) for o in chosen),
               "missing_post_metrics": {key: sum(s[key] is None for s in selected.values()) for key in
                                        ("likes_count", "comments_count", "favorites_count", "shares_count")},
               "cross_observation_parent_links": sum(c["comment_level"] == 2 and c["parent_observation_id"] != c["dataset_parent_observation_id"] for c in comments)}
    counts = dict(Counter(p["inclusion_status"] for p in posts))
    for key in ("included", "detail_unavailable", "type_mismatch"):
        counts.setdefault(key, 0)
    counts.update(posts=len(posts), snapshots=len(selected), comments=len(comments),
                  top_comments=sum(c["comment_level"] == 1 for c in comments),
                  replies=sum(c["comment_level"] == 2 for c in comments), anonymous_comments=len(anonymous), media=len(media))
    assert_integrity(conn)
    return {"selection_rules": rules, "dataset_name": dataset_name, "version": version,
            "scope": {"project_id": project_id, "source_run_id": run_id},
            "source": {"database": str(Path(database).resolve()), "data_dir": str(root),
                       "run_evidence_sha256": _fingerprint(conn, run_id),
                       "finalization_sha256": final["sha256"] if final else None,
                       "run_finished_at": run["finished_at"]},
            "posts": posts, "comments": comments, "media": sorted(media, key=lambda m: m["post_media_id"]),
            "files": sorted(files.values(), key=lambda f: f["path"]), "counts": counts, "quality": quality,
            "warnings": ["Finite observed comments; blanks and unknown metrics retained."]}


def preview_dataset(source_database, *, project_id, run_id, data_dir, dataset_name, version,
                    observation_cutoff_at):
    """Read-only deterministic membership plan. Never returns source text/URLs."""
    conn = _open_read(source_database)
    try:
        return _preview(conn, source_database, project_id=project_id, run_id=run_id, data_dir=data_dir,
                        dataset_name=dataset_name, version=version, observation_cutoff_at=observation_cutoff_at)
    finally:
        conn.close()


def _manifest_path(directory, project_id, name, version):
    return Path(directory).resolve() / f"dataset_{project_id}_{_identifier(name)}_{_identifier(version)}.json"


def _locations(database, source_database, data_dir, manifest_dir, simulation):
    if type(simulation) is not bool:
        raise ValueError("Explicit simulation boolean required")
    target, source, root, output = map(lambda p: Path(p).resolve(), (database, source_database, data_dir, manifest_dir))
    if not target.is_file():
        raise ValueError("Existing target database required; initialization is not a Builder operation")
    same = target.samefile(source)
    if simulation == same:
        raise ValueError("Simulation requires distinct authority and target; formal requires same database")
    if simulation and target.is_relative_to(root):
        raise ValueError("Simulation target overlaps authoritative data")
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Manifest output overlaps authoritative data")
    return target, source, output


def _publish(path, content):
    """Fsync then exclusive hard-link publication; never overwrite a Manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _membership(conn, dataset_id):
    return {"posts": _query(conn, "SELECT post_id,snapshot_id,inclusion_status FROM dataset_posts WHERE dataset_id=? ORDER BY post_id", (dataset_id,)),
            "comments": _query(conn, "SELECT post_id,comment_id,observation_id FROM dataset_comments WHERE dataset_id=? ORDER BY observation_id", (dataset_id,))}


def _expected(plan):
    return {"posts": [{k: p[k] for k in ("post_id", "snapshot_id", "inclusion_status")} for p in plan["posts"]],
            "comments": [{k: c[k] for k in ("post_id", "comment_id", "observation_id")} for c in plan["comments"]]}


def _check_target(conn, plan):
    if _fingerprint(conn, plan["scope"]["source_run_id"]) != plan["source"]["run_evidence_sha256"]:
        raise ValueError("Target Run evidence differs from validated authority")


def _verify(conn, dataset_id, path, plan, target, simulation):
    row = conn.execute("SELECT * FROM dataset_versions WHERE dataset_id=?", (dataset_id,)).fetchone()
    if not row or row["status"] != "frozen":
        raise ValueError("Dataset is not frozen")
    raw = path.read_bytes()
    if _sha(raw) != row["manifest_sha256"]:
        raise ValueError("Manifest SHA-256 mismatch")
    manifest = json.loads(raw)
    if raw != _encode(manifest) or manifest.get("format") != FORMAT or manifest.get("plan") != plan:
        raise ValueError("Manifest differs from independently selected authority members/rules/evidence")
    implementation = manifest.get("implementation", {})
    if (not isinstance(implementation, dict) or set(implementation) != {"module", "sha256"}
            or implementation["module"] != "dataset_builder.py"
            or not isinstance(implementation["sha256"], str)
            or not re.fullmatch("[0-9a-f]{64}", implementation["sha256"])):
        raise ValueError("Invalid implementation provenance")
    descriptor = {k: row[k] for k in ("dataset_id", "project_id", "dataset_name", "version", "source_run_id", "created_at", "frozen_at")}
    if manifest.get("dataset") != descriptor or json.loads(row["selection_rules_json"]) != plan["selection_rules"]:
        raise ValueError("Dataset metadata/selection rules mismatch")
    if manifest.get("execution") != {"simulation": simulation, "target_database": str(target)}:
        raise ValueError("Manifest execution target/mode mismatch")
    if _membership(conn, dataset_id) != _expected(plan):
        raise ValueError("Actual database members differ from selected authority members")
    _check_target(conn, plan)
    checks = assert_integrity(conn)
    return {"dataset_id": dataset_id, "status": "frozen", "simulation": simulation,
            "manifest": str(path), "manifest_sha256": _sha(raw), "verified": True,
            "counts": plan["counts"], "quality": plan["quality"], "integrity": checks}


def verify_dataset(database, dataset_id, *, source_database, data_dir, manifest_dir, simulation):
    """Independent read-only source reselection, DB membership and file verification."""
    target, source, output = _locations(database, source_database, data_dir, manifest_dir, simulation)
    conn = _open_read(target)
    try:
        row = conn.execute("SELECT * FROM dataset_versions WHERE dataset_id=?", (dataset_id,)).fetchone()
        if not row:
            raise ValueError("Unknown Dataset")
        rules = json.loads(row["selection_rules_json"] or "{}")
        plan = preview_dataset(source, project_id=row["project_id"], run_id=row["source_run_id"], data_dir=data_dir,
                               dataset_name=row["dataset_name"], version=row["version"], observation_cutoff_at=rules.get("observation_cutoff_at"))
        path = _manifest_path(output, row["project_id"], row["dataset_name"], row["version"])
        return _verify(conn, dataset_id, path, plan, target, simulation)
    finally:
        conn.close()


def build_and_freeze_dataset(database, *, source_database, data_dir, manifest_dir, simulation,
                             project_id, run_id, dataset_name, version, observation_cutoff_at,
                             fault=lambda stage: None):
    """Explicit write; simulation=True can never target the authority database.

    fault stages support deterministic crash tests. Retry adopts an orphan
    Manifest's original freeze-intent timestamps/ID; conflicting intents fail.
    """
    target, source, output = _locations(database, source_database, data_dir, manifest_dir, simulation)
    args = dict(project_id=project_id, run_id=run_id, data_dir=data_dir, dataset_name=dataset_name,
                version=version, observation_cutoff_at=observation_cutoff_at)
    plan = preview_dataset(source, **args)
    path = _manifest_path(output, project_id, dataset_name, version)
    conn = connect(target)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_target(conn, plan)
        existing = conn.execute("SELECT * FROM dataset_versions WHERE project_id=? AND dataset_name=? AND version=?",
                                (project_id, dataset_name, version)).fetchone()
        if existing:
            result = _verify(conn, existing["dataset_id"], path, plan, target, simulation)
            conn.rollback()
            return {**result, "idempotent": True, "recovered_published_intent": False}
        # Revalidate authority while holding the target write lock. In formal
        # mode use this connection to avoid locking a second reader at commit.
        fresh = _preview(conn, source, **args) if not simulation else preview_dataset(source, **args)
        if fresh != plan:
            raise ValueError("Authority changed before freeze")
        orphan = json.loads(path.read_bytes()) if path.exists() else None
        created = orphan["dataset"]["created_at"] if orphan else _now()
        frozen = orphan["dataset"]["frozen_at"] if orphan else _now()
        if frozen < observation_cutoff_at:
            raise ValueError("Freeze time precedes cutoff")
        dataset_id = conn.execute("""INSERT INTO dataset_versions(project_id,dataset_name,version,source_run_id,
            selection_rules_json,created_at) VALUES(?,?,?,?,?,?)""",
            (project_id, dataset_name, version, run_id, _encode(plan["selection_rules"]).decode(), created)).lastrowid
        for p in plan["posts"]:
            conn.execute("INSERT INTO dataset_posts(dataset_id,post_id,snapshot_id,inclusion_status) VALUES(?,?,?,?)",
                         (dataset_id, p["post_id"], p["snapshot_id"], p["inclusion_status"]))
        for c in plan["comments"]:
            conn.execute("INSERT INTO dataset_comments(dataset_id,post_id,comment_id,observation_id) VALUES(?,?,?,?)",
                         (dataset_id, c["post_id"], c["comment_id"], c["observation_id"]))
        if _membership(conn, dataset_id) != _expected(plan):
            raise ValueError("Inserted members do not match approved selection")
        descriptor = dict(dataset_id=dataset_id, project_id=project_id, dataset_name=dataset_name,
                          version=version, source_run_id=run_id, created_at=created, frozen_at=frozen)
        manifest = {"format": FORMAT, "dataset": descriptor, "plan": plan,
                    "execution": {"simulation": simulation, "target_database": str(target)},
                    "implementation": {"module": "dataset_builder.py", "sha256": _sha(Path(__file__).read_bytes())}}
        if orphan:
            # Recovery requires the same implementation as the published intent.
            # Old frozen versions remain verifiable after compatible code changes.
            if orphan != manifest or path.read_bytes() != _encode(orphan):
                raise ValueError("Existing published Manifest conflicts with this freeze intent")
        raw = _encode(manifest)
        conn.execute("UPDATE dataset_versions SET status='frozen',frozen_at=?,manifest_sha256=? WHERE dataset_id=?",
                     (frozen, _sha(raw), dataset_id))
        assert_integrity(conn)
        fault("before_publish")
        if not orphan:
            _publish(path, raw)
        fault("after_publish")
        _verify(conn, dataset_id, path, plan, target, simulation)
        fault("before_commit")
        conn.commit()
        fault("after_commit")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    result = verify_dataset(target, dataset_id, source_database=source, data_dir=data_dir,
                            manifest_dir=output, simulation=simulation)
    return {**result, "idempotent": False, "recovered_published_intent": orphan is not None}
