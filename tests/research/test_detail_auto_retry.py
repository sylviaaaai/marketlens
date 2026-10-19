"""Synthetic-only detail retries; fake clock, no platform or image requests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_final_preflight import memory
from test_baseline_collection import SyntheticAdapter
from test_smoke import response
from marketlens.research.baseline_config import BaselineConfig, DetailRetryPolicy, ReviewedTimeoutRecovery
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.database import ROOT, initialize


class TinyFormal(BaselineConfig):
    @property
    def keywords(self): return ("synthetic_demo",)
    @property
    def sorts(self): return ("综合",)


class NoMedia:
    def process(self, *args, **kwargs): pass
    def summary(self, run): return {}


class Adapter(SyntheticAdapter):
    def __init__(self, failures=(), *, top=8, ready=True):
        super().__init__(top, 3)
        self.failures = list(failures)
        self.ready = ready
        self.preflights = 0
        self.requested = []

    def search(self, *args):
        result = super().search(*args)
        result.payload["data"]["feeds"] = result.payload["data"]["feeds"][:2]
        return result

    def detail(self, sid, token, *, comment_target=None):
        self.requested.append((sid, comment_target))
        assert comment_target == 20
        if self.failures:
            status, error, payload = self.failures.pop(0)
            return response(payload, "/api/v1/feeds/detail", status, error)
        return super().detail(sid, token, comment_target=comment_target)

    def preflight(self):
        self.preflights += 1
        return {"ready": self.ready}, "synthetic_demo"


def failure(status=500, error="HTTPError", payload=None):
    return status, error, payload or {"success": False, "code": "INTERNAL_ERROR"}


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.on_sleep = lambda: None
    def time(self): return self.now
    def sleep(self, seconds):
        assert 0 <= seconds <= 1
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr("marketlens.research.baseline_collector.time.time", value.time)
    monkeypatch.setattr("marketlens.research.baseline_collector.time.sleep", value.sleep)
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda low, high: 210.0)
    return value


def setup(conn, directory, adapter=None, policy=True):
    return BaselineCollector(conn, adapter or Adapter(), directory,
        TinyFormal(pilot=False, interval_seconds=0), media_store=NoMedia(),
        detail_retry_policy=DetailRetryPolicy() if policy is True else policy or None)


def search(c):
    result = c.run("synthetic_demo", stage="search")
    assert result["result"] == "PAUSED" and not c.adapter.requested
    return result["run_id"]


def journal(directory, run=1):
    return [json.loads(line) for line in (directory / "logs" / f"detail_retry_{run}.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_recovery_and_full_comments(memory, tmp_path, clock, status):
    c = setup(memory, tmp_path, Adapter([failure(status)], top=25))
    run = search(c)
    plan = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    result = c.run(run_id=run, stage="details")
    assert result["result"] == "SUCCESS"
    assert len(c.adapter.requested) == 3 and c.adapter.preflights == 1
    assert c.adapter.requested[0] == c.adapter.requested[1]
    assert result["comment_level_counts"] == {"1": 50, "2": 6}
    assert not any(result["relationship_checks"].values())
    assert sum(clock.sleeps) == 210
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    assert json.loads(plan)["max_attempts"] == 2
    events = journal(tmp_path)
    assert any(e.get("delay_seconds") == 210 and e["latest_attempt"]["http_status"] == status for e in events)
    assert events[-1]["event"] == "succeeded"
    c.run(run_id=run, stage="details")
    assert len(c.adapter.requested) == 3


def test_budget_lifetime_and_successful_task_skipped(memory, tmp_path, clock):
    c = setup(memory, tmp_path, Adapter([failure()] * 9))
    run = search(c)
    result = c.run(run_id=run, stage="details")
    assert result["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5 and sum(clock.sleeps) == 840
    assert [r[0] for r in memory.execute("SELECT attempt_no FROM collection_attempts WHERE endpoint LIKE '%detail'")] == [1, 2, 3, 4, 5]
    restored = setup(memory, tmp_path, c.adapter)
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5
    assert journal(tmp_path)[-1]["reason"] == "AttemptBudgetExhausted"


@pytest.mark.parametrize("bad", [failure(401), failure(403), failure(429), failure(400), failure(404), failure(501),
    failure(None, "TimeoutError"), failure(200, "ServiceError"),
    failure(500, payload={"success": False, "message": "请先登录"})])
def test_nonretryable_failures_stop(memory, tmp_path, clock, bad):
    c = setup(memory, tmp_path, Adapter([bad]))
    run = search(c)
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == 1 and sum(clock.sleeps) == 0
    assert c.adapter.preflights == 0
    assert journal(tmp_path)[-1]["event"] == "stopped"


@pytest.mark.parametrize("ready", [False, None, "true"])
def test_unclear_login_after_wait_stops(memory, tmp_path, clock, ready):
    c = setup(memory, tmp_path, Adapter([failure()], ready=ready))
    run = search(c)
    assert c.run(run_id=run, stage="details")["reason"].startswith("PreflightFailed")
    assert len(c.adapter.requested) == 1


def test_pause_during_wait_restart_reuses_deadline(memory, tmp_path, clock, monkeypatch):
    c = setup(memory, tmp_path, Adapter([failure()]))
    run = search(c)
    flag = tmp_path / f"pause_{run}.request"
    def pause():
        if clock.now >= 1007:
            flag.write_text("pause")
    clock.on_sleep = pause
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    assert len(c.adapter.requested) == 1 and sum(clock.sleeps) == 7
    state_path = next((tmp_path / "logs").glob("detail_retry_wait_*.json"))
    state = state_path.read_bytes()
    flag.unlink()
    clock.on_sleep = lambda: None
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda *a: pytest.fail("Must reuse saved wait"))
    restored = setup(memory, tmp_path, c.adapter)
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert sum(clock.sleeps) == 210 and state_path.read_bytes() == state
    assert len(c.adapter.requested) == 3


def test_unknown_inflight_blocks_even_with_inconsistent_task(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    run = search(c)
    task = memory.execute("SELECT task_id FROM collection_tasks WHERE task_type='feed_detail' LIMIT 1").fetchone()[0]
    c.begin_attempt(task, 1, "/api/v1/feeds/detail")
    assert c.run(run_id=run, stage="details")["reason"] == "UncertainInFlightAttemptNeedsReview"
    assert not c.adapter.requested


def test_ingestion_error_rolls_back_body_and_comments(memory, tmp_path, clock, monkeypatch):
    c = setup(memory, tmp_path)
    run = search(c)
    import marketlens.research.collection_pipeline as pipeline
    original = pipeline.ingest_detail
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("synthetic_demo ingestion failure")
    monkeypatch.setattr(pipeline, "ingest_detail", broken)
    result = c.run(run_id=run, stage="details")
    assert result["result"] == "BLOCKED" and len(c.adapter.requested) == 1
    assert result["table_counts"]["post_snapshots"] == result["table_counts"]["comment_observations"] == 0
    assert result["table_counts"]["comments"] == result["table_counts"]["post_media"] == 0
    assert memory.execute("SELECT error_type FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0].startswith("IngestionError")
    assert sum(clock.sleeps) == 0


def test_local_response_write_failure_leaves_unknown_attempt(memory, tmp_path, clock, monkeypatch):
    c = setup(memory, tmp_path)
    run = search(c)
    monkeypatch.setattr(c, "record_response", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic_disk_error")))
    result = c.run(run_id=run, stage="details")
    assert result["reason"] == "DetailRetryLocalErrorNeedsReview:OSError"
    assert len(c.adapter.requested) == 1
    assert c.run(run_id=run, stage="details")["reason"] == "UncertainInFlightAttemptNeedsReview"


def test_short_comments_and_media_failure_do_not_refetch(memory, tmp_path, clock):
    c = setup(memory, tmp_path, Adapter(top=8))
    def failed_media(*a, **kw): raise OSError("synthetic_image_failure")
    c.media_store.process = failed_media
    run = search(c)
    result = c.run(run_id=run, stage="details")
    assert result["result"] == "SUCCESS" and result["comment_level_counts"] == {"1": 16, "2": 6}
    assert len(c.adapter.requested) == 2 and len(result["media_errors"]) == 2
    c.run(run_id=run, stage="details")
    assert len(c.adapter.requested) == 2 and not c.adapter.preflights


def test_success_skipped_in_open_run(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    run = search(c)
    original = c.collect_media
    def media(run, snapshot):
        original(run, snapshot)
        (tmp_path / f"pause_{run}.request").write_text("pause")
    c.collect_media = media
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    assert len(c.adapter.requested) == 1
    (tmp_path / f"pause_{run}.request").unlink()
    restored = setup(memory, tmp_path, c.adapter)
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert len(c.adapter.requested) == 2 and len(set(sid for sid, _ in c.adapter.requested)) == 2


def test_missing_access_and_config_mismatch_never_search(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    run = search(c)
    restored = setup(memory, tmp_path, c.adapter)
    result = restored.run(run_id=run, stage="details")
    assert "no_automatic_search_refresh" in result["reason"]
    assert not c.adapter.requested and len(c.adapter.calls) == 1
    restored.config = TinyFormal(pilot=False, interval_seconds=0, top_comment_target=30)
    with pytest.raises(ValueError, match="Run/config mismatch"):
        restored.run(run_id=run, stage="details")


def test_default_behavior_and_search_budget_unchanged(memory, tmp_path, clock):
    c = setup(memory, tmp_path, Adapter([failure()]), policy=False)
    run = search(c)
    assert c.run(run_id=run, stage="details")["reason"] == "RequestOrIngestionFailed"
    assert len(c.adapter.requested) == 1 and not list((tmp_path / "logs").glob("detail_retry*"))
    c.detail_retry_policy = DetailRetryPolicy()
    request = memory.execute("SELECT * FROM search_requests").fetchone()
    task = memory.execute("SELECT * FROM collection_tasks WHERE task_type='search'").fetchone()
    # Existing search has attempt 1; a second failure is allowed, third blocked.
    c.adapter.search = lambda *a: response({"success": False}, status=500, error="HTTPError")
    assert c._call(task, request=request) == "RequestOrIngestionFailed"
    assert c._call(task, request=request) == "AttemptBudgetExhausted"


def test_search_barrier_with_retry_enabled(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    run = c.create("synthetic_demo")
    assert c.run(run_id=run, stage="details")["reason"] == "SearchPhaseIncomplete"
    assert not c.adapter.calls and not c.adapter.requested


@pytest.mark.parametrize("kind", ["wait", "journal", "artifact"])
def test_corrupt_runtime_evidence_blocks_before_retry(memory, tmp_path, clock, kind):
    c = setup(memory, tmp_path, Adapter([failure()]))
    run = search(c)
    flag = tmp_path / f"pause_{run}.request"
    clock.on_sleep = lambda: flag.write_text("pause") if clock.now > 1000 else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    flag.unlink()
    clock.on_sleep = lambda: None
    if kind == "wait":
        next((tmp_path / "logs").glob("detail_retry_wait_*.json")).write_text("invalid")
    elif kind == "journal":
        with (tmp_path / "logs" / f"detail_retry_{run}.jsonl").open("ab") as handle:
            handle.write(b'{"torn":')
    else:
        path = memory.execute("SELECT f.file_path FROM raw_artifacts f JOIN collection_attempts a USING(attempt_id) WHERE a.http_status=500").fetchone()[0]
        Path(path).write_text("{}")
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == 1


def test_keyboard_interrupt_preserves_wait_and_pause_checkpoint(memory, tmp_path, clock):
    c = setup(memory, tmp_path, Adapter([failure()]))
    run = search(c)
    clock.on_sleep = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    # Zero-second request interval also invokes the clock, so only interrupt
    # positive countdown chunks after the failed HTTP response was recorded.
    original = clock.sleep
    def sleep(seconds):
        if seconds:
            original(seconds)
    with patch("marketlens.research.baseline_collector.time.sleep", sleep):
        with pytest.raises(KeyboardInterrupt): c.run(run_id=run, stage="details")
    checkpoint = json.loads((tmp_path / "logs" / f"checkpoint_{run}.json").read_text())
    assert checkpoint["execution_status"] == "PAUSED"
    assert checkpoint["detail_retry_policy"]["max_attempts"] == 5
    assert len(c.adapter.requested) == 1
    assert len(list((tmp_path / "logs").glob("detail_retry_wait_*.json"))) == 1


def test_cli_opt_in_restores_historical_plan_without_search(memory, tmp_path, clock, monkeypatch):
    from test_baseline_stages import cli_module
    c = setup(memory, tmp_path, Adapter([failure()]), policy=False)
    run = search(c)
    saved = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    cli = cli_module(tmp_path, monkeypatch)
    placeholder = tmp_path / "existing_placeholder.json"
    placeholder.write_text("{}")
    monkeypatch.setattr(cli, "FORMAL_DB", placeholder)
    monkeypatch.setattr(cli, "FORMAL_DATA", tmp_path)
    class Borrowed:
        def __getattr__(self, name): return getattr(memory, name)
        def close(self): pass
    monkeypatch.setattr(cli, "initialize", lambda path: Borrowed())
    monkeypatch.setattr(cli, "connect", lambda *a, **kw: Borrowed())
    monkeypatch.setattr(cli, "XiaohongshuMCP", lambda *a, **kw: c.adapter)
    monkeypatch.setattr(cli, "load_auth", lambda *a: None)
    class Cache:
        def __init__(self, *a): pass
        def verify_available(self): pass
        def load(self, run_id, attempts):
            assert run_id == run and len(attempts) == 1
            return c.access.copy()
    monkeypatch.setattr(cli, "AccessCache", Cache)
    monkeypatch.setattr(cli, "BaselineConfig", lambda **kw: TinyFormal(**kw))
    policies = []
    def collector(*args, **kwargs):
        policies.append(kwargs["detail_retry_policy"])
        return BaselineCollector(*args, **kwargs, media_store=NoMedia())
    monkeypatch.setattr(cli, "BaselineCollector", collector)
    assert cli.main(["--formal", "--resume", str(run), "--stage", "details", "--auto-retry",
                     "--retry-max-attempts", "5", "--retry-min-seconds", "180", "--retry-max-seconds", "300"]) == 0
    assert policies == [DetailRetryPolicy()]
    assert len(c.adapter.calls) == 1 and len(c.adapter.requested) == 3
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == saved


@pytest.mark.parametrize("kwargs", [{"max_attempts": 6}, {"max_attempts": 0}, {"max_attempts": True},
    {"min_seconds": 0}, {"max_seconds": 301}, {"min_seconds": 250, "max_seconds": 200}, {"min_seconds": float("nan")}])
def test_policy_rejects_invalid(kwargs):
    with pytest.raises(ValueError): DetailRetryPolicy(**kwargs)


@pytest.mark.parametrize("args", [["--auto-retry"], ["--formal", "--resume", "1", "--auto-retry"],
    ["--formal", "--resume", "1", "--stage", "search", "--auto-retry"], ["--retry-max-attempts", "5"],
    ["--formal", "--resume", "1", "--stage", "details", "--auto-retry", "--retry-max-attempts", "6"]])
def test_cli_invalid_retry_options_before_io(args, monkeypatch):
    spec = importlib.util.spec_from_file_location("retry_cli", ROOT / "scripts/phase1d_baseline_collect.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "initialize", lambda *a: pytest.fail("No database opening"))
    monkeypatch.setattr(cli, "load_auth", lambda *a: pytest.fail("No auth read"))
    with pytest.raises(SystemExit) as exc: cli.main(args)
    assert exc.value.code == 2


@pytest.mark.skipif(os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") == "1", reason="Requires isolated temporary disk and subprocess")
@pytest.mark.parametrize("bounds", [(180, 300), (30, 60)])
def test_cross_process_existing_two_attempts_count_towards_five(tmp_path, bounds):
    conn = initialize(tmp_path / "synthetic_retry.db")
    c = setup(conn, tmp_path, Adapter([failure()] * 2), policy=False)
    run = search(c)
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    access = c.access.copy()  # Synthetic-only; real parameters must remain DPAPI protected.
    plan = conn.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    conn.close()
    code = '''
import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / "tests/research"))
from test_detail_auto_retry import setup, Adapter, failure, Clock
from marketlens.research.database import connect
from marketlens.research.baseline_config import DetailRetryPolicy
root = Path(sys.argv[1]); conn = connect(root / "synthetic_retry.db")
c = setup(conn, root, Adapter([failure()] * 8)); c.access.update(json.loads(sys.argv[2]))
low, high = json.loads(sys.argv[3])
c.detail_retry_policy = DetailRetryPolicy(min_seconds=low, max_seconds=high)
clock = Clock()
with patch("marketlens.research.baseline_collector.time.time", clock.time), patch("marketlens.research.baseline_collector.time.sleep", clock.sleep), patch("marketlens.research.baseline_collector.random.uniform", lambda *a: (low+high)/2):
    result = c.run(run_id=1, stage="details")
assert result["reason"] == "AttemptBudgetExhausted", result
assert len(c.adapter.requested) == 3
assert not c.adapter.calls
assert [r[0] for r in conn.execute("SELECT attempt_no FROM collection_attempts WHERE endpoint LIKE '%detail'")] == [1,2,3,4,5]
conn.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(access), json.dumps(bounds)], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    from marketlens.research.database import connect
    conn = connect(tmp_path / "synthetic_retry.db", readonly=True)
    assert conn.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    assert conn.execute("SELECT max(attempt_no) FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0] == 5
    conn.close()


def review_evidence(c, attempt_id, directory):
    import hashlib
    a = c.conn.execute("SELECT a.*,t.run_id,t.post_id FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE attempt_id=?", (attempt_id,)).fetchone()
    raw_hash = c.conn.execute("SELECT sha256 FROM raw_artifacts WHERE attempt_id=?", (attempt_id,)).fetchone()[0]
    db = c.conn.execute("PRAGMA database_list").fetchone()[2]
    evidence = {"binding": {"database_path": str(Path(db).resolve()) if db else ":memory:",
        **{k:a[k] for k in ("run_id", "task_id", "post_id", "attempt_id", "attempt_no", "finished_at")},
        "raw_sha256":raw_hash},
        "checkpoint_sha256":hashlib.sha256((directory / "logs" / f"checkpoint_{a['run_id']}.json").read_bytes()).hexdigest()}
    path = directory / "synthetic_review_evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return ReviewedTimeoutRecovery(attempt_id, "synthetic_demo: server completion reviewed; authorize one retry", path, True)


def timeout_blocked(memory, tmp_path):
    c = setup(memory, tmp_path, Adapter([failure(None, "TimeoutError")]))
    run = search(c)
    assert c.run(run_id=run, stage="details")["reason"].startswith("UnsafeFailure")
    attempt_id = memory.execute("SELECT attempt_id FROM collection_attempts WHERE error_type='TimeoutError'").fetchone()[0]
    return c, run, attempt_id


def test_reviewed_timeout_preserves_original_evidence_and_plan(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    original = dict(memory.execute("SELECT * FROM collection_attempts WHERE attempt_id=?", (attempt,)).fetchone())
    checkpoint = (tmp_path / "logs" / f"checkpoint_{run}.json").read_bytes()
    plan = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert dict(memory.execute("SELECT * FROM collection_attempts WHERE attempt_id=?", (attempt,)).fetchone()) == original
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    review = json.loads((tmp_path / "logs" / f"detail_review_{attempt}.json").read_text())
    assert review["original_checkpoint"].encode() == checkpoint
    assert review["decision"]["allowed_next_attempt_no"] == 2
    assert review["decision"]["reason"] == c.reviewed_timeout.reason
    consumed = [e for e in journal(tmp_path) if e["event"] == "review_consumed"]
    assert len(consumed) == 1 and consumed[0]["reviewed_attempt_id"] == attempt
    assert len(c.adapter.requested) == 3 and len(c.adapter.calls) == 1


def test_review_does_not_approve_a_second_timeout_or_reuse(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    c.adapter.failures = [failure(None, "TimeoutError")]
    assert c.run(run_id=run, stage="details")["reason"].startswith("UnsafeFailure")
    assert len(c.adapter.requested) == 2
    before = (tmp_path / "logs" / f"checkpoint_{run}.json").read_bytes()
    with pytest.raises(ValueError, match="stale"):
        c.run(run_id=run, stage="details")
    assert (tmp_path / "logs" / f"checkpoint_{run}.json").read_bytes() == before
    c.reviewed_timeout = None
    assert c.run(run_id=run, stage="details")["reason"].startswith("UnsafeFailure")
    assert len(c.adapter.requested) == 2


def test_review_budget_not_reset_and_only_first_timeout_approved(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    c.adapter.failures = [failure()] * 9
    assert c.run(run_id=run, stage="details")["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5
    assert [r[0] for r in memory.execute("SELECT attempt_no FROM collection_attempts WHERE endpoint LIKE '%detail'")] == [1, 2, 3, 4, 5]
    assert len([e for e in journal(tmp_path) if e["event"] == "review_consumed"]) == 1


@pytest.mark.parametrize("key", ["database_path", "run_id", "task_id", "post_id", "attempt_id", "attempt_no", "raw_sha256", "finished_at"])
def test_review_binding_rejects_mismatch_before_checkpoint_change(memory, tmp_path, clock, key):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    path = c.reviewed_timeout.evidence_file
    content = json.loads(path.read_text())
    content["binding"][key] = "synthetic_mismatch"
    path.write_text(json.dumps(content))
    checkpoint = (tmp_path / "logs" / f"checkpoint_{run}.json").read_bytes()
    with pytest.raises(ValueError, match="does not match"):
        c.run(run_id=run, stage="details")
    assert (tmp_path / "logs" / f"checkpoint_{run}.json").read_bytes() == checkpoint
    assert not list((tmp_path / "logs").glob("detail_review_*.json"))
    assert len(c.adapter.requested) == 1


@pytest.mark.parametrize("bad", [failure(401), failure(403), failure(429), failure(None, "URLError"), failure(500)])
def test_review_cannot_override_other_failures(memory, tmp_path, clock, bad):
    c = setup(memory, tmp_path, Adapter([bad]))
    run = search(c)
    # One recorded failure without auto-retry, to construct an ineligible review.
    c.detail_retry_policy = None
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    attempt = memory.execute("SELECT max(attempt_id) FROM collection_attempts").fetchone()[0]
    c.detail_retry_policy = DetailRetryPolicy()
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    with pytest.raises(ValueError, match="ineligible"):
        c.run(run_id=run, stage="details")
    assert len(c.adapter.requested) == 1


def test_review_wait_pause_can_resume_without_spending_authorization(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    flag = tmp_path / f"pause_{run}.request"
    clock.on_sleep = lambda: flag.write_text("pause") if clock.now > 1000 else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    decision_path = tmp_path / "logs" / f"detail_review_{attempt}.json"
    decision = decision_path.read_bytes()
    assert len(c.adapter.requested) == 1
    restored = setup(memory, tmp_path, c.adapter)
    restored.access.update(c.access)
    restored.reviewed_timeout = c.reviewed_timeout
    clock.on_sleep = lambda: None
    flag.unlink()
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert decision_path.read_bytes() == decision
    assert len(c.adapter.requested) == 3


def test_review_rejects_unknown_running_and_exhausted_budget(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    c.detail_retry_policy = DetailRetryPolicy(max_attempts=1)
    with pytest.raises(ValueError, match="budget"):
        c.run(run_id=run, stage="details")
    c.detail_retry_policy = DetailRetryPolicy()
    other = memory.execute("SELECT task_id FROM collection_tasks WHERE task_type='feed_detail' AND status='pending'").fetchone()[0]
    c.begin_attempt(other, 1, "/api/v1/feeds/detail")
    with pytest.raises(ValueError, match="in-flight"):
        c.run(run_id=run, stage="details")
    assert len(c.adapter.requested) == 1


@pytest.mark.parametrize("detail_timeout", [None, 120])
def test_http_timeout_override_is_detail_only(detail_timeout):
    import io
    from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP
    adapter = XiaohongshuMCP("synthetic", timeout=90, detail_timeout=detail_timeout)
    calls = []
    class Reply(io.BytesIO): code = 200
    class Opener:
        def open(self, request, *, timeout):
            calls.append((request.full_url.split("18060", 1)[1], timeout))
            return Reply(b'{"success":true,"data":{}}')
    adapter._opener = Opener()
    adapter.request("GET", "/health")
    adapter.request("GET", "/api/v1/login/status")
    adapter.search("synthetic_demo", {})
    adapter.detail("synthetic_demo", "synthetic_access", comment_target=20)
    assert [n for _, n in calls] == [90, 90, 90, detail_timeout or 90]


@pytest.mark.parametrize("args", [
    ["--detail-timeout-seconds", "120"],
    ["--formal", "--resume", "1", "--stage", "search", "--detail-timeout-seconds", "120"],
    ["--approve-timeout-attempt", "164"],
    ["--formal", "--resume", "1", "--stage", "details", "--auto-retry", "--approve-timeout-attempt", "164", "--review-reason", "reviewed", "--review-evidence", "synthetic.json"],
    ["--review-reason", "reviewed"], ["--confirm-server-request-finished"]])
def test_review_cli_invalid_before_io(args, monkeypatch):
    test_cli_invalid_retry_options_before_io(args, monkeypatch)


def test_review_preserves_successful_task_and_skips_its_detail(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    run = search(c)
    flag = tmp_path / f"pause_{run}.request"
    c.collect_media = lambda *a: flag.write_text("pause")
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    success = [tuple(r) for r in memory.execute("SELECT * FROM post_snapshots")]
    first_sid = c.adapter.requested[0][0]
    flag.unlink()
    c.collect_media = lambda *a: None
    c.adapter.failures = [failure(None, "TimeoutError")]
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    attempt = memory.execute("SELECT attempt_id FROM collection_attempts WHERE error_type='TimeoutError'").fetchone()[0]
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert [tuple(r) for r in memory.execute("SELECT * FROM post_snapshots ORDER BY snapshot_id LIMIT 1")] == success
    assert sum(sid == first_sid for sid, _ in c.adapter.requested) == 1
    assert len(c.adapter.requested) == 3


def test_review_evidence_changed_during_wait_stops_before_new_attempt(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    clock.on_sleep = lambda: c.reviewed_timeout.evidence_file.write_text("{}")
    assert c.run(run_id=run, stage="details")["reason"].startswith("DetailRetryLocalErrorNeedsReview")
    assert len(c.adapter.requested) == 1
    assert memory.execute("SELECT count(*) FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0] == 1


def test_review_not_retained_when_opt_in_removed_in_same_process(memory, tmp_path, clock):
    c, run, attempt = timeout_blocked(memory, tmp_path)
    c.reviewed_timeout = review_evidence(c, attempt, tmp_path)
    flag = tmp_path / f"pause_{run}.request"
    clock.on_sleep = lambda: flag.write_text("pause") if clock.now > 1000 else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    flag.unlink()
    clock.on_sleep = lambda: None
    c.reviewed_timeout = None
    assert c.run(run_id=run, stage="details")["reason"].startswith("UnsafeFailure")
    assert len(c.adapter.requested) == 1


@pytest.mark.parametrize("bounds", [(180, 300), (30, 60)])
def test_cli_review_and_runtime_timeout_pass_through(memory, tmp_path, clock, monkeypatch, bounds):
    from test_baseline_stages import cli_module
    c, run, attempt = timeout_blocked(memory, tmp_path)
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda low, high: (low + high)/2)
    review = review_evidence(c, attempt, tmp_path)
    cli = cli_module(tmp_path, monkeypatch)
    placeholder = tmp_path / "existing_placeholder.json"
    placeholder.write_text("{}")
    monkeypatch.setattr(cli, "FORMAL_DB", placeholder)
    monkeypatch.setattr(cli, "FORMAL_DATA", tmp_path)
    class Borrowed:
        def __getattr__(self, name): return getattr(memory, name)
        def close(self): pass
    monkeypatch.setattr(cli, "initialize", lambda path: Borrowed())
    monkeypatch.setattr(cli, "connect", lambda *a, **kw: Borrowed())
    runtime = []
    def adapter(*args, **kwargs):
        runtime.append(kwargs)
        return c.adapter
    monkeypatch.setattr(cli, "XiaohongshuMCP", adapter)
    monkeypatch.setattr(cli, "load_auth", lambda *a: None)
    class Cache:
        def __init__(self, *a): pass
        def verify_available(self): pass
        def load(self, *a): return c.access.copy()
    monkeypatch.setattr(cli, "AccessCache", Cache)
    monkeypatch.setattr(cli, "BaselineConfig", lambda **kw: TinyFormal(**kw))
    collector = BaselineCollector
    monkeypatch.setattr(cli, "BaselineCollector", lambda *a, **kw: collector(*a, **kw, media_store=NoMedia()))
    assert cli.main(["--formal", "--resume", str(run), "--stage", "details", "--auto-retry",
        "--approve-timeout-attempt", str(attempt), "--review-reason", review.reason,
        "--review-evidence", str(review.evidence_file), "--confirm-server-request-finished",
        "--detail-timeout-seconds", "120", "--retry-min-seconds", str(bounds[0]),
        "--retry-max-seconds", str(bounds[1])]) == 0
    assert runtime == [{"timeout": 90, "detail_timeout": 120}]
    assert len(c.adapter.calls) == 1 and len(c.adapter.requested) == 3
    wait = json.loads(next((tmp_path / "logs").glob("detail_retry_wait_*.json")).read_text())
    assert wait["delay_seconds"] == sum(bounds)/2


@pytest.mark.skipif(os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") == "1", reason="Requires isolated temporary disk and subprocess")
def test_review_is_consumed_once_across_processes(tmp_path):
    conn = initialize(tmp_path / "synthetic_review.db")
    c = setup(conn, tmp_path, Adapter([failure(None, "TimeoutError")]))
    run = search(c)
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    attempt = conn.execute("SELECT attempt_id FROM collection_attempts WHERE error_type='TimeoutError'").fetchone()[0]
    review = review_evidence(c, attempt, tmp_path)
    access = c.access.copy()
    conn.close()
    code = '''
import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / "tests/research"))
from test_detail_auto_retry import setup, Adapter, failure, Clock
from marketlens.research.baseline_config import ReviewedTimeoutRecovery
from marketlens.research.database import connect
root=Path(sys.argv[1]); conn=connect(root / "synthetic_review.db")
c=setup(conn, root, Adapter([failure(None,"TimeoutError")]))
c.access.update(json.loads(sys.argv[2]))
c.reviewed_timeout=ReviewedTimeoutRecovery(int(sys.argv[3]),sys.argv[4],root / "synthetic_review_evidence.json",True)
clock=Clock()
with patch("marketlens.research.baseline_collector.time.time",clock.time), patch("marketlens.research.baseline_collector.time.sleep",clock.sleep), patch("marketlens.research.baseline_collector.random.uniform",lambda *a:210):
    result=c.run(run_id=1,stage="details")
assert result["reason"].startswith("UnsafeFailure")
assert len(c.adapter.requested)==1 and not c.adapter.calls
conn.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(access), str(attempt), review.reason], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    from marketlens.research.database import connect
    conn = connect(tmp_path / "synthetic_review.db")
    restored = setup(conn, tmp_path)
    restored.access.update(access)
    restored.reviewed_timeout = review
    with pytest.raises(ValueError, match="stale"):
        restored.run(run_id=run, stage="details")
    assert not restored.adapter.requested
    assert conn.execute("SELECT count(*) FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_short_retry_range_used_and_plan_unchanged(memory, tmp_path, clock, monkeypatch, status):
    def draw(low, high):
        assert (low, high) == (30, 60)
        return 45
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", draw)
    c = setup(memory, tmp_path, Adapter([failure(status)]), DetailRetryPolicy(min_seconds=30, max_seconds=60))
    run = search(c)
    plan = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert sum(clock.sleeps) == 45 and len(c.adapter.requested) == 3
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    assert BaselineConfig(pilot=False).interval_seconds == 3


def test_short_retry_budget_exhausted_at_five(memory, tmp_path, clock, monkeypatch):
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda *a: 45)
    c = setup(memory, tmp_path, Adapter([failure()] * 8), DetailRetryPolicy(min_seconds=30, max_seconds=60))
    run = search(c)
    assert c.run(run_id=run, stage="details")["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5 and sum(clock.sleeps) == 4 * 45


@pytest.mark.parametrize("old_bounds", [(180, 300), (30, 60)])
def test_new_policy_reuses_persisted_old_or_short_schedule(memory, tmp_path, clock, monkeypatch, old_bounds):
    delay = sum(old_bounds)/2
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda *a: delay)
    c = setup(memory, tmp_path, Adapter([failure()]), DetailRetryPolicy(min_seconds=old_bounds[0], max_seconds=old_bounds[1]))
    run = search(c)
    flag = tmp_path / f"pause_{run}.request"
    clock.on_sleep = lambda: flag.write_text("pause") if clock.now >= 1007 else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    path = next((tmp_path / "logs").glob("detail_retry_wait_*.json"))
    original = path.read_bytes()
    clock.on_sleep = lambda: None
    flag.unlink()
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda *a: pytest.fail("Saved wait must not be redrawn"))
    restored = setup(memory, tmp_path, c.adapter, DetailRetryPolicy(min_seconds=30, max_seconds=60))
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert path.read_bytes() == original and sum(clock.sleeps) == delay


def test_retry_range_below_thirty_is_rejected():
    with pytest.raises(ValueError):
        DetailRetryPolicy(min_seconds=29, max_seconds=60)
