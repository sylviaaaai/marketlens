"""Reproducible Run-scoped views; SQLite remains authoritative, no Dataset freeze."""
import hashlib
import json
from pathlib import Path

from .access_cache import atomic_write
from .media_store import MediaStore, run_project


def read_run(conn, project_id, run_id, data_dir):
    """Read a consistent SQLite view without side effects or network access."""
    if conn.in_transaction:
        return _read_run(conn, project_id, run_id, data_dir)
    conn.execute("BEGIN")
    try:
        return _read_run(conn, project_id, run_id, data_dir)
    finally:
        conn.rollback()


def _read_run(conn, project_id, run_id, data_dir):
    """All successful snapshots in this Run, ordered by ID, never global latest.

    Text is returned unchanged. Parent context uses the same detail Attempt.
    This function creates no directories, writes no DB rows and makes no requests.
    """
    run_project(conn, run_id, project_id)
    plan = json.loads(conn.execute("SELECT p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE run_id=?", (run_id,)).fetchone()[0])
    store = MediaStore(conn, data_dir, directory=plan.get("media_directory", "media"))
    snapshots = conn.execute("""SELECT s.*,p.source_post_id FROM post_snapshots s
        JOIN posts p USING(post_id) JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND t.task_type='feed_detail'
        AND a.status='succeeded' ORDER BY s.snapshot_id""", (run_id,)).fetchall()
    fields = ("post_id", "snapshot_id", "source_post_id", "collection_attempt_id", "title", "description",
              "published_at", "published_at_raw", "observed_at", "likes_count", "comments_count",
              "favorites_count", "shares_count", "raw_artifact_id")
    posts, comments = [], []
    for snapshot in snapshots:
        item = {"project_id": project_id, "run_id": run_id, **{k: snapshot[k] for k in fields}}
        item["engagement_precision"] = json.loads(snapshot["engagement_parse_json"] or "null")
        item["search_hits"] = [dict(r) for r in conn.execute("""SELECT h.search_hit_id,h.search_request_id,
            h.collection_attempt_id,h.result_position,q.keyword,q.sort_method FROM search_hits h
            JOIN search_requests q USING(search_request_id) WHERE q.run_id=? AND h.post_id=?
            ORDER BY q.sequence_no,h.result_position,h.search_hit_id""", (run_id, snapshot["post_id"]))]
        posts.append(item)
        rows = conn.execute("""SELECT o.observation_id,o.comment_id,o.post_id,o.collection_attempt_id,
            o.content,o.comment_level,o.parent_observation_id,c.parent_comment_id,o.parent_source_comment_id,
            o.created_at,o.created_at_raw,o.observed_at,o.like_count,o.like_count_raw,o.like_count_precision,
            o.source_path,o.display_position,parent.content AS parent_content
            FROM comment_observations o LEFT JOIN comments c USING(comment_id)
            LEFT JOIN comment_observations parent ON parent.observation_id=o.parent_observation_id
            AND parent.post_id=o.post_id AND parent.collection_attempt_id=o.collection_attempt_id
            WHERE o.collection_attempt_id=? AND o.post_id=? ORDER BY o.observation_id""",
            (snapshot["collection_attempt_id"], snapshot["post_id"]))
        comments.extend({"project_id": project_id, "run_id": run_id, "snapshot_id": snapshot["snapshot_id"], **dict(r)} for r in rows)
    media = [{"project_id": project_id, **r} for r in store.states(run_id)]
    return {"posts": posts, "comments": comments, "media": media}


def export_run(conn, project_id, run_id, data_dir, output_dir=None):
    """Explicit derived-file write. Completion manifest hashes detect partial bundles.

    Caller holds the existing single-writer CLI lock for a stable DB/media view.
    All JSONL are reproducible for unchanged SQLite, journal and verified files.
    """
    if conn.in_transaction:
        raise ValueError("ExportRequiresOwnReadTransaction")
    conn.execute("BEGIN")
    try:
        rows = read_run(conn, project_id, run_id, data_dir)
    finally:
        conn.rollback()
    output = Path(output_dir) if output_dir is not None else Path(data_dir) / "exports" / f"run_{run_id}"
    output = output.resolve()
    # Never replace authoritative evidence through the export API.
    root = Path(data_dir).resolve()
    plan = json.loads(conn.execute("SELECT p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE run_id=?", (run_id,)).fetchone()[0])
    protected = [root / name for name in ("raw", "logs", "private_access", plan.get("media_directory", "media"))]
    if output == root or any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError("ExportOverlapsAuthoritativeStorage")
    manifest = {"version": 1, "project_id": project_id, "run_id": run_id,
                "selection": "all_successful_snapshots_in_run_ordered_by_snapshot_id", "files": {}}
    encoded = {}
    for kind, records in rows.items():
        name = kind + ".jsonl"
        encoded[name] = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))+"\n" for r in records).encode("utf-8")
        manifest["files"][name] = {"rows": len(records), "sha256": hashlib.sha256(encoded[name]).hexdigest()}
    marker = output / "export_manifest.json"
    if marker.exists():
        old = json.loads(marker.read_text(encoding="utf-8"))
        if old.get("project_id") != project_id or old.get("run_id") != run_id:
            raise ValueError("ExportDirectoryBelongsToAnotherRun")
    elif output.exists() and any(output.iterdir()):
        # Allow recovery only for this format's interrupted derived bundle.
        if any(p.name not in encoded for p in output.iterdir()):
            raise ValueError("ExportDirectoryNotEmpty")
        for path in output.iterdir():
            with path.open(encoding="utf-8") as handle:
                first = handle.readline()
            if first:
                row = json.loads(first)
                if row.get("project_id") != project_id or row.get("run_id") != run_id:
                    raise ValueError("InterruptedExportBelongsToAnotherRun")
    output.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    for name, content in encoded.items():
        atomic_write(output / name, content)
    atomic_write(marker, (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode("utf-8"))
    return manifest
