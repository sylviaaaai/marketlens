"""Focused offline checks; no disk SQLite and no real network."""
import io
import json
import socket
import copy
import pytest
from PIL import Image
from test_final_preflight import memory, prohibit_file_databases
from test_baseline_collection import SyntheticAdapter
from test_smoke import response
from marketlens.adapters.xiaohongshu_mcp import XiaohongshuMCP
from marketlens.research.baseline_config import BaselineConfig, SORT_METHODS
from marketlens.research.baseline_collector import BaselineCollector
from marketlens.research import image_preflight as media


class TinyFormal(BaselineConfig):
    @property
    def keywords(self): return ("synthetic_demo",)
    @property
    def sorts(self): return SORT_METHODS[:1]


@pytest.mark.parametrize("pilot,target,top,replies", [
    (False,20,30,15),(False,20,8,3),(False,20,0,0),(True,20,30,15),
    (False,25,30,15),(False,30,25,15)])
def test_http_parameters_and_untrimmed_storage(memory,tmp_path,pilot,target,top,replies):
    fixture = SyntheticAdapter(top,replies)
    class WireAdapter(XiaohongshuMCP):
        def __init__(self):
            super().__init__("synthetic_auth")
            self.bodies = []
        def search(self, keyword, filters):
            payload = fixture.search(keyword,filters).payload
            payload["data"]["feeds"] = payload["data"]["feeds"][:1]
            return response(payload)
        def request(self,method,path,body=None):
            self.bodies.append(copy.deepcopy(body))
            assert method == "POST" and path == "/api/v1/feeds/detail"
            return fixture.detail(body["feed_id"],body["xsec_token"])
    adapter = WireAdapter()
    cfg = TinyFormal(pilot=pilot,top_comment_target=target,interval_seconds=0,media_interval_seconds=0)
    collector = BaselineCollector(memory,adapter,tmp_path,cfg)
    result = collector.run("synthetic_demo")
    if not pilot:
        assert result["result"] == "PAUSED" and not adapter.bodies
        result = collector.run(run_id=result["run_id"], stage="details")
    assert result["result"] == "SUCCESS"
    assert len(adapter.bodies) == 1  # No refill even if fewer than the configured target.
    body = adapter.bodies[0]
    assert body["load_all_comments"] is (not pilot)
    if pilot:
        assert "comment_config" not in body
        assert cfg.persisted()["comment_mode"] == "default_returned_only"
    else:
        plan = json.loads(memory.execute("SELECT comment_config_json FROM collection_plans").fetchone()[0])
        assert plan["top_comment_target"] == target
        assert body["comment_config"] == plan["comment_config"] == {
            "max_comment_items":target,"click_more_replies":False,"max_replies_threshold":10,"scroll_speed":"normal"}
    counts = dict(memory.execute("SELECT comment_level,count(*) FROM comment_observations GROUP BY comment_level"))
    assert counts.get(1,0) == top and counts.get(2,0) == replies
    assert memory.execute("""SELECT count(*) FROM comment_observations child
        JOIN comment_observations parent ON parent.observation_id=child.parent_observation_id
        WHERE child.comment_level=2 AND parent.comment_level=1 AND child.post_id=parent.post_id
        AND child.collection_attempt_id=parent.collection_attempt_id""").fetchone()[0] == replies
    metadata = json.loads(memory.execute("SELECT response_summary_json FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0])
    if not pilot:
        assert metadata["comments"]["top_unique_ids"] == top
        assert metadata["comments"]["replies_returned"] == replies
        assert metadata["comments"]["reached_20_unique"] is (top >= 20)
        assert metadata["comments"]["target_unique"] == target
        assert metadata["comments"]["reached_target_unique"] is (top >= target)
        assert "has_more" in metadata["comments"]
    else:
        assert "comments" not in metadata


@pytest.mark.parametrize("url", ["http://sns-webpic-qc.xhscdn.com/a","https://127.0.0.1/a","https://evil.example/a", "https://sns-webpic-qc.xhscdn.com:444/a", "https://a@ sns-webpic-qc.xhscdn.com/a"])
def test_untrusted_urls(url,monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k: pytest.fail("must reject before DNS"))
    with pytest.raises(ValueError): media.validate_url(url)


def test_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(2,1,6,"",("192.168.1.1",443))])
    with pytest.raises(ValueError): media.validate_url("https://sns-webpic-qc.xhscdn.com/a")


def test_decode():
    data=io.BytesIO(); Image.new("RGB",(3,4)).save(data,format="PNG")
    assert media.decode(data.getvalue()) == {"format":"PNG","width":3,"height":4}
    with pytest.raises(OSError): media.decode(b"not an image")


def test_redirect_and_headers(monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(2,1,6,"",("8.8.8.8",443))])
    class Reply:
        status=302
        def getheader(self,key): return "https://127.0.0.1/private" if key == "Location" else None
    class Connection:
        def __init__(self,*a): pass
        def request(self,method,path,headers):
            assert set(headers)=={"User-Agent","Accept"}
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(media,"PinnedHTTPS",Connection)
    with pytest.raises(ValueError): media.fetch("https://sns-webpic-qc.xhscdn.com/a",[0])


def test_declared_size_limit(monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(2,1,6,"",("8.8.8.8",443))])
    class Reply:
        status=200
        def getheader(self,key): return {"Content-Type":"image/jpeg","Content-Length":str(media.IMAGE_LIMIT+1)}.get(key)
        def read1(self,n): pytest.fail("oversized body must not be read")
    class Connection:
        def __init__(self,*a): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(media,"PinnedHTTPS",Connection)
    with pytest.raises(ValueError): media.fetch("https://sns-webpic-qc.xhscdn.com/a",[0])


def test_unique_ids_are_separate_from_returned():
    from marketlens.research.normalizers import comment_stats
    from test_smoke import detail
    payload=detail("synthetic_demo")
    payload["data"]["data"]["comments"]={"list":[{"id":"same","subComments":[]}] * 21 + [{"subComments":17}],"hasMore":True}
    stats=comment_stats(payload,"synthetic_demo")
    assert stats["top_returned"]==22 and stats["top_unique_ids"]==1
    assert stats["top_missing_id"]==1 and stats["top_duplicates"]==20
    assert not stats["reached_20_unique"] and stats["has_more"]


def test_stream_total_budget(monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(2,1,6,"",("8.8.8.8",443))])
    class Reply:
        status=200
        def getheader(self,key): return "image/webp" if key == "Content-Type" else None
        def read1(self,n): return b"x"*n
    class Connection:
        sock=None
        def __init__(self,*a): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(media,"PinnedHTTPS",Connection)
    budget=[media.TOTAL_LIMIT-2]
    with pytest.raises(ValueError): media.fetch("https://sns-webpic-qc.xhscdn.com/a",budget)
    assert budget[0] == media.TOTAL_LIMIT


@pytest.mark.parametrize("value",[0,19,21,26,31,-1,None,True,False,20.0,"20"])
def test_formal_config_rejects_invalid_target(value):
    with pytest.raises(ValueError,match="Formal comment target"):
        BaselineConfig(pilot=False,top_comment_target=value)


def test_pilot_historical_soft_target_is_not_http_scroll_target():
    cfg=BaselineConfig(pilot=True,top_comment_target=10)
    assert cfg.detail_options == {}
    assert cfg.persisted()["load_all_comments"] is False
    assert "comment_config" not in cfg.persisted()
    assert cfg.persisted()["top_comment_target"] == 10


def test_statistics_distinguish_legacy_twenty_and_configured_target():
    from marketlens.research.normalizers import comment_stats
    from test_smoke import detail
    payload=detail("synthetic_demo")
    parents=[{"id":f"p_{i}","subComments":[{"id":"r_1"}]} for i in range(25)]
    payload["data"]["data"]["comments"]={"list":parents+[copy.deepcopy(parents[0]),{}],"hasMore":True}
    stats=comment_stats(payload,"synthetic_demo",target_unique=30)
    assert stats["top_returned"]==27 and stats["top_unique_ids"]==25
    assert stats["top_duplicates"]==1 and stats["top_missing_id"]==1
    assert stats["target_unique"]==30 and stats["reached_target_unique"] is False
    assert stats["reached_20_unique"] is True and stats["has_more"] is True
    assert stats["replies_returned"]==26 and stats["reply_unique_ids"]==1


@pytest.mark.parametrize("target",[20,25,30])
def test_cli_resume_keeps_saved_target_after_later_default_changes(memory,tmp_path,monkeypatch,target):
    from test_baseline_stages import cli_module
    from test_detail_auto_retry import Clock
    clock = Clock()
    monkeypatch.setattr("marketlens.research.baseline_collector.time.time", clock.time)
    monkeypatch.setattr("marketlens.research.baseline_collector.time.monotonic", clock.time)
    monkeypatch.setattr("marketlens.research.baseline_collector.time.sleep", clock.sleep)
    adapter=SyntheticAdapter(top=35,replies=15)
    cfg=TinyFormal(pilot=False,top_comment_target=target,interval_seconds=0,media_interval_seconds=0)
    old=BaselineCollector(memory,adapter,tmp_path,cfg)
    result=old.run("synthetic_demo")
    assert result["result"]=="PAUSED" and not adapter.detail_ids
    mapping=old.access.copy()
    initial_searches=len(adapter.calls)
    received=[]
    detail=adapter.detail
    def capture(sid,access,*,comment_target=None):
        received.append(comment_target)
        return detail(sid,access,comment_target=comment_target)
    monkeypatch.setattr(adapter,"detail",capture)
    monkeypatch.setattr(adapter,"preflight",lambda:({"ready":True},"synthetic_demo"))
    cli=cli_module(tmp_path,monkeypatch)
    placeholder=tmp_path/"existing_placeholder.json"
    placeholder.write_text("{}",encoding="utf-8")
    monkeypatch.setattr(cli,"FORMAL_DB",placeholder)
    monkeypatch.setattr(cli,"FORMAL_DATA",tmp_path)
    class Borrowed:
        def __getattr__(self,name): return getattr(memory,name)
        def close(self): pass
    monkeypatch.setattr(cli,"initialize",lambda path:Borrowed())
    monkeypatch.setattr(cli,"connect",lambda *a,**kw:Borrowed())
    monkeypatch.setattr(cli,"XiaohongshuMCP",lambda *a,**kw:adapter)
    monkeypatch.setattr(cli,"load_auth",lambda *a:None)
    class SavedSyntheticCache:
        def __init__(self,*a): pass
        def verify_available(self): pass
        def load(self,*a): return mapping.copy()
    monkeypatch.setattr(cli,"AccessCache",SavedSyntheticCache)
    later_default=30 if target!=30 else 20
    monkeypatch.setattr(cli,"BaselineConfig",lambda **kw:TinyFormal(**{"top_comment_target":later_default,**kw}))
    assert cli.main(["--formal","--resume",str(result["run_id"]),"--stage","details","--no-detail-pacing"])==0
    assert received==[target]*8 and len(adapter.calls)==initial_searches
    stats=[json.loads(r[0])["comments"] for r in memory.execute("SELECT response_summary_json FROM collection_attempts WHERE endpoint LIKE '%detail'")]
    assert all(s["target_unique"]==target and s["reached_target_unique"] for s in stats)
    assert memory.execute("SELECT count(*) FROM comment_observations WHERE comment_level=1").fetchone()[0]==35*8


def test_run_config_match_not_bypassed_for_different_target(memory,tmp_path):
    original=TinyFormal(pilot=False,top_comment_target=20,interval_seconds=0)
    run=BaselineCollector(memory,SyntheticAdapter(),tmp_path,original).create("synthetic_demo")
    adapter=SyntheticAdapter()
    changed=TinyFormal(pilot=False,top_comment_target=30,interval_seconds=0)
    with pytest.raises(ValueError,match="Run/config mismatch"):
        BaselineCollector(memory,adapter,tmp_path,changed).run(run_id=run,stage="search")
    assert not adapter.calls and not adapter.detail_ids


def test_inconsistent_historical_plan_is_rejected(memory,tmp_path):
    class InconsistentHistoricalPlan(TinyFormal):
        def persisted(self):
            result=super().persisted()
            result["comment_config"]["max_comment_items"]=20
            return result
    legacy=InconsistentHistoricalPlan(pilot=False,top_comment_target=30,interval_seconds=0)
    run=BaselineCollector(memory,SyntheticAdapter(),tmp_path,legacy).create("synthetic_demo")
    current=TinyFormal(pilot=False,top_comment_target=30,interval_seconds=0)
    adapter=SyntheticAdapter()
    with pytest.raises(ValueError,match="Run/config mismatch"):
        BaselineCollector(memory,adapter,tmp_path,current).run(run_id=run,stage="search")
    assert not adapter.calls and not adapter.detail_ids
