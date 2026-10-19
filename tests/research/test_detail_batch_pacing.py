"""Optional batch rests, synthetic input, virtual time and memory-only SQLite."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_final_preflight import memory
from test_detail_pacing import timer, collector, start, events
from test_detail_auto_retry import Adapter, failure
from marketlens.research.baseline_config import DetailPacingPolicy, DetailRetryPolicy


def policy(**values):
    return DetailPacingPolicy(**{'batch_enabled': True, 'batch_min_tasks': 1,
        'batch_max_tasks': 1, 'batch_min_seconds': 60, 'batch_max_seconds': 60, **values})


def batch_events(directory):
    return [e for e in events(directory) if e['event'].startswith('batch_')]


def pause_in_rest(c, run, directory, timer):
    flag = directory / 'pause_1.request'
    def pause():
        if any(e['batch_state']['rest'] for e in batch_events(directory)):
            flag.write_text('pause')
    timer.on_sleep = pause
    assert c.run(run_id=run, stage='details')['result'] == 'PAUSED'
    timer.on_sleep = lambda: None
    flag.unlink()


def test_batches_add_to_existing_waits(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path, pacing=policy())
    run = start(c)
    before = timer.now
    assert c.run(run_id=run, stage='details')['result'] == 'SUCCESS'
    assert timer.now-before == 151  # 2*(3+5+7.5+60)
    rows = batch_events(tmp_path)
    assert [e['event'] for e in rows] == ['batch_started', 'batch_rest_scheduled',
        'batch_rest_completed', 'batch_rest_scheduled', 'batch_rest_completed']
    assert rows[-1]['batch_state']['seen_tasks'] == [2, 3]
    assert len(c.adapter.requested) == 2
    assert len([e for e in events(tmp_path) if e.get('phase') == 'batch' and e['event'] == 'wait_finished']) == 2


@pytest.mark.parametrize('target', [15, 30])
def test_default_random_boundary(memory, tmp_path, timer, monkeypatch, target):
    from test_smoke import response
    class Many(Adapter):
        def search(self, *args):
            return response({'success': True, 'data': {'feeds': [
                {'id': f'synthetic_demo_{i}', 'xsecToken': 'synthetic_access', 'noteCard': {'type': 'normal'}}
                for i in range(target)]}})
    draws = []
    def draw(low, high):
        draws.append((low, high))
        return target
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', draw)
    c = collector(memory, tmp_path, Many(), pacing=DetailPacingPolicy(batch_enabled=True))
    run = start(c)
    assert c.run(run_id=run, stage='details')['result'] == 'SUCCESS'
    rows = batch_events(tmp_path)
    scheduled = [e for e in rows if e['event'] == 'batch_rest_scheduled']
    assert len(scheduled) == 1 and scheduled[0]['batch_state']['completed'] == target
    assert scheduled[0]['batch_state']['rest']['random_seconds'] == 180
    assert draws == [(15, 30), (15, 30)]


def test_pause_reuses_target_deadline_and_never_recounts(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path, pacing=policy())
    run = start(c)
    pause_in_rest(c, run, tmp_path, timer)
    saved = batch_events(tmp_path)[-1]['batch_state']
    before = timer.now
    restored = collector(memory, tmp_path, c.adapter, pacing=policy(batch_min_tasks=5, batch_max_tasks=5))
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage='details')['result'] == 'SUCCESS'
    rows = batch_events(tmp_path)
    assert sum(e['event']=='batch_rest_scheduled' for e in rows) == 2
    assert rows[1]['batch_state'] == saved
    assert timer.now-before == 134.5  # 59 remaining + next task 15.5 + 60
    assert len(c.adapter.requested) == 2
    assert rows[-1]['batch_state']['target'] == 1  # persisted policy, not new defaults


def test_historical_success_excluded_and_crash_success_reconciled(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path)
    run = start(c)
    task = memory.execute("SELECT * FROM collection_tasks WHERE task_type='feed_detail' ORDER BY task_id").fetchone()
    sid = memory.execute('SELECT source_post_id FROM posts WHERE post_id=?', (task['post_id'],)).fetchone()[0]
    assert c._call(task, source_id=sid) is None  # historical success before activation
    c.detail_pacing_policy = policy()
    assert c._batch_boundary(task) is None
    other = memory.execute("SELECT t.*,p.source_post_id FROM collection_tasks t JOIN posts p USING(post_id) WHERE t.task_type='feed_detail' AND t.status='pending'").fetchone()
    assert c._call(other, source_id=other['source_post_id']) is None  # crash before batch accounting
    restored = collector(memory, tmp_path, c.adapter, pacing=policy())
    restored.access.update(c.access)
    assert restored.run(run_id=run, stage='details')['result'] == 'SUCCESS'
    assert batch_events(tmp_path)[-1]['batch_state']['seen_tasks'] == [other['task_id']]
    assert len(c.adapter.requested) == 2


@pytest.mark.parametrize('bad', [failure(None, 'TimeoutError'), failure(403), failure(429)])
def test_failure_blocks_without_batch_count_or_rest(memory, tmp_path, timer, bad):
    c = collector(memory, tmp_path, Adapter([bad]), pacing=policy(),
        retry=DetailRetryPolicy(min_seconds=30, max_seconds=60))
    run = start(c)
    assert c.run(run_id=run, stage='details')['result'] == 'BLOCKED'
    assert len(c.adapter.requested) == 1
    assert batch_events(tmp_path)[-1]['batch_state']['completed'] == 0
    assert not any(e['batch_state']['rest'] for e in batch_events(tmp_path))


def test_retry_not_counted_as_extra_success(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path, Adapter([failure()]), pacing=policy(batch_min_tasks=2, batch_max_tasks=2),
        retry=DetailRetryPolicy(min_seconds=30, max_seconds=60))
    run = start(c)
    assert c.run(run_id=run, stage='details')['result'] == 'SUCCESS'
    assert len(c.adapter.requested) == 3
    assert len(batch_events(tmp_path)[-1]['batch_state']['seen_tasks']) == 2
    assert sum(e['event']=='batch_rest_scheduled' for e in batch_events(tmp_path)) == 1


def test_partial_batch_restart_no_redraw(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: 2)
    c = collector(memory, tmp_path, pacing=policy(batch_min_tasks=2, batch_max_tasks=2))
    run = start(c)
    task = memory.execute("SELECT * FROM collection_tasks WHERE task_type='feed_detail' ORDER BY task_id").fetchone()
    assert c._batch_boundary(task) is None
    sid = memory.execute('SELECT source_post_id FROM posts WHERE post_id=?',(task['post_id'],)).fetchone()[0]
    assert c._call(task, source_id=sid) is None
    assert c._batch_boundary(task) is None
    restored = collector(memory, tmp_path, c.adapter, pacing=policy())
    assert restored._batch_boundary(task) is None
    assert batch_events(tmp_path)[-1]['batch_state']['completed'] == 1
    assert batch_events(tmp_path)[-1]['batch_state']['target'] == 2


def test_truncated_journal_blocks(memory, tmp_path, timer):
    c = collector(memory, tmp_path, pacing=policy())
    run = start(c)
    (tmp_path/'logs/detail_pacing_1.jsonl').write_bytes(b'{')
    assert c.run(run_id=run, stage='details')['result'] == 'BLOCKED'
    assert not c.adapter.requested


@pytest.mark.parametrize('settings', [{'batch_min_tasks': 0}, {'batch_max_tasks': 1},
    {'batch_min_tasks': True}, {'batch_min_seconds': -1}, {'batch_max_seconds': float('inf')}])
def test_invalid_batch_bounds(settings):
    with pytest.raises(ValueError):
        DetailPacingPolicy(**settings)


def test_real_process_rest_resume_uses_memory_database(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path, pacing=policy())
    run = start(c)
    pause_in_rest(c, run, tmp_path, timer)
    # Reconstruct only synthetic research rows in a new process's :memory: DB.
    sql = '\n'.join(memory.iterdump())
    root = Path(__file__).resolve().parents[2]
    code = f'''
import sys,sqlite3
sys.dont_write_bytecode=True
sys.path.insert(0,{str(root/'tests/research')!r})
from test_detail_pacing import collector
from test_detail_batch_pacing import policy,batch_events
from pathlib import Path
import marketlens.research.baseline_collector as module
c=sqlite3.connect(':memory:',isolation_level=None)
c.row_factory=sqlite3.Row
c.executescript({sql!r})
module.time.time=lambda: 9999.0
module.time.monotonic=lambda: 9999.0
module.random.randint=lambda low,high: low
module.time.sleep=lambda seconds: (_ for _ in ()).throw(AssertionError('Expired rest must not sleep'))
collector=collector(c,Path({str(tmp_path)!r}),pacing=policy())
task=c.execute("SELECT * FROM collection_tasks WHERE task_type='feed_detail' ORDER BY task_id LIMIT 1").fetchone()
assert collector._batch_boundary(task) is None
assert collector._batch_boundary(task) is None
assert len([e for e in batch_events(Path({str(tmp_path)!r})) if e['event']=='batch_rest_completed'])==1
assert batch_events(Path({str(tmp_path)!r}))[-1]['batch_state']['batch_no']==2
print('isolated subprocess batch resume OK')
'''
    result = subprocess.run([sys.executable, '-B', '-'], input=code, cwd=root, env=os.environ.copy(),
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'batch resume OK' in result.stdout


def test_unknown_request_blocks_even_when_rest_is_pending(memory, tmp_path, timer, monkeypatch):
    monkeypatch.setattr('marketlens.research.baseline_collector.random.randint', lambda low, high: low)
    c = collector(memory, tmp_path, pacing=policy())
    run = start(c)
    pause_in_rest(c, run, tmp_path, timer)
    task = memory.execute("SELECT * FROM collection_tasks WHERE task_type='feed_detail' AND status='pending'").fetchone()
    c.begin_attempt(task['task_id'], 1, '/api/v1/feeds/detail')
    before = timer.now
    assert c._batch_boundary(task) == 'UncertainInFlightAttemptNeedsReview'
    assert timer.now == before
    assert len(c.adapter.requested) == 1


@pytest.mark.parametrize('args', [['--detail-batch-rest'], ['--detail-batch-min-tasks', '15'],
    ['--formal', '--resume', '1', '--stage', 'details', '--detail-batch-rest', '--no-detail-pacing']])
def test_cli_batch_scope_and_opt_in(args):
    import importlib.util
    from marketlens.research.database import ROOT
    spec = importlib.util.spec_from_file_location('batch_cli', ROOT/'scripts/phase1d_baseline_collect.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
