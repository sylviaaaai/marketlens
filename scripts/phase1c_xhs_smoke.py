"""Run a bounded live experiment, or inspect the research DB without network I/O."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP, load_auth, utc_now
from marketlens.research.collection_pipeline import CollectionPipeline, audit
from marketlens.research.database import DEFAULT_DB, connect, initialize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Read-only DB/artifact audit; no requests")
    parser.add_argument("--init-only", action="store_true", help="Initialize/verify candidate schema; no requests")
    parser.add_argument("--reset", action="store_true", help="Archive exact smoke DB before creating a fresh one; retain raw files")
    parser.add_argument("--auth-env-file", type=Path, default=ROOT.parent / "data-source-lab/xiaohongshu-mcp/.env")
    args = parser.parse_args()
    if args.inspect and (args.reset or args.init_only):
        parser.error("--inspect cannot be combined with --reset or --init-only")
    if args.inspect:
        with closing(connect(DEFAULT_DB, readonly=True)) as conn:
            print(json.dumps(audit(conn), ensure_ascii=False, indent=2))
        return 0
    data_dir = DEFAULT_DB.parent
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    with closing(initialize(reset=args.reset)) as conn:
        if args.init_only:
            print(json.dumps(audit(conn), ensure_ascii=False, indent=2))
            return 0
        adapter = XiaohongshuMCP(load_auth(args.auth_env_file))
        preflight, version = adapter.preflight()
        print(json.dumps({"preflight": preflight}, ensure_ascii=False), flush=True)
        if preflight["ready"]:
            summary = CollectionPipeline(conn, adapter, data_dir).run(version)
        else:
            summary = {"result": "BLOCKED", "reason": "PreflightFailed", "events": [], **audit(conn)}
        summary.update(preflight=preflight, generated_at=utc_now())
        evidence_file = data_dir / "logs" / f"experiment_{uuid4().hex}.json"
        evidence_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Local evidence:", evidence_file)
        return 0 if summary["result"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
