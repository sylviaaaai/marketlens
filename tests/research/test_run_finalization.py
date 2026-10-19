"""Offline local closure and recovery, synthetic fixtures only."""
import copy
import hashlib
import json
import socket
import sqlite3
import subprocess

import pytest

from test_final_preflight import memory
from test_detail_auto_retry import clock, Adapter, failure, setup
from test_pending_review import blocked, evidence_for, review
from test_baseline_stages import cli_module
from marketlens.research import run_finalization as module
from marketlens.research.database import initialize, assert_integrity, transaction


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Finalization tests forbid network, Docker, subprocesses and DPAPI")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("marketlens.research.access_cache.dpapi", forbidden)


def decision_for(c, b, root):
    db = c.conn.execute("pragma database_list").fetchone()[2]
    from pathlib import Path
    record = root / f"logs/pending_review_{b['task_id']}_{b['attempt_id']}.json"
    d = json.loads(record.read_text(encoding='utf-8'))["decision"]
    return {"version": 1, "database_path": str(Path(db).resolve()) if db else ":memory:",
            "run_id": b["run_id"], "reason": "Human confirmed collection ended; no further refill",
            "checkpoint_sha256": module.sha((root / 'logs/checkpoint_1.json').read_bytes()),
            "permanent_skips": [{"task_id": b["task_id"], "post_id": b["post_id"],
                "attempt_id": b["attempt_id"], "classification": "banned_or_restricted", "no_refill": True,
                "review_sha256": module.sha(record.read_bytes()), "evidence_sha256": d["evidence_sha256"],
                "note": "Human confirmed banned/restricted; permanently stop refill"}]}


def ready(conn, root):
    c, b, evidence = blocked(conn, root)
    review(c, b, evidence)
    assert c.run(run_id=1, stage="details")["result"] == "PARTIAL"
    return c, decision_for(c, b, root)


def history(conn):
    return {name: [tuple(r) for r in conn.execute('SELECT * FROM ' + name)] for name in
            ("collection_tasks", "collection_attempts", "raw_artifacts", "post_snapshots", "comment_observations")}


def test_permanent_skip_seals_partial_preserves_evidence_and_summary(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    before = history(memory)
    files = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert module.finalize_run(memory, tmp_path, 1, d, check_only=True)["result"] == "FINALIZATION_READY"
    assert not module.intent_path(tmp_path, 1).exists()
    result = module.finalize_run(memory, tmp_path, 1, d)
    assert result["status"] == "partial" and result["counts"]["banned_or_restricted"] == 1
    assert result["counts"]["succeeded"] == 1 and result["counts"]["failed"] == 0
    assert history(memory) == before
    for name, content in files.items():
        from pathlib import Path
        if not name.endswith('checkpoint_1.json'):
            assert Path(name).read_bytes() == content
    cp = json.loads((tmp_path / 'logs/checkpoint_1.json').read_text(encoding='utf-8'))
    assert cp["stage"] == "completed" and cp["execution_status"] == "PARTIAL" and cp["run_sealed"]
    summary = c.summary(1)
    assert summary["pending_review_count"] == 0 and summary["permanent_skip_count"] == 1
    assert summary["details"]["banned_or_restricted"] == 1 and summary["run_sealed"]
    assert summary["local_finalization"]["checkpoint_synchronized"]
    assert assert_integrity(memory)["integrity_check"] == ["ok"]
    assert len(memory.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 18
    with pytest.raises(sqlite3.IntegrityError, match="Closed Run"):
        memory.execute("UPDATE collection_runs SET status='running' WHERE run_id=1")
    with pytest.raises(sqlite3.IntegrityError, match="closed"):
        memory.execute("UPDATE collection_tasks SET status='succeeded' WHERE status='partial'")


def test_all_success_can_close_completed(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    c.run(stage='search')
    for t in memory.execute("SELECT t.*,p.source_post_id FROM collection_tasks t JOIN posts p USING(post_id)").fetchall():
        assert c._call(t, source_id=t['source_post_id']) is None
    d = {"version": 1, "database_path": ":memory:", "run_id": 1, "reason": "All successful work verified",
         "checkpoint_sha256": module.sha((tmp_path/'logs/checkpoint_1.json').read_bytes()), "permanent_skips": []}
    assert module.finalize_run(memory, tmp_path, 1, d)["status"] == "completed"


@pytest.mark.parametrize('status', ['pending', 'running', 'failed', 'cancelled'])
def test_unresolved_tasks_rejected(memory, tmp_path, clock, status):
    c, d = ready(memory, tmp_path)
    with transaction(memory):
        memory.execute("UPDATE collection_tasks SET status=? WHERE status='succeeded' AND task_type='feed_detail'", (status,))
    with pytest.raises(ValueError):
        module.finalize_run(memory, tmp_path, 1, d)
    assert not module.intent_path(tmp_path, 1).exists()
    assert memory.execute('select status from collection_runs').fetchone()[0] == 'running'


def test_unfinished_attempt_rejected(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    t = memory.execute("SELECT task_id FROM collection_tasks WHERE status='partial'").fetchone()[0]
    c.begin_attempt(t, 2, '/api/v1/feeds/detail')
    with pytest.raises(ValueError, match='Attempt'):
        module.finalize_run(memory, tmp_path, 1, d)


@pytest.mark.parametrize('change', ['missing', 'identity', 'hash', 'deletion', 'no_refill', 'duplicate', 'stale'])
def test_human_skip_decision_must_match_actual_task_and_evidence(memory, tmp_path, clock, change):
    c, d = ready(memory, tmp_path)
    s = d['permanent_skips'][0]
    if change == 'missing': d['permanent_skips'] = []
    if change == 'identity': s['post_id'] += 1
    if change == 'hash': s['review_sha256'] = '0'*64
    if change == 'deletion': s['classification'] = 'source_deleted'
    if change == 'no_refill': s['no_refill'] = False
    if change == 'duplicate': d['permanent_skips'].append(copy.deepcopy(s))
    if change == 'stale': s['task_id'] += 100
    with pytest.raises(ValueError): module.finalize_run(memory, tmp_path, 1, d)
    assert not module.intent_path(tmp_path, 1).exists()


def test_raw_corruption_and_missing_candidate_task_rejected(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    from pathlib import Path
    raw = Path(memory.execute('select file_path from raw_artifacts limit 1').fetchone()[0])
    raw.write_text('corrupt')
    with pytest.raises(ValueError, match='hash'): module.finalize_run(memory, tmp_path, 1, d)


def test_missing_candidate_task_is_not_hidden_by_counts(memory, tmp_path, clock, monkeypatch):
    c = setup(memory, tmp_path)
    monkeypatch.setattr(c, '_detail_tasks', lambda run: None)
    c.run(stage='search')
    d = {'version': 1, 'database_path': ':memory:', 'run_id': 1, 'reason': 'Explicit finalization',
         'checkpoint_sha256': module.sha((tmp_path/'logs/checkpoint_1.json').read_bytes()), 'permanent_skips': []}
    with pytest.raises(ValueError, match='identities'): module.finalize_run(memory, tmp_path, 1, d)


def test_success_flag_without_attempt_and_snapshot_is_rejected(memory, tmp_path, clock):
    c = setup(memory, tmp_path)
    c.run(stage='search')
    from marketlens.adapters.xiaohongshu_mcp import utc_now
    memory.execute("UPDATE collection_tasks SET status='succeeded',finished_at=? WHERE task_type='feed_detail'", (utc_now(),))
    d = {'version': 1, 'database_path': ':memory:', 'run_id': 1, 'reason': 'Explicit finalization',
         'checkpoint_sha256': module.sha((tmp_path/'logs/checkpoint_1.json').read_bytes()), 'permanent_skips': []}
    with pytest.raises(ValueError, match='Successful task'): module.finalize_run(memory, tmp_path, 1, d)


def test_failed_atomic_intent_publish_leaves_no_false_record(memory, tmp_path, clock, monkeypatch):
    c, d = ready(memory, tmp_path)
    def fail(*args): raise OSError('synthetic link failure')
    monkeypatch.setattr(module.os, 'link', fail)
    with pytest.raises(OSError): module.finalize_run(memory, tmp_path, 1, d)
    assert not module.intent_path(tmp_path, 1).exists()
    assert memory.execute('select status from collection_runs').fetchone()[0] == 'running'


def test_idempotence_and_conflicting_decision(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    module.finalize_run(memory, tmp_path, 1, d)
    files = {str(p): p.read_bytes() for p in (tmp_path/'logs').glob('*')}
    result = module.finalize_run(memory, tmp_path, 1, d)
    assert result['result'] == 'ALREADY_FINALIZED'
    assert files == {str(p): p.read_bytes() for p in (tmp_path/'logs').glob('*')}
    d['reason'] = 'different human reason'
    with pytest.raises(ValueError, match='immutable'): module.finalize_run(memory, tmp_path, 1, d)


def test_later_dataset_and_other_run_do_not_invalidate_sealed_history(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    module.finalize_run(memory, tmp_path, 1, d)
    memory.execute("INSERT INTO dataset_versions(project_id,dataset_name,version,source_run_id) VALUES(1,'synthetic_demo','v1',1)")
    setup(memory, tmp_path).create('synthetic_demo_later_run')
    assert module.read_finalization(memory, tmp_path, 1)['sealed']
    assert module.finalize_run(memory, tmp_path, 1, d)['result'] == 'ALREADY_FINALIZED'


@pytest.mark.parametrize('stage', ['intent_written', 'before_commit', 'after_commit', 'before_checkpoint', 'after_checkpoint'])
def test_recovery_at_every_commit_boundary(memory, tmp_path, clock, stage):
    c, d = ready(memory, tmp_path)
    old_checkpoint = (tmp_path/'logs/checkpoint_1.json').read_bytes()
    def fail(s):
        if s == stage: raise OSError('synthetic crash')
    with pytest.raises(OSError): module.finalize_run(memory, tmp_path, 1, d, fault=fail)
    state = module.read_finalization(memory, tmp_path, 1)
    assert state['sealed'] == (stage not in ('intent_written', 'before_commit'))
    old_requests = list(c.adapter.requested)
    c.run(run_id=1, stage='details')
    assert c.adapter.requested == old_requests
    if not state['sealed']:
        assert (tmp_path/'logs/checkpoint_1.json').read_bytes() == old_checkpoint
    assert module.finalize_run(memory, tmp_path, 1, d)['checkpoint_synchronized']
    assert assert_integrity(memory)['integrity_check'] == ['ok']


def test_checkpoint_conflict_never_overwritten(memory, tmp_path, clock):
    c, d = ready(memory, tmp_path)
    def fail(stage):
        if stage == 'after_commit': raise OSError('synthetic crash')
    with pytest.raises(OSError): module.finalize_run(memory, tmp_path, 1, d, fault=fail)
    cp = tmp_path/'logs/checkpoint_1.json'; cp.write_text('{}')
    with pytest.raises(ValueError, match='Checkpoint'): module.finalize_run(memory, tmp_path, 1, d)
    assert cp.read_text(encoding='utf-8') == '{}'


def test_source_deleted_and_restricted_stay_distinct(memory, tmp_path, clock):
    from test_source_deleted_review import blocked_target, confirm
    c, evidence = blocked_target(memory, tmp_path)
    confirm(c, evidence)
    c.adapter.failures = [failure(None, 'TimeoutError')]
    c.run(run_id=1, stage='details')
    b, evidence = evidence_for(c, tmp_path)
    review(c, b, evidence)
    d = decision_for(c, b, tmp_path)
    result = module.finalize_run(memory, tmp_path, 1, d)
    assert result['counts']['source_deleted'] == result['counts']['banned_or_restricted'] == result['counts']['succeeded'] == 1
    assert c.summary(1)['pending_review_count'] == 0
    assert c.summary(1)['confirmed_unavailable_count'] == 1


def test_cli_is_offline_and_closed_resume_never_initializes_or_reads_auth(memory, tmp_path, clock, monkeypatch):
    c, d = ready(memory, tmp_path)
    cli = cli_module(tmp_path, monkeypatch)
    db = tmp_path/'placeholder.db'; db.write_bytes(b'')
    monkeypatch.setattr(cli, 'FORMAL_DB', db)
    monkeypatch.setattr(cli, 'FORMAL_DATA', tmp_path)
    class Borrowed:
        def __getattr__(self, key): return getattr(memory, key)
        def close(self): pass
    monkeypatch.setattr(cli, 'connect', lambda *a, **kw: Borrowed())
    def forbidden(*a, **kw): pytest.fail('No auth, Adapter, initialize, DPAPI or media allowed')
    for key in ('initialize', 'load_auth', 'XiaohongshuMCP', 'AccessCache', 'MediaStore'):
        monkeypatch.setattr(cli, key, forbidden)
    path = tmp_path/'decision.json'; path.write_text(json.dumps(d))
    args = ['--formal', '--finalization-decision', str(path)]
    assert cli.main(args + ['--check-finalization', '1']) == 0
    assert not module.intent_path(tmp_path, 1).exists()
    assert cli.main(args + ['--finalize-run', '1']) == 0
    assert cli.main(args + ['--finalize-run', '1']) == 0
    assert cli.main(['--formal', '--resume', '1', '--stage', 'details']) == 0
    with pytest.raises(SystemExit): cli.main(args + ['--finalize-run', '1', '--auth-env-file', 'anything'])


def test_active_sqlite_writer_is_rejected(tmp_path, clock):
    conn = initialize(tmp_path/'synthetic.db')
    try:
        c, d = ready(conn, tmp_path)
        other = sqlite3.connect(tmp_path/'synthetic.db')
        other.execute('begin immediate')
        conn.execute('pragma busy_timeout=0')
        try:
            with pytest.raises(sqlite3.OperationalError): module.finalize_run(conn, tmp_path, 1, d)
            assert not module.intent_path(tmp_path, 1).exists()
        finally:
            other.rollback(); other.close()
    finally:
        conn.close()


def test_active_collector_lock_blocks_cli(memory, tmp_path, clock, monkeypatch):
    c, d = ready(memory, tmp_path)
    cli = cli_module(tmp_path, monkeypatch)
    db = tmp_path/'placeholder.db'; db.write_bytes(b'')
    monkeypatch.setattr(cli, 'FORMAL_DB', db)
    monkeypatch.setattr(cli, 'FORMAL_DATA', tmp_path)
    monkeypatch.setattr(cli, 'connect', lambda *a, **kw: pytest.fail('Must fail before opening DB'))
    path = tmp_path/'decision.json'; path.write_text(json.dumps(d))
    with cli.single_writer(tmp_path):
        assert cli.main(['--formal', '--finalize-run', '1', '--finalization-decision', str(path)]) == 2
    assert not module.intent_path(tmp_path, 1).exists()
