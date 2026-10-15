"""Synthetic_demo contract tests only; no network calls."""
import copy
import json

import pytest

from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP
from marketlens.research.baseline_config import BaselineConfig, SORT_METHODS, SUFFIXES
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.database import initialize
from test_smoke import response, detail


class SyntheticAdapter(XiaohongshuMCP):
    version = "synthetic_demo"

    def __init__(self, top=30, replies=15, fail_at=None, crash_at=None, missing_access=False):
        super().__init__("synthetic_auth")
        self.top, self.replies = top, replies
        self.fail_at, self.crash_at = fail_at, crash_at
        self.missing_access = missing_access
        self.calls, self.detail_ids = [], []

    def search(self, keyword, filters):
        assert set(filters) == {"note_type", "sort_by"} and filters["note_type"] == "图文"
        self.calls.append((keyword, filters.copy()))
        if self.crash_at == len(self.calls):
            raise KeyboardInterrupt()
        if self.fail_at == len(self.calls):
            return response({"success": False}, status=500, error="HTTPError")
        feeds = [{"id": f"synthetic_demo_{i}", "noteCard": {"type": "normal"},
                  **({} if self.missing_access else {"xsecToken": "synthetic_access"})} for i in range(8)]
        # Repeated hit in same page, and an invalid item: retain 9 valid hits.
        return response({"success": True, "data": {"feeds": feeds+[copy.deepcopy(feeds[0]), {}]}})

    def detail(self, sid, token, *, comment_target=None):
        self.detail_ids.append(sid)
        payload = detail(sid)
        payload["data"]["data"]["comments"]["list"] = [
            {"id": f"synthetic_parent_{n}", "content": "synthetic_demo",
             "subComments": [{"id": f"synthetic_reply_{j}", "content": "synthetic_demo"}
                             for j in range(self.replies)] if n == 0 else []} for n in range(self.top)]
        return response(payload, "/api/v1/feeds/detail")


@pytest.fixture
def setup(tmp_path):
    conn = initialize(tmp_path / "synthetic_demo.db")
    yield conn, tmp_path
    conn.close()


def collector(setup, adapter=None, pilot=True):
    conn, path = setup
    return BaselineCollector(conn, adapter or SyntheticAdapter(), path,
                             BaselineConfig(pilot=pilot, interval_seconds=0))


def test_full_design_and_all_hits(setup):
    c = collector(setup, pilot=False)
    assert c.config.keywords == tuple("粉底液"+s for s in SUFFIXES)
    assert c.config.sorts == SORT_METHODS
    assert BaselineConfig(category="面霜", pilot=False).keywords[0] == "面霜使用感受"
    search = c.run("synthetic_demo")
    assert search["result"] == "PAUSED"
    result = c.run(run_id=search["run_id"], stage="details")
    assert result["result"] == "SUCCESS"
    assert len(c.adapter.calls) == 50
    assert result["hits"] == 450 and result["unique_posts"] == 8
    assert [g["new_posts"] for g in result["groups"]] == [8]+[0]*49
    assert len(set(c.adapter.detail_ids)) == len(c.adapter.detail_ids) == 8
    assert result["comment_level_counts"] == {"1": 240, "2": 120}
    assert not any(result["relationship_checks"].values())
    assert all(a["verified"] for a in result["artifact_checks"])


@pytest.mark.parametrize("top,replies", [(30, 15), (8, 3), (0, 0)])
def test_pilot_soft_budget_and_repeated_run(setup, top, replies):
    c = collector(setup, SyntheticAdapter(top, replies))
    result = c.run("synthetic_demo")
    assert result["details"] == {"succeeded": 5, "failed": 0, "unrequested": 3, "uncertain": 0}
    assert result["comment_level_counts"].get("1", 0) == top*5
    assert result["comment_level_counts"].get("2", 0) == replies*5
    assert result["table_counts"]["dataset_versions"] == 0
    again = c.run(run_id=result["run_id"])
    assert again["result"] == "ALREADY_CLOSED"
    assert again["table_counts"] == result["table_counts"]
    assert len(c.adapter.calls) == 4 and len(c.adapter.detail_ids) == 5
    assert c.conn.execute("SELECT count(*) FROM search_hits WHERE access_params_json IS NOT NULL").fetchone()[0] == 0


def test_failure_stops_and_same_run_resume(setup):
    c = collector(setup, SyntheticAdapter(fail_at=2))
    failed = c.run("synthetic_demo")
    assert failed["result"] == "BLOCKED" and len(c.adapter.calls) == 2
    assert failed["details"]["unrequested"] == 8
    resumed = c.run(run_id=failed["run_id"])
    assert resumed["result"] == "SUCCESS"
    assert resumed["table_counts"]["collection_runs"] == 1
    assert resumed["attempt_status_counts"] == {"failed": 1, "succeeded": 9}
    assert resumed["hits"] == 36


def test_process_restart_cannot_invent_missing_tokens(setup):
    c = collector(setup, SyntheticAdapter(missing_access=True))
    first = c.run("synthetic_demo")
    assert first["result"] == "BLOCKED" and first["details"]["unrequested"] == 8
    other = collector(setup)
    resumed = other.run(run_id=first["run_id"])
    assert "MissingEphemeral" in resumed["reason"]
    assert not other.adapter.calls and not other.adapter.detail_ids
    assert resumed["table_counts"] == first["table_counts"]


def test_unknown_inflight_never_retried(setup):
    c = collector(setup, SyntheticAdapter(crash_at=2))
    with pytest.raises(KeyboardInterrupt):
        c.run("synthetic_demo")
    other = collector(setup)
    result = other.run(run_id=1)
    assert result["reason"] == "UncertainInFlightAttemptNeedsReview"
    assert not other.adapter.calls
    assert result["attempt_status_counts"] == {"running": 1, "succeeded": 1}


def test_atomic_response_commit_reconciles_without_duplicate_call(setup, monkeypatch):
    c = collector(setup)
    original = c.record_response

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(c, "record_response", crash)
    with pytest.raises(KeyboardInterrupt):
        c.run("synthetic_demo")
    other = collector(setup)
    result = other.run(run_id=1)
    assert result["result"] == "SUCCESS"
    assert len(other.adapter.calls) == 3
    assert result["table_counts"]["collection_attempts"] == 9


def test_config_mismatch_rejected(setup):
    c = collector(setup)
    run = c.create("synthetic_demo")
    with pytest.raises(ValueError, match="mismatch"):
        collector(setup, pilot=False).run(run_id=run)


def test_access_restriction_stops_even_on_resume(setup):
    adapter = SyntheticAdapter()
    adapter.search = lambda *args: response({"success": False}, status=429, error="HTTPError")
    c = collector(setup, adapter)
    first = c.run("synthetic_demo")
    second = c.run(run_id=first["run_id"])
    assert second["reason"].startswith("UnsafeFailure")
    assert second["table_counts"]["collection_attempts"] == 1


def test_retry_budget_cannot_reset_on_resume(setup):
    adapter = SyntheticAdapter()
    adapter.search = lambda *args: response({"success": False}, status=500, error="HTTPError")
    c = collector(setup, adapter)
    first = c.run("synthetic_demo")
    c.run(run_id=first["run_id"])
    third = c.run(run_id=first["run_id"])
    assert third["reason"] == "AttemptBudgetExhausted"
    assert third["table_counts"]["collection_attempts"] == 2


def test_missing_early_token_does_not_change_fixed_sample(setup):
    adapter = SyntheticAdapter()
    original = adapter.search

    def missing_first(*args):
        result = original(*args)
        for item in result.payload["data"]["feeds"]:
            if item.get("id") == "synthetic_demo_0":
                item.pop("xsecToken", None)
        return result

    adapter.search = missing_first
    c = collector(setup, adapter)
    result = c.run("synthetic_demo")
    assert result["result"] == "BLOCKED"
    assert adapter.detail_ids == [f"synthetic_demo_{i}" for i in range(1, 5)]
    assert result["quality"]["unrequested_candidates"] == 4


def test_detail_failure_preserves_successful_observations(setup):
    adapter = SyntheticAdapter()
    original = adapter.detail
    calls = []

    def fail_second(sid, token):
        calls.append(sid)
        if len(calls) == 2:
            return response({"success": False}, "/api/v1/feeds/detail", status=500, error="HTTPError")
        return original(sid, token)

    adapter.detail = fail_second
    c = collector(setup, adapter)
    first = c.run("synthetic_demo")
    assert first["details"]["succeeded"] == first["details"]["failed"] == 1
    before = [tuple(r) for r in c.conn.execute("SELECT * FROM post_snapshots")]
    result = c.run(run_id=first["run_id"])
    assert result["result"] == "SUCCESS"
    assert calls.count("synthetic_demo_0") == 1 and calls.count("synthetic_demo_1") == 2
    assert [tuple(r) for r in c.conn.execute("SELECT * FROM post_snapshots WHERE snapshot_id=1")] == before
    assert result["table_counts"]["post_snapshots"] == 5


def test_reopen_database_continues_pending_searches_same_run(tmp_path):
    path = tmp_path / "synthetic_demo.db"
    config = BaselineConfig(interval_seconds=0)
    conn = initialize(path)
    first = BaselineCollector(conn, SyntheticAdapter(fail_at=2), tmp_path, config).run("synthetic_demo")
    conn.close()
    conn = initialize(path)
    try:
        adapter = SyntheticAdapter()
        result = BaselineCollector(conn, adapter, tmp_path, config).run(run_id=first["run_id"])
        assert result["result"] == "SUCCESS"
        assert len(adapter.calls) == 3
        assert result["table_counts"]["collection_runs"] == result["table_counts"]["collection_plans"] == 1
    finally:
        conn.close()
