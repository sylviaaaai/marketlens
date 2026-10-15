"""One bounded search and at most three detail calls, with atomic response ingestion."""
import hashlib
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from marketlens.adapters.xiaohongshu_mcp import Response, XiaohongshuMCP, utc_now
from .database import assert_integrity, transaction
from .normalizers import (METRICS, count, detail_data, dumps, object_or_empty, platform_time,
                          search_items, snapshot_fields, stable_id, text)

FILTERS = {"sort_by": "综合", "note_type": "图文"}
COMMENT_CONFIG = {"load_all_comments": False}


def insert(conn: sqlite3.Connection, table: str, **values) -> int:
    # Identifiers are internal constants only; all source values are parameterized.
    return conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values())).lastrowid


def post_identity(conn, source_id: str, observed_at: str) -> int:
    existing = conn.execute("SELECT post_id FROM posts WHERE source_platform=? AND source_post_id=?",
                            ("xiaohongshu", source_id)).fetchone()
    return existing[0] if existing else insert(conn, "posts", source_platform="xiaohongshu",
                                              source_post_id=source_id, first_seen_at=observed_at)


def ingest_search(conn, payload: dict, request_id: int, attempt_id: int, artifact_id: int,
                  observed_at: str) -> dict:
    feeds = search_items(payload)
    quality = {"returned_items": len(feeds), "identified_hits": 0, "missing_post_id": 0,
               "normal_hits": 0, "video_hits": 0, "unknown_type_hits": 0}
    for position, feed in enumerate(feeds, 1):
        feed = object_or_empty(feed)
        source_id = stable_id(feed.get("id"))
        if not source_id:
            quality["missing_post_id"] += 1
            continue
        card = object_or_empty(feed.get("noteCard"))
        post_id = post_identity(conn, source_id, observed_at)
        index = feed.get("index")
        insert(conn, "search_hits", search_request_id=request_id, collection_attempt_id=attempt_id,
               post_id=post_id, result_position=position,
               source_index=index if type(index) is int else None, access_params_json=None,
               raw_artifact_id=artifact_id, card_title=text(card.get("displayTitle")),
               card_note_type=text(card.get("type")),
               card_engagement_json=dumps(card.get("interactInfo")), card_cover_json=dumps(card.get("cover")))
        quality["identified_hits"] += 1
        quality[{"normal": "normal_hits", "video": "video_hits"}.get(card.get("type"), "unknown_type_hits")] += 1
    return quality


def comment_identity(conn, post_id, source_id, level, parent_id, observed_at):
    if not source_id:
        return None
    old = conn.execute("SELECT comment_id,comment_level,parent_comment_id FROM comments WHERE post_id=? AND source_comment_id=?",
                       (post_id, source_id)).fetchone()
    if old:
        if old["comment_level"] != level or (old["parent_comment_id"] is not None and parent_id is not None
                                            and old["parent_comment_id"] != parent_id):
            raise ValueError("CommentIdentityConflict")
        return old["comment_id"]
    return insert(conn, "comments", post_id=post_id, source_comment_id=source_id, comment_level=level,
                  parent_comment_id=parent_id, first_seen_at=observed_at)


def ingest_detail(conn, payload, post_id, source_id, attempt_id, artifact_id, observed_at) -> dict:
    note, comments = detail_data(payload, source_id)
    fields = snapshot_fields(note)
    snapshot = insert(conn, "post_snapshots", post_id=post_id, collection_attempt_id=attempt_id,
                      observed_at=observed_at, raw_artifact_id=artifact_id, **fields)
    quality = {"snapshots": 1, "images": 0, "top_comments": 0, "replies": 0, "anonymous_comments": 0,
               "invalid_comment_items": 0, "missing_title": int(not fields["title"]),
               "missing_description": int(not fields["description"]),
               "unverified_publish_time": int(fields["published_at"] is None),
               "missing_comments_container": int(not isinstance(comments.get("list"), list)),
               "comments_has_more": comments.get("hasMore"),
               "missing_metrics": [key for key, (_, column) in METRICS.items() if fields[column] is None]}
    images = note.get("imageList")
    for position, media in enumerate(images if isinstance(images, list) else [], 1):
        media_obj = object_or_empty(media)
        insert(conn, "post_media", snapshot_id=snapshot, media_type="image", source_field="imageList",
               position=position, source_url=text(media_obj.get("urlDefault")) or text(media_obj.get("url")),
               thumbnail_url=text(media_obj.get("urlPre")), media_raw_json=dumps(media))
        quality["images"] += 1

    def save_comment(item, path, level, position, parent_observation=None, parent_identity=None, parent_source=None):
        if not isinstance(item, dict):
            quality["invalid_comment_items"] += 1
            return None, None
        if item.get("noteId") not in (None, "", source_id):
            raise ValueError("CommentPostMismatch")
        identity = comment_identity(conn, post_id, stable_id(item.get("id")), level, parent_identity, observed_at)
        created_raw, created = platform_time(item.get("createTime"))
        likes = count(item.get("likeCount"))
        observation = insert(conn, "comment_observations", comment_id=identity, post_id=post_id,
                             collection_attempt_id=attempt_id, observed_at=observed_at,
                             content=text(item.get("content")), comment_raw_json=dumps(item),
                             parent_observation_id=parent_observation, source_path=path,
                             created_at_raw=created_raw, created_at=created, like_count_raw=likes["raw"],
                             like_count=likes["value"], like_count_precision=likes["precision"],
                             comment_level=level, parent_source_comment_id=parent_source,
                             display_position=position, raw_artifact_id=artifact_id)
        quality["top_comments" if level == 1 else "replies"] += 1
        quality["anonymous_comments"] += int(identity is None)
        return observation, identity

    items = comments.get("list")
    for i, item in enumerate(items if isinstance(items, list) else []):
        parent_obs, parent_id = save_comment(item, f"list[{i}]", 1, i+1)
        if parent_obs is None:
            continue
        replies = item.get("subComments")
        for j, reply in enumerate(replies if isinstance(replies, list) else []):
            save_comment(reply, f"list[{i}].subComments[{j}]", 2, j+1,
                         parent_obs, parent_id, stable_id(item.get("id")))
    return quality


class CollectionPipeline:
    def __init__(self, conn: sqlite3.Connection, adapter: XiaohongshuMCP, data_dir: Path):
        self.conn, self.adapter, self.data_dir = conn, adapter, Path(data_dir)
        (self.data_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.events = []

    def start(self, source_version: str | None, keyword: str = "护手霜") -> tuple[int, int, int]:
        with transaction(self.conn):
            project = insert(self.conn, "research_projects", research_question="图文采集与入库最小链路验证", category=keyword)
            plan = insert(self.conn, "collection_plans", project_id=project, plan_name="Phase1C Experiment01",
                          search_filters_json=dumps({"note_type": "图文"}), comment_config_json=dumps(COMMENT_CONFIG))
            insert(self.conn, "plan_keywords", plan_id=plan, keyword=keyword, sequence_no=1)
            insert(self.conn, "plan_sort_methods", plan_id=plan, sort_method="综合", sequence_no=1)
            run = insert(self.conn, "collection_runs", plan_id=plan, status="running", started_at=utc_now(),
                         adapter_version=self.adapter.version, source_version=source_version)
            request = insert(self.conn, "search_requests", run_id=run, sequence_no=1, keyword=keyword,
                             sort_method="综合", filters_json=dumps(FILTERS), request_method="POST", status="running",
                             started_at=utc_now())
            task = insert(self.conn, "collection_tasks", run_id=run, task_type="search", search_request_id=request,
                          sequence_no=1, status="running")
        return run, request, task

    def begin_attempt(self, task: int, number: int, endpoint: str) -> int:
        with transaction(self.conn):
            return insert(self.conn, "collection_attempts", task_id=task, attempt_no=number,
                          started_at=utc_now(), status="running", http_method="POST", endpoint=endpoint)

    def response_metadata(self, response, safe, source_id):
        return response.summary()

    def record_response(self, attempt: int, response: Response, *, request_id=None, post_id=None,
                        source_id=None) -> dict:
        """Replay same attempt+sanitized hash is a no-op; changed evidence is rejected.

        The running attempt was committed BEFORE network I/O. Artifact + completed
        attempt + parsed rows commit together; a parser/SQL failure rolls them back
        and records that same actual call as failed in a separate transaction.
        """
        safe = self.adapter.safe_payload(response)
        content = (dumps({"response": safe, "transport": response.summary()}) + "\n").encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        existing = self.conn.execute("SELECT sha256,file_path FROM raw_artifacts WHERE attempt_id=?", (attempt,)).fetchall()
        if existing:
            if len(existing) == 1 and existing[0][0] == digest:
                if not Path(existing[0][1]).is_file() or hashlib.sha256(Path(existing[0][1]).read_bytes()).hexdigest() != digest:
                    raise ValueError("ExistingArtifactMissingOrCorrupted")
                return {"replayed": True}
            raise ValueError("AttemptEvidenceConflict")
        path = self.data_dir / "raw" / f"attempt_{attempt}_{uuid4().hex}.json"
        with path.open("xb") as handle:
            handle.write(content)
        error = response.error_type or (None if response.ok else "ServiceError")
        artifact_type = "search_response" if request_id is not None else "detail_response"
        evidence = dict(attempt_id=attempt, artifact_type=artifact_type if response.ok else "error_response",
                        file_path=path.resolve().as_posix(), content_type="application/json", file_size_bytes=len(content),
                        sha256=digest, saved_at=utc_now(), protection_status="local_only;credential_redacted;reserialized")
        quality = {}
        try:
            if error:
                raise ValueError("TransportFailure")
            if request_id is not None:
                returned_posts = len(search_items(safe))
                top_count = reply_count = None
            else:
                _, comment_block = detail_data(safe, source_id)
                returned_posts = 1
                top_items = comment_block.get("list")
                top_items = top_items if isinstance(top_items, list) else []
                top_count = sum(isinstance(x, dict) for x in top_items)
                reply_count = sum(sum(isinstance(y, dict) for y in x["subComments"])
                                  for x in top_items if isinstance(x, dict) and isinstance(x.get("subComments"), list))
            with transaction(self.conn):
                artifact = insert(self.conn, "raw_artifacts", **evidence)
                self.conn.execute("UPDATE collection_attempts SET status='succeeded',finished_at=?,duration_ms=?,http_status=?,returned_posts=?,returned_top_comments=?,returned_replies=?,response_summary_json=? WHERE attempt_id=?",
                                  (response.finished_at, response.duration_ms, response.http_status, returned_posts,
                                   top_count, reply_count, dumps(self.response_metadata(response, safe, source_id)), attempt))
                if request_id is not None:
                    quality = ingest_search(self.conn, safe, request_id, attempt, artifact, response.finished_at)
                else:
                    quality = ingest_detail(self.conn, safe, post_id, source_id, attempt, artifact, response.finished_at)
        except (ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
            if not error:
                error = "IngestionError:" + type(exc).__name__
            with transaction(self.conn):
                insert(self.conn, "raw_artifacts", **evidence)
                self.conn.execute("UPDATE collection_attempts SET status='failed',finished_at=?,duration_ms=?,http_status=?,error_type=?,error_message=? WHERE attempt_id=?",
                                  (response.finished_at, response.duration_ms, response.http_status, error,
                                   "Response retained; ingestion rolled back" if response.ok else "Request failed; see redacted local artifact", attempt))
        event = {"attempt_id": attempt, **response.summary(), "ingestion_error": error,
                 "quality": quality if not error else {}}
        self.events.append(event)
        with (self.data_dir / "logs/events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(dumps(event) + "\n")
        print(dumps(event), flush=True)
        return event

    def finish_task(self, task, status):
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status=?,finished_at=? WHERE task_id=?", (status, utc_now(), task))

    def run(self, source_version: str | None = None) -> dict:
        run, request, task = self.start(source_version)
        success, response = False, None
        for number in (1, 2):
            attempt = self.begin_attempt(task, number, "/api/v1/feeds/search")
            response = self.adapter.search("护手霜", FILTERS.copy())
            result = self.record_response(attempt, response, request_id=request)
            success = not result["ingestion_error"]
            if success or response.http_status in (401, 403, 429) or response.http_status is None or response.ok:
                break  # No retry of auth, access limits, unreachable service or local ingestion failure.
            if number == 1:
                time.sleep(2)
        self.finish_task(task, "succeeded" if success else "failed")
        with transaction(self.conn):
            self.conn.execute("UPDATE search_requests SET status=?,finished_at=?,returned_count=? WHERE search_request_id=?",
                              ("succeeded" if success else "failed", utc_now(),
                               len(search_items(response.payload)) if success else None, request))
        detail_success, detail_calls = 0, 0
        selection = {"eligible_unique_posts": 0, "missing_access_parameter": 0}
        if success:
            candidates, seen = [], set()
            for feed in search_items(response.payload):
                feed = object_or_empty(feed)
                source_id = stable_id(feed.get("id"))
                card = object_or_empty(feed.get("noteCard"))
                if not source_id or source_id in seen or card.get("type") != "normal" or card.get("video"):
                    continue
                access = stable_id(feed.get("xsecToken"))
                if not access:
                    selection["missing_access_parameter"] += 1
                    continue
                seen.add(source_id)
                candidates.append((source_id, access))
            selection["eligible_unique_posts"] = len(candidates)
            for sequence, (source_id, access) in enumerate(candidates[:3], 2):
                post_id = self.conn.execute("SELECT post_id FROM posts WHERE source_platform='xiaohongshu' AND source_post_id=?", (source_id,)).fetchone()[0]
                with transaction(self.conn):
                    detail_task = insert(self.conn, "collection_tasks", run_id=run, task_type="feed_detail",
                                         post_id=post_id, task_config_json=dumps(COMMENT_CONFIG), sequence_no=sequence, status="running")
                attempt = self.begin_attempt(detail_task, 1, "/api/v1/feeds/detail")
                detail_response = self.adapter.detail(source_id, access)
                result = self.record_response(attempt, detail_response, post_id=post_id, source_id=source_id)
                ok = not result["ingestion_error"]
                detail_calls += 1
                detail_success += int(ok)
                self.finish_task(detail_task, "succeeded" if ok else "failed")
                if detail_response.http_status in (401, 403, 429) or detail_response.http_status is None:
                    break
        status = "failed" if not success else ("completed" if detail_success == 3 else "partial")
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_runs SET status=?,finished_at=?,error_summary=? WHERE run_id=?",
                              (status, utc_now(), None if status == "completed" else "Bounded smoke incomplete; see attempt evidence", run))
        return {"result": "BLOCKED" if not success else ("SUCCESS" if status == "completed" else "PARTIAL"),
                "run_id": run, "detail_calls": detail_calls, "detail_success": detail_success,
                "selection": selection, "events": self.events, **audit(self.conn)}


def audit(conn: sqlite3.Connection) -> dict:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in tables}
    artifacts = []
    for row in conn.execute("SELECT raw_artifact_id,file_path,sha256,file_size_bytes FROM raw_artifacts"):
        path = Path(row["file_path"])
        data = path.read_bytes() if path.is_file() else None
        artifacts.append({"raw_artifact_id": row["raw_artifact_id"], "verified": data is not None
                          and len(data) == row["file_size_bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]})
    quality = dict(conn.execute("""SELECT
        count(*) AS snapshots,
        coalesce(sum(title IS NULL OR title=''),0) AS empty_title,
        coalesce(sum(description IS NULL OR description=''),0) AS empty_description,
        coalesce(sum(published_at IS NULL),0) AS unverified_publish_time,
        coalesce(sum(likes_count IS NULL),0) AS unavailable_likes,
        coalesce(sum(comments_count IS NULL),0) AS unavailable_comments,
        coalesce(sum(favorites_count IS NULL),0) AS unavailable_favorites,
        coalesce(sum(shares_count IS NULL),0) AS unavailable_shares FROM post_snapshots""").fetchone())
    quality["unrequested_candidates"] = conn.execute("""SELECT count(*) FROM
        (SELECT DISTINCT q.run_id,h.post_id FROM search_hits h JOIN search_requests q USING(search_request_id)
        WHERE NOT EXISTS (SELECT 1 FROM collection_tasks t WHERE t.run_id=q.run_id
        AND t.post_id=h.post_id AND t.task_type='feed_detail'))""").fetchone()[0]
    quality["anonymous_observations"] = conn.execute("SELECT count(*) FROM comment_observations WHERE comment_id IS NULL").fetchone()[0]
    quality["media_missing_url"] = conn.execute("SELECT count(*) FROM post_media WHERE source_url IS NULL OR source_url=''").fetchone()[0]
    quality["unverified_comment_time"] = conn.execute("SELECT count(*) FROM comment_observations WHERE created_at IS NULL").fetchone()[0]
    relationships = {
        "hit_attempt_request_mismatches": conn.execute("""SELECT count(*) FROM search_hits h
            JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
            JOIN collection_tasks t ON t.task_id=a.task_id
            WHERE t.search_request_id IS NOT h.search_request_id OR t.task_type<>'search'""").fetchone()[0],
        "reply_parent_mismatches": conn.execute("""SELECT count(*) FROM comment_observations c
            LEFT JOIN comment_observations p ON p.observation_id=c.parent_observation_id
            WHERE c.comment_level=2 AND (p.observation_id IS NULL OR p.comment_level<>1
              OR p.post_id<>c.post_id OR p.collection_attempt_id<>c.collection_attempt_id)""").fetchone()[0],
        "non_image_snapshots": conn.execute("SELECT count(*) FROM post_snapshots WHERE note_type IS NOT 'normal'").fetchone()[0],
    }
    return {"table_counts": counts, "artifact_checks": artifacts, "quality": quality,
            "relationship_checks": relationships,
            "attempt_status_counts": {r[0]: r[1] for r in conn.execute("SELECT status,count(*) FROM collection_attempts GROUP BY status")},
            "comment_level_counts": {str(r[0]): r[1] for r in conn.execute("SELECT comment_level,count(*) FROM comment_observations GROUP BY comment_level")},
            **assert_integrity(conn)}
