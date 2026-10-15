"""Synthetic-only phase barriers, local encryption and CLI authorization tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from marketlens.research.access_cache import AccessCache, AccessCacheError
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.baseline_config import BaselineConfig
from marketlens.research.database import initialize, ROOT
from test_baseline_collection import SyntheticAdapter


def make(tmp_path, adapter=None, encrypted=False, pilot=False):
    path = tmp_path / "synthetic_demo.db"
    conn = initialize(path)
    cache = AccessCache(tmp_path / "private_access", path) if encrypted else None
    return BaselineCollector(conn, adapter or SyntheticAdapter(), tmp_path,
                             BaselineConfig(pilot=pilot, interval_seconds=0), cache)


def test_search_barrier_and_durable_pause(tmp_path):
    c = make(tmp_path)
    try:
        result = c.run("synthetic_demo", stage="search")
        assert len(c.adapter.calls) == 50 and not c.adapter.detail_ids
        assert result["result"] == "PAUSED" and result["phase"]["search_complete"]
        state = json.loads((tmp_path / "logs/checkpoint_1.json").read_text(encoding="utf-8"))
        assert state["stage"] == "search_complete" and state["execution_status"] == "PAUSED"
        assert state["search_completed_at"]
        old_calls = len(c.adapter.calls)
        assert c.run(run_id=1, stage="details")["result"] == "SUCCESS"
        assert len(c.adapter.calls) == old_calls
    finally:
        c.conn.close()


def test_detail_phase_cannot_bypass_failed_or_missing_search(tmp_path):
    c = make(tmp_path, SyntheticAdapter(fail_at=2))
    try:
        result = c.run("synthetic_demo", stage="search")
        assert not result["phase"]["search_complete"]
        calls = len(c.adapter.calls)
        assert c.run(run_id=1, stage="details")["reason"] == "SearchPhaseIncomplete"
        assert len(c.adapter.calls) == calls and not c.adapter.detail_ids
        # Even a direct low-level detail call must enforce the barrier.
        task = c.conn.execute("SELECT * FROM collection_tasks WHERE task_type='feed_detail' LIMIT 1").fetchone()
        assert c._call(task, source_id="synthetic_demo_0") == "SearchPhaseIncomplete"
    finally:
        c.conn.close()


def test_pause_during_search_preserves_pending_tasks(tmp_path):
    adapter = SyntheticAdapter()
    original = adapter.search
    def search(*args):
        result = original(*args)
        (tmp_path / "pause_1.request").write_text("pause", encoding="utf-8")
        return result
    adapter.search = search
    c = make(tmp_path, adapter)
    try:
        result = c.run("synthetic_demo", stage="search")
        assert result["result"] == "PAUSED"
        assert result["phase"]["search_succeeded"] == 1
        assert len(adapter.calls) == 1 and not adapter.detail_ids
        assert c.conn.execute("SELECT count(*) FROM search_requests WHERE status='pending'").fetchone()[0] == 49
    finally:
        c.conn.close()


def test_forged_checkpoint_does_not_authorize_details(tmp_path):
    c = make(tmp_path)
    try:
        run = c.create("synthetic_demo")
        (tmp_path / "logs/checkpoint_1.json").write_text('{"search_complete":true}', encoding="utf-8")
        assert c.run(run_id=run, stage="details")["reason"] == "SearchPhaseIncomplete"
        assert not c.adapter.calls and not c.adapter.detail_ids
    finally:
        c.conn.close()


def test_detail_pause_resumes_without_repeating_success(tmp_path):
    c = make(tmp_path, pilot=True)
    try:
        c.run("synthetic_demo", stage="search")
        original = c.adapter.detail
        def detail(*args):
            value = original(*args)
            (tmp_path / "pause_1.request").write_text("pause", encoding="utf-8")
            return value
        c.adapter.detail = detail
        result = c.run(run_id=1, stage="details")
        assert result["result"] == "PAUSED" and result["details"]["succeeded"] == 1
        c.adapter.detail = original
        (tmp_path / "pause_1.request").unlink()
        assert c.run(run_id=1, stage="details")["result"] == "SUCCESS"
        assert len(c.adapter.detail_ids) == len(set(c.adapter.detail_ids)) == 5
        assert len(c.adapter.calls) == 4
    finally:
        c.conn.close()


def test_cache_save_failure_keeps_real_response_evidence(tmp_path):
    class UnwritableCache:
        def load(self, *args):
            return {}
        def save(self, *args):
            raise AccessCacheError("synthetic_demo failure")
    c = make(tmp_path, pilot=True)
    c.access_cache = UnwritableCache()
    try:
        result = c.run("synthetic_demo", stage="search")
        assert result["reason"] == "AccessCacheSaveFailed"
        assert result["table_counts"]["raw_artifacts"] == 1
        assert result["hits"] == 9 and result["attempt_status_counts"] == {"succeeded": 1}
        assert len(c.adapter.calls) == 1 and not c.adapter.detail_ids
    finally:
        c.conn.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI integration")
def test_dpapi_restart_details_without_search_or_plaintext(tmp_path):
    c = make(tmp_path, encrypted=True, pilot=True)
    c.access_cache.verify_available()
    c.run("synthetic_demo", stage="search")
    c.conn.close()
    # New Python process, same Windows user, synthetic credentials only.
    code = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path('tests/research').resolve()))
from test_baseline_stages import make
c=make(Path(sys.argv[1]), encrypted=True, pilot=True)
try:
 r=c.run(run_id=1, stage='details')
 assert r['result']=='SUCCESS'
 assert not c.adapter.calls and len(c.adapter.detail_ids)==5
finally:
 c.conn.close()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"synthetic_access" not in path.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI integration")
def test_corrupt_cache_and_wrong_context_refuse_refresh(tmp_path):
    c = make(tmp_path, encrypted=True, pilot=True)
    try:
        c.run("synthetic_demo", stage="search")
        with pytest.raises(AccessCacheError):
            AccessCache(tmp_path / "private_access", tmp_path / "other.db").load(1, [1])
        (tmp_path / "private_access/run_1_attempt_1.dpapi").write_bytes(b"corrupted")
        c.access.clear()
        result = c.run(run_id=1, stage="details")
        assert result["result"] == "BLOCKED" and "AccessCacheUnavailable" in result["reason"]
        assert len(c.adapter.calls) == 4 and not c.adapter.detail_ids
    finally:
        c.conn.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI integration")
def test_crash_after_response_commit_preserves_encrypted_access(tmp_path, monkeypatch):
    c = make(tmp_path, encrypted=True, pilot=True)
    original = c.record_response
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(c, "record_response", crash)
    with pytest.raises(KeyboardInterrupt):
        c.run("synthetic_demo", stage="search")
    c.conn.close()
    other = make(tmp_path, encrypted=True, pilot=True)
    try:
        result = other.run(run_id=1, stage="search")
        assert result["phase"]["search_complete"] and len(other.adapter.calls) == 3
        assert "synthetic_demo_0" in other.access
    finally:
        other.conn.close()


def cli_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("baseline_cli", ROOT / "scripts/phase1d_baseline_collect.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "DATA", tmp_path / "pilot")
    monkeypatch.setattr(cli, "DB", tmp_path / "pilot/pilot.db")
    monkeypatch.setattr(cli, "FORMAL_DATA", tmp_path / "formal")
    monkeypatch.setattr(cli, "FORMAL_DB", tmp_path / "formal/formal.db")
    return cli


def test_cli_default_and_inspect_never_create_or_call(tmp_path, monkeypatch):
    cli = cli_module(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "XiaohongshuMCP", lambda *a, **k: pytest.fail("unexpected network adapter"))
    assert cli.main([]) == 0
    assert cli.main(["--formal", "--inspect"]) == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI integration")
def test_explicit_cli_start_is_search_only_and_preserves_pilot(tmp_path, monkeypatch):
    cli = cli_module(tmp_path, monkeypatch)
    cli.DATA.mkdir()
    cli.DB.write_bytes(b"synthetic_demo protected pilot")
    digest = hashlib.sha256(cli.DB.read_bytes()).hexdigest()
    adapter = SyntheticAdapter()
    monkeypatch.setattr(adapter, "preflight", lambda: ({"ready": True}, "synthetic_demo"))
    monkeypatch.setattr(cli, "XiaohongshuMCP", lambda *a, **k: adapter)
    monkeypatch.setattr(cli, "load_auth", lambda *a: None)
    monkeypatch.setattr(cli, "BaselineConfig", lambda **kw: BaselineConfig(interval_seconds=0, **kw))
    assert cli.main(["--start-baseline"]) == 0
    assert len(adapter.calls) == 50 and not adapter.detail_ids
    assert hashlib.sha256(cli.DB.read_bytes()).hexdigest() == digest
    # Separate explicit details action; uses encrypted cache, no new searches.
    assert cli.main(["--formal", "--resume", "1", "--stage", "details"]) == 0
    assert len(adapter.calls) == 50 and len(adapter.detail_ids) == 8
    with pytest.raises(SystemExit):
        cli.main(["--start-baseline"])
