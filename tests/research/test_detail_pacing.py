"""Offline scheduler pacing: synthetic responses, memory SQLite, virtual clock."""
import json

import pytest

from test_final_preflight import memory
from test_detail_auto_retry import TinyFormal, Adapter, NoMedia, failure
from marketlens.research.baseline_config import DetailPacingPolicy, DetailRetryPolicy
from marketlens.research.baseline_collector import BaselineCollector


@pytest.fixture
def timer(monkeypatch):
    class Timer:
        now = 1000.0
        on_sleep = lambda self: None
        def sleep(self, seconds):
            self.now += seconds
            self.on_sleep()
    clock = Timer()
    monkeypatch.setattr("marketlens.research.baseline_collector.time.time", lambda: clock.now)
    monkeypatch.setattr("marketlens.research.baseline_collector.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("marketlens.research.baseline_collector.time.sleep", clock.sleep)
    monkeypatch.setattr("marketlens.research.baseline_collector.random.uniform", lambda low, high: (low + high) / 2)
    return clock


def collector(conn, directory, adapter=None, pacing=True, retry=None):
    return BaselineCollector(conn, adapter or Adapter(), directory,
        TinyFormal(pilot=False), media_store=NoMedia(), detail_retry_policy=retry,
        detail_pacing_policy=DetailPacingPolicy() if pacing is True else pacing or None)


def start(c):
    return c.run("synthetic_demo", stage="search")["run_id"]


def events(directory):
    return [json.loads(line) for line in (directory / "logs/detail_pacing_1.jsonl").read_text().splitlines()]


def test_timing_save_order_and_plan_unchanged(memory, tmp_path, timer):
    c = collector(memory, tmp_path)
    run = start(c)
    assert not list((tmp_path / "logs").glob("detail_pacing*"))
    plan = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    before = timer.now
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert timer.now - before == 31  # two * (3 fixed + 5 buffer + 7.5 after)
    records = events(tmp_path)
    assert [r["status"] for r in records if r["event"] == "collected"] == ["succeeded"] * 2
    assert sum(r["actual_wait_seconds"] for r in records if r["event"] == "wait_finished") == 31
    assert all("collection_seconds" in r and "http_duration_ms" in r for r in records if r["event"] == "collected")
    assert memory.execute("SELECT count(*) FROM post_snapshots").fetchone()[0] == 2
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    assert len(c.adapter.requested) == 2


@pytest.mark.parametrize("phase", ["before", "after"])
def test_pause_restart_keeps_deadline_and_success_not_repeated(memory, tmp_path, timer, phase):
    c = collector(memory, tmp_path)
    run = start(c)
    flag = tmp_path / "pause_1.request"
    target = timer.now + (1 if phase == "before" else 9)
    timer.on_sleep = lambda: flag.write_text("pause") if timer.now >= target else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    assert len(c.adapter.requested) == (0 if phase == "before" else 1)
    schedules = {p: p.read_bytes() for p in (tmp_path / "logs").glob("detail_pacing_wait_*.json")}
    flag.unlink()
    timer.on_sleep = lambda: None
    restored = collector(memory, tmp_path, c.adapter,
        pacing=DetailPacingPolicy(buffer_min_seconds=0, buffer_max_seconds=0, after_min_seconds=0, after_max_seconds=0))
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert len(c.adapter.requested) == 2
    assert all(p.read_bytes() == content for p, content in schedules.items())


def test_budget_and_restart_not_reset(memory, tmp_path, timer):
    policy = DetailRetryPolicy(min_seconds=30, max_seconds=30)
    c = collector(memory, tmp_path, Adapter([failure()] * 8), retry=policy)
    run = start(c)
    assert c.run(run_id=run, stage="details")["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5
    assert not any(r.get("phase") == "after" for r in events(tmp_path))
    restored = collector(memory, tmp_path, c.adapter, retry=policy)
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage="details")["reason"] == "AttemptBudgetExhausted"
    assert len(c.adapter.requested) == 5


@pytest.mark.parametrize("bad", [failure(None, "TimeoutError"), failure(403), failure(429),
    failure(500, payload={"success": False, "message": "access denied"}),
    failure(500, payload={"success": False, "message": "\u8bbf\u95ee\u9891\u7e41"}),
    failure(500, payload={"success": False, "message": "300013"})])
def test_unsafe_errors_block_without_next_request(memory, tmp_path, timer, bad):
    c = collector(memory, tmp_path, Adapter([bad]), retry=DetailRetryPolicy(min_seconds=30, max_seconds=60))
    run = start(c)
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == 1
    assert json.loads((tmp_path / "logs/checkpoint_1.json").read_text())["execution_status"] == "BLOCKED"


@pytest.mark.parametrize("settings", [{"after_min_seconds": -1}, {"buffer_max_seconds": float("nan")},
    {"after_min_seconds": 11}, {"after_max_seconds": float("inf")}])
def test_invalid_policy(settings):
    with pytest.raises(ValueError):
        DetailPacingPolicy(**settings)


def test_disabled_uses_only_original_interval(memory, tmp_path, timer):
    c = collector(memory, tmp_path, pacing=False)
    run = start(c)
    before = timer.now
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert timer.now - before == 6
    assert not list((tmp_path / "logs").glob("detail_pacing*"))


def test_media_failure_does_not_repeat_detail(memory, tmp_path, timer):
    c = collector(memory, tmp_path)
    class BrokenMedia(NoMedia):
        def process(self, *args, **kwargs):
            raise OSError("synthetic media failure")
    c.media_store = BrokenMedia()
    run = start(c)
    assert c.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert len(c.adapter.requested) == 2
    assert memory.execute("SELECT count(*) FROM post_snapshots").fetchone()[0] == 2
    assert len([r for r in events(tmp_path) if r.get("phase") == "after" and r["event"] == "wait_finished"]) == 2


def test_disabling_new_waits_still_honors_saved_after_wait(memory, tmp_path, timer):
    c = collector(memory, tmp_path)
    run = start(c)
    target = timer.now + 9
    flag = tmp_path / "pause_1.request"
    timer.on_sleep = lambda: flag.write_text("pause") if timer.now >= target else None
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    flag.unlink()
    timer.on_sleep = lambda: None
    restored = collector(memory, tmp_path, c.adapter, pacing=False)
    restored.access.update(c.access)
    before = timer.now
    assert restored.run(run_id=run, stage="details")["result"] == "SUCCESS"
    assert timer.now - before == 9.5  # 6.5 remaining post wait + original 3s next request
    assert len(c.adapter.requested) == 2


def test_pacing_journal_failure_stops_before_request(memory, tmp_path, timer):
    c = collector(memory, tmp_path)
    run = start(c)
    (tmp_path / "logs/detail_pacing_1.jsonl").write_bytes(b"incomplete")
    result = c.run(run_id=run, stage="details")
    assert result["result"] == "BLOCKED"
    assert not c.adapter.requested


def test_cli_rejects_pacing_outside_formal_details():
    import importlib.util
    from marketlens.research.database import ROOT
    spec = importlib.util.spec_from_file_location("pacing_cli", ROOT / "scripts/phase1d_baseline_collect.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with pytest.raises(SystemExit) as error:
        cli.main(["--detail-buffer-max-seconds", "8"])
    assert error.value.code == 2
