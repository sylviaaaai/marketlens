"""Synthetic responses, in-memory SQLite, generated image bytes; no live I/O."""
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
import pytest

from test_final_preflight import memory, prohibit_file_databases
from test_baseline_collection import SyntheticAdapter
from test_smoke import response
from marketlens.research.baseline_config import BaselineConfig, SORT_METHODS
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research.media_store import MediaStore, media_rows
from marketlens.research.run_export import read_run, export_run
from marketlens.research.database import assert_integrity


class TinyFormal(BaselineConfig):
    @property
    def keywords(self): return ("synthetic_demo",)
    @property
    def sorts(self): return SORT_METHODS[:1]


class Adapter(SyntheticAdapter):
    def __init__(self, label="中文 Brand-X\n第二行", missing=False):
        super().__init__(30, 15)
        self.label, self.missing = label, missing

    def search(self, keyword, filters):
        payload = super().search(keyword, filters).payload
        payload["data"]["feeds"] = payload["data"]["feeds"][:3 if self.missing else 2]
        if self.missing:
            payload["data"]["feeds"][-1].pop("xsecToken")
        return response(payload)

    def detail(self, sid, token, *, comment_target=None):
        assert comment_target == 20
        result = super().detail(sid, token)
        note = result.payload["data"]["data"]["note"]
        note.update(title=self.label, desc="  原文\nYSL / EL 缩写  ", time=1702195200000,
                    imageList=[{"urlDefault":"https://sns-webpic-qc.xhscdn.com/synthetic/a?signature=secret"},
                               {"urlDefault":"https://sns-webpic-qc.xhscdn.com/synthetic/a?signature=secret"},
                               {"urlDefault":"https://sns-webpic-qc.xhscdn.com/synthetic/b"}])
        # Source URL sanitizer removes named signature parameters in recorded evidence.
        return result


@pytest.fixture
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (12, 9), "blue").save(output, format="PNG")
    return output.getvalue()


def setup_collection(memory, tmp_path, image_bytes, *, adapter=None, config=None, fetcher=None):
    calls = []
    def fetch(url, budget):
        calls.append(url)
        assert not memory.in_transaction
        assert memory.execute("SELECT count(*) FROM post_snapshots").fetchone()[0] > 0
        assert memory.execute("SELECT count(*) FROM comment_observations").fetchone()[0] >= 45
        return image_bytes, [{"status":200,"content_type":"image/png"}]
    store = MediaStore(memory, tmp_path, interval_seconds=0, fetcher=fetcher or fetch)
    c = BaselineCollector(memory, adapter or Adapter(), tmp_path,
                          config or TinyFormal(pilot=False, interval_seconds=0, media_interval_seconds=0), media_store=store)
    return c, store, calls


def complete(c):
    search = c.run("synthetic_demo")
    assert search["result"] == "PAUSED"
    return c.run(run_id=search["run_id"], stage="details")


def test_timely_content_addressing_and_associations(memory,tmp_path,image_bytes):
    c,store,calls = setup_collection(memory,tmp_path,image_bytes)
    original=c.adapter.detail
    def detail(*a,**kw):
        if c.adapter.detail_ids:
            assert len(store.states(1)) == 3
            assert store.summary(1)["verified"] == 3
        return original(*a,**kw)
    c.adapter.detail=detail
    result=complete(c)
    assert result["details"]["succeeded"] == 2
    assert len(calls)==2  # Same URL reused; different URL needs fetching before hash dedup.
    assert store.summary(1)["verified"] == 6
    digest=hashlib.sha256(image_bytes).hexdigest()
    files=list((tmp_path/"media/images").glob("*/*.png"))
    assert files == [tmp_path/f"media/images/{digest[:2]}/{digest}.png"]
    states=store.states(1)
    assert len({s["post_id"] for s in states})==2
    assert len({s["relative_path"] for s in states})==1
    assert [s["position"] for s in states]==[1,2,3,1,2,3]
    assert all(s["post_media_id"] and s["snapshot_id"] for s in states)
    store.process(1)
    assert len(calls)==2
    assert memory.execute("SELECT count(*) FROM post_media WHERE local_path IS NOT NULL").fetchone()[0]==0
    assert assert_integrity(memory)["integrity_check"]==["ok"]


def test_fifty_search_barrier_and_no_media_during_search(memory,tmp_path,image_bytes):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes,
        config=BaselineConfig(pilot=False,interval_seconds=0,media_interval_seconds=0))
    run=c.create("synthetic_demo")
    assert c.run(run_id=run,stage="details")["reason"]=="SearchPhaseIncomplete"
    assert not calls
    c.run(run_id=run)  # Formal default ends after all 50 successful searches.
    assert len(c.adapter.calls)==50 and not c.adapter.detail_ids and not calls
    assert not (tmp_path/"media").exists()
    assert c.run(run_id=run,stage="details")["result"]=="SUCCESS"
    assert calls


@pytest.mark.parametrize("status",[403,404,429,500])
def test_http_failure_isolation_and_lifetime_budget(memory,tmp_path,image_bytes,status):
    calls=[]
    def failed(url,budget):
        calls.append(url)
        return None,[{"status":status}]
    c,store,_=setup_collection(memory,tmp_path,image_bytes,fetcher=failed)
    result=complete(c)
    assert result["result"]=="SUCCESS" and result["details"]["succeeded"]==2
    assert memory.execute("SELECT count(*) FROM comment_observations").fetchone()[0]==90
    assert store.summary(1)["unfinished"]==6
    assert len(calls)==(12 if status==500 else 6)
    store.process(1)
    assert len(calls)==(12 if status==500 else 6)


@pytest.mark.parametrize("point",["before_promote","before_success"])
def test_staged_recovery_without_network(memory,tmp_path,image_bytes,monkeypatch,point):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes)
    if point=="before_promote":
        monkeypatch.setattr(store,"promote",lambda event: (_ for _ in ()).throw(KeyboardInterrupt()))
    else:
        append=store.append
        def crash(run,event):
            if event["status"]=="success": raise KeyboardInterrupt()
            return append(run,event)
        monkeypatch.setattr(store,"append",crash)
    with pytest.raises(KeyboardInterrupt): complete(c)
    assert len(calls)==1 and store.states(1)[0]["status"]=="staged"
    # Only first snapshot exists. Its two URL variants need one remaining real fixture call.
    other=MediaStore(memory,tmp_path,interval_seconds=0,fetcher=store.fetcher)
    other.process(1)
    assert len(calls)==2 and other.summary(1)["verified"]==3
    assert not list((tmp_path/"media/images/.partial").glob("*.part"))


def test_partial_download_not_success_and_independent_resume(memory,tmp_path,image_bytes):
    calls=[]
    def interrupted(url,budget):
        calls.append(url)
        raise KeyboardInterrupt()
    c,store,_=setup_collection(memory,tmp_path,image_bytes,fetcher=interrupted)
    with pytest.raises(KeyboardInterrupt): complete(c)
    assert store.summary(1)["verified"]==0
    partial=tmp_path/"media/images/.partial/orphan.part"
    partial.parent.mkdir(parents=True,exist_ok=True);partial.write_bytes(b"partial")
    store.fetcher=lambda url,budget:(image_bytes,[{"status":200}])
    before=(len(c.adapter.calls),len(c.adapter.detail_ids))
    store.process(1)
    assert store.summary(1)["verified"]==3
    assert before==(len(c.adapter.calls),len(c.adapter.detail_ids))
    assert partial.read_bytes()==b"partial"  # Never treated as a verified resource.


@pytest.mark.parametrize("damage",["missing","hash"])
def test_missing_or_corrupt_success_detected(memory,tmp_path,image_bytes,damage):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes);complete(c)
    target=store.path(store.states(1)[0]["relative_path"])
    if damage=="missing": target.unlink()
    else: target.write_bytes(b"corrupt")
    assert store.summary(1)["verified"]==0
    store.process(1)
    assert len(calls)==2 and store.summary(1)["states"]=={"needs_verification":6}
    view=read_run(memory,1,1,tmp_path)
    assert all(not r["verified_local"] and r["relative_path"] is None for r in view["media"])


def test_manifest_torn_tail_and_wrong_association(memory,tmp_path,image_bytes):
    c,store,_=setup_collection(memory,tmp_path,image_bytes);complete(c)
    with store.journal(1).open("ab") as f: f.write(b'{"incomplete":')
    assert all(s["journal_torn_tail"] for s in store.states(1))
    event=store.read(1)[0][-1]
    store.append(1,event)
    assert not store.read(1)[2]
    assert list(store.journal(1).parent.glob("*.torn_*"))
    store.append(1,{**event,"post_id":999})
    assert not store.states(1)[-1]["verified_local"]
    with pytest.raises(ValueError,match="AssociationMismatch"): store.process(1)


def test_file_io_failure_does_not_fail_detail(memory,tmp_path,image_bytes,monkeypatch):
    c,store,_=setup_collection(memory,tmp_path,image_bytes)
    monkeypatch.setattr(store,"process",lambda *a,**kw: (_ for _ in ()).throw(OSError("synthetic disk full")))
    result=complete(c)
    assert result["result"]=="SUCCESS" and result["details"]["succeeded"]==2
    assert len(result["media_errors"])==2 and result["media_downloads"]["unfinished"]==6


def test_exports_isolate_shared_posts_across_projects_and_are_reproducible(memory,tmp_path,image_bytes):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes,adapter=Adapter("研究一"))
    first=complete(c)
    second,store2,calls2=setup_collection(memory,tmp_path,image_bytes,adapter=Adapter("研究二"))
    last=complete(second)
    assert calls2==[]  # Cross-Run same-URL cache verified against SQLite and file hash.
    a=read_run(memory,first["project_id"],first["run_id"],tmp_path)
    b=read_run(memory,last["project_id"],last["run_id"],tmp_path)
    assert {p["post_id"] for p in a["posts"]}=={p["post_id"] for p in b["posts"]}
    assert not {p["snapshot_id"] for p in a["posts"]}&{p["snapshot_id"] for p in b["posts"]}
    assert {p["title"] for p in a["posts"]}=={"研究一"}
    assert {p["title"] for p in b["posts"]}=={"研究二"}
    assert all(p["published_at"]=="2023-12-10T08:00:00.000Z" for p in a["posts"])
    assert a["posts"][0]["description"]=="  原文\nYSL / EL 缩写  "
    assert len(a["comments"])==90
    parents={x["observation_id"]:x for x in a["comments"] if x["comment_level"]==1}
    assert all(x["parent_observation_id"] in parents and x["parent_content"]==parents[x["parent_observation_id"]]["content"]
               for x in a["comments"] if x["comment_level"]==2)
    snapshots={p["snapshot_id"] for p in a["posts"]}
    assert all(m["snapshot_id"] in snapshots for m in a["media"])
    with pytest.raises(ValueError): read_run(memory,first["project_id"],last["run_id"],tmp_path)
    manifest=export_run(memory,1,1,tmp_path)
    out=tmp_path/"exports/run_1"
    before={p.name:p.read_bytes() for p in out.iterdir()}
    assert export_run(memory,1,1,tmp_path)==manifest
    assert before=={p.name:p.read_bytes() for p in out.iterdir()}
    assert not memory.in_transaction
    assert b"secret" not in b"".join(before.values()) and b"https://" not in b"".join(before.values())
    assert memory.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]==0
    with pytest.raises(ValueError): export_run(memory,2,2,tmp_path,out)
    (out/"export_manifest.json").unlink()
    with pytest.raises(ValueError): export_run(memory,2,2,tmp_path,out)
    export_run(memory,1,1,tmp_path,out)
    with pytest.raises(ValueError): export_run(memory,1,1,tmp_path,tmp_path/"raw")


def test_unrequested_candidates_not_exported_and_read_only_no_directories(memory,tmp_path,image_bytes):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes,adapter=Adapter(missing=True))
    search=c.run("synthetic_demo")
    before=set(tmp_path.rglob("*"))
    assert read_run(memory,1,1,tmp_path)=={"posts":[],"comments":[],"media":[]}
    assert before==set(tmp_path.rglob("*")) and not calls
    result=c.run(run_id=search["run_id"],stage="details")
    assert result["details"]["unrequested"]==1
    view=read_run(memory,1,1,tmp_path)
    assert len(view["posts"])==2 and len(view["media"])==6


def test_local_path_trigger_unchanged(memory,tmp_path,image_bytes):
    import sqlite3
    c,store,_=setup_collection(memory,tmp_path,image_bytes);complete(c)
    path=store.states(1)[0]["relative_path"]
    memory.execute("UPDATE post_media SET local_path=? WHERE media_id=1",(path,))
    with pytest.raises(sqlite3.IntegrityError):
        memory.execute("UPDATE post_media SET local_path='changed' WHERE media_id=1")


@pytest.mark.parametrize("directory",["../outside","E:/media","/media","raw","logs/x","exports","private_access","."])
def test_bad_media_config(directory):
    with pytest.raises(ValueError): BaselineConfig(pilot=False,media_directory=directory)


def test_cli_media_and_export_use_readonly_db_without_mcp(memory,tmp_path,image_bytes,monkeypatch):
    from test_baseline_stages import cli_module
    c,store,calls=setup_collection(memory,tmp_path,image_bytes);complete(c)
    cli=cli_module(tmp_path,monkeypatch)
    existing=tmp_path/"existing_placeholder.json"
    existing.write_text("{}",encoding="utf8")
    monkeypatch.setattr(cli,"FORMAL_DB",existing)
    monkeypatch.setattr(cli,"FORMAL_DATA",tmp_path)
    class Borrowed:
        def __getattr__(self,name): return getattr(memory,name)
        def close(self): pass
    def readonly(path,*,readonly=False):
        assert readonly
        return Borrowed()
    monkeypatch.setattr(cli,"connect",readonly)
    monkeypatch.setattr(cli,"XiaohongshuMCP",lambda *a,**k:pytest.fail("No MCP in media/export"))
    monkeypatch.setattr(cli,"initialize",lambda *a,**k:pytest.fail("No DB initialization"))
    assert cli.main(["--formal","--resume-media","1","--project-id","1"])==0
    assert cli.main(["--formal","--export-run","1","--project-id","1"])==0
    assert len(calls)==2
    assert (tmp_path/"exports/run_1/posts.jsonl").exists()
    with pytest.raises(SystemExit): cli.main(["--resume-media","1","--project-id","1"])


def test_timeout_attempts_survive_restart(memory,tmp_path,image_bytes):
    calls=[]
    def failing(url,budget):
        calls.append(url)
        if len(calls)==2: raise KeyboardInterrupt()
        raise TimeoutError()
    c,store,_=setup_collection(memory,tmp_path,image_bytes,fetcher=failing)
    with pytest.raises(KeyboardInterrupt): complete(c)
    assert store.states(1)[0]["attempts"]==2
    other=MediaStore(memory,tmp_path,interval_seconds=0,fetcher=lambda *a: (_ for _ in ()).throw(TimeoutError()))
    other.process(1)
    assert all(s["attempts"]==2 and not s["verified_local"] for s in other.states(1))


def test_committed_snapshot_missing_success_event_recovers_after_os_error(memory,tmp_path,image_bytes,monkeypatch):
    c,store,calls=setup_collection(memory,tmp_path,image_bytes)
    append=store.append
    def error(run,event):
        if event["status"]=="success": raise OSError("synthetic disk failure")
        return append(run,event)
    monkeypatch.setattr(store,"append",error)
    result=complete(c)
    assert result["details"]["succeeded"]==2
    monkeypatch.setattr(store,"append",append)
    store.process(1)
    assert store.summary(1)["verified"]==6


def test_missing_url_preserves_media_metadata(memory,tmp_path,image_bytes):
    adapter=Adapter()
    original=adapter.detail
    def missing(*a,**kw):
        result=original(*a,**kw)
        result.payload["data"]["data"]["note"]["imageList"]=[{}]
        return result
    adapter.detail=missing
    c,store,calls=setup_collection(memory,tmp_path,image_bytes,adapter=adapter)
    complete(c)
    assert not calls and store.summary(1)["unfinished"]==2
    assert all(s["error"]=="MissingURLOrUnsupportedMedia" for s in store.states(1))
