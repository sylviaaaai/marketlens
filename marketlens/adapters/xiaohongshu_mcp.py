"""Bounded read-only HTTP API client for xpzouying/xiaohongshu-mcp v2.5.5.

This uses the MCP server's documented HTTP facade, not JSON-RPC tools/call.
Credentials exist only in memory. Persist only sanitize(result.payload).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_auth(env_file: Path | None = None) -> str | None:
    """Read the existing service credential without displaying or modifying it."""
    value = os.environ.get("XHS_AUTH_TOKEN") or os.environ.get("AUTH_TOKEN")
    if value:
        return value
    if env_file and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            match = re.fullmatch(r"\s*(?:export\s+)?(?:XHS_AUTH_TOKEN|AUTH_TOKEN)\s*=\s*(.*?)\s*", line)
            if match:
                value = match[1]
                if value.startswith(('"', "'")):
                    value = value[1:value.find(value[0], 1)]
                else:
                    value = value.split(" #", 1)[0].strip()
                if value and "${" not in value:
                    return value
    return None


def sanitize(value, secrets: tuple[str, ...] = ()):
    """Remove credential fields and redact embedded access parameters recursively."""
    if isinstance(value, dict):
        return {k: sanitize(v, secrets) for k, v in value.items()
                if not re.search(r"token|cookie|authorization|password|secret|api.?key|signature", k, re.I)}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)((?:xsec[_-]?token|auth[_-]?token|access[_-]?token|token|cookie|authorization|api[_-]?key|signature)\s*[=:]\s*)[^\s&\"'<>]+", r"\1[REDACTED]", value)
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    endpoint: str
    method: str
    started_at: str
    finished_at: str
    duration_ms: int
    http_status: int | None
    payload: dict | None = field(repr=False)
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return (self.error_type is None and self.http_status == 200
                and isinstance(self.payload, dict) and self.payload.get("success") is True)

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("endpoint", "method", "started_at", "finished_at",
                                             "duration_ms", "http_status", "error_type")}


class XiaohongshuMCP:
    version = "phase1c-http-v1"

    def __init__(self, auth: str | None, timeout: float = 120, *, detail_timeout: float | None = None):
        self._auth = auth
        self.timeout = timeout
        if detail_timeout is not None and detail_timeout not in (90, 120):
            raise ValueError("Detail runtime timeout must be 90 or 120 seconds")
        self.detail_timeout = detail_timeout
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method: str, endpoint: str, body: dict | None = None) -> Response:
        allowed = {("GET", "/health"), ("GET", "/api/v1/login/status"),
                   ("POST", "/api/v1/feeds/search"), ("POST", "/api/v1/feeds/detail")}
        if (method, endpoint) not in allowed:
            raise ValueError("Unsupported read-only endpoint")
        started, clock = utc_now(), time.monotonic()
        payload, status, error = None, None, None
        if endpoint != "/health" and not self._auth:
            return Response(endpoint, method, started, utc_now(), 0, None, None, "AuthUnavailable")
        headers = {"Content-Type": "application/json"}
        if self._auth:
            headers["Authorization"] = "Bearer " + self._auth
        req = Request("http://127.0.0.1:18060" + endpoint, method=method, headers=headers,
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        try:
            try:
                timeout = self.detail_timeout if endpoint == "/api/v1/feeds/detail" and self.detail_timeout is not None else self.timeout
                response = self._opener.open(req, timeout=timeout)
            except HTTPError as exc:
                response = exc
            with response:
                status = response.code
                raw = response.read()
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    payload, error = None, "InvalidResponseShape"
            except (ValueError, UnicodeError):
                # Arbitrary HTML/text may contain secrets; do not persist it.
                payload, error = {"non_json_response_bytes": len(raw)}, "InvalidJSON"
            if status != 200:
                error = "HTTPError"
            elif not payload or payload.get("success") is not True:
                error = error or "ServiceError"
        except (URLError, TimeoutError, OSError) as exc:
            error = type(exc).__name__  # Never serialize request/headers or exception text.
        return Response(endpoint, method, started, utc_now(), round((time.monotonic()-clock)*1000),
                        status, payload, error)

    def preflight(self) -> tuple[dict, str | None]:
        health = self.request("GET", "/health")
        info = {"health": health.summary(), "ready": False}
        if not health.ok:
            return info, None
        version = (health.payload.get("data") or {}).get("version")
        info["source_version"] = version
        login = self.request("GET", "/api/v1/login/status")
        info["login"] = login.summary()
        info["ready"] = login.ok and (login.payload.get("data") or {}).get("is_logged_in") is True
        return info, version

    def search(self, keyword: str, filters: dict) -> Response:
        return self.request("POST", "/api/v1/feeds/search", {"keyword": keyword, "filters": filters})

    def detail(self, feed_id: str, access_token: str, *, comment_target: int | None = None) -> Response:
        body = {"feed_id": feed_id, "xsec_token": access_token, "load_all_comments": False}
        if comment_target is not None:
            if type(comment_target) is not int or comment_target not in (20, 25, 30):
                raise ValueError("Bounded comment target must be 20, 25 or 30")
            body.update(load_all_comments=True, comment_config={"max_comment_items": comment_target,
                        "click_more_replies": False, "max_replies_threshold": 10, "scroll_speed": "normal"})
        return self.request("POST", "/api/v1/feeds/detail", body)

    def safe_payload(self, response: Response):
        # Also remove token values if repeated in free text or URLs.
        secrets = [self._auth] if self._auth else []
        def visit(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if re.search(r"token|cookie|authorization|secret", k, re.I) and isinstance(v, str):
                        secrets.append(v)
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)
        visit(response.payload)
        return sanitize(response.payload, tuple(secrets))
