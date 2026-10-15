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


@pytest.mark.parametrize("pilot,top,replies", [(False,30,15),(False,8,3),(False,0,0),(True,30,15)])
def test_http_parameters_and_untrimmed_storage(memory,tmp_path,pilot,top,replies):
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
    cfg = TinyFormal(pilot=pilot,interval_seconds=0)
    collector = BaselineCollector(memory,adapter,tmp_path,cfg)
    result = collector.run("synthetic_demo")
    if not pilot:
        result = collector.run(run_id=result["run_id"], stage="details")
    assert result["result"] == "SUCCESS"
    assert len(adapter.bodies) == 1  # No refill even if fewer than 20.
    body = adapter.bodies[0]
    assert body["load_all_comments"] is (not pilot)
    if pilot:
        assert "comment_config" not in body
        assert cfg.persisted()["comment_mode"] == "default_returned_only"
    else:
        assert body["comment_config"] == cfg.persisted()["comment_config"] == {
            "max_comment_items":20,"click_more_replies":False,"max_replies_threshold":10,"scroll_speed":"normal"}
    counts = dict(memory.execute("SELECT comment_level,count(*) FROM comment_observations GROUP BY comment_level"))
    assert counts.get(1,0) == top and counts.get(2,0) == replies
    metadata = json.loads(memory.execute("SELECT response_summary_json FROM collection_attempts WHERE endpoint LIKE '%detail'").fetchone()[0])
    if not pilot:
        assert metadata["comments"]["top_unique_ids"] == top
        assert metadata["comments"]["replies_returned"] == replies
        assert metadata["comments"]["reached_20_unique"] is (top >= 20)
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
