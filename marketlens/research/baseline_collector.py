"""Single-writer baseline scheduler over the existing append-only ingestion layer.

No pagination or implicit search refresh. Optional current-user encrypted cache.
Uncertain in-flight calls require review; only definitely recorded failures retry.
"""
import json
import time

from marketlens.adapters.xiaohongshu_mcp import utc_now
from .baseline_config import BaselineConfig
from .collection_pipeline import CollectionPipeline, insert, audit
from .database import transaction
from .normalizers import comment_stats, dumps, object_or_empty, search_items, stable_id
from .access_cache import AccessCacheError, atomic_write
from .media_store import MediaStore


class BaselineCollector(CollectionPipeline):
    def __init__(self, conn, adapter, data_dir, config=None, access_cache=None, media_store=None):
        super().__init__(conn, adapter, data_dir)
        self.config = config or BaselineConfig()
        self.access = {}  # Intentionally memory-only, including across in-process resume.
        self.access_cache = access_cache
        self.media_store = None if self.config.pilot else (media_store or MediaStore(
            conn, data_dir, directory=self.config.media_directory, max_attempts=self.config.media_max_attempts,
            interval_seconds=self.config.media_interval_seconds))

    def collect_media(self, run, snapshot):
        if self.media_store is not None:
            try:
                self.media_store.process(run, snapshot_id=snapshot, should_pause=lambda: self.pause_requested(run))
            except Exception as exc:
                # Detail is already committed. Resource I/O cannot mark its Task failed.
                error = {"run_id": run, "snapshot_id": snapshot, "error": type(exc).__name__, "at": utc_now()}
                self.events.append({"media_error": error})
                try:
                    atomic_write(self.data_dir / "logs" / f"media_error_{run}_{snapshot}.json", dumps(error).encode("utf-8"))
                except OSError:
                    pass  # Disk failure remains visible in the returned in-process events.

    def response_metadata(self, response, safe, source_id):
        result = super().response_metadata(response, safe, source_id)
        if source_id is not None and not self.config.pilot:
            result["comments"] = comment_stats(safe, source_id)
        return result

    def phase(self, run):
        row = self.conn.execute("SELECT plan_id,status FROM collection_runs WHERE run_id=?", (run,)).fetchone()
        if row is None:
            raise ValueError("Unknown Run")
        keywords = [r[0] for r in self.conn.execute("SELECT keyword FROM plan_keywords WHERE plan_id=? ORDER BY sequence_no", (row[0],))]
        sorts = [r[0] for r in self.conn.execute("SELECT sort_method FROM plan_sort_methods WHERE plan_id=? ORDER BY sequence_no", (row[0],))]
        requests = self.conn.execute("SELECT * FROM search_requests WHERE run_id=? ORDER BY sequence_no", (run,)).fetchall()
        expected = [(k, s) for k in keywords for s in sorts]
        complete = bool(expected) and [(r["keyword"], r["sort_method"]) for r in requests] == expected
        complete = complete and all(r["status"] == "succeeded" for r in requests)
        clean = self.conn.execute("""SELECT count(*) FROM collection_tasks t WHERE t.run_id=? AND t.task_type='search'
            AND (t.status<>'succeeded' OR NOT EXISTS (SELECT 1 FROM collection_attempts a WHERE a.task_id=t.task_id AND a.status='succeeded')
            OR EXISTS (SELECT 1 FROM collection_attempts a WHERE a.task_id=t.task_id AND a.status='running'))""", (run,)).fetchone()[0] == 0
        task_count = self.conn.execute("SELECT count(*) FROM collection_tasks WHERE run_id=? AND task_type='search'", (run,)).fetchone()[0]
        complete = complete and clean and task_count == len(expected)
        detail_attempts = self.conn.execute("SELECT count(*) FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND t.task_type='feed_detail'", (run,)).fetchone()[0]
        stage = "completed" if row["status"] == "completed" else "details" if complete and detail_attempts else "search_complete" if complete else "search"
        return {"stage": stage, "search_complete": complete, "search_expected": len(expected),
                "search_succeeded": sum(r["status"] == "succeeded" for r in requests),
                "search_completed_at": max(r["finished_at"] for r in requests) if complete else None}

    def checkpoint(self, run, status="RUNNING", reason=None):
        state = {"run_id": run, **self.phase(run), "execution_status": status,
                 "reason": reason, "updated_at": utc_now()}
        atomic_write(self.data_dir / "logs" / f"checkpoint_{run}.json", (dumps(state)+"\n").encode("utf-8"))

    def pause_requested(self, run):
        return (self.data_dir / f"pause_{run}.request").exists()

    def create(self, source_version=None):
        with transaction(self.conn):
            project = insert(self.conn, "research_projects", category=self.config.category,
                             research_question="Phase1D baseline corpus collection")
            plan = insert(self.conn, "collection_plans", project_id=project, plan_name="Phase1D baseline",
                          search_filters_json=dumps({"note_type": "图文"}),
                          comment_config_json=dumps(self.config.persisted()))
            for n, keyword in enumerate(self.config.keywords, 1):
                insert(self.conn, "plan_keywords", plan_id=plan, keyword=keyword, sequence_no=n)
            for n, sort in enumerate(self.config.sorts, 1):
                insert(self.conn, "plan_sort_methods", plan_id=plan, sort_method=sort, sequence_no=n)
            run = insert(self.conn, "collection_runs", plan_id=plan, status="running", started_at=utc_now(),
                         adapter_version=self.adapter.version, source_version=source_version)
            for n, (keyword, sort) in enumerate(self.config.combinations, 1):
                request = insert(self.conn, "search_requests", run_id=run, sequence_no=n,
                                 keyword=keyword, sort_method=sort,
                                 filters_json=dumps({"sort_by": sort, "note_type": "图文"}))
                insert(self.conn, "collection_tasks", run_id=run, task_type="search",
                       search_request_id=request, sequence_no=n)
        return run

    def _reconcile(self, run):
        """Repair only the gap after atomic evidence commit, never uncertain I/O."""
        rows = self.conn.execute("""SELECT t.*,a.status AS attempt_status,a.returned_posts
            FROM collection_tasks t JOIN collection_attempts a USING(task_id)
            WHERE t.run_id=? AND t.status='running' AND a.attempt_no=
            (SELECT max(b.attempt_no) FROM collection_attempts b WHERE b.task_id=t.task_id)""", (run,)).fetchall()
        for row in rows:
            if row["attempt_status"] == "running":
                return "UncertainInFlightAttemptNeedsReview"
            with transaction(self.conn):
                self.conn.execute("UPDATE collection_tasks SET status=?,finished_at=? WHERE task_id=?",
                                  (row["attempt_status"], utc_now(), row["task_id"]))
                if row["task_type"] == "search":
                    self.conn.execute("UPDATE search_requests SET status=?,returned_count=?,finished_at=? WHERE search_request_id=?",
                                      (row["attempt_status"], row["returned_posts"] if row["attempt_status"] == "succeeded" else None,
                                       utc_now(), row["search_request_id"]))
        return None

    def _detail_tasks(self, run):
        # All normal candidates have a task; zero attempts always means unrequested.
        rows = self.conn.execute("""SELECT h.post_id,min(h.search_hit_id) AS first_hit
            FROM search_hits h JOIN search_requests q USING(search_request_id)
            WHERE q.run_id=? AND h.card_note_type='normal' GROUP BY h.post_id ORDER BY first_hit""", (run,)).fetchall()
        with transaction(self.conn):
            sequence = self.conn.execute("SELECT coalesce(max(sequence_no),0) FROM collection_tasks WHERE run_id=?", (run,)).fetchone()[0]
            for row in rows:
                if self.conn.execute("SELECT 1 FROM collection_tasks WHERE run_id=? AND post_id=?", (run, row[0])).fetchone():
                    continue
                sequence += 1
                insert(self.conn, "collection_tasks", run_id=run, post_id=row[0], task_type="feed_detail",
                       sequence_no=sequence, task_config_json=dumps(self.config.persisted()))

    def _call(self, task, *, request=None, source_id=None):
        if request is None and not self.phase(task["run_id"])["search_complete"]:
            return "SearchPhaseIncomplete"
        last = self.conn.execute("SELECT http_status,error_type FROM collection_attempts WHERE task_id=? ORDER BY attempt_no DESC LIMIT 1", (task["task_id"],)).fetchone()
        if last and (last["http_status"] in (401, 403, 429) or last["http_status"] is None
                     or (last["error_type"] or "").startswith("IngestionError")):
            return "UnsafeFailureNeedsReview;no_automatic_retry"
        number = self.conn.execute("SELECT coalesce(max(attempt_no),0)+1 FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        if number > self.config.max_attempts:
            return "AttemptBudgetExhausted"
        # Pause before every call, including the first resumed call.
        time.sleep(self.config.interval_seconds)
        if self.pause_requested(task["run_id"]):
            return "PauseRequested"
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status='running',finished_at=NULL WHERE task_id=?", (task["task_id"],))
            if request:
                self.conn.execute("UPDATE search_requests SET status='running',returned_count=NULL,finished_at=NULL,started_at=coalesce(started_at,?) WHERE search_request_id=?", (utc_now(), request["search_request_id"]))
        endpoint = "/api/v1/feeds/search" if request else "/api/v1/feeds/detail"
        attempt = self.begin_attempt(task["task_id"], number, endpoint)
        if request:
            response = self.adapter.search(request["keyword"], json.loads(request["filters_json"]))
            cache_error = None
            mapping = {}
            if response.ok:
                try:
                    for feed in search_items(response.payload):
                        feed = object_or_empty(feed)
                        card = object_or_empty(feed.get("noteCard"))
                        sid, token = stable_id(feed.get("id")), stable_id(feed.get("xsecToken"))
                        if sid and token and card.get("type") == "normal" and not card.get("video"):
                            mapping[sid] = token
                    if self.access_cache is not None:
                        self.access_cache.save(task["run_id"], attempt, mapping)
                except AccessCacheError:
                    cache_error = "AccessCacheSaveFailed"
                except ValueError:
                    pass  # The existing ingestion layer records malformed responses.
            event = self.record_response(attempt, response, request_id=request["search_request_id"])
        else:
            response = self.adapter.detail(source_id, self.access[source_id], **self.config.detail_options)
            event = self.record_response(attempt, response, post_id=task["post_id"], source_id=source_id)
        ok = not event["ingestion_error"]
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status=?,finished_at=? WHERE task_id=?", ("succeeded" if ok else "failed", utc_now(), task["task_id"]))
            if request:
                self.conn.execute("UPDATE search_requests SET status=?,returned_count=?,finished_at=? WHERE search_request_id=?",
                                  ("succeeded" if ok else "failed", len(search_items(response.payload)) if ok else None, utc_now(), request["search_request_id"]))
        if ok and request:
            self.access.update(mapping)
            if cache_error:
                return cache_error
        if ok and request is None:
            snapshot = self.conn.execute("SELECT snapshot_id FROM post_snapshots WHERE collection_attempt_id=?", (attempt,)).fetchone()[0]
            self.collect_media(task["run_id"], snapshot)
        self.checkpoint(task["run_id"])
        # Stop on ANY error (stronger than consecutive-error circuit breaker).
        # Explicit resume may retry known failures, bounded by max_attempts.
        return None if ok else "RequestOrIngestionFailed"

    def run(self, source_version=None, *, run_id=None, stage="all"):
        if not self.config.pilot and stage == "all":
            stage = "search"  # Formal details always need an explicit second action.
        if stage not in ("all", "search", "details"):
            raise ValueError("Unknown stage")
        if stage == "details" and run_id is None:
            raise ValueError("Details require an existing Run")
        run = run_id if run_id is not None else self.create(source_version)
        try:
            result = self._run(source_version, run_id=run, stage=stage)
        except KeyboardInterrupt:
            self.checkpoint(run, "PAUSED", "Interrupted;inflight_attempt_may_need_review")
            raise
        if result["result"] != "ALREADY_CLOSED":
            self.checkpoint(run, result["result"], result.get("reason"))
        return result

    def _run(self, source_version=None, *, run_id=None, stage="all"):
        run = run_id if run_id is not None else self.create(source_version)
        row = self.conn.execute("""SELECT r.status,p.comment_config_json FROM collection_runs r
            JOIN collection_plans p USING(plan_id) WHERE r.run_id=?""", (run,)).fetchone()
        if not row or json.loads(row["comment_config_json"]) != self.config.persisted():
            raise ValueError("Run/config mismatch")
        if row["status"] != "running":
            return self.summary(run, "ALREADY_CLOSED")
        reason = self._reconcile(run)
        if reason:
            return self.summary(run, "BLOCKED", reason)
        if stage == "details" and not self.phase(run)["search_complete"]:
            return self.summary(run, "BLOCKED", "SearchPhaseIncomplete")
        if self.access_cache is not None:
            attempts = [r[0] for r in self.conn.execute("""SELECT a.attempt_id FROM collection_attempts a JOIN collection_tasks t USING(task_id)
                WHERE t.run_id=? AND t.task_type='search' AND a.status='succeeded' ORDER BY a.attempt_id""", (run,))]
            try:
                self.access.update(self.access_cache.load(run, attempts))
            except AccessCacheError:
                return self.summary(run, "BLOCKED", "AccessCacheUnavailable;no_automatic_search_refresh")
        requests = [] if stage == "details" else self.conn.execute("SELECT * FROM search_requests WHERE run_id=? ORDER BY sequence_no", (run,)).fetchall()
        for request in requests:
            if request["status"] == "succeeded":
                continue
            if self.pause_requested(run):
                return self.summary(run, "PAUSED", "PauseRequested")
            task = self.conn.execute("SELECT * FROM collection_tasks WHERE search_request_id=?", (request["search_request_id"],)).fetchone()
            reason = self._call(task, request=request)
            self._detail_tasks(run)
            if reason:
                return self.summary(run, "PAUSED" if reason == "PauseRequested" else "BLOCKED", reason)
        self._detail_tasks(run)
        if not self.phase(run)["search_complete"]:
            return self.summary(run, "BLOCKED", "SearchPhaseIncomplete")
        self.checkpoint(run, "SEARCH_COMPLETE")
        if stage == "search":
            return self.summary(run, "PAUSED", "SearchComplete;explicit_details_start_required")
        tasks = self.conn.execute("""SELECT t.*,p.source_post_id FROM collection_tasks t JOIN posts p USING(post_id)
            WHERE t.run_id=? AND t.task_type='feed_detail' ORDER BY t.sequence_no""", (run,)).fetchall()
        selected = 0
        for position, task in enumerate(tasks):
            if self.config.detail_limit is not None and position >= self.config.detail_limit:
                break
            # A successful old task remains in the pilot's five-post selection.
            if task["status"] == "succeeded":
                for snapshot in self.conn.execute("SELECT s.snapshot_id FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id WHERE a.task_id=? AND a.status='succeeded'", (task["task_id"],)):
                    self.collect_media(run, snapshot[0])
                selected += 1
                continue
            if self.pause_requested(run):
                return self.summary(run, "PAUSED", "PauseRequested")
            if task["source_post_id"] not in self.access:
                if self.conn.execute("SELECT 1 FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchone():
                    return self.summary(run, "BLOCKED", "PreviouslyRequestedPostMissingAccess;selection_preserved")
                continue
            selected += 1
            reason = self._call(task, source_id=task["source_post_id"])
            if reason:
                return self.summary(run, "PAUSED" if reason == "PauseRequested" else "BLOCKED", reason)
        target = min(len(tasks), self.config.detail_limit) if self.config.detail_limit is not None else len(tasks)
        if selected < target:
            return self.summary(run, "BLOCKED", "MissingEphemeralAccessParameters;no_automatic_search_refresh")
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status='cancelled',finished_at=? WHERE run_id=? AND status='pending'", (utc_now(), run))
            self.conn.execute("UPDATE collection_runs SET status='completed',finished_at=? WHERE run_id=?", (utc_now(), run))
        return self.summary(run, "SUCCESS")

    def summary(self, run, result="INSPECT", reason=None):
        seen, groups = set(), []
        for q in self.conn.execute("SELECT * FROM search_requests WHERE run_id=? ORDER BY sequence_no", (run,)):
            hits = self.conn.execute("SELECT post_id FROM search_hits WHERE search_request_id=? ORDER BY search_hit_id", (q["search_request_id"],)).fetchall()
            ids = {r[0] for r in hits}
            groups.append({"keyword": q["keyword"], "sort": q["sort_method"], "status": q["status"],
                           "returned_items": q["returned_count"], "hits": len(hits), "new_posts": len(ids-seen)})
            seen.update(ids)
        detail = {"succeeded": 0, "failed": 0, "unrequested": 0, "uncertain": 0}
        for task in self.conn.execute("SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", (run,)):
            attempts = self.conn.execute("SELECT status FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchall()
            statuses = {a[0] for a in attempts}
            key = "unrequested" if not attempts else "succeeded" if "succeeded" in statuses else "uncertain" if "running" in statuses else "failed"
            detail[key] += 1
        totals = sum(g["hits"] for g in groups)
        timings = {r[0]: {"requests": r[1], "duration_ms": r[2]} for r in self.conn.execute("""SELECT t.task_type,count(*),coalesce(sum(a.duration_ms),0)
            FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=? GROUP BY t.task_type""", (run,))}
        evidence = audit(self.conn)
        # Keep whole-DB integrity evidence explicitly separate from Run metrics.
        database_audit = evidence
        project_id, plan_id = self.conn.execute("SELECT p.project_id,r.plan_id FROM collection_runs r JOIN collection_plans p USING(plan_id) WHERE r.run_id=?", (run,)).fetchone()
        attempt_sql = "SELECT a.attempt_id FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=?"
        snapshot_sql = f"SELECT snapshot_id FROM post_snapshots WHERE collection_attempt_id IN ({attempt_sql})"
        hit_sql = "SELECT h.post_id FROM search_hits h JOIN search_requests q USING(search_request_id) WHERE q.run_id=?"
        filters = {
            "research_projects": ("project_id=?", project_id), "collection_plans": ("plan_id=?", plan_id),
            "plan_keywords": ("plan_id=?", plan_id), "plan_sort_methods": ("plan_id=?", plan_id),
            "collection_runs": ("run_id=?", run), "search_requests": ("run_id=?", run), "collection_tasks": ("run_id=?", run),
            "collection_attempts": (f"attempt_id IN ({attempt_sql})", run), "raw_artifacts": (f"attempt_id IN ({attempt_sql})", run),
            "posts": (f"post_id IN ({hit_sql})", run),
            "search_hits": ("search_request_id IN (SELECT search_request_id FROM search_requests WHERE run_id=?)", run),
            "post_snapshots": (f"collection_attempt_id IN ({attempt_sql})", run),
            "post_media": (f"snapshot_id IN ({snapshot_sql})", run),
            "comment_observations": (f"collection_attempt_id IN ({attempt_sql})", run),
            "comments": (f"comment_id IN (SELECT comment_id FROM comment_observations WHERE collection_attempt_id IN ({attempt_sql}))", run),
            "dataset_versions": ("source_run_id=?", run),
            "dataset_posts": ("dataset_id IN (SELECT dataset_id FROM dataset_versions WHERE source_run_id=?)", run),
            "dataset_comments": ("dataset_id IN (SELECT dataset_id FROM dataset_versions WHERE source_run_id=?)", run),
        }
        counts = {table: self.conn.execute(f"SELECT count(*) FROM {table} WHERE {condition}", (param,)).fetchone()[0]
                  for table, (condition, param) in filters.items()}
        artifacts = {r[0] for r in self.conn.execute(f"SELECT raw_artifact_id FROM raw_artifacts WHERE attempt_id IN ({attempt_sql})", (run,))}
        quality = {"snapshots": counts["post_snapshots"]}
        for key, condition in {"empty_title": "title IS NULL OR title=''", "empty_description": "description IS NULL OR description=''",
                               "unverified_publish_time": "published_at IS NULL", "unavailable_likes": "likes_count IS NULL",
                               "unavailable_comments": "comments_count IS NULL", "unavailable_favorites": "favorites_count IS NULL",
                               "unavailable_shares": "shares_count IS NULL"}.items():
            quality[key] = self.conn.execute(f"SELECT count(*) FROM post_snapshots WHERE ({condition}) AND collection_attempt_id IN ({attempt_sql})", (run,)).fetchone()[0]
        quality["anonymous_observations"] = self.conn.execute(f"SELECT count(*) FROM comment_observations WHERE comment_id IS NULL AND collection_attempt_id IN ({attempt_sql})", (run,)).fetchone()[0]
        quality["unverified_comment_time"] = self.conn.execute(f"SELECT count(*) FROM comment_observations WHERE created_at IS NULL AND collection_attempt_id IN ({attempt_sql})", (run,)).fetchone()[0]
        quality["media_missing_url"] = self.conn.execute(f"SELECT count(*) FROM post_media WHERE (source_url IS NULL OR source_url='') AND snapshot_id IN ({snapshot_sql})", (run,)).fetchone()[0]
        evidence = {"table_counts": counts, "quality": quality, "database_audit": database_audit,
                    "artifact_checks": [a for a in database_audit["artifact_checks"] if a["raw_artifact_id"] in artifacts],
                    "relationship_checks": database_audit["relationship_checks"], "relationship_check_scope": "whole_database",
                    "integrity_check": database_audit["integrity_check"], "foreign_key_check": database_audit["foreign_key_check"],
                    "comment_level_counts": {str(r[0]): r[1] for r in self.conn.execute(f"SELECT comment_level,count(*) FROM comment_observations WHERE collection_attempt_id IN ({attempt_sql}) GROUP BY comment_level", (run,))},
                    "attempt_status_counts": {r[0]: r[1] for r in self.conn.execute(f"SELECT status,count(*) FROM collection_attempts WHERE attempt_id IN ({attempt_sql}) GROUP BY status", (run,))}}
        # Phase1C's metric counts candidates without tasks; Phase1D precreates tasks.
        # Use actual attempts, including pending/cancelled tasks with zero attempts.
        evidence["quality"]["unrequested_candidates"] = detail["unrequested"]
        if getattr(self, "media_store", None) is not None:
            try:
                evidence["media_downloads"] = self.media_store.summary(run)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                evidence["media_downloads"] = {"error": type(exc).__name__, "unfinished": "unknown"}
            evidence["media_errors"] = [e["media_error"] for e in self.events if "media_error" in e]
        return {"result": result, "reason": reason, "run_id": run, "project_id": project_id, "plan_id": plan_id,
                "phase": self.phase(run), "groups": groups,
                "unique_posts": len(seen), "hits": totals, "duplicate_rate": (totals-len(seen))/totals if totals else None,
                "normal_unique_posts": sum(detail.values()), "details": detail, "timings": timings,
                "extra_comment_requests": 0, "extra_comment_duration_ms": 0,
                "comment_timing": "included_in_detail", **evidence}
