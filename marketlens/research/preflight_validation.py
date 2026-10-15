"""Bounded direct interface validation, no SQLite connections or ingestion runs.

Exposed only through the existing Phase1D CLI. Never starts formal collection.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from marketlens.adapters.xiaohongshu_mcp import utc_now, sanitize
from .access_cache import AccessCache, AccessCacheError, atomic_write
from .baseline_config import SORT_METHODS
from .normalizers import comment_stats, search_items, detail_data, object_or_empty, stable_id, count, platform_time


def write_json(path, data):
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))


def search_stats(payload):
    feeds = search_items(payload)
    normal = [f for f in feeds if isinstance(f, dict) and stable_id(f.get("id"))
              and object_or_empty(f.get("noteCard")).get("type") == "normal" and not object_or_empty(f.get("noteCard")).get("video")]
    metrics = {}
    for label, field in (("likes", "likedCount"), ("comments", "commentCount"), ("favorites", "collectedCount")):
        values = [count(object_or_empty(f["noteCard"].get("interactInfo")).get(field)) for f in normal]
        comparable = [(a["value"], b["value"]) for a, b in zip(values, values[1:]) if a["value"] is not None and b["value"] is not None]
        metrics[label] = {"available": sum(x["value"] is not None for x in values), "parsed": values,
                          "adjacent_comparable": len(comparable), "descending_violations": sum(a < b for a, b in comparable)}
    times = [platform_time(f["noteCard"].get("time"))[1] for f in normal]
    pairs = [(a,b) for a,b in zip(times,times[1:]) if a and b]
    return {"returned": len(feeds), "valid_normal": len(normal),
            "missing_id": sum(not stable_id(object_or_empty(f).get("id")) for f in feeds),
            "abnormal_type": len(feeds)-len(normal), "unique_normal": len({f["id"] for f in normal}),
            "metrics": metrics, "publish_time_available": sum(t is not None for t in times),
            "publish_time_adjacent_pairs": len(pairs), "publish_time_descending_violations": sum(a < b for a,b in pairs)}


class Validation:
    def __init__(self, directory, adapter, database_context):
        self.directory = Path(directory)
        self.adapter = adapter
        self.state_path = self.directory / "validation_state.json"
        self.cache = AccessCache(self.directory / "private_access", database_context)

    def save(self):
        write_json(self.state_path, self.state)

    def call(self, kind, label, operation, params):
        maximum = 8 if kind == "search" else 6
        if sum(e["kind"] == kind for e in self.state["calls"]) >= maximum:
            raise RuntimeError("RequestBudgetExhausted")
        time.sleep(3)
        event = {"number": len(self.state["calls"])+1, "kind": kind, "label": label,
                 "params": params, "state": "inflight"}
        self.state["calls"].append(event)
        self.save()  # Reserve budget before I/O; no automatic replay of unknown calls.
        response = operation()
        safe = self.adapter.safe_payload(response)
        # Also redact request tokens echoed in unstructured service errors.
        safe = sanitize(safe, tuple(getattr(self, "access", {}).values()))
        content = (json.dumps({"response": safe, "transport": response.summary()}, ensure_ascii=False, indent=2)+"\n").encode("utf-8")
        artifact = self.directory / f"call_{event['number']:02}.json"
        atomic_write(artifact, content)
        event.update(state="finished", **response.summary(), business_success=response.ok,
                     artifact=artifact.name, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
        self.save()
        print(json.dumps({k:v for k,v in event.items() if k not in ("artifact", "sha256")}, ensure_ascii=False), flush=True)
        return response, event

    def capture(self):
        if self.state_path.exists():
            raise ValueError("ValidationAlreadyExists;inspect_before_any_new_requests")
        self.cache.verify_available()
        self.state = {"calls": [], "sorts": [], "comments": [], "capture_pid": os.getpid(), "started_at": utc_now()}
        self.save()
        ready, version = self.adapter.preflight()
        self.state.update(preflight=ready, source_version=version)
        self.save()
        if not ready["ready"]:
            return self.stop("PreflightFailed")
        self.access, groups, candidates = {}, {}, []
        for sort in SORT_METHODS:
            for retry in range(2):
                response, event = self.call("search", sort, lambda: self.adapter.search("粉底液", {"sort_by": sort, "note_type": "图文"}), {"keyword": "粉底液", "sort_by": sort, "note_type": "图文"})
                if response.ok or response.http_status != 500 or retry == 1:
                    break
            if not response.ok:
                return self.stop("SearchFailed;remaining_calls_cancelled")
            stats = search_stats(response.payload)
            self.state["sorts"].append({"sort": sort, "call": event["number"], **stats})
            groups[sort] = set()
            for feed in search_items(response.payload):
                f = object_or_empty(feed)
                card = object_or_empty(f.get("noteCard"))
                if stable_id(f.get("id")) and card.get("type") == "normal" and not card.get("video"):
                    groups[sort].add(f["id"])
                    if stable_id(f.get("xsecToken")):
                        self.access[f["id"]] = f["xsecToken"]
                        if sort == "最多评论" and f["id"] not in candidates:
                            candidates.append(f["id"])
            self.save()
        self.state["overlap"] = {a: {b: len(groups[a]&groups[b]) for b in SORT_METHODS} for a in SORT_METHODS}
        self.state["unique_across_sorts"] = len(set().union(*groups.values()))
        if not candidates:
            return self.stop("NoAccessibleCommentCandidates")
        selected = candidates[:2]
        self.cache.save(0, 1, {sid: self.access[sid] for sid in selected})
        self.state["encrypted_at"] = utc_now()
        self.save()
        first_stats = None
        for index, sid in enumerate(selected, 1):
            for target in (None, 20):
                response, event = self.call("detail", f"sample_{index}_{target or 'default'}",
                    lambda: self.adapter.detail(sid, self.access[sid], comment_target=target),
                    {"sample": index, "comment_target": target, "click_more_replies": False})
                if not response.ok:
                    return self.stop("DetailFailed;remaining_calls_cancelled")
                stats = comment_stats(response.payload, sid)
                self.state["comments"].append({"call": event["number"], "sample": index, "target": target, **stats})
                if index == 1 and target == 20:
                    first_stats = stats
                self.save()
        if first_stats and first_stats["top_unique_ids"] <= 20:
            sid = selected[0]
            response, event = self.call("detail", "sample_1_diagnostic_30", lambda: self.adapter.detail(sid, self.access[sid], comment_target=30), {"sample": 1, "comment_target": 30, "click_more_replies": False})
            if not response.ok:
                return self.stop("DiagnosticFailed;remaining_calls_cancelled")
            self.state["comments"].append({"call": event["number"], "sample": 1, "target": 30, **comment_stats(response.payload, sid)})
        self.state.update(capture_finished_at=utc_now(), status="WAITING_FOR_NEW_PROCESS")
        self.save()
        return self.state

    def restore(self):
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        # A failed optional diagnostic does not consume the separately authorized
        # delayed default-mode validation. Never retry the failed scroll call.
        diagnostic_only = False
        if self.state.get("status") == "STOPPED" and self.state.get("reason") == "DiagnosticFailed;remaining_calls_cancelled":
            last = self.state["calls"][-1]
            artifact = json.loads((self.directory / last["artifact"]).read_text(encoding="utf-8"))
            diagnostic_only = (last["http_status"] == 500 and artifact["response"].get("code") == "INTERNAL_ERROR"
                               and not artifact["response"].get("details"))
        if (self.state.get("status") != "WAITING_FOR_NEW_PROCESS" and not diagnostic_only) or self.state["capture_pid"] == os.getpid():
            raise ValueError("RestoreRequiresCompletedCaptureAndNewProcess")
        finished = datetime.fromisoformat(self.state.get("capture_finished_at", self.state.get("finished_at")).replace("Z", "+00:00"))
        wait = (datetime.now(timezone.utc)-finished).total_seconds()
        if not 300 <= wait <= 600:
            raise ValueError("RestoreRequiresFiveToTenMinuteWaitAfterCaptureExit")
        try:
            self.access = self.cache.load(0, [1])
        except AccessCacheError:
            return self.stop("DPAPIDecryptionFailed")
        self.state["restore"] = {"pid": os.getpid(), "wait_seconds": wait, "decryption": "PASS", "started_at": utc_now()}
        if diagnostic_only:
            self.state["restore"]["decision"] = "Independent default-mode DPAPI test after isolated internal 500; no diagnostic retry"
        self.save()
        sid = next(iter(self.access))
        response, event = self.call("detail", "dpapi_restore_sample_1", lambda: self.adapter.detail(sid, self.access[sid]), {"sample": 1, "load_all_comments": False})
        self.state["restore"].update(call=event["number"], platform_accepted=response.ok,
            outcome="ACCEPTED" if response.ok else "ACCESS_REJECTED" if response.http_status in (401,403,429) else "NETWORK_OR_SERVICE_FAILURE")
        if response.ok:
            self.state["restore"]["comments"] = comment_stats(response.payload, sid)
        self.state.update(status="COMPLETE" if response.ok else "STOPPED", finished_at=utc_now())
        self.save()
        return self.state

    def stop(self, reason):
        self.state.update(status="STOPPED", reason=reason, finished_at=utc_now())
        self.save()
        return self.state
