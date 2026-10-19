"""Reusable manual deferral and explicit refill, synthetic data only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_final_preflight import memory
from test_detail_auto_retry import setup, clock, Adapter, failure, Clock
from marketlens.research.database import ROOT, initialize, transaction


def evidence_for(c, directory, attempt=None):
    row = c.conn.execute("""SELECT a.attempt_id,t.* FROM collection_attempts a JOIN collection_tasks t USING(task_id)
        WHERE a.attempt_id=?""", (attempt,)).fetchone() if attempt else c.conn.execute("""SELECT a.attempt_id,t.* FROM collection_attempts a JOIN collection_tasks t USING(task_id)
        WHERE t.task_type='feed_detail' AND a.error_type='TimeoutError' ORDER BY a.attempt_id DESC LIMIT 1""").fetchone()
    binding = c._pending_binding(row["run_id"], row["task_id"], row["post_id"], row["attempt_id"])
    path = directory / f"synthetic_evidence_{row['attempt_id']}.json"
    path.write_text(json.dumps({"binding": binding, "checkpoint_sha256": hashlib.sha256((directory / f"logs/checkpoint_{row['run_id']}.json").read_bytes()).hexdigest()}), encoding="utf-8")
    return binding, path


def blocked(conn, directory):
    c = setup(conn, directory, Adapter([failure(None, "TimeoutError")]))
    run = c.run(stage="search")["run_id"]
    assert c.run(run_id=run, stage="details")["result"] == "BLOCKED"
    return c, *evidence_for(c, directory)


def review(c, b, evidence=None, *, classification="comment_loading_stalled", note="人工确认评论停滞，暂缓补采", activate=False):
    return c.review_pending_detail(b["run_id"], b["task_id"], b["post_id"], b["attempt_id"],
        classification=classification, note=note, evidence_file=evidence, activate=activate)


@pytest.mark.parametrize("classification", ["comment_loading_stalled", "detail_timeout_unknown"])
def test_defer_preserves_history_and_is_idempotent(memory, tmp_path, clock, classification):
    c, b, evidence = blocked(memory, tmp_path)
    old_attempts = [tuple(r) for r in memory.execute("SELECT * FROM collection_attempts")]
    checkpoint = (tmp_path / "logs/checkpoint_1.json").read_bytes()
    result = review(c, b, evidence, classification=classification)
    assert result["pending_review_count"] == 1 and result["details"]["succeeded"] == 0
    assert result["normal_unique_posts"] == 2 and result["confirmed_unavailable_count"] == 0
    path = tmp_path / f"logs/pending_review_{b['task_id']}_{b['attempt_id']}.json"
    original = path.read_bytes()
    review(c, b, evidence, classification=classification)
    assert path.read_bytes() == original
    assert [tuple(r) for r in memory.execute("SELECT * FROM collection_attempts")] == old_attempts
    assert (tmp_path / "logs/checkpoint_1.json").read_bytes() == checkpoint
    assert memory.execute("SELECT count(*) FROM post_snapshots").fetchone()[0] == 0
    with pytest.raises(ValueError, match="immutable"):
        review(c, b, evidence, note="different human decision")


def test_pending_partial_is_open_and_explicit_activation_refills_original_task(memory, tmp_path, clock):
    c, b, evidence = blocked(memory, tmp_path)
    review(c, b, evidence)
    result = c.run(run_id=1, stage="details")
    assert result["result"] == "PARTIAL" and result["pending_review_count"] == 1
    assert result["details"]["succeeded"] == 1 and not result["run_sealed"]
    assert memory.execute("SELECT status,finished_at FROM collection_runs").fetchone()[:] == ("running", None)
    assert result["phase"]["stage"] == "pending_review"
    before = len(c.adapter.requested)
    c.run(run_id=1, stage="details")
    assert len(c.adapter.requested) == before
    review(c, b, activate=True, note="明确授权对原任务再尝试一次")
    assert len(c.adapter.requested) == before
    result = c.run(run_id=1, stage="details")
    assert result["result"] == "SUCCESS" and result["run_sealed"]
    assert result["pending_review_count"] == 0 and result["details"]["succeeded"] == 2
    assert len(c.adapter.requested) == before + 1
    assert [r[0] for r in memory.execute("SELECT attempt_no FROM collection_attempts WHERE task_id=? ORDER BY attempt_no", (b["task_id"],))] == [1, 2]


def test_activation_does_not_approve_another_timeout(memory, tmp_path, clock):
    c, b, evidence = blocked(memory, tmp_path)
    review(c, b, evidence)
    review(c, b, activate=True, note="authorize one refill")
    c.adapter.failures = [failure(None, "TimeoutError")]
    assert c.run(run_id=1, stage="details")["reason"].startswith("UnsafeFailure")
    before = len(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == before
    with pytest.raises(ValueError, match="Stale"):
        review(c, b, activate=True)
    newer, path = evidence_for(c, tmp_path)
    assert review(c, newer, path)["pending_review_count"] == 1


def test_never_reset_lifetime_attempt_budget(memory, tmp_path, clock):
    c, b, evidence = blocked(memory, tmp_path)
    for number in range(1, 6):
        assert b["history"][-1]["attempt_no"] == number
        review(c, b, evidence)
        if number == 5:
            with pytest.raises(ValueError, match="budget"):
                review(c, b, activate=True)
            break
        review(c, b, activate=True, note="explicit one-attempt refill")
        c.adapter.failures = [failure(None, "TimeoutError")]
        assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
        b, evidence = evidence_for(c, tmp_path)
    assert len(c.adapter.requested) == 5


@pytest.mark.parametrize("classification", [None, "source_deleted", "auto_timeout", ""])
def test_classification_requires_explicit_supported_human_choice(memory, tmp_path, clock, classification):
    c, b, evidence = blocked(memory, tmp_path)
    with pytest.raises(ValueError): review(c, b, evidence, classification=classification)
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"


@pytest.mark.parametrize("key", ["run_id", "task_id", "post_id", "attempt_id", "database_path", "raw_hashes"])
def test_wrong_identity_or_evidence_is_rejected(memory, tmp_path, clock, key):
    c, b, evidence = blocked(memory, tmp_path)
    obj = json.loads(evidence.read_text()); obj["binding"][key] = "wrong"
    evidence.write_text(json.dumps(obj))
    with pytest.raises(ValueError): review(c, b, evidence)
    assert memory.execute("SELECT status FROM collection_tasks WHERE task_id=?", (b["task_id"],)).fetchone()[0] == "failed"


def test_partial_state_without_audit_never_skips(memory, tmp_path, clock):
    c, b, _ = blocked(memory, tmp_path)
    with transaction(memory):
        memory.execute("UPDATE collection_tasks SET status='partial' WHERE task_id=?", (b["task_id"],))
    before = len(c.adapter.requested)
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    assert len(c.adapter.requested) == before


def test_unknown_running_attempt_prevents_deferral(memory, tmp_path, clock):
    c, b, evidence = blocked(memory, tmp_path)
    other = memory.execute("SELECT task_id FROM collection_tasks WHERE task_type='feed_detail' AND task_id<>?", (b["task_id"],)).fetchone()[0]
    c.begin_attempt(other, 1, "/api/v1/feeds/detail")
    with pytest.raises(ValueError, match="uncertain"):
        review(c, b, evidence)


def test_deleted_and_pending_counts_stay_separate(memory, tmp_path, clock):
    from test_source_deleted_review import blocked_target, confirm
    c, deleted_evidence = blocked_target(memory, tmp_path)
    confirm(c, deleted_evidence)
    c.adapter.failures = [failure(None, "TimeoutError")]
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    b, evidence = evidence_for(c, tmp_path)
    review(c, b, evidence)
    result = c.run(run_id=1, stage="details")
    assert result["normal_unique_posts"] == 3
    assert result["details"]["succeeded"] == result["confirmed_unavailable_count"] == result["pending_review_count"] == 1
    assert not result["run_sealed"]
    review(c, b, activate=True)
    result = c.run(run_id=1, stage="details")
    assert result["result"] == "PARTIAL" and result["run_sealed"]
    assert result["pending_review_count"] == 0 and result["details"]["succeeded"] == 2


@pytest.mark.parametrize("which", ["defer", "activate"])
def test_file_database_gap_needs_explicit_repair(memory, tmp_path, clock, monkeypatch, which):
    c, b, evidence = blocked(memory, tmp_path)
    if which == "activate": review(c, b, evidence)
    import marketlens.research.baseline_collector as module
    from contextlib import contextmanager
    @contextmanager
    def broken(conn):
        raise OSError("synthetic transaction failure")
        yield
    with monkeypatch.context() as m:
        m.setattr(module, "transaction", broken)
        with pytest.raises(OSError): review(c, b, evidence, activate=which == "activate")
    assert c.run(run_id=1, stage="details")["result"] == "BLOCKED"
    review(c, b, evidence, activate=which == "activate")
    assert c.run(run_id=1, stage="details")["result"] in ("PARTIAL", "SUCCESS")


def test_cli_local_defer_and_activate_no_adapter_or_auth(memory, tmp_path, clock, monkeypatch):
    from test_baseline_stages import cli_module
    from test_detail_auto_retry import TinyFormal
    c, b, evidence = blocked(memory, tmp_path)
    cli = cli_module(tmp_path, monkeypatch)
    placeholder = tmp_path / "existing.json"; placeholder.write_text("{}")
    monkeypatch.setattr(cli, "FORMAL_DB", placeholder); monkeypatch.setattr(cli, "FORMAL_DATA", tmp_path)
    class Borrowed:
        def __getattr__(self, name): return getattr(memory, name)
        def close(self): pass
    monkeypatch.setattr(cli, "connect", lambda *a, **kw: Borrowed())
    monkeypatch.setattr(cli, "BaselineConfig", TinyFormal)
    monkeypatch.setattr(cli, "load_auth", lambda *a: pytest.fail("No credential reads"))
    monkeypatch.setattr(cli, "XiaohongshuMCP", lambda *a, **kw: pytest.fail("No Adapter"))
    common = ["--formal", "--run-id", str(b["run_id"]), "--task-id", str(b["task_id"]), "--post-id", str(b["post_id"]), "--review-note", "Human reviewed"]
    assert cli.main(common + ["--defer-detail-attempt", str(b["attempt_id"]), "--review-classification", "comment_loading_stalled", "--review-evidence", str(evidence)]) == 0
    assert cli.main(common + ["--reactivate-detail-attempt", str(b["attempt_id"]), "--confirm-server-request-finished"]) == 0


@pytest.mark.skipif(os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") == "1", reason="Isolated disk and subprocess required")
def test_process_restart_then_explicit_refill(tmp_path):
    conn = initialize(tmp_path / "synthetic_pending.db")
    clock = Clock()
    with patch("marketlens.research.baseline_collector.time.time", clock.time), patch("marketlens.research.baseline_collector.time.sleep", clock.sleep):
        c, b, evidence = blocked(conn, tmp_path)
    review(c, b, evidence)
    access = c.access.copy(); conn.close()
    code = '''
import sys,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/"tests/research"))
from test_detail_auto_retry import setup,Adapter,Clock
from test_pending_review import review
from marketlens.research.database import connect
root=Path(sys.argv[1]);conn=connect(root/"synthetic_pending.db")
c=setup(conn,root,Adapter());c.access.update(json.loads(sys.argv[2]));b=json.loads(sys.argv[3]);clock=Clock()
with patch("marketlens.research.baseline_collector.time.sleep",clock.sleep),patch("marketlens.research.baseline_collector.time.time",clock.time),patch("marketlens.research.baseline_collector.random.uniform",lambda *a:210):
    result=c.run(run_id=1,stage="details")
    assert result["result"]=="PARTIAL" and not result["run_sealed"]
    assert c.adapter.requested==[("synthetic_demo_1",20)]
    review(c,b,activate=True)
    result=c.run(run_id=1,stage="details")
    assert result["result"]=="SUCCESS" and result["pending_review_count"]==0
assert not c.adapter.calls
assert conn.execute("SELECT max(attempt_no) FROM collection_attempts WHERE task_id=?",(b["task_id"],)).fetchone()[0]==2
conn.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(access), json.dumps(b)], cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
