"""Single-writer baseline scheduler over the existing append-only ingestion layer.

No pagination or implicit search refresh. Optional current-user encrypted cache.
Uncertain in-flight calls require review; only definitely recorded failures retry.
"""
import json
import hashlib
import math
import os
from pathlib import Path
import random
import re
import time

from marketlens.adapters.xiaohongshu_mcp import utc_now
from .baseline_config import BaselineConfig
from .collection_pipeline import CollectionPipeline, insert, audit
from .database import transaction
from .normalizers import comment_stats, dumps, object_or_empty, search_items, stable_id
from .access_cache import AccessCacheError, atomic_write
from .media_store import MediaStore


class BaselineCollector(CollectionPipeline):
    def __init__(self, conn, adapter, data_dir, config=None, access_cache=None, media_store=None,
                 *, detail_retry_policy=None, reviewed_timeout=None, detail_pacing_policy=None):
        super().__init__(conn, adapter, data_dir)
        self.config = config or BaselineConfig()
        if detail_retry_policy is not None and self.config.pilot:
            raise ValueError("Automatic detail retries are formal-only")
        self.detail_retry_policy = detail_retry_policy
        if detail_pacing_policy is not None and self.config.pilot:
            raise ValueError("Detail pacing is formal-only")
        self.detail_pacing_policy = detail_pacing_policy
        if reviewed_timeout is not None and (self.config.pilot or detail_retry_policy is None):
            raise ValueError("Reviewed recovery requires formal detail retry policy")
        self.reviewed_timeout = reviewed_timeout
        self._review_context = None
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
            result["comments"] = comment_stats(safe, source_id, target_unique=self.config.top_comment_target)
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
        deferred = self.conn.execute("SELECT count(*) FROM collection_tasks WHERE run_id=? AND task_type='feed_detail' AND status='partial'", (run,)).fetchone()[0]
        stage = "completed" if row["status"] in ("completed", "partial") else "pending_review" if complete and deferred else "details" if complete and detail_attempts else "search_complete" if complete else "search"
        return {"stage": stage, "search_complete": complete, "search_expected": len(expected),
                "search_succeeded": sum(r["status"] == "succeeded" for r in requests),
                "search_completed_at": max(r["finished_at"] for r in requests) if complete else None}

    def checkpoint(self, run, status="RUNNING", reason=None):
        state = {"run_id": run, **self.phase(run), "execution_status": status,
                 "reason": reason, "updated_at": utc_now()}
        if self.detail_retry_policy:
            state["detail_retry_policy"] = self.detail_retry_policy.persisted()
        if self.detail_pacing_policy:
            state["detail_pacing_policy"] = self.detail_pacing_policy.persisted()
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
        last = self.conn.execute("SELECT * FROM collection_attempts WHERE task_id=? ORDER BY attempt_no DESC LIMIT 1", (task["task_id"],)).fetchone()
        reviewed = request is None and self._is_reviewed_timeout(task, last)
        queue_activation = self._pending_activation(task, last) if reviewed and self._review_context is None else None
        if last and (last["http_status"] in (401, 403, 429) or last["http_status"] is None
                     or (last["error_type"] or "").startswith("IngestionError")) and not reviewed:
            return "UnsafeFailureNeedsReview;no_automatic_retry"
        number = self.conn.execute("SELECT coalesce(max(attempt_no),0)+1 FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        budget = self.detail_retry_policy.max_attempts if request is None and self.detail_retry_policy else self.config.max_attempts
        if number > budget:
            return "AttemptBudgetExhausted"
        # Pause before every call, including the first resumed call.
        if request is None and (self.detail_pacing_policy or
                (self.data_dir / "logs" / f"detail_pacing_wait_{task['task_id']}_{number}_before.json").exists()):
            reason = self._pacing_wait(task, "before", number)
            if reason:
                return reason
        else:
            time.sleep(self.config.interval_seconds)
        if self.pause_requested(task["run_id"]):
            return "PauseRequested"
        if reviewed and queue_activation is None:
            review_path = self.data_dir / "logs" / f"detail_review_{last['attempt_id']}.json"
            if (hashlib.sha256(review_path.read_bytes()).hexdigest() != self._review_context["record_sha256"]
                    or hashlib.sha256(Path(self.reviewed_timeout.evidence_file).read_bytes()).hexdigest() != self._review_context["evidence_sha256"]):
                raise ValueError("Review evidence changed before consumption")
        if queue_activation is not None and self._pending_activation(task, last) != queue_activation:
            raise ValueError("Pending activation changed before consumption")
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status='running',finished_at=NULL WHERE task_id=?", (task["task_id"],))
            if request:
                self.conn.execute("UPDATE search_requests SET status='running',returned_count=NULL,finished_at=NULL,started_at=coalesce(started_at,?) WHERE search_request_id=?", (utc_now(), request["search_request_id"]))
        endpoint = "/api/v1/feeds/search" if request else "/api/v1/feeds/detail"
        attempt = self.begin_attempt(task["task_id"], number, endpoint)
        if reviewed:
            # The newly committed Attempt consumes this decision BEFORE I/O.
            # A crash here leaves a running Attempt and cannot silently replay.
            self._retry_event(task, "pending_review_activation_consumed" if queue_activation else "review_consumed",
                              reviewed_attempt_id=last["attempt_id"], new_attempt_id=attempt,
                              review_sha256=queue_activation if queue_activation else self._review_context["record_sha256"])
            self._review_context = None
        started = time.monotonic()
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
        if request is None and self.detail_pacing_policy:
            self._pacing_event(task, "collected", attempt_id=attempt, attempt_no=number,
                               collection_seconds=time.monotonic() - started,
                               http_duration_ms=response.duration_ms,
                               status="succeeded" if ok else "failed", http_status=response.http_status,
                               error_type=response.error_type)
            if ok:
                reason = self._pacing_wait(task, "after", number)
                if reason:
                    return reason
        # Stop on ANY error (stronger than consecutive-error circuit breaker).
        # Explicit resume may retry known failures, bounded by max_attempts.
        return None if ok else "RequestOrIngestionFailed"

    def _pacing_event(self, task, event, **values):
        """Append scheduler evidence without response bodies or access parameters."""
        record = {"run_id": task["run_id"], "task_id": task["task_id"],
                  "event": event, "at": utc_now(), **values}
        with (self.data_dir / "logs" / f"detail_pacing_{task['run_id']}.jsonl").open("a+b") as handle:
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    raise ValueError("IncompletePacingJournalNeedsReview")
            handle.write((dumps(record) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def _pacing_wait(self, task, phase, number):
        # Keep each sampled deadline across pauses/restarts, even if runtime
        # bounds subsequently change. Never create new Attempts during a wait.
        path = self.data_dir / "logs" / f"detail_pacing_wait_{task['task_id']}_{number}_{phase}.json"
        if not path.exists():
            policy = self.detail_pacing_policy
            fixed = self.config.interval_seconds if phase == "before" else 0
            low, high = ((policy.buffer_min_seconds, policy.buffer_max_seconds) if phase == "before"
                         else (policy.after_min_seconds, policy.after_max_seconds))
            sampled = random.uniform(low, high)
            now = time.time()
            state = {"run_id": task["run_id"], "task_id": task["task_id"], "attempt_no": number,
                     "phase": phase, "fixed_seconds": fixed, "random_seconds": sampled,
                     "scheduled_at": now, "not_before": now + fixed + sampled}
            atomic_write(path, (dumps(state) + "\n").encode("utf-8"))
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state["run_id"] != task["run_id"] or state["task_id"] != task["task_id"]
                or state["attempt_no"] != number or state["phase"] != phase
                or not all(math.isfinite(state[k]) for k in ("fixed_seconds", "random_seconds", "scheduled_at", "not_before"))
                or min(state["fixed_seconds"], state["random_seconds"]) < 0
                or abs(state["not_before"] - state["scheduled_at"] - state["fixed_seconds"] - state["random_seconds"]) > .001):
            raise ValueError("InvalidPacingWaitEvidence")
        return self._pacing_deadline(task, state, phase, number)

    def _pacing_deadline(self, task, state, phase, number):
        self._pacing_event(task, "wait_started", **{k: v for k, v in state.items() if k not in ("run_id", "task_id")})
        started = time.monotonic()
        try:
            while True:
                if self.pause_requested(task["run_id"]):
                    return "PauseRequested"
                remaining = state["not_before"] - time.time()
                if remaining <= 0:
                    return None
                time.sleep(min(1.0, remaining))
        finally:
            self._pacing_event(task, "wait_finished", phase=phase, attempt_no=number,
                               actual_wait_seconds=time.monotonic() - started,
                               deadline_reached=time.time() >= state["not_before"])

    def _batch_boundary(self, task):
        """Append-only batch state in the existing pacing journal, under writer lock.

        A persisted activation watermark excludes historical successes. Querying
        committed Attempts also recovers a crash between success and logging it.
        """
        if self.config.pilot:
            return None
        run = task["run_id"]
        path = self.data_dir / "logs" / f"detail_pacing_{run}.jsonl"
        enabled = self.detail_pacing_policy and self.detail_pacing_policy.batch_enabled
        if not enabled and not path.exists():
            return None
        try:
            state = None
            if path.exists():
                data = path.read_bytes()
                if data and not data.endswith(b"\n"):
                    raise ValueError("IncompletePacingJournalNeedsReview")
                for line in data.splitlines():
                    event = json.loads(line)
                    if event["run_id"] != run:
                        raise ValueError("BatchRunMismatch")
                    if event["event"].startswith("batch_"):
                        state = event["batch_state"]
            if state is None:
                if not enabled:
                    return None
                watermark = self.conn.execute("SELECT coalesce(max(a.attempt_id),0) FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE t.run_id=?", (run,)).fetchone()[0]
                policy = self.detail_pacing_policy
                state = {"run_id": run, "after_attempt_id": watermark, "seen_tasks": [],
                         "completed": 0, "batch_no": 1,
                         "target": random.randint(policy.batch_min_tasks, policy.batch_max_tasks),
                         "policy": policy.persisted(), "rest": None}
                self._pacing_event(task, "batch_started", batch_state=state)
            policy = state["policy"]
            if (state["run_id"] != run or type(state["target"]) is not int
                    or not policy["batch_min_tasks"] <= state["target"] <= policy["batch_max_tasks"]
                    or type(state["completed"]) is not int or not 0 <= state["completed"] <= state["target"]
                    or len(state["seen_tasks"]) != len(set(state["seen_tasks"]))):
                raise ValueError("InvalidBatchState")
            if self.conn.execute("SELECT 1 FROM collection_tasks t LEFT JOIN collection_attempts a USING(task_id) WHERE t.run_id=? AND (t.status='running' OR a.status='running') LIMIT 1", (run,)).fetchone():
                return "UncertainInFlightAttemptNeedsReview"
            successes = [r[0] for r in self.conn.execute("SELECT DISTINCT t.task_id FROM collection_tasks t JOIN collection_attempts a USING(task_id) WHERE t.run_id=? AND t.task_type='feed_detail' AND a.status='succeeded' AND a.attempt_id>? ORDER BY t.task_id", (run, state["after_attempt_id"]))]
            if not set(state["seen_tasks"]).issubset(successes):
                raise ValueError("BatchSuccessEvidenceMissing")
            new = [sid for sid in successes if sid not in state["seen_tasks"]]
            if new:
                if state["rest"] or state["completed"] + len(new) > state["target"]:
                    raise ValueError("UnexpectedSuccessDuringBatchRest")
                state["seen_tasks"].extend(new)
                state["completed"] += len(new)
                event = "batch_counted"
                if state["completed"] == state["target"]:
                    delay = random.uniform(policy["batch_min_seconds"], policy["batch_max_seconds"])
                    now = time.time()
                    state["rest"] = {"phase": "batch", "attempt_no": state["batch_no"],
                                     "scheduled_at": now, "not_before": now + delay,
                                     "fixed_seconds": 0, "random_seconds": delay}
                    event = "batch_rest_scheduled"
                self._pacing_event(task, event, batch_state=state)
            rest = state["rest"]
            if rest is not None:
                if (state["completed"] != state["target"] or rest["attempt_no"] != state["batch_no"]
                        or not all(math.isfinite(rest[k]) for k in ("scheduled_at", "not_before", "random_seconds"))
                        or not policy["batch_min_seconds"] <= rest["random_seconds"] <= policy["batch_max_seconds"]
                        or abs(rest["not_before"] - rest["scheduled_at"] - rest["random_seconds"]) > .001):
                    raise ValueError("InvalidBatchRest")
                reason = self._pacing_deadline(task, rest, "batch", state["batch_no"])
                if reason:
                    return reason
                state["batch_no"] += 1
                state["completed"] = 0
                state["rest"] = None
                state["target"] = random.randint(policy["batch_min_tasks"], policy["batch_max_tasks"])
                self._pacing_event(task, "batch_rest_completed", batch_state=state)
            return None
        except (OSError, ValueError, KeyError, TypeError):
            return "BatchPacingEvidenceNeedsReview;no_automatic_retry"

    def _prepare_timeout_review(self, run, stage):
        self._review_context = None
        review = self.reviewed_timeout
        if review is None:
            return
        if stage != "details" or not self.phase(run)["search_complete"]:
            raise ValueError("Review requires completed search and explicit details stage")
        row = self.conn.execute("""SELECT a.*,t.run_id,t.post_id,t.task_type,t.status task_status
            FROM collection_attempts a JOIN collection_tasks t USING(task_id) WHERE a.attempt_id=?""", (review.attempt_id,)).fetchone()
        latest = self.conn.execute("SELECT max(attempt_no),count(*) FROM collection_attempts WHERE task_id=?", (row["task_id"],)).fetchone() if row else None
        if (row is None or row["run_id"] != run or row["task_type"] != "feed_detail"
                or row["task_status"] != "failed" or row["status"] != "failed"
                or row["endpoint"] != "/api/v1/feeds/detail" or row["error_type"] != "TimeoutError"
                or row["http_status"] is not None or not row["finished_at"]
                or latest[0] != row["attempt_no"]
                or max(latest) >= self.detail_retry_policy.max_attempts):
            raise ValueError("Review target is stale, ineligible or out of attempt budget")
        if self.conn.execute("""SELECT 1 FROM collection_attempts a JOIN collection_tasks t USING(task_id)
                WHERE t.run_id=? AND (a.status='running' OR t.status='running') LIMIT 1""", (run,)).fetchone():
            raise ValueError("Review cannot authorize an uncertain in-flight request")
        if self.conn.execute("""SELECT 1 FROM collection_attempts a LEFT JOIN post_snapshots s
                ON s.collection_attempt_id=a.attempt_id WHERE a.task_id=? AND (a.status='succeeded' OR s.snapshot_id IS NOT NULL) LIMIT 1""", (row["task_id"],)).fetchone():
            raise ValueError("Review cannot repeat an already successful detail")
        artifact = self.conn.execute("SELECT file_path,sha256 FROM raw_artifacts WHERE attempt_id=?", (review.attempt_id,)).fetchone()
        if artifact is None or hashlib.sha256(Path(artifact[0]).read_bytes()).hexdigest() != artifact[1]:
            raise ValueError("Review requires intact original failure evidence")
        evidence_bytes = Path(review.evidence_file).read_bytes()
        evidence = json.loads(evidence_bytes)
        db = self.conn.execute("PRAGMA database_list").fetchone()[2]
        binding = {"database_path": str(Path(db).resolve()) if db else ":memory:",
                   "run_id": run, "task_id": row["task_id"], "post_id": row["post_id"],
                   "attempt_id": review.attempt_id, "attempt_no": row["attempt_no"],
                   "finished_at": row["finished_at"], "raw_sha256": artifact[1]}
        if evidence.get("binding") != binding:
            raise ValueError("Review evidence does not match this database and Attempt")
        path = self.data_dir / "logs" / f"detail_review_{review.attempt_id}.json"
        decision = {"binding": binding, "reason": review.reason,
                    "server_request_finished_confirmed": True,
                    "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                    "evidence_path": str(Path(review.evidence_file).resolve()),
                    "allowed_next_attempt_no": row["attempt_no"] + 1}
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["decision"] != decision:
                raise ValueError("An immutable review already exists with different evidence")
            checkpoint = saved["original_checkpoint"]
        else:
            checkpoint = (self.data_dir / "logs" / f"checkpoint_{run}.json").read_bytes().decode("utf-8")
        if hashlib.sha256(checkpoint.encode("utf-8")).hexdigest() != evidence.get("checkpoint_sha256"):
            raise ValueError("Review checkpoint evidence mismatch")
        if not path.exists():
            # Exclusive append-only decision: preserve the blocked Checkpoint
            # before ordinary progress checkpoints are advanced by the scheduler.
            with path.open("xb") as handle:
                handle.write((dumps({"decision": decision, "original_checkpoint": checkpoint,
                                     "recorded_at": utc_now()}) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        self._review_context = {**binding, "evidence_sha256": decision["evidence_sha256"],
                                "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def _is_reviewed_timeout(self, task, attempt):
        context = self._review_context
        return bool(context and attempt and task["run_id"] == context["run_id"]
                    and task["task_id"] == context["task_id"] and attempt["attempt_id"] == context["attempt_id"]
                    and attempt["status"] == "failed" and attempt["error_type"] == "TimeoutError"
                    and attempt["http_status"] is None) or self._pending_activation(task, attempt) is not None

    def _pending_binding(self, run, task_id, post_id, attempt_id):
        task = self.conn.execute("SELECT * FROM collection_tasks WHERE task_id=?", (task_id,)).fetchone()
        target = self.conn.execute("SELECT * FROM collection_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if (task is None or target is None or task["run_id"] != run or task["post_id"] != post_id
                or task["task_type"] != "feed_detail" or target["task_id"] != task_id
                or target["status"] != "failed" or target["error_type"] != "TimeoutError"
                or target["http_status"] is not None or not target["finished_at"]
                or target["endpoint"] != "/api/v1/feeds/detail"):
            raise ValueError("Pending review requires a bound finished detail TimeoutError/null")
        history = self.conn.execute("""SELECT attempt_id,attempt_no,status,http_status,error_type,started_at,finished_at
            FROM collection_attempts WHERE task_id=? AND attempt_no<=? ORDER BY attempt_no""", (task_id, target["attempt_no"])).fetchall()
        artifacts = self.conn.execute("""SELECT a.attempt_id,f.sha256,f.file_path FROM collection_attempts a
            JOIN raw_artifacts f USING(attempt_id) WHERE a.task_id=? AND a.attempt_no<=? ORDER BY attempt_no""", (task_id, target["attempt_no"])).fetchall()
        if (len(artifacts) != len(history) or any(r["status"] != "failed" for r in history)
                or any(hashlib.sha256(Path(r["file_path"]).read_bytes()).hexdigest() != r["sha256"] for r in artifacts)):
            raise ValueError("Pending review history/raw evidence is incomplete")
        db = self.conn.execute("PRAGMA database_list").fetchone()[2]
        return {"database_path": str(Path(db).resolve()) if db else ":memory:", "run_id": run,
                "task_id": task_id, "post_id": post_id, "attempt_id": attempt_id,
                "history": [dict(r) for r in history],
                "raw_hashes": [{k:r[k] for k in ("attempt_id", "sha256")} for r in artifacts]}

    def _pending_entry(self, task):
        entries = []
        for path in (self.data_dir / "logs").glob(f"pending_review_{task['task_id']}_*.json"):
            if path.name.endswith("_activation.json"):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            decision = record["decision"]
            binding = self._pending_binding(task["run_id"], task["task_id"], task["post_id"], decision["binding"]["attempt_id"])
            evidence = Path(decision["evidence_path"]).read_bytes()
            if (decision["binding"] != binding or decision["classification"] not in ("comment_loading_stalled", "detail_timeout_unknown")
                    or hashlib.sha256(evidence).hexdigest() != decision["evidence_sha256"]
                    or json.loads(evidence)["binding"] != binding
                    or hashlib.sha256(record["original_checkpoint"].encode()).hexdigest() != decision["checkpoint_sha256"]):
                raise ValueError("Pending review evidence changed")
            activation_path = path.with_name(path.stem + "_activation.json")
            activation = json.loads(activation_path.read_text(encoding="utf-8")) if activation_path.exists() else None
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if activation is not None and (activation["review_sha256"] != digest or activation["binding"] != binding):
                raise ValueError("Pending activation binding changed")
            entries.append((binding["history"][-1]["attempt_no"], record, activation, digest))
        entries.sort(key=lambda x: x[0])
        if any(e[2] is None for e in entries[:-1]):
            raise ValueError("Unactivated historical pending review")
        return entries[-1] if entries else None

    def _pending_state(self, task):
        entry = self._pending_entry(task)
        if entry is None:
            if task["status"] == "partial":
                raise ValueError("Partial detail lacks pending-review evidence")
            return None
        number, record, activation, _ = entry
        latest = self.conn.execute("SELECT max(attempt_no) FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchone()[0]
        if activation is None:
            if latest != number or task["status"] != "partial" or task["finished_at"] != record["reviewed_at"]:
                raise ValueError("Pending review state requires explicit repair")
            return "pending_review"
        if latest == number and (task["status"] != "failed" or task["finished_at"] != activation["activated_at"]):
            raise ValueError("Pending activation state requires explicit repair")
        return "activated"

    def _pending_activation(self, task, attempt):
        if attempt is None or not hasattr(self, "data_dir") or task["task_type"] != "feed_detail":
            return None
        if self._pending_state(task) != "activated":
            return None
        number, record, activation, digest = self._pending_entry(task)
        if attempt["attempt_no"] == number and attempt["attempt_id"] == record["decision"]["binding"]["attempt_id"]:
            return digest
        return None

    def review_pending_detail(self, run, task_id, post_id, attempt_id, *, classification=None,
                              note, evidence_file=None, activate=False):
        """Local explicit defer/reactivate; never calls Adapter or changes Plan."""
        if (self.config.pilot or not note.strip() or len(note) > 2000
                or re.search(r"(?i)bearer\s|(?:token|cookie|password|authorization|secret)\s*[:=]", note)):
            raise ValueError("Formal pending review requires a safe human explanation")
        row = self.conn.execute("""SELECT r.status,p.comment_config_json FROM collection_runs r
            JOIN collection_plans p USING(plan_id) WHERE run_id=?""", (run,)).fetchone()
        if not row or row["status"] != "running" or json.loads(row["comment_config_json"]) != self.config.persisted():
            raise ValueError("Run/config mismatch; sealed Runs cannot be reactivated")
        if not self.phase(run)["search_complete"] or self.conn.execute("""SELECT 1 FROM collection_tasks t LEFT JOIN collection_attempts a USING(task_id)
                WHERE t.run_id=? AND (t.status='running' OR a.status='running') LIMIT 1""", (run,)).fetchone():
            raise ValueError("Incomplete search or uncertain request")
        binding = self._pending_binding(run, task_id, post_id, attempt_id)
        task = self.conn.execute("SELECT * FROM collection_tasks WHERE task_id=?", (task_id,)).fetchone()
        if (task["status"] not in ("failed", "partial")
                or (self.data_dir / "logs" / f"detail_unavailable_{task_id}.json").exists()
                or self.conn.execute("SELECT max(attempt_id) FROM collection_attempts WHERE task_id=?", (task_id,)).fetchone()[0] != attempt_id
                or self.conn.execute("SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON s.collection_attempt_id=a.attempt_id WHERE a.task_id=?", (task_id,)).fetchone()):
            raise ValueError("Stale review or already successful/unavailable task")
        path = self.data_dir / "logs" / f"pending_review_{task_id}_{attempt_id}.json"
        if activate:
            entry = self._pending_entry(task)
            if entry is None or entry[1]["decision"]["binding"] != binding or len(binding["history"]) >= 5 or binding["history"][-1]["attempt_no"] >= 5:
                raise ValueError("No matching deferred task or lifetime budget exhausted")
            activation_path = path.with_name(path.stem + "_activation.json")
            if activation_path.exists():
                activation = json.loads(activation_path.read_text(encoding="utf-8"))
                if activation["note"] != note:
                    raise ValueError("Activation decision is immutable")
            else:
                activation = {"binding": binding, "review_sha256": entry[3], "note": note,
                              "activated_at": utc_now(), "allowed_next_attempt_no": entry[0] + 1}
                self._write_exclusive_review(activation_path, activation)
            status, at = "failed", activation["activated_at"]
        else:
            if classification not in ("comment_loading_stalled", "detail_timeout_unknown") or evidence_file is None:
                raise ValueError("Explicit supported pending-review classification/evidence required")
            if path.with_name(path.stem + "_activation.json").exists():
                raise ValueError("This pending decision has already been activated")
            evidence_bytes = Path(evidence_file).read_bytes()
            evidence = json.loads(evidence_bytes)
            if evidence.get("binding") != binding:
                raise ValueError("Pending-review identity/evidence mismatch")
            decision = {"binding": binding, "classification": classification, "note": note,
                        "evidence_path": str(Path(evidence_file).resolve()), "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                        "checkpoint_sha256": evidence["checkpoint_sha256"]}
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["decision"] != decision or hashlib.sha256(record["original_checkpoint"].encode()).hexdigest() != decision["checkpoint_sha256"]:
                    raise ValueError("Pending review decision is immutable")
            else:
                checkpoint = (self.data_dir / "logs" / f"checkpoint_{run}.json").read_bytes()
                if hashlib.sha256(checkpoint).hexdigest() != decision["checkpoint_sha256"]:
                    raise ValueError("Pending review checkpoint mismatch")
                record = {"decision": decision, "reviewed_at": utc_now(), "original_checkpoint": checkpoint.decode("utf-8")}
                self._write_exclusive_review(path, record)
            status, at = "partial", record["reviewed_at"]
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status=?,finished_at=? WHERE task_id=?", (status, at, task_id))
        self._pending_state(self.conn.execute("SELECT * FROM collection_tasks WHERE task_id=?", (task_id,)).fetchone())
        return self.summary(run, "PENDING_REVIEW_ACTIVATED" if activate else "PENDING_REVIEW_CONFIRMED")

    @staticmethod
    def _write_exclusive_review(path, record):
        with path.open("xb") as handle:
            handle.write((dumps(record) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def _source_deleted_binding(self, run, task_id, post_id):
        # Deliberately scoped to the one user-reviewed source, never a general
        # timeout/cancelled-task escape hatch.
        if (run, task_id, post_id) != (1, 153, 108):
            raise ValueError("Source deletion review is restricted to Run1/Task153/Post108")
        task = self.conn.execute("SELECT * FROM collection_tasks WHERE task_id=?", (task_id,)).fetchone()
        if (task is None or task["run_id"] != run or task["post_id"] != post_id
                or task["task_type"] != "feed_detail" or task["status"] not in ("failed", "cancelled")):
            raise ValueError("Source deletion target mismatch")
        rows = self.conn.execute("""SELECT attempt_id,attempt_no,status,http_status,error_type,started_at,finished_at,duration_ms
            FROM collection_attempts WHERE task_id=? ORDER BY attempt_no""", (task_id,)).fetchall()
        if (len(rows) != 2 or [r["attempt_no"] for r in rows] != [1, 2]
                or any(r["status"] != "failed" or r["http_status"] is not None
                       or r["error_type"] != "TimeoutError" or not r["finished_at"] for r in rows)
                or self.conn.execute("SELECT count(*) FROM collection_attempts WHERE task_id=? AND endpoint='/api/v1/feeds/detail'", (task_id,)).fetchone()[0] != 2
                or self.conn.execute("SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON s.collection_attempt_id=a.attempt_id WHERE a.task_id=?", (task_id,)).fetchone()):
            raise ValueError("Source deletion requires the exact two failed details and no snapshot")
        artifacts = self.conn.execute("""SELECT attempt_id,sha256,file_path FROM raw_artifacts
            WHERE attempt_id IN (SELECT attempt_id FROM collection_attempts WHERE task_id=?) ORDER BY attempt_id""", (task_id,)).fetchall()
        if len(artifacts) != 2 or any(hashlib.sha256(Path(r["file_path"]).read_bytes()).hexdigest() != r["sha256"] for r in artifacts):
            raise ValueError("Source deletion requires intact original raw evidence")
        db = self.conn.execute("PRAGMA database_list").fetchone()[2]
        return {"database_path": str(Path(db).resolve()) if db else ":memory:", "run_id": run,
                "task_id": task_id, "post_id": post_id, "attempts": [dict(r) for r in rows],
                "raw_hashes": [{k: r[k] for k in ("attempt_id", "sha256")} for r in artifacts]}

    def _source_deleted_record(self, task):
        path = self.data_dir / "logs" / f"detail_unavailable_{task['task_id']}.json"
        if not path.exists():
            if task["status"] == "cancelled":
                raise ValueError("Cancelled detail lacks source deletion review")
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        decision = record["decision"]
        binding = self._source_deleted_binding(task["run_id"], task["task_id"], task["post_id"])
        evidence = Path(decision["evidence_path"]).read_bytes()
        if (decision["reason"] != "source_deleted" or decision["binding"] != binding
                or hashlib.sha256(evidence).hexdigest() != decision["evidence_sha256"]
                or json.loads(evidence)["binding"] != binding
                or hashlib.sha256(record["original_checkpoint"].encode("utf-8")).hexdigest() != decision["checkpoint_sha256"]
                or task["status"] != "cancelled" or task["finished_at"] != record["reviewed_at"]):
            raise ValueError("Incomplete or inconsistent source deletion review")
        return record

    def confirm_source_deleted(self, run, task_id, post_id, reason, evidence_file):
        """Explicit local-only confirmation. Never calls the Adapter or resumes."""
        if self.config.pilot or reason != "source_deleted":
            raise ValueError("Formal source_deleted confirmation required")
        row = self.conn.execute("""SELECT r.status,p.comment_config_json FROM collection_runs r
            JOIN collection_plans p USING(plan_id) WHERE run_id=?""", (run,)).fetchone()
        if not row or row["status"] != "running" or json.loads(row["comment_config_json"]) != self.config.persisted():
            raise ValueError("Run/config mismatch or closed Run")
        if not self.phase(run)["search_complete"] or self.conn.execute("""SELECT 1 FROM collection_tasks t
                LEFT JOIN collection_attempts a USING(task_id) WHERE t.run_id=?
                AND (t.status='running' OR a.status='running') LIMIT 1""", (run,)).fetchone():
            raise ValueError("Source deletion cannot bypass incomplete search or uncertain requests")
        binding = self._source_deleted_binding(run, task_id, post_id)
        evidence_bytes = Path(evidence_file).read_bytes()
        evidence = json.loads(evidence_bytes)
        if evidence.get("binding") != binding or evidence.get("reason") != reason:
            raise ValueError("Source deletion evidence binding mismatch")
        decision = {"binding": binding, "reason": reason, "evidence_path": str(Path(evidence_file).resolve()),
                    "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                    "checkpoint_sha256": evidence["checkpoint_sha256"]}
        path = self.data_dir / "logs" / f"detail_unavailable_{task_id}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["decision"] != decision:
                raise ValueError("Source deletion decision is immutable")
            if hashlib.sha256(record["original_checkpoint"].encode("utf-8")).hexdigest() != decision["checkpoint_sha256"]:
                raise ValueError("Archived deletion checkpoint is corrupted")
        else:
            checkpoint = (self.data_dir / "logs" / f"checkpoint_{run}.json").read_bytes()
            if hashlib.sha256(checkpoint).hexdigest() != decision["checkpoint_sha256"]:
                raise ValueError("Source deletion checkpoint mismatch")
            record = {"decision": decision, "reviewed_at": utc_now(),
                      "original_checkpoint": checkpoint.decode("utf-8")}
            with path.open("xb") as handle:
                handle.write((dumps(record) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        # Journal first, then the existing Task state. A crash in between blocks
        # resume; repeating this same explicit confirmation completes the gap.
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status='cancelled',finished_at=? WHERE task_id=? AND status='failed'", (record["reviewed_at"], task_id))
        task = self.conn.execute("SELECT * FROM collection_tasks WHERE task_id=?", (task_id,)).fetchone()
        self._source_deleted_record(task)
        return self.summary(run, "SOURCE_UNAVAILABLE_CONFIRMED", "source_deleted")

    def _retry_event(self, task, event, **values):
        """Durable local runtime evidence; no credentials or response text."""
        latest = self.conn.execute("SELECT attempt_id,attempt_no,status,http_status,error_type FROM collection_attempts WHERE task_id=? ORDER BY attempt_no DESC LIMIT 1", (task["task_id"],)).fetchone()
        record = {"run_id": task["run_id"], "task_id": task["task_id"], "event": event,
                  "at": utc_now(), "policy": self.detail_retry_policy.persisted(),
                  "latest_attempt": dict(latest) if latest else None, **values}
        with (self.data_dir / "logs" / f"detail_retry_{task['run_id']}.jsonl").open("a+b") as handle:
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    raise ValueError("IncompleteRetryJournalNeedsReview")
            handle.write((dumps(record) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def _retry_wait(self, task, attempt):
        # One immutable schedule per failed Attempt. Resume keeps the deadline;
        # the research Plan and completed Attempt rows are never rewritten.
        path = self.data_dir / "logs" / f"detail_retry_wait_{attempt['attempt_id']}.json"
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if (state["run_id"] != task["run_id"] or state["task_id"] != task["task_id"]
                    or state["attempt_id"] != attempt["attempt_id"]
                    or not 30 <= state["delay_seconds"] <= 300
                    or not 0 <= state["not_before"] - state["scheduled_at"] <= 301):
                raise ValueError("InvalidRetryWaitEvidence")
        else:
            delay = random.uniform(self.detail_retry_policy.min_seconds, self.detail_retry_policy.max_seconds)
            now = time.time()
            state = {"run_id": task["run_id"], "task_id": task["task_id"],
                     "attempt_id": attempt["attempt_id"], "attempt_no": attempt["attempt_no"],
                     "http_status": attempt["http_status"], "delay_seconds": delay,
                     "scheduled_at": now, "not_before": now + delay}
            atomic_write(path, (dumps(state) + "\n").encode("utf-8"))
        self._retry_event(task, "waiting", **{k: v for k, v in state.items() if k not in ("run_id", "task_id")})
        self.checkpoint(task["run_id"], "WAITING", "DetailTransientHTTPRetry")
        while True:
            if self.pause_requested(task["run_id"]):
                return "PauseRequested"
            remaining = state["not_before"] - time.time()
            if remaining <= 0:
                return None
            time.sleep(min(1.0, remaining))

    def _detail_with_retries(self, task, source_id):
        if self.detail_retry_policy is None:
            try:
                return self._call(task, source_id=source_id)
            except Exception:
                if self.detail_pacing_policy is None:
                    raise
                return "DetailPacingLocalErrorNeedsReview;no_automatic_retry"
        try:
            self._retry_event(task, "started")
            while True:
                attempts = self.conn.execute("SELECT * FROM collection_attempts WHERE task_id=? ORDER BY attempt_no", (task["task_id"],)).fetchall()
                if any(a["status"] == "running" for a in attempts):
                    reason = "UncertainInFlightAttemptNeedsReview"
                    break
                if any(a["status"] == "succeeded" for a in attempts):
                    # Repair a lost task-state update, without repeating successful I/O.
                    with transaction(self.conn):
                        self.conn.execute("UPDATE collection_tasks SET status='succeeded',finished_at=? WHERE task_id=?", (utc_now(), task["task_id"]))
                    self._retry_event(task, "success_already_recorded")
                    return None
                if attempts:
                    last = attempts[-1]
                    reviewed = self._is_reviewed_timeout(task, last)
                    if not reviewed and (last["status"] != "failed" or last["http_status"] not in (500, 502, 503, 504)
                            or last["error_type"] != "HTTPError"):
                        reason = "UnsafeFailureNeedsReview;no_automatic_retry"
                        break
                    # A 5xx wrapper can still contain an explicit login failure.
                    artifact = self.conn.execute("SELECT file_path,sha256 FROM raw_artifacts WHERE attempt_id=?", (last["attempt_id"],)).fetchone()
                    if artifact is None:
                        raise ValueError("MissingFailureEvidence")
                    content = Path(artifact[0]).read_bytes()
                    if hashlib.sha256(content).hexdigest() != artifact[1]:
                        raise ValueError("CorruptFailureEvidence")
                    evidence = json.loads(content)
                    text = dumps(evidence["response"]).casefold()
                    if any(marker in text for marker in ("\u8bbf\u95ee\u9891\u7e41", "\u8bbf\u95ee\u9650\u5236", "\u5b89\u5168\u9a8c\u8bc1", "access denied", "rate limit", "too many requests", "300013")):
                        reason = "LoginOrAccessFailureNeedsReview"
                        break
                    if any(marker in text for marker in ("login", "logged_in", "unauthorized", "authentication", "登录", "登陆", "cookie", "captcha", "验证码")):
                        reason = "LoginOrAccessFailureNeedsReview"
                        break
                    if max(a["attempt_no"] for a in attempts) >= self.detail_retry_policy.max_attempts or len(attempts) >= self.detail_retry_policy.max_attempts:
                        reason = "AttemptBudgetExhausted"
                        break
                    reason = self._retry_wait(task, last)
                    if reason:
                        break
                    # Login must still be unambiguously ready after the wait.
                    preflight, _ = self.adapter.preflight()
                    ready = preflight.get("ready") is True
                    self._retry_event(task, "retry_preflight", ready=ready)
                    if not ready:
                        reason = "PreflightFailed;no_automatic_retry"
                        break
                reason = self._call(task, source_id=source_id)
                if reason is None:
                    self._retry_event(task, "succeeded")
                    return None
                if reason != "RequestOrIngestionFailed":
                    break
            self._retry_event(task, "stopped", reason=reason)
            return reason
        except Exception as exc:
            # Includes local SQLite/filesystem/preflight errors. Never reinterpret
            # them as an HTTP failure, and never repeat an uncertain request.
            reason = "DetailRetryLocalErrorNeedsReview:" + type(exc).__name__
            try:
                self._retry_event(task, "stopped", reason=reason)
            except Exception:
                self.events.append({"detail_retry_error": reason})
            return reason

    def run(self, source_version=None, *, run_id=None, stage="all"):
        if not self.config.pilot and stage == "all":
            stage = "search"  # Formal details always need an explicit second action.
        if stage not in ("all", "search", "details"):
            raise ValueError("Unknown stage")
        if stage == "details" and run_id is None:
            raise ValueError("Details require an existing Run")
        run = run_id if run_id is not None else self.create(source_version)
        from .run_finalization import intent_path
        if intent_path(self.data_dir, run).exists():
            closed = self.conn.execute("SELECT status FROM collection_runs WHERE run_id=?", (run,)).fetchone()[0] != "running"
            return self.summary(run, "ALREADY_CLOSED" if closed else "BLOCKED",
                                "LocalFinalizationIntent;repeat_explicit_finalization")
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
        self._prepare_timeout_review(run, stage)
        if self.detail_retry_policy and stage == "details" and self.conn.execute("""SELECT 1 FROM collection_attempts a
                JOIN collection_tasks t USING(task_id) WHERE t.run_id=? AND a.status='running' LIMIT 1""", (run,)).fetchone():
            return self.summary(run, "BLOCKED", "UncertainInFlightAttemptNeedsReview")
        reason = self._reconcile(run)
        if reason:
            return self.summary(run, "BLOCKED", reason)
        if self.detail_retry_policy and stage == "details" and self.conn.execute(
                "SELECT 1 FROM collection_tasks WHERE run_id=? AND status='running' LIMIT 1", (run,)).fetchone():
            return self.summary(run, "BLOCKED", "UncertainTaskStateNeedsReview")
        if stage == "details" and not self.phase(run)["search_complete"]:
            return self.summary(run, "BLOCKED", "SearchPhaseIncomplete")
        unavailable, pending_review = set(), set()
        if stage == "details" and not self.config.pilot:
            try:
                for task in self.conn.execute("SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", (run,)):
                    if self._source_deleted_record(task):
                        unavailable.add(task["task_id"])
            except (OSError, ValueError, KeyError, TypeError):
                return self.summary(run, "BLOCKED", "SourceDeletionReviewNeedsRepair;no_automatic_skip")
            try:
                for task in self.conn.execute("SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", (run,)):
                    if self._pending_state(task) == "pending_review":
                        pending_review.add(task["task_id"])
            except (OSError, ValueError, KeyError, TypeError):
                return self.summary(run, "BLOCKED", "PendingReviewEvidenceNeedsRepair;no_automatic_skip")
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
        if self.detail_retry_policy and any(t["status"] != "succeeded" and t["task_id"] not in unavailable | pending_review and t["source_post_id"] not in self.access for t in tasks):
            return self.summary(run, "BLOCKED", "MissingEphemeralAccessParameters;no_automatic_search_refresh")
        if tasks:
            reason = self._batch_boundary(tasks[0])
            if reason:
                return self.summary(run, "PAUSED" if reason == "PauseRequested" else "BLOCKED", reason)
        selected = 0
        for position, task in enumerate(tasks):
            if self.config.detail_limit is not None and position >= self.config.detail_limit:
                break
            if task["task_id"] in unavailable | pending_review:
                selected += 1  # Accounted for, but never a successful detail.
                continue
            # A successful old task remains in the pilot's five-post selection.
            if task["status"] == "succeeded":
                latest = self.conn.execute("SELECT attempt_no FROM collection_attempts WHERE task_id=? AND status='succeeded' ORDER BY attempt_no DESC LIMIT 1", (task["task_id"],)).fetchone()
                if latest and (self.data_dir / "logs" / f"detail_pacing_wait_{task['task_id']}_{latest[0]}_after.json").exists():
                    reason = self._pacing_wait(task, "after", latest[0])
                    if reason:
                        return self.summary(run, "PAUSED", reason)
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
            reason = self._detail_with_retries(task, task["source_post_id"])
            if reason:
                return self.summary(run, "PAUSED" if reason == "PauseRequested" else "BLOCKED", reason)
            reason = self._batch_boundary(task)
            if reason:
                return self.summary(run, "PAUSED" if reason == "PauseRequested" else "BLOCKED", reason)
        target = min(len(tasks), self.config.detail_limit) if self.config.detail_limit is not None else len(tasks)
        if selected < target:
            return self.summary(run, "BLOCKED", "MissingEphemeralAccessParameters;no_automatic_search_refresh")
        if pending_review:
            # A sealed partial Run is immutable under the existing Triggers.
            # Report provisional PARTIAL, but retain the original Run for refill.
            return self.summary(run, "PARTIAL", "PendingReview;run_open_for_explicit_refill")
        with transaction(self.conn):
            self.conn.execute("UPDATE collection_tasks SET status='cancelled',finished_at=? WHERE run_id=? AND status='pending'", (utc_now(), run))
            self.conn.execute("UPDATE collection_runs SET status=?,finished_at=? WHERE run_id=?", ("partial" if unavailable else "completed", utc_now(), run))
        return self.summary(run, "PARTIAL" if unavailable else "SUCCESS")

    def summary(self, run, result="INSPECT", reason=None):
        seen, groups = set(), []
        for q in self.conn.execute("SELECT * FROM search_requests WHERE run_id=? ORDER BY sequence_no", (run,)):
            hits = self.conn.execute("SELECT post_id FROM search_hits WHERE search_request_id=? ORDER BY search_hit_id", (q["search_request_id"],)).fetchall()
            ids = {r[0] for r in hits}
            groups.append({"keyword": q["keyword"], "sort": q["sort_method"], "status": q["status"],
                           "returned_items": q["returned_count"], "hits": len(hits), "new_posts": len(ids-seen)})
            seen.update(ids)
        detail = {"succeeded": 0, "failed": 0, "unrequested": 0, "uncertain": 0}
        confirmed_unavailable, pending_review_count, permanent_skip_count = 0, 0, 0
        from .run_finalization import read_finalization
        finalization, finalization_error = None, None
        try:
            finalization = read_finalization(self.conn, self.data_dir, run)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            finalization_error = type(exc).__name__
        final_skips = {d["task_id"] for d in finalization["intent"]["dispositions"]
                       if d["disposition"] == "banned_or_restricted"} if finalization and finalization["sealed"] else set()
        for task in self.conn.execute("SELECT * FROM collection_tasks WHERE run_id=? AND task_type='feed_detail'", (run,)):
            if task["task_id"] in final_skips:
                permanent_skip_count += 1
                continue
            attempts = self.conn.execute("SELECT status FROM collection_attempts WHERE task_id=?", (task["task_id"],)).fetchall()
            statuses = {a[0] for a in attempts}
            if hasattr(self, "data_dir"):
                try:
                    if self._pending_state(task) == "pending_review":
                        pending_review_count += 1
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Invalid audit never authorizes a skip.
            if task["status"] == "cancelled" and hasattr(self, "data_dir"):
                try:
                    if self._source_deleted_record(task):
                        confirmed_unavailable += 1
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Never infer a reviewed skip from cancelled alone.
            key = "unrequested" if not attempts else "succeeded" if "succeeded" in statuses else "uncertain" if "running" in statuses else "failed"
            detail[key] += 1
        if confirmed_unavailable:
            detail["confirmed_unavailable"] = confirmed_unavailable
        if pending_review_count:
            detail["pending_review"] = pending_review_count
        if permanent_skip_count:
            detail["banned_or_restricted"] = permanent_skip_count
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
        finalization_info = {} if finalization is None and finalization_error is None else {
            "local_finalization": {"error": finalization_error} if finalization_error else {
                "sealed": finalization["sealed"], "checkpoint_synchronized": finalization["checkpoint_synchronized"],
                "recovery_required": finalization["recovery_required"], "sha256": finalization["sha256"],
                "counts": finalization["intent"]["counts"]},
            "permanent_skip_count": permanent_skip_count}
        return {"result": result, "reason": reason, "run_id": run, "project_id": project_id, "plan_id": plan_id,
                "confirmed_unavailable_count": confirmed_unavailable,
                "pending_review_count": pending_review_count,
                "run_sealed": self.conn.execute("SELECT status FROM collection_runs WHERE run_id=?", (run,)).fetchone()[0] != "running",
                "phase": self.phase(run), "groups": groups,
                "unique_posts": len(seen), "hits": totals, "duplicate_rate": (totals-len(seen))/totals if totals else None,
                "normal_unique_posts": sum(detail.values()), "details": detail, "timings": timings,
                "extra_comment_requests": 0, "extra_comment_duration_ms": 0,
                "comment_timing": "included_in_detail", **evidence, **finalization_info}
