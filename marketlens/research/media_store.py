"""Content-addressed body images over immutable SQLite media observations.

One writer (the existing collector CLI lock) per data root. No DB writes here.
Journal events are fsynced before promotion; only a torn final line is repairable.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from marketlens.adapters.xiaohongshu_mcp import utc_now
from . import image_preflight
from .access_cache import atomic_write

FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}
IDENTITY = ("run_id", "post_id", "snapshot_id", "post_media_id", "position", "url_fingerprint")
FILE_FIELDS = ("relative_path", "sha256", "format", "width", "height", "bytes", "downloaded_at")


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or path == PurePosixPath("."):
        raise ValueError("InvalidRelativeMediaPath")
    return path


def run_project(conn, run_id, project_id=None):
    row = conn.execute("SELECT p.project_id FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (run_id,)).fetchone()
    if row is None or (project_id is not None and row[0] != project_id):
        raise ValueError("RunProjectMismatch")
    return row[0]


def media_rows(conn, run_id, snapshot_id=None):
    run_project(conn, run_id)
    return [dict(r) for r in conn.execute("""SELECT t.run_id,s.post_id,m.snapshot_id,
        m.media_id AS post_media_id,m.position,m.source_url,m.source_field,m.media_type
        FROM post_media m JOIN post_snapshots s USING(snapshot_id)
        JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t USING(task_id)
        WHERE t.run_id=? AND t.task_type='feed_detail' AND a.status='succeeded'
        AND (? IS NULL OR m.snapshot_id=?) ORDER BY m.snapshot_id,m.position,m.media_id""",
        (run_id, snapshot_id, snapshot_id))]


def identity(row):
    return {**{k: row[k] for k in IDENTITY if k != "url_fingerprint"},
            "url_fingerprint": hashlib.sha256(row["source_url"].encode("utf-8")).hexdigest() if row["source_url"] else None}


class MediaStore:
    def __init__(self, conn, data_dir, *, directory="media", max_attempts=2, interval_seconds=.5, fetcher=None):
        if type(max_attempts) is not int or max_attempts not in (1, 2) or interval_seconds < 0:
            raise ValueError("InvalidMediaBudget")
        self.conn, self.root = conn, Path(data_dir).resolve()
        self.directory = relative_path(directory).as_posix()
        if PurePosixPath(self.directory).parts[0] in ("raw", "logs", "private_access", "exports"):
            raise ValueError("MediaOverlapsAuthoritativeStorage")
        self.base = self.path(self.directory)
        self.max_attempts, self.interval = max_attempts, interval_seconds
        self.fetcher = fetcher or image_preflight.fetch
        self._verified = {}  # In-process only; a new process always rehashes/decodes.

    def path(self, value):
        path = (self.root / relative_path(value)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("MediaPathEscapesRoot")
        return path

    def journal(self, run_id):
        return self.path(f"{self.directory}/manifests/run_{int(run_id)}.jsonl")

    def read(self, run_id):
        """Read-only: ignore incomplete final line, reject malformed committed lines."""
        path = self.journal(run_id)
        raw = path.read_bytes() if path.exists() else b""
        valid = raw[:raw.rfind(b"\n")+1]
        events = [json.loads(line) for line in valid.splitlines()]
        for n, event in enumerate(events, 1):
            if event.get("event_no") != n or event.get("run_id") != run_id:
                raise ValueError("InvalidMediaJournal")
        return events, valid, raw[len(valid):]

    def append(self, run_id, event):
        events, valid, tail = self.read(run_id)
        path = self.journal(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if tail:
            # Preserve torn bytes separately, then atomically restore committed prefix.
            atomic_write(path.with_name(path.name + ".torn_" + uuid4().hex), tail)
            atomic_write(path, valid)
        event = {**event, "event_no": len(events)+1, "updated_at": utc_now()}
        with path.open("ab") as handle:
            handle.write((json.dumps(event, ensure_ascii=False, sort_keys=True)+"\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def bound(self, event, row):
        return all(event.get(k) == v for k, v in identity(row).items())

    def verify(self, event, *, temporary=False):
        try:
            digest = event["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or event["format"] not in FORMATS:
                return False
            expected = f"{self.directory}/images/{digest[:2]}/{digest}.{FORMATS[event['format']]}"
            if event["relative_path"] != expected:
                return False
            if temporary:
                temp = event.get("temporary_path", "")
                if not re.fullmatch(re.escape(self.directory)+r"/images/\.partial/[0-9a-f]{32}\.part", temp):
                    return False
                path = self.path(temp)
            else:
                path = self.path(expected)
            if not path.is_file():
                return False
            stat = path.stat()
            if stat.st_size != event["bytes"] or stat.st_size > image_preflight.IMAGE_LIMIT:
                return False
            key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino,
                   digest, event["format"], event["width"], event["height"])
            if key in self._verified:
                return True
            data = path.read_bytes()
            valid = (hashlib.sha256(data).hexdigest() == digest and
                     image_preflight.decode(data) == {k: event[k] for k in ("format", "width", "height")})
            if valid:
                self._verified[key] = True
            return valid
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def states(self, run_id):
        events, _, tail = self.read(run_id)
        latest = {e["post_media_id"]: e for e in events}
        result = []
        for row in media_rows(self.conn, run_id):
            event = latest.get(row["post_media_id"], {})
            matched = bool(event) and self.bound(event, row)
            verified = matched and event.get("status") == "success" and self.verify(event)
            state = event.get("status", "pending") if matched else ("needs_verification" if event else "pending")
            if matched and event.get("status") == "success" and not verified:
                state = "needs_verification"
            result.append({**identity(row), "status": state, "verified_local": bool(verified),
                           **{k: event.get(k) if verified else None for k in FILE_FIELDS},
                           "attempts": event.get("attempts", 0) if matched else 0,
                           "error": event.get("error") if matched else ("MediaAssociationMismatch" if event else None),
                           "journal_torn_tail": bool(tail)})
        return result

    def summary(self, run_id):
        rows = self.states(run_id)
        counts = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0)+1
        return {"total": len(rows), "verified": sum(r["verified_local"] for r in rows),
                "unfinished": sum(not r["verified_local"] for r in rows), "states": counts}

    def reusable(self):
        """Only reuse success bound to real DB observations, never a free-standing hash."""
        result = {}
        for path in sorted((self.base / "manifests").glob("run_*.jsonl")):
            match = re.fullmatch(r"run_(\d+)\.jsonl", path.name)
            if not match:
                continue
            run = int(match[1])
            if not self.conn.execute("SELECT 1 FROM collection_runs WHERE run_id=?", (run,)).fetchone():
                continue
            events, _, _ = self.read(run)
            latest = {e["post_media_id"]: e for e in events}
            for row in media_rows(self.conn, run):
                event = latest.get(row["post_media_id"], {})
                if (event.get("status") == "success" and self.bound(event, row)
                        and self.verify(event) and event.get("url_fingerprint")):
                    result[event["url_fingerprint"]] = event
        return result

    def promote(self, event):
        """No overwrite of corrupt content-addressed files: require review instead."""
        destination = self.path(event["relative_path"])
        temporary = self.path(event["temporary_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not self.verify(event):
                raise ValueError("ContentAddressConflict")
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)

    def process(self, run_id, *, snapshot_id=None, should_pause=None):
        """Explicit writer operation. Retries are capped across process restarts."""
        if snapshot_id is None:
            self._verified.clear()  # Explicit independent resume revalidates bytes.
        rows = media_rows(self.conn, run_id, snapshot_id)
        if not rows:
            return self.summary(run_id)
        if self.conn.in_transaction:
            raise ValueError("MediaRequiresCommittedDetail")
        latest = {e["post_media_id"]: e for e in self.read(run_id)[0]}
        reusable = self.reusable()
        for row in rows:
            if should_pause and should_pause():
                break
            old = latest.get(row["post_media_id"], {})
            if old and not self.bound(old, row):
                raise ValueError("MediaAssociationMismatch")
            base = {**identity(row), "attempts": old.get("attempts", 0),
                    "relative_path": None, "sha256": None, "format": None, "width": None,
                    "height": None, "bytes": None, "downloaded_at": None, "error": None}
            if old.get("status") == "success" and self.verify(old):
                continue
            if old.get("status") == "staged":
                if self.verify(old) or self.verify(old, temporary=True):
                    if not self.verify(old):
                        self.promote(old)
                    done = self.append(run_id, {**old, "status": "success", "error": None})
                    reusable[base["url_fingerprint"]] = done
                    continue
            cached = reusable.get(base["url_fingerprint"])
            if cached and self.verify(cached):
                self.append(run_id, {**base, **{k: cached[k] for k in FILE_FIELDS}, "status": "success", "reused": True})
                continue
            if old.get("status") == "success":
                # Never silently replace damaged evidence under its old hash.
                self.append(run_id, {**old, "status": "needs_verification", "error": "MissingOrCorruptFile", "retryable": False})
                continue
            if base["attempts"] >= self.max_attempts and old.get("status") in ("pending", "staged"):
                self.append(run_id, {**old, "status": "needs_verification", "error": "UncertainResourceBudgetExhausted", "retryable": False})
                continue
            if old.get("retryable") is False or base["attempts"] >= self.max_attempts:
                continue
            if not row["source_url"] or row["source_field"] != "imageList" or row["media_type"] != "image":
                self.append(run_id, {**base, "status": "failed", "error": "MissingURLOrUnsupportedMedia", "retryable": False})
                continue
            for number in range(base["attempts"]+1, self.max_attempts+1):
                if should_pause and should_pause():
                    break
                pending = self.append(run_id, {**base, "status": "pending", "attempts": number, "retryable": True})
                started = time.monotonic()
                staged = None
                try:
                    parsed = urlsplit(row["source_url"])
                    url = urlunsplit(parsed._replace(scheme="https")) if parsed.scheme == "http" else row["source_url"]
                    # Existing transport performs TLS, host, public DNS and redirect checks.
                    time.sleep(self.interval)
                    data, trace = self.fetcher(url, [0])
                    if data is None:
                        status = trace[-1]["status"] if trace else None
                        self.append(run_id, {**pending, "status": "failed", "error": "HTTPFailure", "http_status": status,
                                             "retryable": status is not None and 500 <= status < 600})
                        if status is not None and 500 <= status < 600:
                            continue
                        break
                    if not data or len(data) > image_preflight.IMAGE_LIMIT:
                        raise ValueError("InvalidImageSize")
                    temp = f"{self.directory}/images/.partial/{uuid4().hex}.part"
                    atomic_write(self.path(temp), data)
                    info = image_preflight.decode(data)
                    if info["format"] not in FORMATS:
                        raise ValueError("UnsupportedImageFormat")
                    digest = hashlib.sha256(data).hexdigest()
                    staged = self.append(run_id, {**pending, **info, "sha256": digest, "bytes": len(data),
                        "temporary_path": temp, "relative_path": f"{self.directory}/images/{digest[:2]}/{digest}.{FORMATS[info['format']]}",
                        "downloaded_at": utc_now(), "elapsed_seconds": round(time.monotonic()-started, 3),
                        "status": "staged", "https_upgrade": parsed.scheme == "http"})
                    self.promote(staged)
                    done = self.append(run_id, {**staged, "status": "success"})
                    reusable[base["url_fingerprint"]] = done
                    break
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    if staged is not None:
                        raise  # Retain the durable staged intent for safe file reconciliation.
                    retryable = isinstance(exc, (TimeoutError, ConnectionError))
                    codes = {"UntrustedMediaURL", "NonPublicMediaAddress", "NonImageContentType", "ByteBudgetExceeded",
                             "ByteBudgetReached", "ImageDeadline", "RedirectLimit", "ImagePixelLimit",
                             "InvalidImageSize", "UnsupportedImageFormat", "IncompleteImageBody"}
                    code = str(exc) if str(exc) in codes else type(exc).__name__
                    self.append(run_id, {**pending, "status": "failed", "error": code,
                                         "retryable": retryable})
                    if not retryable:
                        break
        return self.summary(run_id)
