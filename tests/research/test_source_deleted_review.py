"""Explicit single-source deletion review; synthetic data, no network."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_final_preflight import memory
from test_detail_auto_retry import setup, clock, Adapter, failure, review_evidence
from test_baseline_collection import SyntheticAdapter
from marketlens.adapters.xiaohongshu_mcp import utc_now
from marketlens.research.collection_pipeline import insert
from marketlens.research.database import transaction, ROOT, initialize


class ThreePosts(Adapter):
    def search(self, *args):
        result = SyntheticAdapter.search(self, *args)
        result.payload["data"]["feeds"] = result.payload["data"]["feeds"][:3]
        return result


def blocked_target(conn, directory):
    c = setup(conn, directory, ThreePosts())
    run = c.create("synthetic_demo")
    with transaction(conn):
        insert(conn, "posts", post_id=108, source_platform="xiaohongshu", source_post_id="synthetic_demo_0", first_seen_at=utc_now())
        insert(conn, "collection_tasks", task_id=153, run_id=run, task_type="feed_detail", post_id=108, sequence_no=2)
    assert c.run(run_id=run, stage="search")["result"] == "PAUSED"
    other = conn.execute("SELECT t.*,p.source_post_id FROM collection_tasks t JOIN posts p USING(post_id) WHERE t.task_id=154").fetchone()
    assert c._call(other, source_id=other["source_post_id"]) is None
    c.adapter.failures = [failure(None, "TimeoutError")] * 2
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    first = conn.execute("SELECT attempt_id FROM collection_attempts WHERE task_id=153").fetchone()[0]
    c.reviewed_timeout = review_evidence(c, first, directory)
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    c.reviewed_timeout = None
    evidence = {"binding": c._source_deleted_binding(1, 153, 108), "reason": "source_deleted",
                "checkpoint_sha256": hashlib.sha256((directory / "logs/checkpoint_1.json").read_bytes()).hexdigest()}
    path = directory / "synthetic_deleted_evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return c, path


def confirm(c, evidence):
    return c.confirm_source_deleted(1, 153, 108, "source_deleted", evidence)


def test_explicit_confirmation_preserves_all_history(memory, tmp_path, clock):
    c, evidence = blocked_target(memory, tmp_path)
    attempts = [tuple(r) for r in memory.execute("SELECT * FROM collection_attempts")]
    plan = memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0]
    raw = {str(p): p.read_bytes() for p in (tmp_path / "raw").glob('*')}
    checkpoint = (tmp_path / "logs/checkpoint_1.json").read_bytes()
    calls = list(c.adapter.requested)
    result = confirm(c, evidence)
    assert result["result"] == "SOURCE_UNAVAILABLE_CONFIRMED"
    assert result["details"] == {"succeeded": 1, "failed": 0, "unrequested": 1, "uncertain": 0, "confirmed_unavailable": 1}
    assert result["normal_unique_posts"] == result["unique_posts"] == 3
    assert result["confirmed_unavailable_count"] == 1
    assert [tuple(r) for r in memory.execute("SELECT * FROM collection_attempts")] == attempts
    assert memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0] == plan
    assert all(Path(p).read_bytes() == content for p, content in raw.items())
    assert (tmp_path / "logs/checkpoint_1.json").read_bytes() == checkpoint
    record_path = tmp_path / "logs/detail_unavailable_153.json"
    record = json.loads(record_path.read_text())
    assert record["decision"]["reason"] == "source_deleted"
    assert record["reviewed_at"] and record["original_checkpoint"].encode() == checkpoint
    before = record_path.read_bytes()
    confirm(c, evidence)
    assert record_path.read_bytes() == before and c.adapter.requested == calls


def test_resume_skips_deleted_and_successful_details_and_finishes_partial(memory, tmp_path, clock):
    c, evidence = blocked_target(memory, tmp_path)
    confirm(c, evidence)
    successful = [tuple(r) for r in memory.execute("SELECT * FROM post_snapshots")]
    previous = list(c.adapter.requested)
    c.access.pop("synthetic_demo_0")  # Unavailable source requires no token.
    assert c.run(run_id=1, stage="details")["result"] == "PARTIAL"
    assert c.adapter.requested == previous + [("synthetic_demo_2", 20)]
    assert [tuple(r) for r in memory.execute("SELECT * FROM post_snapshots ORDER BY snapshot_id LIMIT 1")] == successful
    summary = c.summary(1)
    assert summary["details"]["succeeded"] == 2 and summary["confirmed_unavailable_count"] == 1
    assert summary["normal_unique_posts"] == 3
    assert summary["phase"]["stage"] == "completed"
    assert memory.execute("SELECT status FROM collection_runs").fetchone()[0] == "partial"
    assert c.run(run_id=1, stage="details")["result"] == "ALREADY_CLOSED"


def test_without_review_timeout_still_blocks(memory, tmp_path, clock):
    c, _ = blocked_target(memory, tmp_path)
    calls = list(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["reason"].startswith("UnsafeFailure")
    assert c.adapter.requested == calls


@pytest.mark.parametrize("identity", [(2, 153, 108), (1, 154, 108), (1, 153, 109)])
def test_other_identity_never_authorized(memory, tmp_path, clock, identity):
    c, evidence = blocked_target(memory, tmp_path)
    with pytest.raises(ValueError):
        c.confirm_source_deleted(*identity, "source_deleted", evidence)
    assert memory.execute("SELECT status FROM collection_tasks WHERE task_id=153").fetchone()[0] == "failed"


@pytest.mark.parametrize("mutation", ["binding", "checkpoint", "reason", "raw"])
def test_invalid_evidence_stops_before_task_update(memory, tmp_path, clock, mutation):
    c, evidence = blocked_target(memory, tmp_path)
    obj = json.loads(evidence.read_text())
    if mutation == "binding": obj["binding"]["post_id"] = 109
    elif mutation == "checkpoint": obj["checkpoint_sha256"] = "0" * 64
    elif mutation == "reason": obj["reason"] = "unknown_timeout"
    else:
        path = memory.execute("SELECT file_path FROM raw_artifacts WHERE attempt_id IN (SELECT attempt_id FROM collection_attempts WHERE task_id=153) LIMIT 1").fetchone()[0]
        Path(path).write_text("{}")
    evidence.write_text(json.dumps(obj))
    with pytest.raises(ValueError): confirm(c, evidence)
    assert memory.execute("SELECT status FROM collection_tasks WHERE task_id=153").fetchone()[0] == "failed"
    assert not (tmp_path / "logs/detail_unavailable_153.json").exists()


def test_running_request_blocks_confirmation(memory, tmp_path, clock):
    c, evidence = blocked_target(memory, tmp_path)
    c.begin_attempt(155, 1, "/api/v1/feeds/detail")
    with pytest.raises(ValueError, match="uncertain"): confirm(c, evidence)


def test_third_attempt_invalidates_old_review(memory, tmp_path, clock):
    c, evidence = blocked_target(memory, tmp_path)
    c.begin_attempt(153, 3, "/api/v1/feeds/detail")
    with pytest.raises(ValueError): confirm(c, evidence)


def test_cancelled_alone_does_not_allow_skip(memory, tmp_path, clock):
    c, _ = blocked_target(memory, tmp_path)
    with transaction(memory):
        memory.execute("UPDATE collection_tasks SET status='cancelled',finished_at=? WHERE task_id=153", (utc_now(),))
    calls = list(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["reason"].startswith("SourceDeletionReviewNeedsRepair")
    assert c.summary(1)["confirmed_unavailable_count"] == 0 and c.adapter.requested == calls


def test_missing_or_changed_review_record_blocks_resume(memory, tmp_path, clock):
    c, evidence = blocked_target(memory, tmp_path)
    confirm(c, evidence)
    record_path = tmp_path / "logs/detail_unavailable_153.json"
    original = record_path.read_bytes()
    record_path.write_text("{}")
    calls = list(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    assert c.adapter.requested == calls
    record_path.write_bytes(original)
    assert c.run(run_id=1, stage="details")["result"] == "PARTIAL"


def test_file_before_database_crash_requires_explicit_repair(memory, tmp_path, clock, monkeypatch):
    c, evidence = blocked_target(memory, tmp_path)
    import marketlens.research.baseline_collector as module
    from contextlib import contextmanager
    @contextmanager
    def failed_transaction(conn):
        raise OSError("synthetic disk failure")
        yield
    with monkeypatch.context() as m:
        m.setattr(module, "transaction", failed_transaction)
        with pytest.raises(OSError): confirm(c, evidence)
    assert memory.execute("SELECT status FROM collection_tasks WHERE task_id=153").fetchone()[0] == "failed"
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    confirm(c, evidence)
    assert c.run(run_id=1, stage="details")["result"] == "PARTIAL"


def test_cli_confirmation_never_constructs_adapter_or_reads_auth(memory, tmp_path, clock, monkeypatch):
    from test_baseline_stages import cli_module
    from test_detail_auto_retry import TinyFormal
    c, evidence = blocked_target(memory, tmp_path)
    cli = cli_module(tmp_path, monkeypatch)
    placeholder = tmp_path / "existing_placeholder.json"
    placeholder.write_text("{}")
    monkeypatch.setattr(cli, "FORMAL_DB", placeholder)
    monkeypatch.setattr(cli, "FORMAL_DATA", tmp_path)
    class Borrowed:
        def __getattr__(self, key): return getattr(memory, key)
        def close(self): pass
    monkeypatch.setattr(cli, "connect", lambda *a, **kw: Borrowed())
    monkeypatch.setattr(cli, "BaselineConfig", TinyFormal)
    monkeypatch.setattr(cli, "XiaohongshuMCP", lambda *a, **kw: pytest.fail("No Adapter permitted"))
    monkeypatch.setattr(cli, "load_auth", lambda *a: pytest.fail("No credential read permitted"))
    assert cli.main(["--formal", "--confirm-source-deleted", "153", "--run-id", "1", "--post-id", "108",
                     "--review-reason", "source_deleted", "--review-evidence", str(evidence)]) == 0
    assert c.summary(1)["confirmed_unavailable_count"] == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_other_access_failures_still_stop_after_confirmed_skip(memory, tmp_path, clock, status):
    c, evidence = blocked_target(memory, tmp_path)
    confirm(c, evidence)
    c.adapter.failures = [failure(status)]
    before = len(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == before + 1
    assert c.run(run_id=1, stage="details")["reason"].startswith("UnsafeFailure")
    assert len(c.adapter.requested) == before + 1
    assert c.summary(1)["confirmed_unavailable_count"] == 1


@pytest.mark.skipif(os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") == "1", reason="Requires isolated temporary disk and subprocess")
def test_confirmed_skip_survives_new_process(tmp_path):
    from unittest.mock import patch
    from test_detail_auto_retry import Clock
    conn = initialize(tmp_path / "synthetic_deleted.db")
    fake_clock = Clock()
    with patch("marketlens.research.baseline_collector.time.time", fake_clock.time), patch("marketlens.research.baseline_collector.time.sleep", fake_clock.sleep), patch("marketlens.research.baseline_collector.random.uniform", lambda *a: 210):
        c, evidence = blocked_target(conn, tmp_path)
    confirm(c, evidence)
    mapping = c.access.copy()
    conn.close()
    code = '''
import json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/"tests/research"))
from test_detail_auto_retry import setup,Adapter
from marketlens.research.database import connect
root=Path(sys.argv[1]);conn=connect(root/"synthetic_deleted.db")
c=setup(conn,root,Adapter());c.access.update(json.loads(sys.argv[2]))
with patch("marketlens.research.baseline_collector.time.sleep",lambda *a:None):
    result=c.run(run_id=1,stage="details")
assert result["result"]=="PARTIAL",result
assert result["confirmed_unavailable_count"]==1
assert c.adapter.requested==[("synthetic_demo_2",20)]
assert not c.adapter.calls
assert conn.execute("SELECT count(*) FROM collection_attempts WHERE task_id=153").fetchone()[0]==2
conn.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(mapping)], cwd=ROOT, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
