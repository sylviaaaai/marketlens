"""Explicit two-stage collection. No arguments means no collection or DB writes."""
import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP, load_auth, utc_now
from marketlens.research.baseline_config import BaselineConfig
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
    """Existing runs require an explicit, nonempty new-Project choice."""
    return not conn.execute("SELECT 1 FROM collection_runs LIMIT 1").fetchone() or bool(explicit_category and explicit_category.strip())


@contextmanager
def single_writer(data_dir=DATA):
    """OS lock releases on crash; retain the harmless lock file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "collector.lock").open("a+b") as handle:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


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
    parser.add_argument("--project-id", type=int, help="Required ownership check for media resume and export")
    parser.add_argument("--output-dir", type=Path, help="Derived export directory, only with --export-run")
    parser.add_argument("--formal", action="store_true", help="Select formal DB for inspect/resume/pause")
    parser.add_argument("--new-project", metavar="CATEGORY", help="Explicitly create another Project with --start-baseline")
    parser.add_argument("--stage", choices=("search", "details"), help="Resume only this stage; formal default is search")
    parser.add_argument("--auth-env-file", type=Path, default=ROOT.parent / "data-source-lab/xiaohongshu-mcp/.env")
    args = parser.parse_args(argv)
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
                args.start_baseline, args.init_formal, args.pause is not None, args.preflight_validation, args.preflight_restore, resource_action)):
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
    with single_writer(data_dir), closing(initialize(db)) as conn:
        if args.init_formal:
            print(json.dumps({"database": str(db), "state": "INITIALIZED_ONLY", **audit(conn)}))
            return 0
        if args.resume is None and not new_run_allowed(conn, args.new_project):
            parser.error("Run already exists; use --inspect or explicitly --resume RUN_ID. No automatic new Run.")
        adapter = XiaohongshuMCP(load_auth(args.auth_env_file), timeout=90)
        cache = AccessCache(data_dir / "private_access", db) if formal else None
        if args.resume is not None:
            row = conn.execute("SELECT r.status,p.comment_config_json FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (args.resume,)).fetchone()
            if row is None:
                parser.error("Unknown Run")
            saved = json.loads(row["comment_config_json"])
            config = BaselineConfig(**{key: saved[key] for key in ("category", "pilot", "top_comment_target", "reply_target_per_post", "interval_seconds", "max_attempts", "media_directory", "media_max_attempts", "media_interval_seconds") if key in saved})
            if config.pilot == formal:
                parser.error("Run mode does not match selected database")
        collector = BaselineCollector(conn, adapter, data_dir, config, access_cache=cache)
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
                          "resume": args.resume, "interval_seconds": 3, "timeout_seconds": 90}, ensure_ascii=False), flush=True)
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
        summary.update(preflight=preflight, generated_at=utc_now())
        path = data_dir / "logs" / f"{'baseline' if formal else 'pilot'}_{uuid4().hex}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["result"] in ("SUCCESS", "PAUSED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
