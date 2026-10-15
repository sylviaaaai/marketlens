"""Final preflight: SQLite writes strictly :memory:, all network fixtures synthetic."""
import copy
import importlib.util
import json
import sqlite3

import pytest

from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP
from marketlens.research.database import DDL, ROOT, assert_integrity
from marketlens.research.baseline_config import BaselineConfig
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.collection_pipeline import insert
from marketlens.research.preflight_validation import search_stats, comment_stats
import test_baseline_collection as prior
from test_smoke import detail, response


@pytest.fixture(autouse=True)
def prohibit_file_databases(monkeypatch):
    original = sqlite3.connect
    def memory_only(database, *args, **kwargs):
        if database != ":memory:":
            raise AssertionError("Final preflight forbids creating or opening file DB in tests")
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", memory_only)


@pytest.fixture
def memory():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA recursive_triggers=ON")
    yield conn
    conn.close()


@pytest.mark.parametrize("name", [
    "test_full_design_and_all_hits", "test_failure_stops_and_same_run_resume",
    "test_process_restart_cannot_invent_missing_tokens", "test_unknown_inflight_never_retried",
    "test_config_mismatch_rejected", "test_access_restriction_stops_even_on_resume",
    "test_retry_budget_cannot_reset_on_resume", "test_missing_early_token_does_not_change_fixed_sample",
    "test_detail_failure_preserves_successful_observations"])
def test_existing_contracts_in_memory(memory, tmp_path, name):
    getattr(prior, name)((memory, tmp_path))


@pytest.mark.parametrize("top,replies", [(30,15),(8,3),(0,0)])
def test_comment_storage_contracts(memory, tmp_path, top, replies):
    prior.test_pilot_soft_budget_and_repeated_run((memory, tmp_path), top, replies)


def test_atomic_commit_reconcile(memory, tmp_path, monkeypatch):
    prior.test_atomic_response_commit_reconciles_without_duplicate_call((memory, tmp_path), monkeypatch)


def test_stage_isolation_and_pause(memory, tmp_path):
    adapter = prior.SyntheticAdapter()
    c = BaselineCollector(memory, adapter, tmp_path, BaselineConfig(interval_seconds=0))
    run = c.create("synthetic_demo")
    assert c.run(run_id=run, stage="details")["reason"] == "SearchPhaseIncomplete"
    assert not adapter.calls and not adapter.detail_ids
    result = c.run(run_id=run, stage="search")
    assert result["result"] == "PAUSED" and len(adapter.calls) == 4 and not adapter.detail_ids
    state = json.loads((tmp_path/f"logs/checkpoint_{run}.json").read_text(encoding="utf-8"))
    assert state["search_complete"] and state["search_succeeded"] == 4
    (tmp_path/f"pause_{run}.request").write_text("pause",encoding="utf-8")
    assert c.run(run_id=run, stage="details")["result"] == "PAUSED"
    assert not adapter.detail_ids
    (tmp_path/f"pause_{run}.request").unlink()
    assert c.run(run_id=run,stage="details")["result"] == "SUCCESS"
    assert len(adapter.calls) == 4 and len(adapter.detail_ids) == 5
    before=c.summary(run)["table_counts"]
    c.run(run_id=run,stage="details")
    assert c.summary(run)["table_counts"] == before


def test_multi_project_shared_identity_isolated_evidence_and_datasets(memory,tmp_path):
    first = BaselineCollector(memory,prior.SyntheticAdapter(top=8,replies=3),tmp_path,
                              BaselineConfig(category="粉底液",interval_seconds=0))
    a=first.run("synthetic_demo")
    second = BaselineCollector(memory,prior.SyntheticAdapter(top=30,replies=15),tmp_path,
                               BaselineConfig(category="护手霜",interval_seconds=0))
    b=second.run("synthetic_demo",stage="search")
    assert a["project_id"] != b["project_id"] and a["run_id"] != b["run_id"]
    assert b["details"]["unrequested"] == 8 and not b["comment_level_counts"]
    assert b["table_counts"]["post_snapshots"] == 0 and b["table_counts"]["search_hits"] == 36
    b=second.run(run_id=b["run_id"],stage="details")
    assert b["result"] == "SUCCESS"
    assert memory.execute("SELECT count(*) FROM posts").fetchone()[0] == 8
    assert memory.execute("SELECT count(*) FROM search_hits").fetchone()[0] == 72
    assert first.summary(a["run_id"])["comment_level_counts"] == {"1":40,"2":15}
    assert second.summary(b["run_id"])["comment_level_counts"] == {"1":150,"2":75}
    assert first.summary(a["run_id"])["table_counts"]["raw_artifacts"] == 9
    for result, category in ((a,"粉底液"),(b,"护手霜")):
        rows=memory.execute("SELECT q.keyword,p.project_id FROM search_requests q JOIN collection_runs r USING(run_id) JOIN collection_plans p USING(plan_id) WHERE q.run_id=?",(result["run_id"],)).fetchall()
        assert all(row[0].startswith(category) and row[1]==result["project_id"] for row in rows)
        for version in ("v1","v2"):
            dataset=insert(memory,"dataset_versions",project_id=result["project_id"],dataset_name="synthetic_demo",version=version,source_run_id=result["run_id"])
            snapshot=memory.execute("SELECT s.snapshot_id,s.post_id FROM post_snapshots s JOIN collection_attempts x ON x.attempt_id=s.collection_attempt_id JOIN collection_tasks t USING(task_id) WHERE t.run_id=? LIMIT 1",(result["run_id"],)).fetchone()
            insert(memory,"dataset_posts",dataset_id=dataset,post_id=snapshot[1],snapshot_id=snapshot[0])
    with pytest.raises(sqlite3.IntegrityError):
        insert(memory,"dataset_versions",project_id=a["project_id"],dataset_name="synthetic_demo",version="wrong",source_run_id=b["run_id"])
    assert memory.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]==4
    assert assert_integrity(memory)["integrity_check"]==["ok"]


@pytest.mark.parametrize("target",[20,25,30,None])
def test_comment_parameter_mapping(target):
    adapter=XiaohongshuMCP("synthetic_auth")
    bodies=[]
    adapter.request=lambda method,path,body:bodies.append(body)
    adapter.detail("synthetic_demo","synthetic_access",comment_target=target)
    body=bodies[0]
    assert body["load_all_comments"] == (target is not None)
    if target:
        assert body["comment_config"]=={"max_comment_items":target,"click_more_replies":False,"max_replies_threshold":10,"scroll_speed":"normal"}


def test_default_cli_no_network_no_database(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location("preflight_cli",ROOT/"scripts/phase1d_baseline_collect.py")
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    monkeypatch.setattr(cli,"XiaohongshuMCP",lambda *a,**k:pytest.fail("network construction forbidden"))
    monkeypatch.setattr(cli,"initialize",lambda *a,**k:pytest.fail("file DB forbidden"))
    assert cli.main([])==0
    with pytest.raises(SystemExit):
        cli.main(["--new-project","护手霜"])


def test_cli_new_project_guard_with_existing_run(memory,tmp_path):
    spec=importlib.util.spec_from_file_location("multi_project_cli",ROOT/"scripts/phase1d_baseline_collect.py")
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    assert cli.new_run_allowed(memory)
    c=BaselineCollector(memory,prior.SyntheticAdapter(),tmp_path,BaselineConfig(interval_seconds=0))
    c.run("synthetic_demo")
    assert not cli.new_run_allowed(memory)
    assert not cli.new_run_allowed(memory," ")
    assert cli.new_run_allowed(memory,"护手霜")


def test_metrics_do_not_claim_missing_sort_evidence():
    stats=search_stats({"success":True,"data":{"feeds":[{"id":"synthetic_demo","noteCard":{"type":"normal","interactInfo":{"likedCount":"1.2万"}}}]}})
    assert stats["metrics"]["likes"]["available"]==1
    assert stats["metrics"]["comments"]["available"]==0 and stats["publish_time_available"]==0


def test_comment_dedup_and_raw_retention():
    payload=detail("synthetic_demo")
    parent={"id":"parent","subComments":[{"id":"reply"},{"id":"reply"}]}
    payload["data"]["data"]["comments"]["list"]=[copy.deepcopy(parent),copy.deepcopy(parent)]
    before=copy.deepcopy(payload)
    stats=comment_stats(payload,"synthetic_demo")
    assert stats["top_returned"]==2 and stats["top_unique_ids"]==1
    assert stats["replies_returned"]==4 and stats["reply_unique_ids"]==1
    assert payload==before
