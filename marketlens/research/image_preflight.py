"""Bounded Pilot media validation only; no collection scheduler or database writes."""
import hashlib
import http.client
import io
import ipaddress
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import time
from urllib.parse import urlsplit, urlunsplit, urljoin
from PIL import Image

HOSTS = frozenset({"sns-webpic-qc.xhscdn.com"})
IMAGE_LIMIT = 15 * 1024 * 1024
TOTAL_LIMIT = 60 * 1024 * 1024


def validate_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password
            or p.port not in (None, 443) or p.fragment or any(ord(c) < 32 for c in url)):
        raise ValueError("UntrustedMediaURL")
    addresses = sorted({r[4][0] for r in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("NonPublicMediaAddress")
    return p, addresses[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def decode(data):
    try:
        opened = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        raise ValueError("ImagePixelLimit") from exc
    with opened as im:
        if im.width * im.height > 40_000_000:
            raise ValueError("ImagePixelLimit")
        result = dict(format=im.format, width=im.width, height=im.height)
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
    return result


def fetch(url, budget):
    """One attempt, up to three checked redirects; 30s total, 8s socket timeout."""
    deadline = time.monotonic() + 30
    trace = []
    for hop in range(4):
        p, address = validate_url(url)
        conn = PinnedHTTPS(p.hostname, address, min(8, max(.1, deadline-time.monotonic())))
        try:
            conn.request("GET", urlunsplit(("", "", p.path or "/", p.query, "")),
                         headers={"User-Agent": "MarketLens-Image-Preflight/1.0", "Accept": "image/*"})
            response = conn.getresponse()
            trace.append({"host": p.hostname, "status": response.status,
                          "content_type": response.getheader("Content-Type"),
                          "cache_control": response.getheader("Cache-Control"),
                          "expires": response.getheader("Expires")})
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location or hop == 3:
                    raise ValueError("RedirectLimit")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                return None, trace
            if not (response.getheader("Content-Type") or "").lower().startswith("image/"):
                raise ValueError("NonImageContentType")
            limit = min(IMAGE_LIMIT, TOTAL_LIMIT-budget[0])
            length = response.getheader("Content-Length")
            if limit <= 0 or (length and int(length) > limit):
                raise ValueError("ByteBudgetExceeded")
            chunks, size = [], 0
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ImageDeadline")
                if conn.sock:
                    conn.sock.settimeout(min(8, remaining))
                if size == limit:
                    raise ValueError("ByteBudgetReached")
                block = response.read1(min(65536, limit-size))
                if not block:
                    break
                size += len(block)
                budget[0] += len(block)
                if size > limit:
                    raise ValueError("ByteBudgetExceeded")
                chunks.append(block)
            if length and size != int(length):
                raise ValueError("IncompleteImageBody")
            return b"".join(chunks), trace
        finally:
            conn.close()
    raise ValueError("RedirectLimit")


def candidates(database):
    conn = sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        groups = conn.execute("SELECT snapshot_id,post_id,count(*) n FROM post_media JOIN post_snapshots USING(snapshot_id) GROUP BY snapshot_id ORDER BY n DESC,snapshot_id").fetchall()
        multi = next(r for r in groups if 1 < r["n"] <= 9)
        few = next(r for r in reversed(groups) if r["n"] == 1 and r["post_id"] != multi["post_id"])
        result = []
        for group in (multi, few):
            post, snap = group["post_id"], group["snapshot_id"]
            hit = conn.execute("SELECT search_hit_id,card_cover_json FROM search_hits WHERE post_id=? AND card_cover_json IS NOT NULL ORDER BY search_hit_id LIMIT 1", (post,)).fetchone()
            if hit:
                cover = json.loads(hit["card_cover_json"])
                result.append(dict(post_id=post, snapshot_id=snap, media_id=None, position=None,
                                   search_hit_id=hit["search_hit_id"], role="cover", source_field="card_cover_json.urlDefault",
                                   url=cover.get("urlDefault") or cover.get("url")))
            for row in conn.execute("SELECT * FROM post_media WHERE snapshot_id=? ORDER BY position", (snap,)):
                result.append(dict(post_id=post, snapshot_id=snap, media_id=row["media_id"], position=row["position"],
                                   role="body", source_field="post_media.source_url", url=row["source_url"],
                                   thumbnail_differs=row["thumbnail_url"] != row["source_url"],
                                   metadata_dimensions={k:json.loads(row["media_raw_json"]).get(k) for k in ("width","height")}))
        if len(result) > 12:
            raise ValueError("ImageCountLimit")
        return result
    finally:
        conn.close()


def run(database, directory, *, retry_blocked=False):
    directory = Path(directory)
    manifest = directory / "manifest.json"
    blocked = None
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf8"))
        if not (retry_blocked and not previous.get("blocked_pass") and previous.get("received_bytes") == 0
                and len(previous["results"]) == 12
                and all(r["attempts"] == [{"error":"PermissionError"}] for r in previous["results"])):
            raise ValueError("ExistingImageValidation; inspect before any replay")
        blocked = previous["results"]
    items = candidates(database)
    directory.mkdir(parents=True, exist_ok=True)
    state = {"purpose":"isolated_technical_validation", "started_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "limits":{"posts":2,"images":12,"image_bytes":IMAGE_LIMIT,"total_bytes":TOTAL_LIMIT}, "results":[]}
    if blocked:
        state["blocked_pass"] = blocked
    budget = [0]
    def save():
        state["received_bytes"] = budget[0]
        manifest.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf8")
    save()
    for index, source in enumerate(items, 1):
        item = dict(source)
        raw = item.pop("url")
        parsed = urlsplit(raw)
        url = urlunsplit(parsed._replace(scheme="https")) if parsed.scheme == "http" else raw
        item.update(url_sha256=hashlib.sha256(raw.encode()).hexdigest(), host=parsed.hostname,
                    https_upgrade=parsed.scheme == "http", variant=parsed.path.rsplit("!",1)[-1] if "!" in parsed.path else None,
                    state="inflight", attempts=[])
        state["results"].append(item)
        save()
        start = time.monotonic()
        for attempt in range(1 if blocked else 2):
            try:
                data, trace = fetch(url, budget)
                item["attempts"].append({"trace":trace})
                if data is None:
                    item["state"] = "http_failure"
                    if trace[-1]["status"] >= 500 and attempt == 0 and not blocked:
                        time.sleep(1)
                        continue
                    break
                info = decode(data)
                digest = hashlib.sha256(data).hexdigest()
                extension = {"JPEG":"jpg","PNG":"png","WEBP":"webp","GIF":"gif"}.get(info["format"], "img")
                name = f"post_{item['post_id']}_snapshot_{item['snapshot_id']}_{item['role']}_{item.get('position') or 0}_{digest[:12]}.{extension}"
                with (directory/name).open("xb") as output:
                    output.write(data)
                item.update(state="success",file=name,bytes=len(data),sha256=digest,decode_ok=True,**info)
                break
            except (OSError, ValueError, http.client.HTTPException) as exc:
                item["attempts"].append({"error":type(exc).__name__})
                item["state"] = "failure"
                if isinstance(exc, (TimeoutError, ConnectionError)) and attempt == 0 and not blocked:
                    time.sleep(1)
                    continue
                break
        item["elapsed_seconds"] = round(time.monotonic()-start,3)
        save()
        print(json.dumps({k:item.get(k) for k in ("post_id","role","position","state","bytes","width","height","elapsed_seconds")}),flush=True)
        time.sleep(.5)
    state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save()
    return state
