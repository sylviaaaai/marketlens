"""Explicit two-stage collection. No arguments means no collection or DB writes."""
import argparse
from contextlib import closing, contextmanager
import json
import sqlite3
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP, load_auth, utc_now
from marketlens.research.baseline_config import BaselineConfig, DetailRetryPolicy, ReviewedTimeoutRecovery, DetailPacingPolicy
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.access_cache import AccessCache, AccessCacheError, atomic_write
from marketlens.research.collection_pipeline import audit
from marketlens.research.database import connect, initialize
from marketlens.research.media_store import MediaStore, run_project
from marketlens.research.run_export import export_run

DATA = ROOT / "local_data/phase1d_baseline"
DB = DATA / "research_baseline.db"
FORMAL_DATA = ROOT / "local_data/phase1d_formal_baseline"
FORMAL_DB = FORMAL_DATA / "research_baseline.db"


def new_run_allowed(conn, explicit_category=None):
    """Existing runs require an explicit有效品类输入, nonempty new-Project choice."""
    return not conn.execute("SELECT 1 FROM collection_runs LIMIT 1").fetchone() or bool(explicit_category and explicit_category.strip())
##先看有没有runs，如果没有就允许新建，如果有就看有没有输入explicit_category，看看本身和去掉空格是否为空，如果有就允许新建，如果没有就不允许新建，这里同时用了短路求值

#文件锁，同一个采集目录，在同一时间只允许一个遵守该锁机制的采集进程进入执行流程很有趣
@contextmanager
def single_writer(data_dir=DATA):
    """OS lock releases on crash; retain the harmless lock file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "collector.lock").open("a+b") as handle:#"a+b" :append and read/write in binary mode. If the file does not exist, it will be created. The file pointer is at the end of the file after opening.
        handle.seek(0)#文件的当前位置移动到开头。
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)#锁定文件，此时如果其他进程也尝试锁定这个文件，就会失败。
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield#出去执行代码采集数据
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)#解锁文件，允许其他进程锁定这个文件。


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--pilot", action="store_true")
    action.add_argument("--resume", type=int, metavar="RUN_ID")
    action.add_argument("--inspect", action="store_true")
    action.add_argument("--plan", action="store_true", help="Print all 50 combinations; no requests")
    action.add_argument("--start-baseline", action="store_true", help="Explicitly start formal 50 searches, then pause")
    action.add_argument("--init-formal", action="store_true", help="Create/verify independent formal DB only; no requests")
    action.add_argument("--pause", type=int, metavar="RUN_ID", help="Request pause at next request boundary")
    action.add_argument("--preflight-validation", action="store_true", help="Bounded real validation; logs only, no SQLite writes")
    action.add_argument("--preflight-restore", action="store_true", help="New-process DPAPI validation after 5-10 minutes")
    action.add_argument("--resume-media", type=int, metavar="RUN_ID", help="Resume body images only; no MCP or database writes")
    action.add_argument("--export-run", type=int, metavar="RUN_ID", help="Export Run-scoped text and verified media references")
    action.add_argument("--confirm-source-deleted", type=int, metavar="TASK_ID", help="Explicit local review only; never resumes or uses MCP")
    action.add_argument("--defer-detail-attempt", type=int, metavar="ATTEMPT_ID", help="Explicit local pending-review classification only")
    action.add_argument("--reactivate-detail-attempt", type=int, metavar="ATTEMPT_ID", help="Authorize one refill of a previously deferred task; no HTTP here")
    action.add_argument("--finalize-run", type=int, metavar="RUN_ID", help="Explicit offline finalization only; never uses Adapter or credentials")
    action.add_argument("--check-finalization", type=int, metavar="RUN_ID", help="Read-only offline finalization validation")
    parser.add_argument("--finalization-decision", type=Path, help="Explicit bound human finalization JSON")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--post-id", type=int, default=None)
    parser.add_argument("--task-id", type=int, default=None)
    parser.add_argument("--review-classification", choices=("comment_loading_stalled", "detail_timeout_unknown"))
    parser.add_argument("--review-note")
    parser.add_argument("--project-id", type=int, help="Required ownership check for media resume and export")
    parser.add_argument("--output-dir", type=Path, help="Derived export directory, only with --export-run")
    parser.add_argument("--formal", action="store_true", help="Select formal DB for inspect/resume/pause")
    parser.add_argument("--new-project", metavar="CATEGORY", help="Explicitly create another Project with --start-baseline")
    parser.add_argument("--stage", choices=("search", "details"), help="Resume only this stage; formal default is search")
    parser.add_argument("--auto-retry", action="store_true", help="Opt in to bounded formal detail retries only")
    parser.add_argument("--retry-max-attempts", type=int, default=None)
    parser.add_argument("--retry-min-seconds", type=float, default=None)
    parser.add_argument("--retry-max-seconds", type=float, default=None)
    parser.add_argument("--detail-timeout-seconds", type=int, choices=(90, 120), default=None)
    parser.add_argument("--no-detail-pacing", action="store_true", help="Disable new pacing schedules; existing post-success deadlines still apply")
    parser.add_argument("--detail-buffer-min-seconds", type=float, default=None)
    parser.add_argument("--detail-buffer-max-seconds", type=float, default=None)
    parser.add_argument("--detail-after-min-seconds", type=float, default=None)
    parser.add_argument("--detail-after-max-seconds", type=float, default=None)
    parser.add_argument("--detail-batch-rest", action="store_true", help="Enable persisted formal detail batches (15-30 successes, 60-300s rest)")
    parser.add_argument("--detail-batch-min-tasks", type=int, default=None)
    parser.add_argument("--detail-batch-max-tasks", type=int, default=None)
    parser.add_argument("--detail-batch-min-seconds", type=float, default=None)
    parser.add_argument("--detail-batch-max-seconds", type=float, default=None)
    parser.add_argument("--approve-timeout-attempt", type=int, default=None)
    parser.add_argument("--review-reason", default=None)
    parser.add_argument("--review-evidence", type=Path, default=None)
    parser.add_argument("--confirm-server-request-finished", action="store_true")
    parser.add_argument("--auth-env-file", type=Path, default=ROOT.parent / "data-source-lab/xiaohongshu-mcp/.env")
    args = parser.parse_args(argv)
    local_run = args.finalize_run if args.finalize_run is not None else args.check_finalization
    if local_run is not None:
        allowed = {"--formal", "--finalize-run", "--check-finalization", "--finalization-decision"}
        if (not args.formal or args.finalization_decision is None
                or any(v.startswith("--") and v.split("=", 1)[0] not in allowed for v in (argv if argv is not None else sys.argv[1:]))):
            parser.error("Local finalization requires --formal, a bound --finalization-decision, and no collection options")
        if not FORMAL_DB.is_file():
            parser.error("Cannot finalize a missing database")
        from marketlens.research.run_finalization import finalize_run
        try:
            decision = json.loads(args.finalization_decision.read_text(encoding="utf-8"))
            if args.check_finalization is not None:
                with closing(connect(FORMAL_DB, readonly=True)) as conn:
                    result = finalize_run(conn, FORMAL_DATA, local_run, decision, check_only=True)
            else:
                with single_writer(FORMAL_DATA), closing(connect(FORMAL_DB)) as conn:
                    result = finalize_run(conn, FORMAL_DATA, local_run, decision)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            # Do not print raw decision contents or arbitrary filesystem errors.
            print(json.dumps({"result": "BLOCKED", "reason": "LocalFinalizationValidationOrRecoveryRequired", "error_type": type(exc).__name__}))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.finalization_decision is not None:
        parser.error("--finalization-decision requires --finalize-run or --check-finalization")
    pacing_values = {key: value for key, value in {
        "buffer_min_seconds": args.detail_buffer_min_seconds, "buffer_max_seconds": args.detail_buffer_max_seconds,
        "after_min_seconds": args.detail_after_min_seconds, "after_max_seconds": args.detail_after_max_seconds}.items() if value is not None}
    batch_values = {key: value for key, value in {
        "batch_min_tasks": args.detail_batch_min_tasks, "batch_max_tasks": args.detail_batch_max_tasks,
        "batch_min_seconds": args.detail_batch_min_seconds, "batch_max_seconds": args.detail_batch_max_seconds}.items() if value is not None}
    if batch_values and not args.detail_batch_rest:
        parser.error("Batch settings require --detail-batch-rest")
    if args.detail_batch_rest:
        pacing_values.update(batch_enabled=True, **batch_values)
    explicit_details = args.formal and args.resume is not None and args.stage == "details"
    if (pacing_values or args.no_detail_pacing) and not explicit_details:
        parser.error("Detail pacing settings require --formal --resume RUN_ID --stage details")
    if pacing_values and args.no_detail_pacing:
        parser.error("Cannot configure and disable detail pacing together")
    try:
        pacing_policy = DetailPacingPolicy(**pacing_values) if explicit_details and not args.no_detail_pacing else None
    except ValueError as exc:
        parser.error(str(exc))
    pending_action = args.defer_detail_attempt is not None or args.reactivate_detail_attempt is not None
    retry_policy = None
    if args.auto_retry:
        if not (args.formal and args.resume is not None and args.stage == "details"):
            parser.error("--auto-retry requires --formal --resume RUN_ID --stage details")
        try:
            retry_policy = DetailRetryPolicy(**{key: value for key, value in {
                "max_attempts": args.retry_max_attempts, "min_seconds": args.retry_min_seconds,
                "max_seconds": args.retry_max_seconds}.items() if value is not None})
        except ValueError as exc:
            parser.error(str(exc))
    elif any(v is not None for v in (args.retry_max_attempts, args.retry_min_seconds, args.retry_max_seconds)):
        parser.error("Retry settings require --auto-retry")
    if args.detail_timeout_seconds is not None and not (args.formal and args.resume is not None and args.stage == "details"):
        parser.error("--detail-timeout-seconds requires explicit formal detail resume")
    reviewed_timeout = None
    if args.approve_timeout_attempt is not None:
        if not args.auto_retry or args.review_reason is None or args.review_evidence is None:
            parser.error("Timeout review requires --auto-retry, --review-reason and --review-evidence")
        try:
            reviewed_timeout = ReviewedTimeoutRecovery(args.approve_timeout_attempt, args.review_reason,
                args.review_evidence, args.confirm_server_request_finished)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.confirm_source_deleted is None and not pending_action and (args.review_reason is not None or args.review_evidence is not None or args.confirm_server_request_finished):
        parser.error("Review settings require --approve-timeout-attempt")
    if args.confirm_source_deleted is not None:
        if (not args.formal or (args.run_id, args.confirm_source_deleted, args.post_id) != (1, 153, 108)
                or args.review_reason != "source_deleted" or args.review_evidence is None
                or args.stage is not None or args.auto_retry or args.approve_timeout_attempt is not None
                or args.confirm_server_request_finished):
            parser.error("Deletion confirmation requires --formal --run-id 1 --confirm-source-deleted 153 --post-id 108 --review-reason source_deleted --review-evidence FILE; no collection options")
    elif not pending_action and (args.run_id is not None or args.post_id is not None):
        parser.error("--run-id/--post-id require --confirm-source-deleted")
    if pending_action:
        if (not args.formal or args.run_id is None or args.task_id is None or args.post_id is None
                or not args.review_note or args.stage is not None or args.auto_retry
                or args.approve_timeout_attempt is not None or args.review_reason is not None):
            parser.error("Pending review requires --formal --run-id --task-id --post-id --review-note, without collection options")
        if args.defer_detail_attempt is not None and (args.review_classification is None or args.review_evidence is None or args.confirm_server_request_finished):
            parser.error("Deferral requires --review-classification and --review-evidence")
        if args.reactivate_detail_attempt is not None and (not args.confirm_server_request_finished or args.review_classification is not None or args.review_evidence is not None):
            parser.error("Activation requires --confirm-server-request-finished and the existing deferred evidence")
    elif args.task_id is not None or args.review_classification is not None or args.review_note is not None:
        parser.error("Pending review options require --defer-detail-attempt or --reactivate-detail-attempt")
    resource_action = args.resume_media is not None or args.export_run is not None
    if resource_action and (args.project_id is None or args.stage is not None):
        parser.error("Resource/export actions require --project-id and no --stage")
    if args.project_id is not None and not resource_action:
        parser.error("--project-id requires --resume-media or --export-run")
    if args.output_dir is not None and args.export_run is None:
        parser.error("--output-dir requires --export-run")
    if args.resume_media is not None and not args.formal:
        parser.error("Media recovery is formal-only; historical Pilot remains unchanged")
    if args.new_project is not None and (not args.start_baseline or not args.new_project.strip()):
        parser.error("--new-project requires --start-baseline and a nonempty category")
    if not any((args.pilot, args.resume is not None, args.inspect, args.plan,
                args.start_baseline, args.init_formal, args.pause is not None, args.preflight_validation, args.preflight_restore, resource_action, args.confirm_source_deleted is not None, pending_action)):
        parser.print_help()
        return 0
    if args.preflight_validation or args.preflight_restore:
        if args.formal or args.stage:
            parser.error("Preflight is independent of formal collection stages")
        from marketlens.research.preflight_validation import Validation
        with single_writer(DATA):
            validation = Validation(DATA / "logs/final_preflight", XiaohongshuMCP(load_auth(args.auth_env_file), timeout=120), FORMAL_DB)
            result = validation.capture() if args.preflight_validation else validation.restore()
            print(json.dumps({"status": result["status"], "calls": len(result["calls"]), "reason": result.get("reason")}, ensure_ascii=False))
            return 0 if result["status"] in ("WAITING_FOR_NEW_PROCESS", "COMPLETE") else 2
    if args.pilot and args.formal:
        parser.error("--pilot cannot select formal DB")
    if args.stage and args.resume is None:
        parser.error("--stage requires --resume RUN_ID")
    formal = args.formal or args.start_baseline or args.init_formal
    data_dir, db = (FORMAL_DATA, FORMAL_DB) if formal else (DATA, DB)
    config = BaselineConfig(pilot=not formal, **({"category": args.new_project} if args.new_project else {}))
    stage = args.stage or ("search" if formal else "all")
    if args.confirm_source_deleted is not None or pending_action:
        if not db.exists():
            parser.error("Cannot review a missing database")
        # This route has no Adapter construction, auth read or preflight. The
        # existing DB and single-writer lock are used only after explicit review.
        with single_writer(data_dir), closing(connect(db)) as conn:
            row = conn.execute("SELECT p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (args.run_id,)).fetchone()
            if row is None:
                parser.error("Unknown Run")
            saved = json.loads(row[0])
            config = BaselineConfig(**{key: value for key, value in saved.items() if key in BaselineConfig.__dataclass_fields__})
            collector = BaselineCollector(conn, None, data_dir, config)
            if pending_action:
                result = collector.review_pending_detail(args.run_id, args.task_id, args.post_id,
                    args.defer_detail_attempt if args.defer_detail_attempt is not None else args.reactivate_detail_attempt,
                    classification=args.review_classification, note=args.review_note, evidence_file=args.review_evidence,
                    activate=args.reactivate_detail_attempt is not None)
            else:
                result = collector.confirm_source_deleted(args.run_id, args.confirm_source_deleted, args.post_id,
                                                         args.review_reason, args.review_evidence)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if resource_action:
        if not db.exists():
            parser.error("Cannot read a missing database")
        run = args.resume_media if args.resume_media is not None else args.export_run
        with closing(connect(db, readonly=True)) as conn:
            run_project(conn, run, args.project_id)
            saved = json.loads(conn.execute("SELECT p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (run,)).fetchone()[0])
            with single_writer(data_dir):
                if args.export_run is not None:
                    result = export_run(conn, args.project_id, run, data_dir, args.output_dir)
                else:
                    if saved.get("pilot", True):
                        parser.error("Cannot download historical Pilot resources")
                    store = MediaStore(conn, data_dir, directory=saved.get("media_directory", "media"),
                        max_attempts=saved.get("media_max_attempts", 2), interval_seconds=saved.get("media_interval_seconds", .5))
                    result = store.process(run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("unfinished", 0) == 0 else 2
    if args.plan:
        print(json.dumps(BaselineConfig(pilot=False).combinations, ensure_ascii=False, indent=2))
        return 0
    if args.inspect:
        if not db.exists():
            print(json.dumps({"database": str(db), "state": "NOT_INITIALIZED"}))
            return 0
        with closing(connect(db, readonly=True)) as conn:
            # No collector construction: read-only inspection creates no directories.
            inspector = object.__new__(BaselineCollector)
            inspector.conn = conn
            inspector.data_dir = data_dir
            results = []
            for row in conn.execute("SELECT run_id FROM collection_runs"):
                value = inspector.summary(row[0])
                if formal:
                    saved = json.loads(conn.execute("SELECT p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (row[0],)).fetchone()[0])
                    value["media_downloads"] = MediaStore(conn, data_dir, directory=saved.get("media_directory", "media")).summary(row[0])
                checkpoint = data_dir / "logs" / f"checkpoint_{row[0]}.json"
                value["saved_checkpoint"] = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else None
                value["pause_requested"] = (data_dir / f"pause_{row[0]}.request").exists()
                results.append(value)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.pause is not None:
        with closing(connect(db, readonly=True)) as conn:
            row = conn.execute("SELECT status FROM collection_runs WHERE run_id=?", (args.pause,)).fetchone()
            if row is None or row[0] != "running":
                parser.error("Pause requires an open Run")
        atomic_write(data_dir / f"pause_{args.pause}.request", b"pause\n")
        print("Pause requested; current in-flight call will finish before stopping.")
        return 0
    if args.resume is not None and not db.exists():
        parser.error("Cannot resume a missing database")
    if args.resume is not None:
        # Reject closed Runs and durable finalization intents BEFORE initialize,
        # auth loading, Adapter construction, DPAPI, preflight or checkpoint I/O.
        from marketlens.research.run_finalization import intent_path
        with closing(connect(db, readonly=True)) as conn:
            row = conn.execute("SELECT status FROM collection_runs WHERE run_id=?", (args.resume,)).fetchone()
            if row is None:
                parser.error("Unknown Run")
            blocked = intent_path(data_dir, args.resume).exists()
            if row[0] != "running" or blocked:
                inspector = object.__new__(BaselineCollector)
                inspector.conn, inspector.data_dir = conn, data_dir
                value = inspector.summary(args.resume, "BLOCKED" if blocked and row[0] == "running" else "ALREADY_CLOSED",
                    "LocalFinalizationIntent;repeat_explicit_finalization" if blocked else None)
                print(json.dumps(value, ensure_ascii=False, indent=2))
                return 2 if blocked and row[0] == "running" else 0
    with single_writer(data_dir), closing(initialize(db)) as conn:
        if args.init_formal:
            print(json.dumps({"database": str(db), "state": "INITIALIZED_ONLY", **audit(conn)}))
            return 0
        if args.resume is None and not new_run_allowed(conn, args.new_project):
            parser.error("Run already exists; use --inspect or explicitly --resume RUN_ID. No automatic new Run.")
        adapter = XiaohongshuMCP(load_auth(args.auth_env_file), timeout=90,
            **({"detail_timeout": args.detail_timeout_seconds} if args.detail_timeout_seconds is not None else {}))
        cache = AccessCache(data_dir / "private_access", db) if formal else None
        if args.resume is not None:
            row = conn.execute("SELECT r.status,p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (args.resume,)).fetchone()
            if row is None:
                parser.error("Unknown Run")
            saved = json.loads(row["comment_config_json"])
            config = BaselineConfig(**{key: saved[key] for key in ("category", "pilot", "top_comment_target", "reply_target_per_post", "interval_seconds", "max_attempts", "media_directory", "media_max_attempts", "media_interval_seconds") if key in saved})
            if config.pilot == formal:
                parser.error("Run mode does not match selected database")
        collector = BaselineCollector(conn, adapter, data_dir, config, access_cache=cache,
                                      detail_retry_policy=retry_policy, reviewed_timeout=reviewed_timeout,
                                      detail_pacing_policy=pacing_policy)
        if args.resume is not None:
            if row[0] != "running":
                print(json.dumps(collector.summary(args.resume, "ALREADY_CLOSED"), ensure_ascii=False, indent=2))
                return 0
            if stage == "details" and not collector.phase(args.resume)["search_complete"]:
                print(json.dumps(collector.summary(args.resume, "BLOCKED", "SearchPhaseIncomplete")))
                return 2
        if cache is not None:
            try:
                cache.verify_available()
            except AccessCacheError:
                print(json.dumps({"result": "BLOCKED", "reason": "WindowsDPAPIUnavailable"}))
                return 2
        search_remaining = len(config.combinations) if args.resume is None else conn.execute(
            "SELECT count(*) FROM search_requests WHERE run_id=? AND status<>'succeeded'", (args.resume,)).fetchone()[0]
        print(json.dumps({"expected_requests": {"health": 1, "login": 1,
                          "search": search_remaining if stage != "details" else 0,
                          "detail_max_unique_posts": 0 if stage == "search" else config.detail_limit,
                          "extra_comments": 0}, "database": str(db), "stage": stage,
                          "resume": args.resume, "interval_seconds": 3, "timeout_seconds": 90,
                          "detail_timeout_seconds": args.detail_timeout_seconds or 90}, ensure_ascii=False), flush=True)
        if args.resume is not None:
            (data_dir / f"pause_{args.resume}.request").unlink(missing_ok=True)
        preflight, version = adapter.preflight()
        print(json.dumps({"preflight": preflight}, ensure_ascii=False), flush=True)
        if preflight["ready"]:
            try:
                summary = collector.run(version, run_id=args.resume, stage=stage)
            except KeyboardInterrupt:
                print("Interrupted; checkpoint retained. Inspect before resuming uncertain attempts.")
                return 130
        else:
            summary = {"result": "BLOCKED", "reason": "PreflightFailed", **audit(conn)}
        summary.update(preflight=preflight, generated_at=utc_now(),
                       detail_timeout_seconds=args.detail_timeout_seconds or 90,
                       detail_retry_policy=retry_policy.persisted() if retry_policy else None)
        path = data_dir / "logs" / f"{'baseline' if formal else 'pilot'}_{uuid4().hex}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["result"] in ("SUCCESS", "PARTIAL", "PAUSED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
