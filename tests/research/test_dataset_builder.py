"""Synthetic offline Dataset publication, isolation and failure recovery."""
import hashlib
import json
import socket
import sqlite3
import subprocess
import time

import pytest

from test_smoke import FakeAdapter, detail, response
from test_run_metrics import Pipeline, feed, search_response
from test_detail_auto_retry import clock
from marketlens.adapters.xiaohongshu_mcp import utc_now
from marketlens.research.collection_pipeline import insert
from marketlens.research.database import initialize, connect
from marketlens.research import dataset_builder as builder


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Dataset Builder must be offline; no platform, Docker, subprocess or DPAPI")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("marketlens.research.access_cache.dpapi", forbidden)


def collect(conn, root, *, images=False, zero_first=False):
    p = Pipeline(conn, FakeAdapter(), root)
    run, request, task = p.start("synthetic_demo")
    search_response(p, run, request, task, [feed('synthetic_0'), feed('synthetic_0'), feed('synthetic_1'), feed('synthetic_video', 'video')])
    for seq, sid in enumerate(('synthetic_1', 'synthetic_0') if zero_first else ('synthetic_0', 'synthetic_1'), 2):
        post = conn.execute('SELECT post_id FROM posts WHERE source_post_id=?', (sid,)).fetchone()[0]
        task = insert(conn, 'collection_tasks', run_id=run, task_type='feed_detail', post_id=post, sequence_no=seq, status='running')
        for number in range(1, 3 if sid == 'synthetic_0' else 2):
            time.sleep(.002)  # Distinct observed times for cutoff selection tests.
            attempt = p.begin_attempt(task, number, '/api/v1/feeds/detail')
            payload = detail(sid)
            note = payload['data']['data']['note']
            note.update(title=' ' if number == 2 else 'synthetic_demo', desc='synthetic_private_body')
            if not images:
                note['imageList'] = []
            if sid == 'synthetic_1':
                payload['data']['data']['comments']['list'] = []
            elif number == 2:
                top = payload['data']['data']['comments']['list'][:1]
                top[0]['subComments'] = []  # Reply retains earlier parent context.
                top[0]['content'] = ' '
                payload['data']['data']['comments']['list'] = top
            event = p.record_response(attempt, response(payload, '/api/v1/feeds/detail'), post_id=post, source_id=sid)
            assert not event['ingestion_error']
        p.finish_task(task, 'succeeded')
    cutoff = utc_now()
    conn.execute("UPDATE collection_runs SET status='completed',finished_at=? WHERE run_id=?", (cutoff, run))
    project = conn.execute('SELECT project_id FROM collection_plans JOIN collection_runs USING(plan_id) WHERE run_id=?', (run,)).fetchone()[0]
    return project, run, cutoff


@pytest.fixture
def case(tmp_path):
    source, target, root, output = tmp_path/'authority.db', tmp_path/'copy.db', tmp_path/'source_data', tmp_path/'manifests'
    conn = initialize(source)
    project, run, cutoff = collect(conn, root)
    dest = sqlite3.connect(target)
    conn.backup(dest)
    dest.close()
    conn.close()
    args = dict(source_database=source, data_dir=root, manifest_dir=output, simulation=True,
                project_id=project, run_id=run, dataset_name='synthetic_baseline', version='v1', observation_cutoff_at=cutoff)
    return target, args


def preview(args):
    return builder.preview_dataset(**{k:v for k,v in args.items() if k not in ('manifest_dir', 'simulation')})


def verify(target, result, args):
    return builder.verify_dataset(target, result['dataset_id'], **{k:args[k] for k in ('source_database','data_dir','manifest_dir','simulation')})


def rows(path):
    c = connect(path, readonly=True)
    try:
        return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('dataset_versions','dataset_posts','dataset_comments')}
    finally:
        c.close()


def test_selection_freeze_manifest_privacy_and_parent_identity(case):
    target, args = case
    source_before = args['source_database'].read_bytes()
    plan = preview(args)
    assert plan['counts'] == dict(included=2, type_mismatch=1, detail_unavailable=0,
                                 posts=3, snapshots=2, comments=4, top_comments=2, replies=2, anonymous_comments=2, media=0)
    assert plan['quality']['cross_observation_parent_links'] == 1
    assert plan['quality']['empty_titles'] == plan['quality']['empty_comment_content'] == 1
    result = builder.build_and_freeze_dataset(target, **args)
    assert result['verified'] and result['status'] == 'frozen'
    assert verify(target, result, args)['counts'] == plan['counts']
    manifest = json.loads(open(result['manifest'], encoding='utf-8').read())
    raw = builder._encode(manifest)
    for private in ('synthetic_private_body', 'synthetic_access_secret', 'synthetic_access_', 'synthetic_demo anonymous'):
        assert private.encode() not in raw
    assert b'manifest_sha256' not in raw and b'private_access' not in raw
    assert source_before == args['source_database'].read_bytes()
    c = connect(target, readonly=True)
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    c.close()


@pytest.mark.parametrize('stage', ['before_publish', 'after_publish', 'before_commit', 'after_commit'])
def test_failures_and_immutable_intent_recovery(case, stage):
    target, args = case
    def fault(at):
        if at == stage:
            raise RuntimeError('synthetic failure')
    with pytest.raises(RuntimeError):
        builder.build_and_freeze_dataset(target, **args, fault=fault)
    before = rows(target)
    assert len(before['dataset_versions']) == (1 if stage == 'after_commit' else 0)
    result = builder.build_and_freeze_dataset(target, **args)
    assert result['verified']
    assert result['idempotent'] == (stage == 'after_commit')
    assert result['recovered_published_intent'] == (stage in ('after_publish','before_commit'))
    saved = rows(target)
    again = builder.build_and_freeze_dataset(target, **args)
    assert again['idempotent'] and saved == rows(target)


def test_publication_io_failure_rolls_back(case, monkeypatch):
    target, args = case
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(builder, '_publish', fail)
    with pytest.raises(OSError):
        builder.build_and_freeze_dataset(target, **args)
    assert not any(rows(target).values())


@pytest.mark.parametrize('damage', ['missing','corrupt'])
def test_manifest_damage_detected_without_replacement(case, damage):
    from pathlib import Path
    target, args = case
    result = builder.build_and_freeze_dataset(target, **args)
    path = Path(result['manifest'])
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'{}\n')
    before = rows(target)
    with pytest.raises((ValueError,FileNotFoundError)):
        verify(target, result, args)
    with pytest.raises((ValueError,FileNotFoundError)):
        builder.build_and_freeze_dataset(target, **args)
    assert before == rows(target)


def test_target_source_mismatch_and_mode_guards(case):
    target, args = case
    with pytest.raises(ValueError, match='distinct'):
        builder.build_and_freeze_dataset(args['source_database'], **args)
    with pytest.raises(ValueError, match='formal requires same'):
        builder.build_and_freeze_dataset(target, **{**args,'simulation':False})
    c = connect(target)
    # Additional unrelated Run is permitted, but changing source-Run evidence is not.
    plan = c.execute('SELECT plan_id FROM collection_runs WHERE run_id=1').fetchone()[0]
    extra = insert(c,'collection_runs',plan_id=plan)
    assert extra != 1
    c.close()
    assert builder.build_and_freeze_dataset(target, **args)['verified']


def test_freeze_rejects_different_source_rows(case):
    target, args = case
    # A valid extra Raw is forbidden by source tables; use a separately collected
    # database to demonstrate mismatch without dropping/bypassing triggers.
    other = target.with_name('other.db')
    c = initialize(other)
    collect(c, target.parent/'other_data')
    c.close()
    with pytest.raises(ValueError, match='Target Run evidence differs'):
        builder.build_and_freeze_dataset(other, **args)
    assert not any(rows(other).values())


def test_evidence_changed_after_preview_rejected(case):
    target, args = case
    plan = preview(args)
    from pathlib import Path
    artifact = next(f for f in plan['files'] if f['role']=='raw')
    Path(artifact['path']).write_bytes(b'synthetic damaged Raw')
    with pytest.raises(ValueError, match='Raw evidence integrity'):
        builder.build_and_freeze_dataset(target, **args)
    assert not any(rows(target).values())


def test_frozen_members_are_immutable_and_new_run_cannot_change_v1(case):
    target, args = case
    result = builder.build_and_freeze_dataset(target, **args)
    saved = rows(target)
    c = connect(target)
    for table in ('dataset_versions','dataset_posts','dataset_comments'):
        for sql in ('DELETE FROM '+table, 'UPDATE '+table+' SET dataset_id=dataset_id',
                    'INSERT OR REPLACE INTO '+table+' SELECT * FROM '+table+' LIMIT 1'):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(sql)
    collect(c, target.parent/'new_run_data')
    c.close()
    assert saved == rows(target)
    assert verify(target, result, args)['verified']


def test_formal_mode_on_synthetic_authority_only(case):
    _, args = case
    result = builder.build_and_freeze_dataset(args['source_database'], **{**args, 'simulation':False})
    assert result['verified'] and not result['simulation']


def test_cutoff_selection_and_conflicting_version(case):
    target, args = case
    c = connect(args['source_database'],readonly=True)
    first = c.execute('SELECT observed_at FROM post_snapshots ORDER BY snapshot_id LIMIT 1').fetchone()[0]
    c.close()
    # At this earlier cutoff the second normal Post has no disposition yet.
    with pytest.raises(ValueError,match='Unsupported/unresolved'):
        preview({**args,'observation_cutoff_at':first})
    builder.build_and_freeze_dataset(target, **args)
    with pytest.raises(ValueError):
        builder.build_and_freeze_dataset(target, **{**args,'observation_cutoff_at':first})


def test_deleted_and_restricted_actual_review_binding(tmp_path, monkeypatch, clock):
    from test_source_deleted_review import blocked_target, confirm
    from test_pending_review import evidence_for, review
    from test_run_finalization import decision_for
    from test_detail_auto_retry import failure
    from test_baseline_collection import SyntheticAdapter
    from marketlens.research.run_finalization import finalize_run
    original = SyntheticAdapter.detail
    def no_media(self, *a, **kw):
        result = original(self, *a, **kw)
        result.payload['data']['data']['note']['imageList'] = []
        return result
    monkeypatch.setattr(SyntheticAdapter,'detail',no_media)
    source, target, root = tmp_path/'authority.db',tmp_path/'copy.db',tmp_path/'source_data'
    c = initialize(source)
    collector, evidence = blocked_target(c,root)
    confirm(collector,evidence)
    collector.adapter.failures = [failure(None,'TimeoutError')]
    collector.run(run_id=1,stage='details')
    binding,evidence = evidence_for(collector,root)
    review(collector,binding,evidence)
    finalize_run(c,root,1,decision_for(collector,binding,root))
    cutoff = c.execute('SELECT finished_at FROM collection_runs').fetchone()[0]
    copy_conn = sqlite3.connect(target);c.backup(copy_conn);copy_conn.close();c.close()
    args = dict(source_database=source,data_dir=root,manifest_dir=tmp_path/'out',simulation=True,
                project_id=1,run_id=1,dataset_name='synthetic',version='v1',observation_cutoff_at=cutoff)
    plan = preview(args)
    assert {p['disposition']['disposition'] for p in plan['posts'] if p['disposition']} == {'source_deleted','banned_or_restricted'}
    result = builder.build_and_freeze_dataset(target,**args)
    assert result['counts']['detail_unavailable'] == 2
    # The original decision is not rebased to the copy; direct validation there
    # must still reject its machine/path binding.
    from marketlens.research.run_finalization import read_finalization
    conn = connect(target,readonly=True)
    with pytest.raises(ValueError,match='binding mismatch'):
        read_finalization(conn,root,1)
    conn.close()
    from pathlib import Path
    evidence_path = next(p['disposition']['evidence_path'] for p in plan['posts']
                         if p['disposition'] and p['disposition']['disposition']=='banned_or_restricted')
    Path(evidence_path).write_bytes(b'synthetic changed human evidence')
    with pytest.raises(ValueError):
        verify(target,result,args)


def test_readonly_preview_and_verification(case, monkeypatch):
    target, args = case
    result = builder.build_and_freeze_dataset(target,**args)
    before = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.parent.rglob('*') if p.is_file()}
    original = builder.connect
    def readonly(path, *, readonly=False):
        assert readonly
        c = original(path,readonly=True)
        def authorizer(op,*args):
            assert op not in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                              sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_ALTER_TABLE)
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c
    monkeypatch.setattr(builder,'connect',readonly)
    assert preview(args)['counts']==result['counts']
    assert verify(target,result,args)['verified']
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.parent.rglob('*') if p.is_file()}


def test_verification_checks_actual_members_not_only_valid_manifest_hash(case):
    from pathlib import Path
    target,args=case
    result=builder.build_and_freeze_dataset(target,**args)
    manifest=json.loads(Path(result['manifest']).read_bytes())
    other=target.with_name('wrong_members.db')
    source=connect(args['source_database'],readonly=True)
    c=sqlite3.connect(other);source.backup(c);c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON');source.close()
    manifest['execution']['target_database']=str(other.resolve())
    raw=builder._encode(manifest);d=manifest['dataset']
    c.execute('INSERT INTO dataset_versions(dataset_id,project_id,dataset_name,version,source_run_id,selection_rules_json,created_at) VALUES(?,?,?,?,?,?,?)',
              (d['dataset_id'],d['project_id'],d['dataset_name'],d['version'],d['source_run_id'],json.dumps(manifest['plan']['selection_rules']),d['created_at']))
    for p in manifest['plan']['posts']:
        status='detail_unavailable' if p['inclusion_status']=='type_mismatch' else p['inclusion_status']
        c.execute('INSERT INTO dataset_posts(dataset_id,post_id,snapshot_id,inclusion_status) VALUES(?,?,?,?)',
                  (d['dataset_id'],p['post_id'],p['snapshot_id'],status))
    for o in manifest['plan']['comments']:
        c.execute('INSERT INTO dataset_comments(dataset_id,post_id,comment_id,observation_id) VALUES(?,?,?,?)',
                  (d['dataset_id'],o['post_id'],o['comment_id'],o['observation_id']))
    c.execute("UPDATE dataset_versions SET status='frozen',frozen_at=?,manifest_sha256=?",(d['frozen_at'],hashlib.sha256(raw).hexdigest()))
    c.commit();c.close()
    out=args['manifest_dir'].with_name('wrong_members_manifest');out.mkdir()
    (out/Path(result['manifest']).name).write_bytes(raw)
    with pytest.raises(ValueError,match='Actual database members differ'):
        builder.verify_dataset(other,1,source_database=args['source_database'],data_dir=args['data_dir'],manifest_dir=out,simulation=True)


def test_media_verification_and_corruption(tmp_path):
    import io
    from PIL import Image
    from marketlens.research.media_store import MediaStore
    source=tmp_path/'authority.db';root=tmp_path/'source_data'
    c=initialize(source);project,run,cutoff=collect(c,root,images=True)
    buf=io.BytesIO();Image.new('RGB',(8,6),'blue').save(buf,format='PNG')
    store=MediaStore(c,root,interval_seconds=0,fetcher=lambda url,budget:(buf.getvalue(),[]))
    store.process(run)
    target=tmp_path/'copy.db';dest=sqlite3.connect(target);c.backup(dest);dest.close();c.close()
    args=dict(source_database=source,data_dir=root,manifest_dir=tmp_path/'out',simulation=True,
              project_id=project,run_id=run,dataset_name='synthetic',version='v1',observation_cutoff_at=cutoff)
    result=builder.build_and_freeze_dataset(target,**args)
    assert result['counts']['media']==2
    next((root/'media/images').rglob('*.png')).write_bytes(b'synthetic damaged media')
    with pytest.raises(ValueError,match='Selected media'):
        verify(target,result,args)


def test_earlier_cutoff_selects_earlier_snapshot_and_comment(tmp_path):
    source=tmp_path/'authority.db';root=tmp_path/'source_data';c=initialize(source)
    project,run,_=collect(c,root,zero_first=True)
    cutoff=c.execute('SELECT observed_at FROM post_snapshots ORDER BY snapshot_id LIMIT 1 OFFSET 1').fetchone()[0]
    target=tmp_path/'copy.db';dest=sqlite3.connect(target);c.backup(dest);dest.close();c.close()
    args=dict(source_database=source,data_dir=root,manifest_dir=tmp_path/'out',simulation=True,
              project_id=project,run_id=run,dataset_name='synthetic',version='v1',observation_cutoff_at=cutoff)
    plan=preview(args)
    assert {p['snapshot_id'] for p in plan['posts'] if p['snapshot_id']}=={1,2}
    assert plan['quality']['cross_observation_parent_links']==0
    assert builder.build_and_freeze_dataset(target,**args)['verified']


def test_conflicting_orphan_manifest_is_not_adopted_or_overwritten(case):
    target,args=case
    def fault(at):
        if at=='before_commit':
            raise RuntimeError('synthetic crash')
    with pytest.raises(RuntimeError):
        builder.build_and_freeze_dataset(target,**args,fault=fault)
    path=next(args['manifest_dir'].glob('*.json'))
    manifest=json.loads(path.read_bytes());manifest['plan']['counts']['comments']=999
    raw=builder._encode(manifest);path.write_bytes(raw)
    with pytest.raises(ValueError,match='conflicts'):
        builder.build_and_freeze_dataset(target,**args)
    assert not any(rows(target).values()) and path.read_bytes()==raw


def test_successful_empty_search_produces_empty_frozen_dataset(tmp_path):
    source=tmp_path/'authority.db';root=tmp_path/'source_data';c=initialize(source)
    p=Pipeline(c,FakeAdapter(),root);run,request,task=p.start('synthetic_demo')
    search_response(p,run,request,task,[])
    cutoff=utc_now();c.execute("UPDATE collection_runs SET status='completed',finished_at=?",(cutoff,))
    target=tmp_path/'copy.db';dest=sqlite3.connect(target);c.backup(dest);dest.close();c.close()
    result=builder.build_and_freeze_dataset(target,source_database=source,data_dir=root,manifest_dir=tmp_path/'out',simulation=True,
                                          project_id=1,run_id=run,dataset_name='empty',version='v1',observation_cutoff_at=cutoff)
    assert result['verified'] and result['counts']['posts']==result['counts']['comments']==0
