"""Run analytics over synthetic_demo evidence; all external operations forbidden."""
from contextlib import contextmanager
import hashlib
import json
import socket
import sqlite3
import subprocess

import pytest

from test_final_preflight import memory
from test_detail_auto_retry import clock
from test_smoke import FakeAdapter, detail, response
from marketlens.research.collection_pipeline import CollectionPipeline, insert
from marketlens.research.database import initialize, assert_integrity
from marketlens.research.normalizers import comment_stats
from marketlens.research import run_metrics as metrics


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Analytics must not access network, Docker, subprocesses or DPAPI")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("marketlens.research.access_cache.dpapi", forbidden)


class Pipeline(CollectionPipeline):
    target = 2

    def response_metadata(self, resp, safe, sid):
        result = resp.summary()
        if sid:
            result["comments"] = comment_stats(safe, sid, target_unique=self.target or 2)
            if self.target is None:
                result["comments"].pop("target_unique")
        return result


@contextmanager
def reading(conn):
    before = conn.total_changes
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
    denied = []
    def authorizer(op, *args):
        if op in writes:
            denied.append(op)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorizer)
    try:
        yield
        assert not denied and conn.total_changes == before
    finally:
        conn.set_authorizer(None)
        conn.rollback()
        conn.execute("PRAGMA query_only=OFF")


def feed(sid, kind="normal"):
    return {"id": sid, "noteCard": {"type": kind}}


def search_response(p, run, request, task, feeds):
    attempt = p.begin_attempt(task, 1, "/api/v1/feeds/search")
    assert not p.record_response(attempt, response({"success": True, "data": {"feeds": feeds}}), request_id=request)["ingestion_error"]
    p.finish_task(task, "succeeded")
    p.conn.execute("UPDATE search_requests SET status='succeeded',returned_count=? WHERE search_request_id=?", (len(feeds), request))


def add_search(p, run, sequence, keyword, sort, feeds):
    request = insert(p.conn, "search_requests", run_id=run, sequence_no=sequence, keyword=keyword, sort_method=sort,
                     filters_json=json.dumps({"note_type": "图文", "sort_by": sort}), status="running")
    task = insert(p.conn, "collection_tasks", run_id=run, task_type="search", search_request_id=request, sequence_no=sequence, status="running")
    search_response(p, run, request, task, feeds)


def build(conn, root, *, second_observation=True, reverse=False, target=2, anonymous=True):
    p = Pipeline(conn, FakeAdapter(), root)
    p.target = target
    project = insert(conn, "research_projects", research_question="synthetic_demo", category="synthetic_demo")
    plan = insert(conn, "collection_plans", project_id=project, plan_name="synthetic_demo",
                  search_filters_json=json.dumps({"note_type": "图文"}), comment_config_json=json.dumps({"top_comment_target": target}))
    for seq, keyword in enumerate(("synthetic_a", "synthetic_b", "synthetic_empty"), 1):
        insert(conn, "plan_keywords", plan_id=plan, keyword=keyword, sequence_no=seq)
    for seq, sort in enumerate(("综合", "最新", "最多点赞"), 1):
        insert(conn, "plan_sort_methods", plan_id=plan, sort_method=sort, sequence_no=seq)
    run = insert(conn, "collection_runs", plan_id=plan, status="running")
    searches = [("synthetic_a", "综合", [feed("synthetic_0"), feed("synthetic_0"), feed("synthetic_1"), feed("synthetic_video", "video")]),
                ("synthetic_b", "最新", [feed("synthetic_0"), feed("synthetic_2")])]
    for seq, (keyword, sort, feeds) in enumerate(reversed(searches) if reverse else searches, 1):
        add_search(p, run, seq, keyword, sort, feeds)
    add_search(p, run, 3, "synthetic_empty", "最多点赞", [])
    tasks = {}
    for seq, sid in enumerate(("synthetic_0", "synthetic_1", "synthetic_2"), 10):
        post = conn.execute("SELECT post_id FROM posts WHERE source_post_id=?", (sid,)).fetchone()[0]
        tasks[sid] = insert(conn, "collection_tasks", run_id=run, task_type="feed_detail", post_id=post, sequence_no=seq, status="running")
        if sid == "synthetic_2":
            continue
        for number in range(1, (3 if second_observation else 2) if sid == "synthetic_0" else 2):
            attempt = p.begin_attempt(tasks[sid], number, "/api/v1/feeds/detail")
            payload = detail(sid)
            note = payload["data"]["data"]["note"]
            note.update(title=" " if number == 1 else "synthetic_demo", desc="synthetic_demo", time=1702195200000)
            note["interactInfo"] = {"likedCount": "0", "commentCount": "", "collectedCount": "1.2万"}
            block = payload["data"]["data"]["comments"]
            if not anonymous:
                block["list"][1]["id"] = "synthetic_second_parent"
                block["list"][1]["subComments"][0]["id"] = "synthetic_second_reply"
            if sid == "synthetic_1":
                block["list"] = []
                block.pop("hasMore", None)
            else:
                block["hasMore"] = number == 1
                if number == 2:
                    block["list"] = block["list"][:1]
            assert not p.record_response(attempt, response(payload, "/api/v1/feeds/detail"), post_id=post, source_id=sid)["ingestion_error"]
        p.finish_task(tasks[sid], "succeeded")
    return p, project, run


def value(section, key):
    return section["metrics"][key]["value"]


def test_scope_observations_and_completeness(memory, tmp_path):
    p, project, run = build(memory, tmp_path)
    # Another project observes the SAME platform identities with different contents.
    _, other_project, other_run = build(memory, tmp_path / "other", second_observation=False)
    # Empty second Run in the first project; stable IDs are not its observations.
    plan = memory.execute("SELECT plan_id FROM collection_runs WHERE run_id=?", (run,)).fetchone()[0]
    empty_run = insert(memory, "collection_runs", plan_id=plan)
    with reading(memory):
        search = metrics.search_sampling_metrics(memory, project, run)
        assert value(search, "search_hits") == 6
        assert value(search, "unique_candidates") == 4
        assert value(search, "normal_candidates") == 3
        assert value(search, "video_candidates") == 1
        assert [r["new_candidates"] for r in search["tables"]["discovery_timeline"]] == [3, 1, 0]
        assert search["tables"]["keywords"][0]["exclusive_candidates"] == 2
        assert search["tables"]["sort_overlap"][0]["jaccard"] == .25
        detail_result = metrics.detail_coverage_metrics(memory, project, run, data_dir=tmp_path)
        assert value(detail_result, "successful_snapshots") == 3
        assert value(detail_result, "posts_with_successful_snapshot") == 2
        assert value(detail_result, "posts_with_multiple_snapshots") == 1
        assert value(detail_result, "candidates_without_detail") == 2
        comment = metrics.comment_metrics(memory, project, run)
        assert value(comment, "comment_observations") == 6
        assert value(comment, "stable_comment_identities") == 2
        assert value(comment, "extra_stable_observations") == 2
        assert value(comment, "anonymous_observations") == 2
        assert value(comment, "posts_without_observed_comments") == 1
        assert value(comment, "top_comments_per_post")["mean"] == .5
        assert value(comment, "has_more") == {"true": 0, "false": 1, "unknown": 1}
        assert not any(value(comment, "relationship_errors").values())
        quality = metrics.completeness_metrics(memory, project, run)
        assert value(quality, "snapshot.likes_count")["known_zero"] == 3
        assert value(quality, "snapshot.comments_count")["null"] == 3
        assert value(quality, "snapshot.favorites_count")["precision"] == {"approximate": 3}
        assert value(quality, "snapshot.title")["missing"] == 2
        for func in (metrics.search_sampling_metrics, metrics.comment_metrics, metrics.completeness_metrics):
            with pytest.raises(ValueError, match="RunProjectMismatch"):
                func(memory, other_project, run)
        empty = metrics.comment_metrics(memory, project, empty_run)
        assert value(empty, "comment_observations") == 0
        assert value(empty, "target_attainment_rate") is None
        assert value(empty, "top_comments_per_post")["median"] is None
        assert value(metrics.completeness_metrics(memory, project, empty_run), "snapshot.title.missing_rate") is None
        assert value(metrics.search_sampling_metrics(memory, project, empty_run), "repeat_hit_rate") is None
        assert value(metrics.search_sampling_metrics(memory, other_project, other_run), "search_hits") == 6
    assert assert_integrity(memory)["integrity_check"] == ["ok"]


def test_discovery_order_does_not_change_exclusive_contribution(memory, tmp_path):
    p, project, run = build(memory, tmp_path)
    with reading(memory):
        first = metrics.search_sampling_metrics(memory, project, run)
    _, project2, run2 = build(memory, tmp_path / "reversed", reverse=True)
    with reading(memory):
        second = metrics.search_sampling_metrics(memory, project2, run2)
    assert [r["new_candidates"] for r in second["tables"]["discovery_timeline"]] == [2, 2, 0]
    assert {r['keyword']: r['exclusive_candidates'] for r in first['tables']['keywords']} == {r['keyword']: r['exclusive_candidates'] for r in second['tables']['keywords']}


def test_target_uses_stable_ids_and_anonymous_is_not_deduplicated(memory, tmp_path):
    _, project, run = build(memory, tmp_path, second_observation=False)
    with reading(memory):
        result = metrics.comment_metrics(memory, project, run)
    assert value(result, "top_comments_per_post")["max"] == 2
    assert value(result, "stable_top_comments_per_post")["max"] == 1
    assert value(result, "posts_below_target") == 2
    assert value(result, "has_more")["true"] == 1


def test_query_only_and_transaction_required(memory):
    with pytest.raises(ValueError, match="query_only"):
        metrics.search_sampling_metrics(memory, 1, 1)
    memory.execute("PRAGMA query_only=ON")
    with pytest.raises(ValueError, match="active read transaction"):
        metrics.comment_metrics(memory, 1, 1)


@pytest.mark.parametrize("target,reached,below,above,unknown", [
    (1, 1, 1, 1, 0), (2, 1, 1, 0, 0), (3, 0, 2, 0, 0), (None, 0, 0, 0, 2)])
def test_target_boundaries(memory, tmp_path, target, reached, below, above, unknown):
    _, project, run = build(memory, tmp_path, second_observation=False, anonymous=False, target=target)
    with reading(memory):
        result = metrics.comment_metrics(memory, project, run)
    assert value(result, 'posts_at_or_above_target') == reached
    assert value(result, 'posts_below_target') == below
    assert value(result, 'posts_above_target') == above
    assert value(result, 'posts_with_unknown_target') == unknown


def test_parent_anomalies_are_reported_without_modifying_evidence(memory, tmp_path, monkeypatch):
    _, project, run = build(memory, tmp_path, second_observation=False)
    with reading(memory):
        rows = metrics._comments(memory, run)
        reply = next(r for r in rows if r['comment_level'] == 2 and r['comment_id'] is not None)
        reply['parent_observation_id'] = -1
        monkeypatch.setattr(metrics, '_comments', lambda conn, run: rows)
        result = metrics.comment_metrics(memory, project, run)
    assert value(result, 'relationship_errors')['parent_observation'] == 1
    assert 'Comment relationship anomalies require review.' in result['warnings']


def test_real_readonly_entrypoint_and_no_missing_database_creation(tmp_path, monkeypatch):
    path = tmp_path / "synthetic_demo.db"
    conn = initialize(path)
    _, project, run = build(conn, tmp_path)
    conn.close()
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    original = metrics.connect
    def checked(path, *, readonly):
        assert readonly is True
        c = original(path, readonly=readonly)
        def deny(op, *args):
            assert op not in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            return sqlite3.SQLITE_OK
        c.set_authorizer(deny)
        return c
    monkeypatch.setattr(metrics, "connect", checked)
    result = metrics.analyze_run(path, project_id=project, run_id=run, data_dir=tmp_path)
    assert value(result['search'], 'search_hits') == 6
    json.dumps(result, allow_nan=False)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match="RunProjectMismatch"):
        metrics.analyze_run(path, project_id=999, run_id=run, data_dir=tmp_path)
    missing = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        metrics.analyze_run(missing, project_id=1, run_id=1, data_dir=tmp_path)
    assert not missing.exists()


def test_validated_deleted_and_restricted_remain_distinct(memory, tmp_path, clock):
    from test_source_deleted_review import blocked_target, confirm
    from test_pending_review import evidence_for, review
    from test_run_finalization import decision_for
    from test_detail_auto_retry import failure
    from marketlens.research.run_finalization import finalize_run
    # Reuse existing evidence-producing fixtures, not a second finalization implementation.
    c, evidence = blocked_target(memory, tmp_path)
    confirm(c, evidence)
    c.adapter.failures = [failure(None, 'TimeoutError')]
    c.run(run_id=1, stage='details')
    binding, evidence = evidence_for(c, tmp_path)
    review(c, binding, evidence)
    finalize_run(memory, tmp_path, 1, decision_for(c, binding, tmp_path))
    with reading(memory):
        result = metrics.detail_coverage_metrics(memory, 1, 1, data_dir=tmp_path)
        assert value(result, 'source_deleted') == 1
        assert value(result, 'permanently_skipped_restricted') == 1
        assert value(result, 'unresolved_detail_tasks') == 0
        assert result['finalization']['checkpoint_synchronized']
    intent = tmp_path / 'logs/finalization_1.json'
    original = intent.read_bytes()
    intent.write_bytes(original.replace(b'"version":1', b'"version":9'))
    with reading(memory), pytest.raises(ValueError, match="Invalid finalization intent"):
        metrics.detail_coverage_metrics(memory, 1, 1, data_dir=tmp_path)
