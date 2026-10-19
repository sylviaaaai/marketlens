"""Conservative parsing of observed XHS fields; missing never means zero."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def raw_text(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else dumps(value)


def text(value) -> str | None:
    return value if isinstance(value, str) else None


def stable_id(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def count(value) -> dict:
    raw = raw_text(value)
    result = {"raw": raw, "value": None, "precision": "missing" if raw is None else "unparseable"}
    if raw is None or isinstance(value, bool):
        return result
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万|亿|[kKwWmM])?(\+)?", raw.strip())
    if not match:
        return result
    unit, lower = match[2], bool(match[3])
    multiplier = {None: 1, "万": 10000, "亿": 100000000, "k": 1000, "w": 10000, "m": 1000000}[unit.lower() if unit else None]
    try:
        number = Decimal(match[1]) * multiplier
        if number != number.to_integral_value() or number > 9223372036854775807:
            return result
        result.update(value=int(number), precision=("approximate_lower_bound" if lower else "approximate")
                      if unit else ("lower_bound" if lower else "exact_display"))
    except InvalidOperation:
        pass
    return result


def platform_time(value) -> tuple[str | None, str | None]:
    """Only modern unambiguous 10-digit seconds/13-digit milliseconds or zoned ISO."""
    raw = raw_text(value)
    if raw is None:
        return None, None
    try:
        if not isinstance(value, bool) and re.fullmatch(r"\d{10}|\d{13}", raw):
            result = datetime.fromtimestamp(int(raw) / (1000 if len(raw) == 13 else 1), timezone.utc)
        elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?(?:Z|[+-]\d\d:\d\d)", raw):
            result = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        else:
            return raw, None
        return raw, result.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return raw, None


def object_or_empty(value) -> dict:
    return value if isinstance(value, dict) else {}


def search_items(payload: dict) -> list:
    data = object_or_empty(payload.get("data"))
    if not isinstance(data.get("feeds"), list):
        raise ValueError("InvalidSearchShape")
    return data["feeds"]


def detail_data(payload: dict, expected_id: str) -> tuple[dict, dict]:
    outer = object_or_empty(payload.get("data"))
    detail = object_or_empty(outer.get("data"))
    note = object_or_empty(detail.get("note"))
    if stable_id(note.get("noteId")) != expected_id:
        raise ValueError("DetailIdentityMismatch")
    if outer.get("feed_id") not in (None, expected_id):
        raise ValueError("DetailEnvelopeIdentityMismatch")
    if note.get("type") != "normal" or note.get("video"):
        raise ValueError("NonImageDetail")
    return note, object_or_empty(detail.get("comments"))


METRICS = {"likes": ("likedCount", "likes_count"), "comments": ("commentCount", "comments_count"),
           "favorites": ("collectedCount", "favorites_count"), "shares": ("sharedCount", "shares_count")}


def snapshot_fields(note: dict) -> dict:
    parsed = {key: count(object_or_empty(note.get("interactInfo")).get(source))
              for key, (source, _) in METRICS.items()}
    published_raw, published = platform_time(note.get("time"))
    return {"title": text(note.get("title")), "description": text(note.get("desc")), "note_type": "normal",
            "published_at_raw": published_raw, "published_at": published,
            "engagement_raw_json": dumps({k: v["raw"] for k, v in parsed.items()}),
            "engagement_parse_json": dumps(parsed),
            **{column: parsed[key]["value"] for key, (_, column) in METRICS.items()}}


def comment_stats(payload, sid, *, target_unique=20):
    """Quality only, never a storage limit. Default preserves the legacy benchmark."""
    if type(target_unique) is not int or target_unique <= 0:
        raise ValueError("Comment quality target must be a positive integer")
    note, block = detail_data(payload, sid)
    top = block.get("list", [])
    top = top if isinstance(top, list) else []
    parents = [x for x in top if isinstance(x, dict)]
    replies = [(p, r) for p in parents for r in (p.get("subComments") if isinstance(p.get("subComments"), list) else []) if isinstance(r, dict)]
    ids = [p["id"] for p in parents if stable_id(p.get("id"))]
    rids = [r["id"] for _, r in replies if stable_id(r.get("id"))]
    advertised = [count(p.get("subCommentCount"))["value"] for p in parents]
    return {"top_returned": len(parents), "top_unique_ids": len(set(ids)),
            "top_missing_id": len(parents)-len(ids), "top_duplicates": len(ids)-len(set(ids)),
            "replies_returned": len(replies), "reply_unique_ids": len(set(rids)),
            "reply_duplicates": len(rids)-len(set(rids)), "reply_missing_id": len(replies)-len(rids),
            "reply_note_mismatches": sum(r.get("noteId") not in (None, "", sid) for _, r in replies),
            "reply_parent_missing_id": sum(not stable_id(p.get("id")) for p, _ in replies),
            "parents_with_unreturned_replies": sum(n is not None and n > len(p.get("subComments") if isinstance(p.get("subComments"), list) else []) for p, n in zip(parents, advertised)),
            "parents_reply_total_available": sum(n is not None for n in advertised),
            "has_more": block.get("hasMore"), "reached_20_unique": len(set(ids)) >= 20,
            "target_unique": target_unique, "reached_target_unique": len(set(ids)) >= target_unique,
            "internal_scroll_count": None, "total_display_comments": count(object_or_empty(note.get("interactInfo")).get("commentCount"))}
