"""Aggregate-only offline final reporting over synthetic frozen datasets."""
import hashlib
import json

import pytest

from test_dataset_builder import case, offline
from marketlens.research import dataset_builder, run_report
from marketlens.research.database import connect
from marketlens.research.collection_pipeline import insert
from marketlens.adapters.xiaohongshu_mcp import utc_now


@pytest.fixture
def report_case(case):
    _,args = case
    db=args['source_database']
    dataset_builder.build_and_freeze_dataset(db,**{**args,'simulation':False})
    return db,dict(project_id=args['project_id'],run_id=args['run_id'],dataset_id=1,
                   data_dir=args['data_dir'],manifest_dir=args['manifest_dir'])


def hashes(root):
    return {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_end_to_end_scope_units_privacy_and_readonly(report_case,tmp_path,monkeypatch):
    db,args=report_case
    before=hashes(db.parent)
    original=run_report.connect
    def read_only(path,*,readonly=False):
        assert readonly
        c=original(path,readonly=True)
        def authorizer(action,*args):
            import sqlite3
            assert action not in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                                  sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_ALTER_TABLE)
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c
    monkeypatch.setattr(run_report,'connect',read_only)
    report=run_report.collect_report(db,**args)
    assert hashes(db.parent)==before
    run=report['run'];dataset=report['dataset']['metrics']
    assert run['search']['metrics']['search_hits']['value']==4
    assert run['comments']['metrics']['comment_observations']['value']==5
    assert dataset['comments']['observations']==4
    assert run['comments']['metrics']['top_comments_per_post']['value']['mean']==.5
    assert dataset['top_comments_per_post']['mean']==1
    assert dataset['completeness']['snapshots']['title']['total']==2
    assert dataset['completeness']['snapshots']['title']['missing']==1
    assert dataset['reply_coverage']['parents']==2
    assert dataset['comments']['extra_stable_observations']==0
    encoded=json.dumps(report,ensure_ascii=False,allow_nan=False)
    md=run_report.render_markdown(report)
    for text in (encoded,md):
        for secret in ('synthetic_private_body','synthetic_access_secret','synthetic_demo anonymous',str(tmp_path)):
            assert secret not in text
    assert report['reliability']['warnings'] and 'log unavailable' in md
    # Renderer consumes the result object; no independently maintained counts.
    report['run']['search']['metrics']['search_hits']['value']=987
    assert '| 搜索命中 | 987 |' in run_report.render_markdown(report)


def test_saved_json_and_markdown_share_result(report_case,tmp_path):
    db,args=report_case
    report,paths=run_report.generate_report(db,**args,output_dir=tmp_path/'reports')
    from pathlib import Path
    assert json.loads(Path(paths['json']).read_bytes())==report
    assert Path(paths['markdown']).read_text(encoding='utf-8')==run_report.render_markdown(report)
    assert Path(paths['markdown']).read_text(encoding='utf-8')==run_report.render_markdown(json.loads(Path(paths['json']).read_bytes()))


@pytest.mark.parametrize('field,value',[('project_id',999),('run_id',999),('dataset_id',999)])
def test_cross_scope_rejected(report_case,field,value):
    db,args=report_case
    with pytest.raises(ValueError,match='scope mismatch'):
        run_report.collect_report(db,**{**args,field:value})


def test_protected_output_and_bad_manifest_refused(report_case,tmp_path):
    db,args=report_case
    before=hashes(db.parent)
    for output in (args['data_dir'],args['manifest_dir'],db.parent):
        with pytest.raises(ValueError,match='overlaps'):
            run_report.generate_report(db,**args,output_dir=output)
    assert before==hashes(db.parent)
    manifest=next(args['manifest_dir'].glob('*.json'))
    manifest.write_bytes(b'{}\n')
    output=tmp_path/'refused_reports'
    with pytest.raises(ValueError,match='SHA-256'):
        run_report.generate_report(db,**args,output_dir=output)
    assert not output.exists()


def test_waits_planned_actual_and_missing_periods(report_case):
    db,args=report_case;logs=args['data_dir']/'logs'
    pacing=[dict(run_id=1,event='wait_finished',phase='before',at='2026-01-01T00:00:00Z',actual_wait_seconds=2.5),
            dict(run_id=1,event='wait_finished',phase='after',at='2026-01-01T00:00:01Z'),
            dict(run_id=1,event='batch_rest_scheduled',at='2026-01-01T00:00:02Z')]
    retry=[dict(run_id=1,event='waiting',delay_seconds=100,at='2026-01-01T00:00:00Z')]
    for name,rows in (('detail_pacing_1.jsonl',pacing),('detail_retry_1.jsonl',retry)):
        (logs/name).write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    before=hashes(args['data_dir'])
    c=connect(db,readonly=True)
    rel=run_report._reliability(c,1,args['data_dir']);c.close()
    assert rel['actual_wait_segments']['before']['actual_seconds']==2.5
    assert rel['actual_wait_segments']['after']['missing_or_invalid_duration']==1
    assert rel['logs']['detail_retry']['retry_delay']['planned_seconds']==100
    assert rel['logs']['detail_retry']['retry_delay']['actual_seconds'] is None
    assert 'Full-Run actual waiting/active time' in rel['unavailable']
    assert before==hashes(args['data_dir'])
    (logs/'detail_pacing_1.jsonl').write_text(json.dumps(pacing[0]),encoding='utf-8')
    c=connect(db,readonly=True)
    with pytest.raises(ValueError,match='Incomplete timing'):
        run_report._reliability(c,1,args['data_dir'])
    c.close()


def test_empty_run_quantiles_and_zero_denominators(report_case):
    db,args=report_case
    c=connect(db)
    plan=c.execute('SELECT plan_id FROM collection_runs WHERE run_id=1').fetchone()[0]
    run=insert(c,'collection_runs',plan_id=plan,status='completed',finished_at=utc_now())
    c.close();c=connect(db,readonly=True)
    rel=run_report._reliability(c,run,args['data_dir']);c.close()
    assert not rel['groups'] and rel['timing']['capture_span_seconds'] is None
    assert run_report._duration([])['median'] is None
    assert run_report._pct(None)=='未知/不适用'


def test_markdown_escapes_untrusted_cells():
    assert run_report._cell('keyword|one\ntwo')=='keyword\\|one two'


def test_invocation_logs_without_run_scope_are_not_attributed(report_case):
    db,args=report_case;logs=args['data_dir']/'logs'
    for name,row in (('scoped',dict(run_id=1,result='PAUSED')),
                     ('unscoped',dict(result='BLOCKED',error_message='synthetic_private_diagnostic')),
                     ('other',dict(run_id=999,result='BLOCKED'))):
        (logs/f'baseline_{name}.json').write_text(json.dumps(row),encoding='utf-8')
    c=connect(db,readonly=True)
    result=run_report._reliability(c,1,args['data_dir']);c.close()
    assert result['saved_invocation_results']=={'PAUSED':1}
    assert result['unscoped_invocation_reports_excluded']==result['other_run_invocation_reports_excluded']==1
    assert 'synthetic_private_diagnostic' not in json.dumps(result)
