"""Offline aggregate Run/Dataset report; no collection or storage mutations.

Reuse Run Analytics P0 and the independent Dataset verifier. Additional SQL
and log queries live here only to support the final report, not a new analytics
framework. Source text is processed in memory and never exported.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from .access_cache import atomic_write
from .database import ROOT, connect
from .dataset_builder import verify_dataset
from .normalizers import count
from .run_metrics import analyze_run, _distribution


def _value(section, key):
    return section['metrics'][key]['value']


def _path(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name


def _profile(conn, source, fields):
    """Observation-level completeness. Identifiers are internal constants."""
    result = {}
    for field, text, precision in fields:
        sql = f"""SELECT count(*) total,coalesce(sum({field} IS NULL),0) nulls,
            coalesce(sum({field} IS NOT NULL AND trim({field})=''),0) blanks,
            coalesce(sum({field}=0),0) zeros {source}"""
        r = conn.execute(sql).fetchone()
        missing = r['nulls'] + (r['blanks'] if text else 0)
        precisions = {str(p[0]) if p[0] is not None else 'unknown': p[1]
                      for p in conn.execute(f'SELECT {precision},count(*) {source} GROUP BY {precision}')} if precision else None
        result[field.split('.')[-1]] = dict(total=r['total'], valid=r['total']-missing, missing=missing,
            missing_rate=missing/r['total'] if r['total'] else None, null=r['nulls'],
            blank=r['blanks'] if text else 0, known_zero=r['zeros'] if not text else None, precision=precisions)
    return result


def _dataset_metrics(conn, dataset_id):
    # dataset_id is an internally validated integer, never an SQL identifier.
    if type(dataset_id) is not int:
        raise ValueError('Integer Dataset ID required')
    posts = f'FROM dataset_posts d JOIN post_snapshots s ON s.snapshot_id=d.snapshot_id WHERE d.dataset_id={dataset_id}'
    comments = f'FROM dataset_comments d JOIN comment_observations o USING(observation_id) WHERE d.dataset_id={dataset_id}'
    post_fields = [(f's.{k}', True, None) for k in ('title','description','published_at_raw','published_at')]
    for key, name in (('likes_count','likes'),('comments_count','comments'),('favorites_count','favorites'),('shares_count','shares')):
        post_fields.append((f's.{key}', False, f"json_extract(s.engagement_parse_json,'$.{name}.precision')"))
    comment_fields = [(f'o.{k}', True, None) for k in ('content','created_at_raw','created_at')]
    comment_fields.append(('o.like_count',False,'o.like_count_precision'))
    composition = dict(conn.execute('SELECT inclusion_status,count(*) FROM dataset_posts WHERE dataset_id=? GROUP BY inclusion_status', (dataset_id,)))
    for key in ('included','detail_unavailable','type_mismatch'):
        composition.setdefault(key,0)
    totals = dict(conn.execute(f"""SELECT count(*) observations,count(DISTINCT o.comment_id) stable_identities,
        coalesce(sum(o.comment_id IS NULL),0) anonymous_observations,
        coalesce(sum(o.comment_level=1),0) top_observations,coalesce(sum(o.comment_level=2),0) reply_observations {comments}""").fetchone())
    totals['extra_stable_observations'] = totals['observations']-totals['anonymous_observations']-totals['stable_identities']
    distribution, stable_distribution, reached, above, below, unknown = [], [], 0, 0, 0, 0
    has_more = Counter({'true':0,'false':0,'unknown':0})
    members = conn.execute("""SELECT d.post_id,a.response_summary_json,p.comment_config_json FROM dataset_posts d
        JOIN post_snapshots s USING(snapshot_id) JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN dataset_versions v USING(dataset_id) JOIN collection_runs r ON r.run_id=v.source_run_id
        JOIN collection_plans p USING(plan_id) WHERE d.dataset_id=? ORDER BY d.post_id""", (dataset_id,)).fetchall()
    per_post = {r['post_id']:dict(r) for r in conn.execute(f"""SELECT o.post_id,count(*) all_comments,
        sum(o.comment_level=1) top,count(DISTINCT CASE WHEN o.comment_level=1 THEN o.comment_id END) stable_top
        {comments} GROUP BY o.post_id""")}
    zero = 0
    for m in members:
        n = per_post.get(m['post_id'],{})
        distribution.append(n.get('top',0))
        stable_distribution.append(n.get('stable_top',0))
        zero += not n.get('all_comments',0)
        summary = json.loads(m['response_summary_json'] or '{}').get('comments',{})
        target = summary.get('target_unique',json.loads(m['comment_config_json']).get('top_comment_target'))
        if type(target) is int and target>0:
            reached += n.get('stable_top',0)>=target
            above += n.get('stable_top',0)>target
            below += n.get('stable_top',0)<target
        else:
            unknown += 1
        h = summary.get('has_more')
        has_more['true' if h is True else 'false' if h is False else 'unknown'] += 1
    reply_coverage = dict(parents=0, known_displayed_reply_count=0, parents_with_unreturned_replies=0,
                          displayed_replies_sum=0, replies_returned_in_parent_response=0, positive_gap_sum=0)
    precision = Counter()
    for row in conn.execute(f"""SELECT json_extract(o.comment_raw_json,'$.subCommentCount') displayed,
        CASE WHEN json_type(o.comment_raw_json,'$.subComments')='array'
        THEN json_array_length(o.comment_raw_json,'$.subComments') ELSE 0 END returned {comments} AND o.comment_level=1"""):
        reply_coverage['parents'] += 1
        parsed = count(row['displayed'])
        precision[parsed['precision']] += 1
        n = parsed['value']
        if n is not None:
            reply_coverage['known_displayed_reply_count'] += 1
            reply_coverage['displayed_replies_sum'] += n
            reply_coverage['replies_returned_in_parent_response'] += row['returned']
            reply_coverage['parents_with_unreturned_replies'] += n>row['returned']
            reply_coverage['positive_gap_sum'] += max(0,n-row['returned'])
    reply_coverage['precision'] = dict(precision)
    return {'definition':'Frozen Dataset membership only; never global latest or all Run observations.',
        'composition':composition,'candidate_posts':sum(composition.values()),'selected_snapshots':len(members),
        'comments':totals,'top_comments_per_post':_distribution(distribution),
        'stable_top_comments_per_post':_distribution(stable_distribution),
        'posts_without_observed_comments':zero,'posts_at_or_above_target':reached,'posts_above_target':above,
        'posts_below_target':below,'posts_with_unknown_target':unknown,
        'target_attainment_rate':reached/(len(members)-unknown) if len(members)>unknown else None,
        'source_snapshot_attempt_has_more':dict(has_more),
        'reply_coverage':reply_coverage,
        'reply_coverage_definition':'Selected parent Observation raw response: displayed subCommentCount vs returned subComments; not the platform population or necessarily all Dataset replies.',
        'completeness':{'snapshots':_profile(conn,posts,post_fields),'comments':_profile(conn,comments,comment_fields)}}


def _duration(values):
    d = _distribution(values)
    return {k:d[k] for k in ('n','mean','median','p90','p95','min','max','sum','quantile_method')}


def _reliability(conn, run_id, data_dir):
    rows = [dict(r) for r in conn.execute("""SELECT t.task_id,t.task_type,t.status task_status,a.attempt_no,
        a.status,a.http_status,a.error_type,a.duration_ms,a.started_at,a.finished_at FROM collection_tasks t
        LEFT JOIN collection_attempts a USING(task_id) WHERE t.run_id=? ORDER BY t.task_id,a.attempt_no""",(run_id,))]
    groups = {}
    for kind in sorted({r['task_type'] for r in rows}):
        selected = [r for r in rows if r['task_type']==kind]
        tasks = defaultdict(list)
        for r in selected:
            tasks[r['task_id']].append(r)
        attempts = [r for r in selected if r['attempt_no'] is not None]
        failures = Counter((r['error_type'],r['http_status']) for r in attempts if r['status']=='failed')
        durations = {status:_duration([r['duration_ms']/1000 for r in attempts
                                      if r['duration_ms'] is not None and (status=='all' or r['status']==status)])
                     for status in ('all','succeeded','failed')}
        groups[kind] = {'tasks':len(tasks),'attempts':len(attempts),
            'attempt_statuses':dict(Counter(r['status'] for r in attempts)),
            'first_attempt_success_tasks':sum(rs[0]['status']=='succeeded' for rs in tasks.values()),
            'retry_success_tasks':sum(rs[0]['status']!='succeeded' and any(r['status']=='succeeded' for r in rs[1:]) for rs in tasks.values()),
            'failed_attempts':[{'error_type':k[0],'http_status':k[1],'count':v} for k,v in sorted(failures.items(),key=lambda p:str(p[0]))],
            'durations_seconds':durations,'missing_duration_attempts':sum(r['duration_ms'] is None for r in attempts)}
    def delta(a,b):
        return (datetime.fromisoformat(b.replace('Z','+00:00'))-datetime.fromisoformat(a.replace('Z','+00:00'))).total_seconds() if a and b else None
    run = conn.execute('SELECT started_at,finished_at FROM collection_runs WHERE run_id=?',(run_id,)).fetchone()
    attempts = [r for r in rows if r['attempt_no'] is not None]
    starts, ends = [r['started_at'] for r in attempts if r['started_at']], [r['finished_at'] for r in attempts if r['finished_at']]
    timing = {'run_started_at':run['started_at'],'run_sealed_at':run['finished_at'],
        'first_attempt_started_at':min(starts) if starts else None,'last_attempt_finished_at':max(ends) if ends else None,
        'capture_span_seconds':delta(min(starts),max(ends)) if starts and ends else None,
        'creation_to_seal_seconds':delta(run['started_at'],run['finished_at']),
        'recorded_attempt_duration_sum_seconds':sum(r['duration_ms'] for r in attempts if r['duration_ms'] is not None)/1000}
    logs, waits, fingerprints, warnings = {}, {}, {}, []
    for prefix in ('detail_pacing','detail_retry'):
        path = Path(data_dir)/'logs'/f'{prefix}_{run_id}.jsonl'
        if not path.exists():
            warnings.append(f'{prefix} log unavailable; do not infer zero waits.')
            continue
        raw = path.read_bytes()
        if raw and not raw.endswith(b'\n'):
            raise ValueError('Incomplete timing journal; report refused')
        events = [json.loads(line) for line in raw.splitlines()]
        if any(e.get('run_id')!=run_id for e in events):
            raise ValueError('Timing journal Run mismatch')
        fingerprints[path.name] = hashlib.sha256(raw).hexdigest()
        dates = [e['at'] for e in events if e.get('at')]
        logs[prefix] = {'records':len(events),'first_record_at':min(dates) if dates else None,
                        'last_record_at':max(dates) if dates else None}
        if prefix=='detail_pacing':
            for phase in ('before','after','batch'):
                finished = [e for e in events if e.get('event')=='wait_finished' and e.get('phase')==phase]
                values = [e['actual_wait_seconds'] for e in finished if type(e.get('actual_wait_seconds')) in (int,float) and math.isfinite(e['actual_wait_seconds']) and e['actual_wait_seconds']>=0]
                waits[phase] = dict(segments=len(finished),actual_seconds=sum(values),missing_or_invalid_duration=len(finished)-len(values))
            logs[prefix]['batch_rest_scheduled'] = sum(e.get('event')=='batch_rest_scheduled' for e in events)
            logs[prefix]['batch_rest_completed'] = sum(e.get('event')=='batch_rest_completed' for e in events)
        else:
            planned = [e for e in events if e.get('event')=='waiting']
            values = [e['delay_seconds'] for e in planned if type(e.get('delay_seconds')) in (int,float) and math.isfinite(e['delay_seconds']) and e['delay_seconds']>=0]
            logs[prefix]['retry_delay'] = dict(schedules=len(planned),planned_seconds=sum(values),missing_or_invalid_duration=len(planned)-len(values),actual_seconds=None)
    manual = set()
    for pattern in ('detail_review_*.json','detail_unavailable_*.json','pending_review_*.json'):
        for p in (Path(data_dir)/'logs').glob(pattern):
            raw = p.read_bytes()
            fingerprints[p.name] = hashlib.sha256(raw).hexdigest()
            record = json.loads(raw)
            binding = record.get('decision',record).get('binding',{})
            if binding.get('run_id')==run_id and type(binding.get('task_id')) is int:
                manual.add(binding['task_id'])
    invocations = Counter()
    unscoped, other_runs = 0, 0
    for p in (Path(data_dir)/'logs').glob('baseline_*.json'):
        raw = p.read_bytes()
        fingerprints[p.name] = hashlib.sha256(raw).hexdigest()
        record = json.loads(raw)
        if record.get('run_id')==run_id:
            label = record.get('result')
            invocations[label if label in ('BLOCKED','PAUSED','PARTIAL','SUCCESS','INSPECT') else 'other'] += 1
        elif record.get('run_id') is None:
            unscoped += 1
        else:
            other_runs += 1
    return {'source':'Run-scoped Task/Attempt SQL and available local journals only.',
        'duration_definition':'Seconds in local collection HTTP client call through response reading/parsing; detail includes service-side comment loading. Excludes pacing, media, health/login checks.',
        'groups':groups,'timing':timing,'logs':logs,'actual_wait_segments':waits,
        'manual_intervention':{'unique_tasks':len(manual),'task_ids':sorted(manual)},
        'saved_invocation_results':dict(invocations),'unscoped_invocation_reports_excluded':unscoped,
        'other_run_invocation_reports_excluded':other_runs,'journal_sha256':fingerprints,'warnings':warnings,
        'unavailable':['Full-Run actual waiting/active time','Exact complete pause/resume count','Internal platform HTTP duration and scroll count'],
        'limitations':'Wait segments cover only journaled periods; planned retry delay is not actual waiting; invocation reports are not a complete lifecycle event log.'}


def _provenance():
    git = ROOT/'.git'
    head = (git/'HEAD').read_text().strip() if git.is_dir() and (git/'HEAD').exists() else None
    if head and head.startswith('ref: '):
        ref = head[5:]
        p = git/ref
        if p.is_file():
            head = p.read_text().strip()
        else:
            head = next((line.split(' ')[0] for line in (git/'packed-refs').read_text().splitlines()
                         if line.endswith(' '+ref)),None) if (git/'packed-refs').exists() else None
    names = ('marketlens/research/run_report.py','marketlens/research/run_metrics.py',
             'marketlens/research/dataset_builder.py','marketlens/research/normalizers.py')
    return {'git_head':head,'implementation_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names},
            'note':'HEAD is repository context; working implementation hashes identify code, including uncommitted files.'}


def collect_report(database, *, project_id, run_id, dataset_id, data_dir, manifest_dir):
    """Validate then return aggregates only; no report files are written here."""
    conn = connect(Path(database),readonly=True)
    try:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        row = conn.execute('SELECT * FROM dataset_versions WHERE dataset_id=?',(dataset_id,)).fetchone()
        if not row or row['project_id']!=project_id or row['source_run_id']!=run_id:
            raise ValueError('Dataset Project/Run scope mismatch')
        metadata = {k:row[k] for k in ('dataset_id','project_id','source_run_id','dataset_name','version','status','created_at','frozen_at','manifest_sha256')}
        rules = json.loads(row['selection_rules_json'])
    finally:
        conn.close()
    verification = verify_dataset(database,dataset_id,source_database=database,data_dir=data_dir,manifest_dir=manifest_dir,simulation=False)
    run = analyze_run(database,project_id=project_id,run_id=run_id,data_dir=data_dir)
    if run['source']['run_status'] not in ('completed','partial','failed','cancelled'):
        raise ValueError('Final report requires terminal Run')
    final = run['detail']['finalization']
    if final['validated'] and (not final['sealed'] or not final['checkpoint_synchronized'] or final['recovery_required']):
        raise ValueError('Run finalization not synchronized')
    conn = connect(Path(database),readonly=True)
    try:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        dataset = _dataset_metrics(conn,dataset_id)
        reliability = _reliability(conn,run_id,data_dir)
        plan = conn.execute('SELECT p.comment_config_json FROM collection_plans p JOIN collection_runs r USING(plan_id) WHERE run_id=?',(run_id,)).fetchone()
        config = json.loads(plan[0])
        design = {k:config.get(k) for k in ('category','top_comment_target','load_all_comments','extra_comment_requests','selection','interval_seconds')}
        design['comment_config'] = {k:config.get('comment_config',{}).get(k) for k in ('max_comment_items','click_more_replies','max_replies_threshold','scroll_speed')}
        dispositions = [dict(r) for r in conn.execute("""SELECT t.task_id,t.post_id FROM collection_tasks t
            JOIN dataset_posts d ON d.post_id=t.post_id WHERE t.run_id=? AND d.dataset_id=?
            AND t.task_type='feed_detail' AND d.inclusion_status='detail_unavailable'""",(run_id,dataset_id))]
        # Reasons come from verified immutable Manifest, never guessed from Task status.
        manifest_path = Path(verification['manifest'])
        manifest_raw = manifest_path.read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest()!=metadata['manifest_sha256']:
            raise ValueError('Manifest changed while reporting')
        manifest = json.loads(manifest_raw)
        media_files = {item['relative_path']:item['bytes'] for item in manifest['plan']['media']}
        dataset['media'] = {'verified_associations':len(manifest['plan']['media']),
                            'unique_files':len(media_files),'unique_file_bytes':sum(media_files.values())}
        raw = dict(conn.execute("""SELECT count(*) records,coalesce(sum(f.file_size_bytes),0) declared_bytes
            FROM raw_artifacts f JOIN collection_attempts a USING(attempt_id)
            JOIN collection_tasks t USING(task_id) WHERE t.run_id=?""",(run_id,)).fetchone())
        reasons = [{'task_id':p['disposition']['task_id'],'reason':p['disposition']['disposition']}
                   for p in manifest['plan']['posts'] if p.get('disposition')]
        if set(r['task_id'] for r in reasons)!=set(r['task_id'] for r in dispositions):
            raise ValueError('Unavailable task dispositions mismatch')
        if manifest['plan']['source']['run_evidence_sha256']!=run['source']['run_evidence_sha256']:
            raise ValueError('Run/Dataset source fingerprint mismatch')
        # Cross-check independent membership aggregates against validated Manifest.
        counts = verification['counts']
        for key in ('included','detail_unavailable','type_mismatch'):
            if dataset['composition'][key]!=counts[key]:raise ValueError('Dataset composition mismatch')
        if (dataset['candidate_posts'],dataset['selected_snapshots'],dataset['comments']['observations'])!=(counts['posts'],counts['snapshots'],counts['comments']):
            raise ValueError('Dataset aggregate mismatch')
    finally:
        conn.close()
    run['source']['database'] = _path(database)
    return {'report_format':'marketlens_run_final_report_v1','generated_at':run['analyzed_at'],
        'scope':{'project_id':project_id,'run_id':run_id,'dataset_id':dataset_id},'design':design,
        'run':run,'verified_run_raw_artifacts':raw,'dataset':{'metadata':metadata,'selection_rules':rules,'metrics':dataset,'dispositions':reasons,
            'verification':{k:verification[k] for k in ('verified','simulation','status','counts','quality','integrity','manifest_sha256')},
            'manifest':_path(manifest_path),'manifest_bytes':len(manifest_raw)},
        'reliability':reliability,'provenance':_provenance()}


def _cell(value):
    if value is None:return '未知/不适用'
    if isinstance(value,float):return f'{value:,.3f}'
    if isinstance(value,int):return f'{value:,}'
    return str(value).replace('|','\\|').replace('\n',' ')


def _table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(_cell(v) for v in row)+' |' for row in rows])


def _pct(value):
    return f'{value:.2%}' if value is not None else '未知/不适用'


def render_markdown(report):
    """All numeric tables are rendered from this same JSON-ready result."""
    r,d,rel = report['run'],report['dataset'],report['reliability']
    s,c,q = r['search'],r['comments'],r['completeness']
    dm = d['metrics'];m = d['metadata'];v = lambda section,key:_value(section,key)
    sections = [f"# MarketLens Phase 1D — Run {report['scope']['run_id']} 最终报告",
        f"生成时间：{report['generated_at']}（UTC）。本文与同目录 `run{report['scope']['run_id']}_metrics.json`由同一聚合结果生成，不包含评论原文或作者信息。",
        '## 1. 实验目标与采样设计',
        '建立可追溯的研究基线，观察既定关键词与排序下的候选及有限评论样本；不估计平台总体、销量或市场份额。',
        f"研究范围：Project {report['scope']['project_id']}，Run {report['scope']['run_id']}。关键词{len(s['tables']['keywords'])}个、排序{len(s['tables']['sorts'])}种、实际组合{v(s,'search_requests')}组；图文筛选仍可能返回视频。",
        '采集配置（来源collection_plans.comment_config_json）：`'+json.dumps(report['design'],ensure_ascii=False,sort_keys=True)+'`。配置目标不是成功与否的判据。',
        '## 2. 数据采集流程概述',
        '先搜索并保留各次Hit，再按首次搜索及结果位置安排图文详情；保留真实Attempt、Raw及评论观察历史。失败按既有策略重试或人工复核，最后显式本地封口；Dataset冻结是后续独立步骤。',
        f"Dataset独立验证同时复核Run原始证据。Run Raw记录{report['verified_run_raw_artifacts']['records']}份，声明字节合计{report['verified_run_raw_artifacts']['declared_bytes']:,}；媒体通过本地哈希、文件大小及图像解码尺寸校验。",
        '## 3. 最终数据规模与统计边界',
        _table(['Run指标','数值','来源/定义'],[
            ['成功搜索组 / 搜索Attempt',f"{v(s,'successful_search_requests')} / {v(s,'search_attempts')}",'search_requests状态 / 实际持久化Attempt'],
            ['搜索命中',v(s,'search_hits'),'Run内search_hits行数'],['唯一候选',v(s,'unique_candidates'),'去重post_id'],
            ['重复命中',v(s,'search_hits')-v(s,'unique_candidates'),'Hit数−唯一候选数'],['重复命中率',_pct(v(s,'repeat_hit_rate')),'重复命中 / 全部Hit'],
            ['成功详情Task',v(r['detail'],'successful_detail_tasks'),'成功normal Snapshot可核实'],
            ['来源删除',v(r['detail'],'source_deleted') if 'source_deleted' in r['detail']['metrics'] else None,'审核证据验证，不从超时推断'],
            ['人工受限永久跳过',v(r['detail'],'permanently_skipped_restricted'),'与来源删除分开'],
            ['未解决详情Task',v(r['detail'],'unresolved_detail_tasks'),'成功观察及终结证据分类'],
            ['图文详情成功率',_pct(v(r['detail'],'normal_detail_success_rate')),'拥有成功Snapshot的图文候选 / 图文候选'],
            ['成功Snapshot / 唯一Post',f"{v(r['detail'],'successful_snapshots')} / {v(r['detail'],'posts_with_successful_snapshot')}",'观察与身份分开'],
            ['同Post多Snapshot',v(r['detail'],'posts_with_multiple_snapshots'),'Run内成功Snapshot数>1的Post']]),
        _table(['冻结Dataset指标','数值'],[['候选台账',dm['candidate_posts']],*[[key,dm['composition'][key]] for key in ('included','detail_unavailable','type_mismatch')],
            ['选定Snapshot',dm['selected_snapshots']],['评论Observation',dm['comments']['observations']],['稳定Comment身份',dm['comments']['stable_identities']],
            ['匿名Observation',dm['comments']['anonymous_observations']],['已验证图片关联',dm['media']['verified_associations']],
            ['已验证独立图片文件',dm['media']['unique_files']],['独立图片文件字节',dm['media']['unique_file_bytes']]]),
        'Run保留全部成功观察；Dataset按冻结成员引用固定Snapshot/Observation。Post与Snapshot是一对多，当前数量相同不意味着Schema是一对一。图片是关联数，不自动代表不同文件数。',
        '## 4. 关键词、排序与发现增量',
        '下列表来源Run Analytics search_sampling_metrics。独有贡献指仅由该关键词或排序发现，与执行顺序无关；边际新增指相对之前所有搜索组尚未出现，依赖sequence_no/search_request_id顺序。各关键词的唯一数存在交叉，不能相加作为全体唯一候选数。',
        _table(['关键词','命中','唯一候选','独有贡献'],[[x['keyword'],x['hits'],x['unique_candidates'],x['exclusive_candidates']] for x in s['tables']['keywords']]),
        _table(['排序','命中','唯一候选','独有贡献'],[[x['sort_method'],x['hits'],x['unique_candidates'],x['exclusive_candidates']] for x in s['tables']['sorts']]),
        '排序集合重叠：Jaccard=交集/并集，空并集返回未知；不等同于重复Hit率。',
        _table(['排序A','排序B','交集','并集','Jaccard'],[[x['left'],x['right'],x['intersection'],x['union'],_pct(x['jaccard'])] for x in s['tables']['sort_overlap']]),
        _table(['顺序','关键词','排序','Hit','本组唯一','边际新增','累计唯一'],[[x['sequence_no'],x['keyword'],x['sort_method'],x['hits'],x['unique_candidates'],x['new_candidates'],x['cumulative_candidates']] for x in s['tables']['discovery_timeline']]),
        '## 5. 评论覆盖与字段质量',
        _table(['评论指标','Run成功观察','冻结Dataset成员'],[
            ['一级评论',v(c,'top_observations'),dm['comments']['top_observations']],['二级回复',v(c,'reply_observations'),dm['comments']['reply_observations']],
            ['全部Observation',v(c,'comment_observations'),dm['comments']['observations']],['稳定身份',v(c,'stable_comment_identities'),dm['comments']['stable_identities']],
            ['重复稳定身份的额外观察',v(c,'extra_stable_observations'),dm['comments']['extra_stable_observations']],['匿名观察',v(c,'anonymous_observations'),dm['comments']['anonymous_observations']],
            ['未观察到评论的笔记',v(c,'posts_without_observed_comments'),dm['posts_without_observed_comments']],
            ['达到目标',v(c,'posts_at_or_above_target'),dm['posts_at_or_above_target']],['低于目标',v(c,'posts_below_target'),dm['posts_below_target']],
            ['超过目标',v(c,'posts_above_target'),dm['posts_above_target']],['目标未知',v(c,'posts_with_unknown_target'),dm['posts_with_unknown_target']],
            ['目标达成比例',_pct(v(c,'target_attainment_rate')),_pct(dm['target_attainment_rate'])]]),
        '目标以唯一稳定一级评论ID计算，未知目标不进入比例分母。Run每篇分布取最新成功Snapshot所属Attempt；Dataset取其全部冻结评论成员。匿名Observation单列、不按正文合并。',
        _table(['一级评论分布','Run每篇','Dataset每篇'],[[k,v(c,'top_comments_per_post')[k],dm['top_comments_per_post'][k]] for k in ('n','sum','mean','min','p25','median','p75','p90','p95','max')]),
        '分位数采用linear插值，rank=(n−1)p；零评论笔记进入每篇分母。Dataset每篇一级评论频数：',
        _table(['一级评论数','笔记数'],[[x['value'],x['posts']] for x in dm['top_comments_per_post']['histogram']]),
        _table(['has_more','Run最新Snapshot的Attempt','Dataset选定Snapshot的Attempt'],[[k,v(c,'has_more')[k],dm['source_snapshot_attempt_has_more'][k]] for k in ('true','false','unknown')]),
        'has_more是源响应元数据，不证明Dataset或平台评论区完整；未观察到评论不等于原帖没有评论。',
        'Run父子关系异常：`'+json.dumps(v(c,'relationship_errors'),sort_keys=True)+'`；Dataset保留原始父Observation，同时按稳定父身份追溯，跨观察父链接数：'+str(d['verification']['quality']['cross_observation_parent_links'])+'。',
        '冻结一级评论所保存的父响应回复覆盖（来源comment_raw_json，复用normalizers.count）：',
        _table(['指标','数值'],[[k,dm['reply_coverage'][k]] for k in ('parents','known_displayed_reply_count','parents_with_unreturned_replies','displayed_replies_sum','replies_returned_in_parent_response','positive_gap_sum')]),
        '展示回复数与该父响应返回回复数之间的正差不是全评论区缺失总量。当前规则未展开的回复仍然未观察；不能将这些评论作为完整总体。',
        '字段完整性：Run按全部成功观察；Dataset按固定成员。文本NULL及纯空白均为缺失；数值NULL为未知、真实0保留；valid表示可用而非人工确认真实。',
        _table(['对象/字段','Run观察数','Run缺失','Dataset观察数','Dataset有效','Dataset缺失','缺失率','真实0','展示精度'],[
            [entity+'/'+field,v(q,run_prefix+'.'+field)['total'],v(q,run_prefix+'.'+field)['missing'],p['total'],p['valid'],p['missing'],_pct(p['missing_rate']),p['known_zero'],json.dumps(p['precision'],ensure_ascii=False,sort_keys=True) if p['precision'] else '不适用']
            for entity,run_prefix in (('snapshots','snapshot'),('comments','comment')) for field,p in dm['completeness'][entity].items()]),
        '精确展示值、近似展示值及下界分别保留解析类别；不把未知互动数填成0，不因空文本删除原始成员。',
        '## 6. 请求可靠性与异常恢复',
        '来源collection_tasks/collection_attempts。首次成功指该Task最早Attempt成功；重试成功指最早未成功而后续成功。请求次数仅包含持久化采集Attempt，不含健康/登录预检查、媒体下载或服务内部请求。',
        _table(['类型','Task','Attempt','成功Attempt','失败Attempt','首次成功Task','重试后成功Task'],[
            [k,g['tasks'],g['attempts'],g['attempt_statuses'].get('succeeded',0),g['attempt_statuses'].get('failed',0),g['first_attempt_success_tasks'],g['retry_success_tasks']] for k,g in rel['groups'].items()]),
        _table(['类型','错误类别','HTTP状态','失败Attempt'],[[k,f['error_type'],f['http_status'],f['count']] for k,g in rel['groups'].items() for f in g['failed_attempts']]),
        '本地HTTP客户端耗时，单位秒；包括响应读取/解析，详情包含服务端评论加载，不等于平台内部单次HTTP耗时。',
        _table(['类型','状态','n','均值','中位数','P90','P95'],[[k,status,x['n'],x['mean'],x['median'],x['p90'],x['p95']] for k,g in rel['groups'].items() for status,x in g['durations_seconds'].items()]),
        '日志中明确记录的实际等待段（次数是段数，不是Task数）：',
        _table(['阶段','等待段','实际秒','缺失/无效时长'],[[k,x['segments'],x['actual_seconds'],x['missing_or_invalid_duration']] for k,x in rel['actual_wait_segments'].items()]),
        _table(['日志','记录数','首记录UTC','末记录UTC'],[[k,x['records'],x['first_record_at'],x['last_record_at']] for k,x in rel['logs'].items()]),
        '日志可用性警告：'+('；'.join(rel['warnings']) if rel['warnings'] else '无缺失日志警告；记录覆盖范围仍不等于全Run。'),
        '重试计划延迟：`'+json.dumps(rel['logs'].get('detail_retry',{}).get('retry_delay',{}),ensure_ascii=False,sort_keys=True)+'`；计划时长不能当作实际等待。',
        f"有审核记录的不同Task：{rel['manual_intervention']['unique_tasks']}个；ID：{rel['manual_intervention']['task_ids']}。保存的运行结果报告次数：`{json.dumps(rel['saved_invocation_results'],sort_keys=True)}`；这些不是完整暂停/恢复次数。",
        f"另排除{rel['unscoped_invocation_reports_excluded']}份缺少Run ID的运行输出、{rel['other_run_invocation_reports_excluded']}份其他Run输出，不据此推断本Run的活动。",
        _table(['时间口径','数值/UTC'],list(rel['timing'].items())),
        '首末Attempt跨度包含暂停和人工处理；创建至封口跨度还包含采集结束后的处理时间。不得用墙钟时间减去请求耗时反推等待，不提供全Run活跃运行时长。',
        '## 7. Run封口与Dataset冻结',
        f"Run状态：`{r['source']['run_status']}`；本地封口校验：`{json.dumps(r['detail']['finalization'],sort_keys=True)}`。",
        _table(['Dataset属性','值'],list(m.items())),
        '成员规则：`'+json.dumps(d['selection_rules'],ensure_ascii=False,sort_keys=True)+'`。稳定身份选截止前最新Observation；匿名观察全部保留。未来再次采集通过新Run，不重新计算并覆盖旧成员。',
        _table(['不可获取Task','经证据验证的原因'],[[x['task_id'],x['reason']] for x in d['dispositions']]),
        f"正式Manifest：`{d['manifest']}`，{d['manifest_bytes']:,}字节；SHA-256：`{m['manifest_sha256']}`。独立验证通过：`{d['verification']['verified']}`，simulation：`{d['verification']['simulation']}`；完整性/外键：`{json.dumps(d['verification']['integrity'],sort_keys=True)}`。",
        'Dataset成员表及底层观察受不可变触发器保护；文件哈希之外，验证器还重新核对实际成员、规则、来源、Raw及媒体。',
        '## 8. 采样偏差、缺失与研究局限',
        '- 关键词、排序、有限搜索深度及采集时间共同决定样本，不是随机抽样，不能外推平台或市场总体。\n- 搜索重复、视频混入及两类不可获取候选均保留台账；成功空响应不证明平台没有相关内容。\n- 评论目标与自然返回数量不同，二级回复未完整展开；评论展示数也不等于本次观察数。\n- 空文本和未知/近似互动指标可能影响下游分析，应按有效分母及精度报告，不能无依据插补。\n- 日志仅覆盖已记录时段；完整暂停/恢复次数、全Run等待/活跃时间和内部平台耗时不能可靠计算。',
        '## 9. 数据复现方法与版本',
        f"Run Analytics契约：`{r['contract_version']}`；Run来源指纹：`{r['source']['run_evidence_sha256']}`。",
        'Run统计复用`marketlens.research.run_metrics.analyze_run()`；Dataset验证复用`dataset_builder.verify_dataset()`；附加SQL只读取指定Dataset成员和指定Run的Task/Attempt。日志指纹见聚合JSON。',
        '仓库HEAD：`'+str(report['provenance']['git_head'])+'`。HEAD仅作基点，未声称未提交代码已经纳入该Commit；实际实现以文件SHA-256标识：',
        _table(['实现文件','SHA-256'],list(report['provenance']['implementation_sha256'].items())),
        '在项目根目录运行（只读正式数据，只写报告目录）：',
        '```powershell\npython -B -m marketlens.research.run_report '+
        '--database '+"'"+r['source']['database'].replace("'","''")+"'"+
        ' --data-dir '+"'"+Path(r['source']['database']).parent.as_posix().replace("'","''")+"'"+
        ' --manifest-dir '+"'"+Path(d['manifest']).parent.as_posix().replace("'","''")+"' "+
        f"--project-id {report['scope']['project_id']} --run-id {report['scope']['run_id']} --dataset-id {m['dataset_id']} --output-dir docs/phase1d_xhs_baseline_collection\n```",
        '复现需要相同数据库、Manifest、Raw、媒体及审核证据；仅有聚合JSON不能重建原始研究数据。封口决定绑定原数据库路径，迁移副本需保留原决定并验证权威来源，不能修改绑定以通过检查。聚合报告不包含原文、作者信息或认证缓存。']
    return '\n\n'.join(sections)+'\n'


def generate_report(database, *, project_id, run_id, dataset_id, data_dir, manifest_dir, output_dir):
    output = Path(output_dir).resolve()
    for protected in (Path(data_dir).resolve(),Path(manifest_dir).resolve()):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError('Report output overlaps authoritative data/evidence')
    report = collect_report(database,project_id=project_id,run_id=run_id,dataset_id=dataset_id,
                            data_dir=data_dir,manifest_dir=manifest_dir)
    # JSON key order must also be the renderer's order, so reloading the saved
    # aggregate recreates byte-for-byte identical Markdown tables.
    report = json.loads(json.dumps(report,ensure_ascii=False,sort_keys=True,allow_nan=False))
    json_path, md_path = output/f'run{run_id}_metrics.json', output/f'run{run_id}_final_report.md'
    atomic_write(json_path,(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
    atomic_write(md_path,render_markdown(report).encode())
    return report, {'markdown':str(md_path),'json':str(json_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database','data-dir','manifest-dir','output-dir'):
        parser.add_argument('--'+name,required=True,type=Path)
    for name in ('project-id','run-id','dataset-id'):
        parser.add_argument('--'+name,required=True,type=int)
    args = vars(parser.parse_args())
    _,paths = generate_report(**args)
    print(json.dumps(paths,ensure_ascii=False))


if __name__=='__main__':
    main()
