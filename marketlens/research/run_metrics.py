"""Offline Run analytics, contract v1; no collection, export or Dataset writes.

``analyze_run(database, project_id=..., run_id=..., data_dir=...)`` opens its
own mode=ro connection. Section functions accept a query_only connection with
an active read transaction; callers must not share that connection with writers.
Results contain aggregates and internal IDs, never source text or credentials.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import sqlite3

from .database import connect
from .media_store import run_project
from .normalizers import METRICS
from .run_finalization import read_finalization, _fingerprint, _reviewer


CONTRACT_VERSION = "run_metrics_v1"
OBSERVATIONS = "All normal Snapshots/comments from succeeded feed_detail Attempts in this Run."
LATEST = ("One latest successful normal Snapshot per Post in this Run, ordered by "
          "observed_at then snapshot_id; comments from that Snapshot's Attempt only.")


def _scope(conn, project_id, run_id):
    if type(project_id) is not int or type(run_id) is not int:
        raise ValueError("Explicit integer Project and Run IDs required")
    if not conn.execute("PRAGMA query_only").fetchone()[0] or not conn.in_transaction:
        raise ValueError("Analytics requires query_only connection and active read transaction")
    run_project(conn, run_id, project_id)
    return {"project_id": project_id, "run_id": run_id}


def _section(scope, source, selection, warnings=()):
    return {"scope": scope, "source": source, "selection": selection,
            "warnings": list(warnings), "metrics": {}, "tables": {}}


def _put(section, name, value, unit="count", *, numerator=None, denominator=None,
         selection=None, missing="Missing values are not replaced with zero.", warnings=()):
    section["metrics"][name] = {
        "name": name, "value": value, "unit": unit, "numerator": numerator,
        "denominator": denominator, "scope": section["scope"],
        "selection": selection or section["selection"], "missing": missing,
        "warnings": list(warnings),
    }


def _rows(conn, sql, run_id):
    return [dict(r) for r in conn.execute(sql, (run_id,))]


def _snapshots(conn, run_id):
    rows = _rows(conn, """SELECT s.*,a.task_id,a.response_summary_json
        FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t USING(task_id)
        WHERE t.run_id=? AND t.task_type='feed_detail' AND a.status='succeeded'
        AND s.note_type='normal' AND s.post_id=t.post_id
        ORDER BY s.observed_at,s.snapshot_id""", run_id)
    return rows


def _comments(conn, run_id):
    return _rows(conn, """SELECT o.*,c.source_comment_id,c.parent_comment_id,
        c.comment_level AS identity_level,c.post_id AS identity_post_id
        FROM comment_observations o JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id
        JOIN collection_tasks t USING(task_id) LEFT JOIN comments c USING(comment_id)
        WHERE t.run_id=? AND t.task_type='feed_detail' AND a.status='succeeded'
        AND o.post_id=t.post_id AND EXISTS (SELECT 1 FROM post_snapshots s
          WHERE s.collection_attempt_id=a.attempt_id AND s.post_id=o.post_id AND s.note_type='normal')
        ORDER BY o.observation_id""", run_id)


def _distribution(values):
    values = sorted(values)
    def q(p):
        if not values:
            return None
        index = (len(values)-1)*p
        lo = int(index)
        return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(index-lo)
    return {"n": len(values), "sum": sum(values),
            "mean": sum(values)/len(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "p25": q(.25), "median": q(.5), "p75": q(.75), "p90": q(.9), "p95": q(.95),
            "quantile_method": "linear; rank=(n-1)*p",
            "histogram": [{"value": k, "posts": v} for k, v in sorted(Counter(values).items())]}


def search_sampling_metrics(conn: sqlite3.Connection, project_id: int, run_id: int) -> dict:
    """Persisted hits, including repeated hits; set metrics deduplicate post_id."""
    scope = _scope(conn, project_id, run_id)
    out = _section(scope, ["search_requests", "search_hits", "collection_tasks", "collection_attempts"],
                   "All persisted search Hits in this Run; unique candidate key=post_id.")
    requests = _rows(conn, "SELECT * FROM search_requests WHERE run_id=? ORDER BY sequence_no,search_request_id", run_id)
    hits = _rows(conn, """SELECT h.post_id,h.card_note_type,h.search_request_id,
        q.keyword,q.sort_method,q.sequence_no,t.run_id AS attempt_run,t.search_request_id AS attempt_request,
        t.task_type FROM search_hits h JOIN search_requests q USING(search_request_id)
        JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
        JOIN collection_tasks t USING(task_id) WHERE q.run_id=? ORDER BY h.search_hit_id""", run_id)
    if any(h["attempt_run"] != run_id or h["attempt_request"] != h["search_request_id"]
           or h["task_type"] != "search" for h in hits):
        raise ValueError("Search Hit/Attempt/Run scope mismatch")
    attempt_counts = dict(conn.execute("""SELECT a.status,count(*) FROM collection_attempts a
        JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND t.task_type='search' GROUP BY a.status""", (run_id,)))
    by_request, types = defaultdict(list), defaultdict(set)
    for h in hits:
        by_request[h["search_request_id"]].append(h["post_id"])
        types[h["post_id"]].add(h["card_note_type"])
    for name, value in (("search_requests", len(requests)),
                        ("successful_search_requests", sum(r["status"] == "succeeded" for r in requests)),
                        ("search_attempts", sum(attempt_counts.values())),
                        ("successful_search_attempts", attempt_counts.get("succeeded", 0)),
                        ("search_hits", len(hits)), ("unique_candidates", len(types)),
                        ("normal_candidates", sum(t == {"normal"} for t in types.values())),
                        ("video_candidates", sum(t == {"video"} for t in types.values())),
                        ("conflicting_type_candidates", sum(len(t) > 1 for t in types.values())),
                        ("unknown_type_candidates", sum(len(t) == 1 and not t <= {"normal", "video"} for t in types.values()))):
        _put(out, name, value)
    _put(out, "search_attempt_statuses", attempt_counts, "attempts_by_status")
    _put(out, "repeat_hit_rate", (len(hits)-len(types))/len(hits) if hits else None, "ratio",
         numerator=len(hits)-len(types), denominator=len(hits), missing="Zero hits => null rate.")
    for field, label in (("keyword", "keywords"), ("sort_method", "sorts")):
        keys = list(dict.fromkeys(r[field] for r in requests))
        sets = {k: {h["post_id"] for h in hits if h[field] == k} for k in keys}
        memberships = Counter(p for ids in sets.values() for p in ids)
        out["tables"][label] = [{field: k, "hits": sum(h[field] == k for h in hits),
            "unique_candidates": len(sets[k]),
            "exclusive_candidates": sum(memberships[p] == 1 for p in sets[k])} for k in keys]
        if label == "sorts":
            out["tables"]["sort_overlap"] = [{"left": a, "right": b,
                "intersection": len(sets[a] & sets[b]), "union": len(sets[a] | sets[b]),
                "jaccard": len(sets[a] & sets[b])/len(sets[a] | sets[b]) if sets[a] | sets[b] else None}
                for a, b in combinations(keys, 2)]
    seen, timeline = set(), []
    for r in requests:
        ids = set(by_request[r["search_request_id"]])
        new = len(ids-seen)
        seen.update(ids)
        timeline.append({"search_request_id": r["search_request_id"], "sequence_no": r["sequence_no"],
                         "keyword": r["keyword"], "sort_method": r["sort_method"],
                         "hits": len(by_request[r["search_request_id"]]), "unique_candidates": len(ids),
                         "new_candidates": new, "cumulative_candidates": len(seen)})
    out["tables"]["discovery_timeline"] = timeline
    out["table_definitions"] = {"exclusive_candidates": "Seen under exactly one keyword/sort, independent of execution order.",
        "new_candidates": "Not seen before this request in sequence_no/search_request_id order.",
        "jaccard": "intersection/union; empty union => null."}
    if any(len(t) > 1 for t in types.values()):
        out["warnings"].append("Conflicting candidate types are separate, not counted as normal or video.")
    return out


def detail_coverage_metrics(conn: sqlite3.Connection, project_id: int, run_id: int, *, data_dir) -> dict:
    """Reuse the collector's immutable disposition validators; never infer from status alone."""
    scope = _scope(conn, project_id, run_id)
    out = _section(scope, ["search_hits", "collection_tasks", "post_snapshots", "finalization/review evidence"], OBSERVATIONS)
    state = read_finalization(conn, data_dir, run_id)
    inspector = _reviewer(conn, data_dir)  # Same read-only reviewer used by local finalization.
    tasks = _rows(conn, "SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", run_id)
    deleted, skipped = set(), set()
    if state and state["sealed"]:
        for d in state["intent"]["dispositions"]:
            if d["disposition"] == "source_deleted":
                deleted.add(d["task_id"])
            elif d["disposition"] == "banned_or_restricted":
                skipped.add(d["task_id"])
    else:
        for t in tasks:
            if inspector._source_deleted_record(t):
                deleted.add(t["task_id"])
    snaps = _snapshots(conn, run_id)
    successful = {s["task_id"] for s in snaps}
    candidates = defaultdict(set)
    for h in _rows(conn, """SELECT h.post_id,h.card_note_type FROM search_hits h
        JOIN search_requests q USING(search_request_id) WHERE q.run_id=?""", run_id):
        candidates[h["post_id"]].add(h["card_note_type"])
    normal = {p for p, t in candidates.items() if t == {"normal"}}
    video = {p for p, t in candidates.items() if t == {"video"}}
    per_post = Counter(s["post_id"] for s in snaps)
    for name, value in (("normal_candidates", len(normal)), ("video_candidates", len(video)),
                        ("detail_tasks", len(tasks)), ("successful_detail_tasks", len(successful)),
                        ("successful_snapshots", len(snaps)), ("posts_with_successful_snapshot", len(per_post)),
                        ("posts_with_multiple_snapshots", sum(n > 1 for n in per_post.values())),
                        ("source_deleted", len(deleted)), ("permanently_skipped_restricted", len(skipped)),
                        ("candidates_without_detail", len(set(candidates)-per_post.keys())),
                        ("normal_candidates_without_detail", len(normal-per_post.keys())),
                        ("successful_posts_outside_candidates", len(per_post.keys()-candidates.keys())),
                        ("unresolved_detail_tasks", len({t['task_id'] for t in tasks}-successful-deleted-skipped))):
        _put(out, name, value)
    _put(out, "normal_detail_success_rate", len(normal & per_post.keys())/len(normal) if normal else None,
         "ratio", numerator=len(normal & per_post.keys()), denominator=len(normal), missing="No normal candidates => null rate.")
    out["finalization"] = {"validated": state is not None, "sealed": bool(state and state["sealed"]),
        "checkpoint_synchronized": state["checkpoint_synchronized"] if state else None,
        "recovery_required": state["recovery_required"] if state else None,
        "sha256": state["sha256"] if state else None}
    if state and state["recovery_required"]:
        out["warnings"].append("Finalization recovery is required; this is not a synchronized final report.")
    return out


def comment_metrics(conn: sqlite3.Connection, project_id: int, run_id: int) -> dict:
    """Observation totals preserve history; post distributions avoid repeat weighting."""
    scope = _scope(conn, project_id, run_id)
    out = _section(scope, ["comment_observations", "comments", "post_snapshots", "Attempt summaries"], OBSERVATIONS,
                   ["No observed comments does not establish that the source has no comments.",
                    "Finite returned comments and has_more do not establish complete comment coverage."])
    rows = _comments(conn, run_id)
    identities = Counter(r["comment_id"] for r in rows if r["comment_id"] is not None)
    for name, value in (("top_observations", sum(r["comment_level"] == 1 for r in rows)),
                        ("reply_observations", sum(r["comment_level"] == 2 for r in rows)),
                        ("comment_observations", len(rows)), ("stable_comment_identities", len(identities)),
                        ("identities_with_repeat_observations", sum(n > 1 for n in identities.values())),
                        ("extra_stable_observations", sum(n-1 for n in identities.values())),
                        ("anonymous_observations", sum(r["comment_id"] is None for r in rows)),
                        ("anonymous_top_observations", sum(r["comment_id"] is None and r["comment_level"] == 1 for r in rows)),
                        ("anonymous_reply_observations", sum(r["comment_id"] is None and r["comment_level"] == 2 for r in rows)),
                        ("stable_top_identities", len({r['comment_id'] for r in rows if r['comment_level'] == 1 and r['comment_id'] is not None})),
                        ("stable_reply_identities", len({r['comment_id'] for r in rows if r['comment_level'] == 2 and r['comment_id'] is not None}))):
        _put(out, name, value)
    latest = {s["post_id"]: s for s in _snapshots(conn, run_id)}
    config = json.loads(conn.execute("""SELECT p.comment_config_json FROM collection_runs r
        JOIN collection_plans p USING(plan_id) WHERE r.run_id=?""", (run_id,)).fetchone()[0])
    default_target = config.get("top_comment_target")
    by_observation = {r["observation_id"]: r for r in rows}
    selected = defaultdict(list)
    for r in rows:
        s = latest.get(r["post_id"])
        if s and s["collection_attempt_id"] == r["collection_attempt_id"]:
            selected[r["post_id"]].append(r)
    counts, stable_counts, zero, reached, below, exceeded, unknown = [], [], 0, 0, 0, 0, 0
    has_more = Counter({"true": 0, "false": 0, "unknown": 0})
    for post, s in latest.items():
        observed = selected[post]
        top = [r for r in observed if r["comment_level"] == 1]
        n = len({r["comment_id"] for r in top if r["comment_id"] is not None})
        counts.append(n + sum(r["comment_id"] is None for r in top))
        stable_counts.append(n)
        zero += not observed
        summary = json.loads(s["response_summary_json"] or "{}").get("comments", {})
        h = summary.get("has_more")
        has_more["true" if h is True else "false" if h is False else "unknown"] += 1
        target = summary.get("target_unique", default_target)
        if type(target) is not int or target <= 0:
            unknown += 1
        else:
            reached += n >= target
            below += n < target
            exceeded += n > target
    for name, value in (("posts_evaluated", len(latest)), ("posts_without_observed_comments", zero),
                        ("posts_without_observed_top_comments", counts.count(0)),
                        ("posts_at_or_above_target", reached), ("posts_below_target", below),
                        ("posts_above_target", exceeded), ("posts_with_unknown_target", unknown)):
        _put(out, name, value, selection=LATEST + " Target uses unique stable top IDs; anonymous observations cannot establish target attainment.")
    _put(out, "top_comments_per_post", _distribution(counts), "distribution", selection=LATEST + " Stable IDs deduplicated; anonymous observations retained separately.")
    _put(out, "stable_top_comments_per_post", _distribution(stable_counts), "distribution", selection=LATEST)
    _put(out, "has_more", dict(has_more), "posts_by_state", selection=LATEST + " Read saved Attempt summary; only JSON booleans are known.")
    _put(out, "target_attainment_rate", reached/(len(latest)-unknown) if len(latest) > unknown else None,
         "ratio", numerator=reached, denominator=len(latest)-unknown, selection=LATEST,
         missing="Unknown targets excluded from denominator; zero denominator => null.")
    errors = Counter({"parent_observation": 0, "parent_identity": 0, "parent_source_id": 0, "identity_binding": 0})
    for r in rows:
        if r["comment_id"] is not None and (r["identity_level"] != r["comment_level"] or r["identity_post_id"] != r["post_id"]):
            errors["identity_binding"] += 1
        if r["comment_level"] != 2:
            continue
        parent = by_observation.get(r["parent_observation_id"])
        valid = parent and parent["comment_level"] == 1 and parent["post_id"] == r["post_id"] and parent["collection_attempt_id"] == r["collection_attempt_id"]
        if not valid:
            errors["parent_observation"] += 1
        else:
            errors["parent_identity"] += r["parent_comment_id"] != parent["comment_id"]
            errors["parent_source_id"] += r["parent_source_comment_id"] != parent["source_comment_id"]
    _put(out, "relationship_errors", dict(errors), "errors_by_type")
    if any(errors.values()):
        out["warnings"].append("Comment relationship anomalies require review.")
    return out


def completeness_metrics(conn: sqlite3.Connection, project_id: int, run_id: int) -> dict:
    """Completeness per observation, not per stable identity; no cleaning/deletion."""
    scope = _scope(conn, project_id, run_id)
    out = _section(scope, ["post_snapshots", "comment_observations", "engagement_parse_json"], OBSERVATIONS)
    snapshots, comments = _snapshots(conn, run_id), _comments(conn, run_id)
    fields = []
    def field(rows, entity, key, *, text=False, precision=None):
        n = len(rows)
        nulls = sum(r[key] is None for r in rows)
        blanks = sum(isinstance(r[key], str) and not r[key].strip() for r in rows) if text else 0
        missing = nulls+blanks
        data = {"total": n, "valid": n-missing, "missing": missing, "null": nulls, "blank": blanks,
                "known_zero": sum(r[key] == 0 for r in rows) if not text else 0,
                "precision": dict(Counter(precision)) if precision is not None else None}
        _put(out, f"{entity}.{key}", data, "field_completeness",
             numerator=missing, denominator=n,
             selection=OBSERVATIONS + f" Unit={entity} observation; field={key}.",
             missing="NULL/blank text missing; numeric NULL unknown, actual zero retained; precision categories retained.",
             warnings=["Missing observations retained; do not impute zero or delete records."] if missing else [])
        _put(out, f"{entity}.{key}.missing_rate", missing/n if n else None, "ratio",
             numerator=missing, denominator=n, missing="Zero observations => null rate.")
        if missing:
            fields.append(f"{entity}.{key}")
    for key in ("title", "description", "published_at_raw", "published_at"):
        field(snapshots, "snapshot", key, text=True)
    for metric, (_, column) in METRICS.items():
        precision = [json.loads(s["engagement_parse_json"] or "{}").get(metric, {}).get("precision", "unknown") for s in snapshots]
        field(snapshots, "snapshot", column, precision=precision)
    for key in ("content", "created_at_raw", "created_at"):
        field(comments, "comment", key, text=True)
    field(comments, "comment", "like_count", precision=[r["like_count_precision"] or "unknown" for r in comments])
    if fields:
        out["warnings"].append("Missing fields flagged, records retained: " + ", ".join(fields))
    return out


def analyze_run(database, *, project_id: int, run_id: int, data_dir) -> dict:
    """Analyze a consistent mode=ro SQLite view. Does not initialize or migrate DB."""
    conn = connect(Path(database), readonly=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        scope = _scope(conn, project_id, run_id)
        run = dict(conn.execute("SELECT * FROM collection_runs WHERE run_id=?", (run_id,)).fetchone())
        sections = {
            "search": search_sampling_metrics(conn, project_id, run_id),
            "detail": detail_coverage_metrics(conn, project_id, run_id, data_dir=data_dir),
            "comments": comment_metrics(conn, project_id, run_id),
            "completeness": completeness_metrics(conn, project_id, run_id),
        }
        return {"contract_version": CONTRACT_VERSION, "scope": scope,
                "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "source": {"database": str(Path(database).resolve()), "access": "mode=ro;query_only;read_transaction",
                           "run_status": run["status"], "run_started_at": run["started_at"],
                           "run_finished_at": run["finished_at"], "run_evidence_sha256": _fingerprint(conn, run_id)},
                "warnings": ["Run metrics describe collected observations, not a frozen Dataset or platform population."],
                **sections}
    finally:
        conn.close()
