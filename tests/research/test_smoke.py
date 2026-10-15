"""All fixtures in this module are synthetic_demo, never platform observations."""
from contextlib import closing
import copy
import json
import sqlite3
import subprocess

import pytest

from marketlens.adapters.xiaohongshu_mcp import Response, XiaohongshuMCP, utc_now
from marketlens.research.collection_pipeline import CollectionPipeline, audit
from marketlens.research.database import ROOT, connect, initialize, transaction
from marketlens.research.normalizers import count, platform_time


def response(payload, endpoint="/api/v1/feeds/search", status=200, error=None):
    now = utc_now()
    return Response(endpoint, "POST", now, now, 12, status, payload, error)


def search():
    return {"success": True, "data": {"feeds": [
        {"id": f"synthetic_demo_{i}", "xsecToken": f"synthetic_access_{i}", "index": i,
         "noteCard": {"type": "normal" if i < 4 else "video", "displayTitle": "synthetic_demo"}}
        for i in range(5)] + [{"noteCard": {"type": "normal"}}]}}


def detail(source_id):
    return {"success": True, "data": {"feed_id": source_id, "data": {
        "note": {"noteId": source_id, "type": "normal", "xsecToken": "synthetic_access_secret",
                 "interactInfo": {"likedCount": "1.2万", "commentCount": "100+", "sharedCount": "0"},
                 "time": "yesterday", "imageList": [{"width": 100, "urlDefault": "https://example.invalid/demo.jpg"}]},
        "comments": {"hasMore": True, "list": [
            {"id": "synthetic_parent", "content": "synthetic_demo", "likeCount": "0",
             "subComments": [{"id": "synthetic_reply", "content": "synthetic_demo", "createTime": 1702195200000}]},
            {"content": "synthetic_demo anonymous", "subComments": [{"content": "synthetic_demo anonymous reply"}]}]}}}}


class FakeAdapter(XiaohongshuMCP):
    version = "synthetic_demo"

    def __init__(self, fail_search=0, corrupt_detail=False, video_detail=False):
        super().__init__("synthetic_auth")
        self.fail_search, self.corrupt_detail, self.video_detail = fail_search, corrupt_detail, video_detail
        self.search_calls, self.detail_calls = 0, 0

    def search(self, keyword, filters):
        assert keyword == "护手霜" and filters == {"sort_by": "综合", "note_type": "图文"}
        self.search_calls += 1
        if self.search_calls <= self.fail_search:
            return response({"error": "synthetic_demo failure"}, status=500, error="HTTPError")
        return response(search())

    def detail(self, source_id, token):
        self.detail_calls += 1
        payload = detail(source_id)
        if self.corrupt_detail:
            payload["data"]["data"]["comments"]["list"][0]["subComments"][0]["noteId"] = "wrong_post"
        if self.video_detail:
            payload["data"]["data"]["note"]["type"] = "video"
        return response(payload, "/api/v1/feeds/detail")


@pytest.fixture
def db(tmp_path):
    with closing(initialize(tmp_path / "synthetic_demo.db")) as conn:
        yield conn


@pytest.mark.parametrize("raw,value,precision", [
    (None, None, "missing"), ("", None, "unparseable"), ("0", 0, "exact_display"),
    ("1.2万", 12000, "approximate"), ("100+", 100, "lower_bound"),
    ("1.2万+", 12000, "approximate_lower_bound"), ("?", None, "unparseable"),
    (False, None, "unparseable"), ("-2", None, "unparseable"), ("1.5", None, "unparseable"),
    ("999999999999999999999", None, "unparseable")])
def test_count(raw, value, precision):
    parsed = count(raw)
    assert parsed["value"] == value and parsed["precision"] == precision
    if isinstance(raw, str):
        assert parsed["raw"] == raw


def test_platform_time():
    assert platform_time(1702195200000) == ("1702195200000", "2023-12-10T08:00:00.000Z")
    assert platform_time(1702195200)[1] == "2023-12-10T08:00:00.000Z"
    for unknown in ("yesterday", "2026-02-30T00:00:00Z", "2026-10-14 00:00:00", 12345, True):
        assert platform_time(unknown)[1] is None


def test_schema_connection_and_safe_reopen(db, tmp_path):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    assert len(audit(db)["table_counts"]) == 18
    with closing(initialize(tmp_path / "synthetic_demo.db")) as reopened:
        assert audit(reopened)["integrity_check"] == ["ok"]
    with pytest.raises(ValueError):
        connect(ROOT / "marketlens.db")
    with pytest.raises(ValueError):
        initialize(tmp_path / "synthetic_demo.db", reset=True)


def test_reject_other_existing_schema(tmp_path):
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE existing(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        initialize(path)
    assert path.read_bytes() == before


def test_end_to_end_retains_all_hits_and_history(db, tmp_path):
    adapter = FakeAdapter()
    first = CollectionPipeline(db, adapter, tmp_path).run("synthetic_demo")
    assert first["result"] == "SUCCESS"
    counts = first["table_counts"]
    assert counts["search_hits"] == 5 and counts["posts"] == 5
    assert counts["post_snapshots"] == 3 and counts["post_media"] == 3
    assert counts["comment_observations"] == 12 and counts["comments"] == 6
    assert counts["dataset_versions"] == 0
    assert adapter.search_calls == 1 and adapter.detail_calls == 3
    assert all(x["verified"] for x in first["artifact_checks"])
    snapshot = db.execute("SELECT * FROM post_snapshots LIMIT 1").fetchone()
    assert snapshot["title"] is None and snapshot["favorites_count"] is None
    assert snapshot["shares_count"] == 0 and snapshot["published_at"] is None
    assert snapshot["published_at_raw"] == "yesterday"
    assert db.execute("SELECT count(*) FROM comment_observations WHERE comment_level=2 AND parent_observation_id IS NOT NULL").fetchone()[0] == 6
    old = [tuple(r) for r in db.execute("SELECT * FROM post_snapshots")]
    second = CollectionPipeline(db, adapter, tmp_path).run("synthetic_demo")
    assert second["table_counts"]["posts"] == 5
    assert second["table_counts"]["post_snapshots"] == 6
    assert [tuple(r) for r in db.execute("SELECT * FROM post_snapshots LIMIT 3")] == old
    assert db.execute("SELECT returned_posts,returned_top_comments,returned_replies FROM collection_attempts WHERE attempt_id=2").fetchone()[:] == (1, 2, 2)
    for path in (tmp_path / "raw").glob("*.json"):
        assert "synthetic_access" not in path.read_text(encoding="utf-8")


def test_same_response_replay_and_conflict(db, tmp_path):
    pipeline = CollectionPipeline(db, FakeAdapter(), tmp_path)
    _, request, task = pipeline.start("synthetic_demo")
    attempt = pipeline.begin_attempt(task, 1, "/api/v1/feeds/search")
    actual = response(search())
    pipeline.record_response(attempt, actual, request_id=request)
    assert pipeline.record_response(attempt, actual, request_id=request) == {"replayed": True}
    changed = copy.deepcopy(actual)
    changed.payload["data"]["feeds"][0]["noteCard"]["displayTitle"] = "changed evidence"
    with pytest.raises(ValueError, match="AttemptEvidenceConflict"):
        pipeline.record_response(attempt, changed, request_id=request)
    assert db.execute("SELECT count(*) FROM search_hits").fetchone()[0] == 5


def test_failure_retry_keeps_actual_attempts(db, tmp_path, monkeypatch):
    monkeypatch.setattr("marketlens.research.collection_pipeline.time.sleep", lambda _: None)
    adapter = FakeAdapter(fail_search=1)
    result = CollectionPipeline(db, adapter, tmp_path).run("synthetic_demo")
    assert result["attempt_status_counts"] == {"failed": 1, "succeeded": 4}
    assert adapter.search_calls == 2
    rows = db.execute("SELECT attempt_no,status FROM collection_attempts WHERE task_id=1 ORDER BY attempt_no").fetchall()
    assert [tuple(r) for r in rows] == [(1, "failed"), (2, "succeeded")]


def test_search_blocked_stops_after_two(db, tmp_path, monkeypatch):
    monkeypatch.setattr("marketlens.research.collection_pipeline.time.sleep", lambda _: None)
    adapter = FakeAdapter(fail_search=99)
    result = CollectionPipeline(db, adapter, tmp_path).run("synthetic_demo")
    assert result["result"] == "BLOCKED"
    assert adapter.search_calls == 2 and adapter.detail_calls == 0
    assert result["table_counts"]["posts"] == 0
    assert db.execute("SELECT returned_count FROM search_requests").fetchone()[0] is None


@pytest.mark.parametrize("kwargs", [{"corrupt_detail": True}, {"video_detail": True}])
def test_detail_batch_rollback_and_no_video_snapshot(db, tmp_path, kwargs):
    result = CollectionPipeline(db, FakeAdapter(**kwargs), tmp_path).run("synthetic_demo")
    assert result["result"] == "PARTIAL"
    for table in ("post_snapshots", "post_media", "comments", "comment_observations"):
        assert result["table_counts"][table] == 0
    assert result["table_counts"]["raw_artifacts"] == 4
    assert result["attempt_status_counts"] == {"failed": 3, "succeeded": 1}


def test_transaction_rolls_back(db):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(db):
            db.execute("INSERT INTO research_projects(research_question,category) VALUES ('synthetic_demo','demo')")
            db.execute("INSERT INTO research_projects(research_question,category) VALUES ('','demo')")
    assert db.execute("SELECT count(*) FROM research_projects").fetchone()[0] == 0


def test_credentials_redacted():
    adapter = XiaohongshuMCP("synthetic_auth")
    payload = {"token": "synthetic_access", "nested": [{"Cookie": "synthetic_cookie"}],
               "url": "https://example.invalid/?xsec_token=synthetic_access&x=1",
               "text": "Bearer synthetic_auth token=synthetic_other synthetic_access"}
    result = json.dumps(adapter.safe_payload(response(payload)))
    for secret in ("synthetic_auth", "synthetic_access", "synthetic_cookie", "synthetic_other"):
        assert secret not in result


def test_http_request_contract_and_no_mutation_endpoints():
    class TransportResponse:
        code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"success":true,"data":{"feeds":[]}}'
    class Transport:
        def open(self, req, timeout):
            assert req.full_url == "http://127.0.0.1:18060/api/v1/feeds/search"
            assert req.method == "POST"
            assert json.loads(req.data)["filters"]["note_type"] == "图文"
            return TransportResponse()
    adapter = XiaohongshuMCP("synthetic_auth")
    adapter._opener = Transport()
    assert adapter.search("护手霜", {"note_type": "图文"}).ok
    with pytest.raises(ValueError):
        adapter.request("DELETE", "/api/v1/login/cookies")


def test_private_paths_are_ignored_and_untracked():
    paths = ["local_data/phase1c_smoke/raw/demo.json", "local_data/phase1c_smoke/research_smoke.db",
             "cookies.json", ".env", ".env.local", "private.pem", "private.key"]
    result = subprocess.run(["git", "check-ignore", "--stdin", "-z"], input=("\0".join(paths)+"\0").encode(),
                            cwd=ROOT, capture_output=True, check=True)
    assert set(result.stdout.decode().rstrip("\0").split("\0")) == set(paths)
    tracked = subprocess.run(["git", "ls-files", "local_data/", "*.db", "*cookies*.json", ".env"],
                             cwd=ROOT, text=True, capture_output=True, check=True)
    assert not tracked.stdout.strip()
