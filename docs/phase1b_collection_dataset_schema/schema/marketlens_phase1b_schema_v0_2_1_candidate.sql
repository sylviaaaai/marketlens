-- Task 1B contracts (synthetic tests only; no production migration):
-- Every connection MUST enable AND verify foreign_keys=1, recursive_triggers=1.
-- Do not use REPLACE. Conflict guards also reject duplicate inserts / blind UPSERTs.
-- Idempotent import: verify existing evidence, then skip it; explicit DO NOTHING
-- may be used with INSERT ... SELECT ... WHERE NOT EXISTS inside a transaction.
-- frozen_at = freeze operation time. selection_rules_json.observation_cutoff_at
-- optionally fixes an earlier observation cutoff; otherwise cutoff=frozen_at.
-- All normalized times: YYYY-MM-DDTHH:MM:SS.sssZ, valid Gregorian UTC, year>=0001.
-- observed_at is OUR observation time, not an unverified platform edit time.
-- Stable ties: snapshot_id / observation_id descending. Select latest NORMAL
-- successful/partial snapshot within cutoff; retain every unique Run candidate.
-- source_path: list[0] or list[0].subComments[0], canonical zero-based integers.
-- Count raw JSON: object with optional likes/comments/favorites/shares string|null.
-- Parse JSON: same keys, each {raw:string|null,value:integer|null,precision:enum}.
-- No inferred exact counts: non-null numeric columns require matching metadata.
-- Missing raw=>missing/null; known unparsed raw=>unparseable/null. Parsing belongs
-- to the future Adapter, including seconds/milliseconds detection; unknown UTC=NULL.
-- Search Task {} inherits immutable Request config; optional request_method/filters
-- must match. Attempts use the actual MCP-service HTTP method, NOT every internal
-- browser request. A failed/cancelled mismatched method remains failure evidence.
-- Media local_path is a cache locator excluded from frozen experimental content.
-- MarketLens Phase 1B — SQLite DDL v0.2.1 (CANDIDATE; Task 1B)
-- Reconciled with user-supplied 18-table/162-column DBML on 2026-10-13.
-- Adds precisely 3 columns to the baseline: parent_observation_id, source_path,
-- engagement_parse_json. Extra indexes/triggers are deliberately SQL-only.
-- Purpose: a proposed schema for a NEW, EMPTY research database.
-- Do NOT run against the existing marketlens.db / legacy products, snapshots tables.
-- No migrations, inserts, source calls, crawling, or data modifications are included.
--
-- Design conventions:
--   * TEXT times: UTC ISO-8601 (e.g. 2026-10-13T02:17:05.123Z).
--   * *_json: JSON serialized in TEXT, validated with SQLite JSON1.
--   * Unknown / unverified MCP fields are NULL, not fabricated defaults.
--   * IDs represent stable local identity; observations are append-only by policy.
--   * Comments without stable source IDs can still have observations with NULL comment_id.
--   * Raw artifacts may contain sensitive source data; use protected local storage.
--   * Database triggers below enforce major same-Run/append-only/freeze invariants.
--   * Manifest SHA256 and externally stored files still need application verification.
--   * IMPORTANT: This is an empty-DB CREATE script, NOT a migration.

PRAGMA foreign_keys = ON;
PRAGMA recursive_triggers = ON;
BEGIN TRANSACTION;

-- ============================================================
-- A. Research configuration (5 tables)
-- ============================================================
CREATE TABLE research_projects (
    project_id          INTEGER PRIMARY KEY,
    research_question   TEXT NOT NULL CHECK (length(trim(research_question)) > 0),
    category            TEXT NOT NULL CHECK (length(trim(category)) > 0),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'archived'))
    ,CHECK (created_at IS NULL OR (typeof(created_at)='text' AND length(created_at)=24 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',created_at) IS created_at AND date(created_at,'+0 days') IS substr(created_at,1,10)))
);

CREATE TABLE collection_plans (
    plan_id             INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL REFERENCES research_projects(project_id),
    plan_name           TEXT NOT NULL CHECK (length(trim(plan_name)) > 0),
    source_platform     TEXT NOT NULL DEFAULT 'xiaohongshu',
    search_filters_json TEXT CHECK (search_filters_json IS NULL OR
                        (json_valid(search_filters_json) AND json_type(search_filters_json) = 'object')),
    comment_config_json TEXT CHECK (comment_config_json IS NULL OR
                        (json_valid(comment_config_json) AND json_type(comment_config_json) = 'object')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    ,CHECK (project_id IS NULL OR typeof(project_id)='integer'),
    CHECK (created_at IS NULL OR (typeof(created_at)='text' AND length(created_at)=24 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',created_at) IS created_at AND date(created_at,'+0 days') IS substr(created_at,1,10)))
);

-- Fixed, ordered plan members; plans become immutable once used by any Run.
CREATE TABLE plan_keywords (
    keyword_id          INTEGER PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES collection_plans(plan_id),
    keyword             TEXT NOT NULL CHECK (length(trim(keyword)) > 0),
    sequence_no         INTEGER NOT NULL CHECK (sequence_no >= 1),
    UNIQUE (plan_id, keyword),
    UNIQUE (plan_id, sequence_no)
    ,CHECK (plan_id IS NULL OR typeof(plan_id)='integer'),
    CHECK (sequence_no IS NULL OR typeof(sequence_no)='integer')
);

CREATE TABLE plan_sort_methods (
    sort_method_id      INTEGER PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES collection_plans(plan_id),
    sort_method         TEXT NOT NULL CHECK (sort_method IN
                        ('综合','最新','最多点赞','最多评论','最多收藏')),
    sequence_no         INTEGER NOT NULL CHECK (sequence_no >= 1),
    UNIQUE (plan_id, sort_method),
    UNIQUE (plan_id, sequence_no)
    ,CHECK (plan_id IS NULL OR typeof(plan_id)='integer'),
    CHECK (sequence_no IS NULL OR typeof(sequence_no)='integer')
);

CREATE TABLE collection_runs (
    run_id              INTEGER PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES collection_plans(plan_id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','completed','partial','failed','cancelled')),
    started_at          TEXT,
    finished_at         TEXT,
    adapter_version     TEXT,
    source_version      TEXT,  -- NULL until current deployed MCP version is confirmed
    error_summary       TEXT
    ,CHECK (plan_id IS NULL OR typeof(plan_id)='integer'),
    CHECK (started_at IS NULL OR (typeof(started_at)='text' AND length(started_at)=24 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(started_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',started_at) IS started_at AND date(started_at,'+0 days') IS substr(started_at,1,10))),
    CHECK (finished_at IS NULL OR (typeof(finished_at)='text' AND length(finished_at)=24 AND finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(finished_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',finished_at) IS finished_at AND date(finished_at,'+0 days') IS substr(finished_at,1,10))),
    CHECK (started_at IS NULL OR finished_at IS NULL OR finished_at>=started_at)
);

-- ============================================================
-- B. Search provenance (2 tables)
-- ============================================================
CREATE TABLE search_requests (
    search_request_id   INTEGER PRIMARY KEY,
    run_id              INTEGER NOT NULL REFERENCES collection_runs(run_id),
    sequence_no         INTEGER NOT NULL CHECK (sequence_no >= 1),
    keyword             TEXT NOT NULL CHECK (length(trim(keyword)) > 0),
    sort_method         TEXT NOT NULL CHECK (sort_method IN
                        ('综合','最新','最多点赞','最多评论','最多收藏')),
    filters_json        TEXT CHECK (filters_json IS NULL OR
                        (json_valid(filters_json) AND json_type(filters_json) = 'object')),
    request_method      TEXT NOT NULL DEFAULT 'POST'
                        CHECK (request_method IN ('GET','POST')),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    started_at          TEXT,
    finished_at         TEXT,
    returned_count      INTEGER CHECK (returned_count IS NULL OR returned_count >= 0),
    -- Failed/noncompleted searches are NOT represented as successful empty results.
    CHECK ((status = 'succeeded' AND returned_count IS NOT NULL) OR
           (status <> 'succeeded' AND returned_count IS NULL)),
    UNIQUE (run_id, sequence_no),
    UNIQUE (run_id, keyword, sort_method)
    ,CHECK (run_id IS NULL OR typeof(run_id)='integer'),
    CHECK (sequence_no IS NULL OR typeof(sequence_no)='integer'),
    CHECK (started_at IS NULL OR (typeof(started_at)='text' AND length(started_at)=24 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(started_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',started_at) IS started_at AND date(started_at,'+0 days') IS substr(started_at,1,10))),
    CHECK (finished_at IS NULL OR (typeof(finished_at)='text' AND length(finished_at)=24 AND finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(finished_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',finished_at) IS finished_at AND date(finished_at,'+0 days') IS substr(finished_at,1,10))),
    CHECK (returned_count IS NULL OR typeof(returned_count)='integer'),
    CHECK (started_at IS NULL OR finished_at IS NULL OR finished_at>=started_at),
    CHECK (request_method='POST'),
    CHECK (filters_json IS NOT NULL AND json_extract(filters_json,'$.note_type') IS '图文' AND json_extract(filters_json,'$.sort_by') IS sort_method)
);

-- Create posts early, as search_hits points to the canonical post identity.
-- ============================================================
-- C. Note identity / observations / media (3 tables)
-- ============================================================
CREATE TABLE posts (
    post_id             INTEGER PRIMARY KEY,
    source_platform     TEXT NOT NULL,
    source_post_id      TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_platform, source_post_id)
    ,CHECK (first_seen_at IS NULL OR (typeof(first_seen_at)='text' AND length(first_seen_at)=24 AND first_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(first_seen_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',first_seen_at) IS first_seen_at AND date(first_seen_at,'+0 days') IS substr(first_seen_at,1,10))),
    CHECK (length(trim(source_post_id, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)))>0),
    CHECK (length(trim(source_platform))>0)
);

CREATE TABLE search_hits (
    search_hit_id       INTEGER PRIMARY KEY,
    search_request_id   INTEGER NOT NULL REFERENCES search_requests(search_request_id),
    collection_attempt_id INTEGER NOT NULL REFERENCES collection_attempts(attempt_id),
    post_id             INTEGER NOT NULL REFERENCES posts(post_id),
    result_position     INTEGER NOT NULL CHECK (result_position >= 1),
    source_index        INTEGER,
    access_params_json  TEXT CHECK (access_params_json IS NULL OR json_valid(access_params_json)),
    raw_artifact_id     INTEGER REFERENCES raw_artifacts(raw_artifact_id),
    card_title          TEXT,
    card_note_type      TEXT,
    card_engagement_json TEXT CHECK (card_engagement_json IS NULL OR json_valid(card_engagement_json)),
    card_cover_json     TEXT CHECK (card_cover_json IS NULL OR json_valid(card_cover_json)),
    UNIQUE (collection_attempt_id, result_position)
    ,CHECK (search_request_id IS NULL OR typeof(search_request_id)='integer'),
    CHECK (collection_attempt_id IS NULL OR typeof(collection_attempt_id)='integer'),
    CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (result_position IS NULL OR typeof(result_position)='integer'),
    CHECK (source_index IS NULL OR typeof(source_index)='integer'),
    CHECK (raw_artifact_id IS NULL OR typeof(raw_artifact_id)='integer')
);

CREATE TABLE post_snapshots (
    snapshot_id         INTEGER PRIMARY KEY,
    post_id             INTEGER NOT NULL REFERENCES posts(post_id),
    collection_attempt_id INTEGER NOT NULL REFERENCES collection_attempts(attempt_id),
    observed_at         TEXT NOT NULL,
    title               TEXT,
    description         TEXT,
    note_type           TEXT,
    published_at_raw    TEXT,
    published_at        TEXT,
    likes_count         INTEGER CHECK (likes_count IS NULL OR likes_count >= 0),
    comments_count      INTEGER CHECK (comments_count IS NULL OR comments_count >= 0),
    favorites_count     INTEGER CHECK (favorites_count IS NULL OR favorites_count >= 0),
    shares_count        INTEGER CHECK (shares_count IS NULL OR shares_count >= 0),
    engagement_raw_json TEXT CHECK (engagement_raw_json IS NULL OR json_valid(engagement_raw_json)),
    engagement_parse_json TEXT CHECK (engagement_parse_json IS NULL OR
                        (json_valid(engagement_parse_json) AND json_type(engagement_parse_json)='object')),
    author_source_id    TEXT,
    author_display_name TEXT,
    raw_artifact_id     INTEGER REFERENCES raw_artifacts(raw_artifact_id),
    UNIQUE (post_id, collection_attempt_id),
    UNIQUE (snapshot_id, post_id) -- enables dataset_posts snapshot/post consistency FK
    ,CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (collection_attempt_id IS NULL OR typeof(collection_attempt_id)='integer'),
    CHECK (observed_at IS NULL OR (typeof(observed_at)='text' AND length(observed_at)=24 AND observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(observed_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',observed_at) IS observed_at AND date(observed_at,'+0 days') IS substr(observed_at,1,10))),
    CHECK (published_at IS NULL OR (typeof(published_at)='text' AND length(published_at)=24 AND published_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(published_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',published_at) IS published_at AND date(published_at,'+0 days') IS substr(published_at,1,10))),
    CHECK (likes_count IS NULL OR typeof(likes_count)='integer'),
    CHECK (comments_count IS NULL OR typeof(comments_count)='integer'),
    CHECK (favorites_count IS NULL OR typeof(favorites_count)='integer'),
    CHECK (shares_count IS NULL OR typeof(shares_count)='integer'),
    CHECK (raw_artifact_id IS NULL OR typeof(raw_artifact_id)='integer'),
    CHECK (published_at IS NULL OR published_at_raw IS NOT NULL),
    CHECK (engagement_raw_json IS NULL OR json_type(engagement_raw_json)='object')
);

CREATE TABLE post_media (
    media_id            INTEGER PRIMARY KEY,
    snapshot_id         INTEGER NOT NULL REFERENCES post_snapshots(snapshot_id),
    media_type          TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (media_type IN ('image','video','unknown')),
    source_field        TEXT NOT NULL,  -- e.g. imageList / video.media.stream
    position            INTEGER NOT NULL CHECK (position >= 1),
    source_url          TEXT,
    thumbnail_url       TEXT,
    local_path          TEXT,           -- NULL if only link/metadata was retained
    media_raw_json      TEXT CHECK (media_raw_json IS NULL OR json_valid(media_raw_json)),
    UNIQUE (snapshot_id, source_field, position)
    ,CHECK (snapshot_id IS NULL OR typeof(snapshot_id)='integer'),
    CHECK (position IS NULL OR typeof(position)='integer')
);

-- ============================================================
-- C. Comment identity / per-fetch observations (2 tables)
-- ============================================================
CREATE TABLE comments (
    comment_id          INTEGER PRIMARY KEY,
    post_id             INTEGER NOT NULL REFERENCES posts(post_id),
    source_comment_id   TEXT,           -- unknown IDs go only into comment_observations
    parent_comment_id   INTEGER,
    comment_level       INTEGER NOT NULL CHECK (comment_level IN (1,2)),
    first_seen_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (post_id, source_comment_id), -- SQLite allows multiple NULL source IDs
    UNIQUE (comment_id, post_id),
    FOREIGN KEY (parent_comment_id, post_id)
        REFERENCES comments(comment_id, post_id),
    CHECK (parent_comment_id IS NULL OR parent_comment_id <> comment_id),
    CHECK (comment_level <> 1 OR parent_comment_id IS NULL)
    -- A level-2 comment can have a NULL parent temporarily if its identity cannot
    -- be resolved; comment_observations.parent_source_comment_id retains evidence.
    ,CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (parent_comment_id IS NULL OR typeof(parent_comment_id)='integer'),
    CHECK (comment_level IS NULL OR typeof(comment_level)='integer'),
    CHECK (first_seen_at IS NULL OR (typeof(first_seen_at)='text' AND length(first_seen_at)=24 AND first_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(first_seen_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',first_seen_at) IS first_seen_at AND date(first_seen_at,'+0 days') IS substr(first_seen_at,1,10)))
);

CREATE TABLE comment_observations (
    observation_id      INTEGER PRIMARY KEY,
    comment_id          INTEGER,
    post_id             INTEGER NOT NULL REFERENCES posts(post_id),
    collection_attempt_id INTEGER NOT NULL REFERENCES collection_attempts(attempt_id),
    observed_at         TEXT NOT NULL,
    content             TEXT,
    comment_raw_json    TEXT CHECK (comment_raw_json IS NULL OR json_valid(comment_raw_json)),
    parent_observation_id INTEGER REFERENCES comment_observations(observation_id),
    source_path         TEXT NOT NULL CHECK (length(trim(source_path)) > 0),
    created_at_raw      TEXT,
    created_at          TEXT,
    like_count_raw      TEXT,
    like_count          INTEGER CHECK (like_count IS NULL OR like_count >= 0),
    like_count_precision TEXT CHECK (like_count_precision IS NULL OR like_count_precision IN
                        ('exact_display','approximate','lower_bound','approximate_lower_bound','missing','unparseable')),
    comment_level       INTEGER NOT NULL CHECK (comment_level IN (1,2)),
    parent_source_comment_id TEXT,
    display_position    INTEGER CHECK (display_position IS NULL OR display_position >= 1),
    raw_artifact_id     INTEGER REFERENCES raw_artifacts(raw_artifact_id),
    FOREIGN KEY (comment_id, post_id)
        REFERENCES comments(comment_id, post_id),
    UNIQUE (observation_id, post_id),
    UNIQUE (observation_id, comment_id),
    UNIQUE (collection_attempt_id, source_path),
    CHECK (comment_level <> 1 OR parent_observation_id IS NULL)
    ,CHECK (comment_id IS NULL OR typeof(comment_id)='integer'),
    CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (collection_attempt_id IS NULL OR typeof(collection_attempt_id)='integer'),
    CHECK (observed_at IS NULL OR (typeof(observed_at)='text' AND length(observed_at)=24 AND observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(observed_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',observed_at) IS observed_at AND date(observed_at,'+0 days') IS substr(observed_at,1,10))),
    CHECK (parent_observation_id IS NULL OR typeof(parent_observation_id)='integer'),
    CHECK (created_at IS NULL OR (typeof(created_at)='text' AND length(created_at)=24 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',created_at) IS created_at AND date(created_at,'+0 days') IS substr(created_at,1,10))),
    CHECK (like_count IS NULL OR typeof(like_count)='integer'),
    CHECK (comment_level IS NULL OR typeof(comment_level)='integer'),
    CHECK (display_position IS NULL OR typeof(display_position)='integer'),
    CHECK (raw_artifact_id IS NULL OR typeof(raw_artifact_id)='integer'),
    CHECK (created_at IS NULL OR created_at_raw IS NOT NULL),
    CHECK (comment_level<>1 OR parent_source_comment_id IS NULL),
    CHECK (parent_source_comment_id IS NULL OR length(trim(parent_source_comment_id))>0),
    CHECK (
        (like_count IS NULL AND
          ((like_count_raw IS NULL AND (like_count_precision IS NULL OR like_count_precision='missing')) OR
           (like_count_raw IS NOT NULL AND like_count_precision IS 'unparseable')))
        OR (like_count IS NOT NULL AND like_count_raw IS NOT NULL AND
            like_count_precision IS NOT NULL AND like_count_precision IN ('exact_display','approximate','lower_bound','approximate_lower_bound'))
        )
);

-- ============================================================
-- D. Collection orchestration and original responses (3 tables)
-- ============================================================
CREATE TABLE collection_tasks (
    task_id             INTEGER PRIMARY KEY,
    run_id              INTEGER NOT NULL REFERENCES collection_runs(run_id),
    task_type           TEXT NOT NULL CHECK (task_type IN ('search','feed_detail')),
    search_request_id   INTEGER REFERENCES search_requests(search_request_id),
    post_id             INTEGER REFERENCES posts(post_id),
    task_config_json    TEXT NOT NULL DEFAULT '{}'
                        CHECK (json_valid(task_config_json) AND json_type(task_config_json) = 'object'),
    sequence_no         INTEGER NOT NULL CHECK (sequence_no >= 1),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','succeeded','partial','failed','cancelled')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at         TEXT,
    CHECK ((task_type='search' AND search_request_id IS NOT NULL AND post_id IS NULL)
        OR (task_type='feed_detail' AND search_request_id IS NULL AND post_id IS NOT NULL)),
    UNIQUE (run_id, sequence_no)
    ,CHECK (run_id IS NULL OR typeof(run_id)='integer'),
    CHECK (search_request_id IS NULL OR typeof(search_request_id)='integer'),
    CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (sequence_no IS NULL OR typeof(sequence_no)='integer'),
    CHECK (created_at IS NULL OR (typeof(created_at)='text' AND length(created_at)=24 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',created_at) IS created_at AND date(created_at,'+0 days') IS substr(created_at,1,10))),
    CHECK (finished_at IS NULL OR (typeof(finished_at)='text' AND length(finished_at)=24 AND finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(finished_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',finished_at) IS finished_at AND date(finished_at,'+0 days') IS substr(finished_at,1,10)))
);

CREATE TABLE collection_attempts (
    attempt_id          INTEGER PRIMARY KEY,
    task_id             INTEGER NOT NULL REFERENCES collection_tasks(task_id),
    attempt_no          INTEGER NOT NULL CHECK (attempt_no >= 1),
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    duration_ms         INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    http_method         TEXT CHECK (http_method IS NULL OR http_method IN ('GET','POST')),
    endpoint            TEXT,
    http_status         INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    status              TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed','cancelled')),
    error_type          TEXT,
    error_message       TEXT,           -- redacted; NEVER store Bearer / Cookie / token
    returned_posts      INTEGER CHECK (returned_posts IS NULL OR returned_posts >= 0),
    returned_top_comments INTEGER CHECK (returned_top_comments IS NULL OR returned_top_comments >= 0),
    returned_replies    INTEGER CHECK (returned_replies IS NULL OR returned_replies >= 0),
    response_summary_json TEXT CHECK (response_summary_json IS NULL OR json_valid(response_summary_json)),
    UNIQUE (task_id, attempt_no)
    ,CHECK (task_id IS NULL OR typeof(task_id)='integer'),
    CHECK (attempt_no IS NULL OR typeof(attempt_no)='integer'),
    CHECK (started_at IS NULL OR (typeof(started_at)='text' AND length(started_at)=24 AND started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(started_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',started_at) IS started_at AND date(started_at,'+0 days') IS substr(started_at,1,10))),
    CHECK (finished_at IS NULL OR (typeof(finished_at)='text' AND length(finished_at)=24 AND finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(finished_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',finished_at) IS finished_at AND date(finished_at,'+0 days') IS substr(finished_at,1,10))),
    CHECK (duration_ms IS NULL OR typeof(duration_ms)='integer'),
    CHECK (http_status IS NULL OR typeof(http_status)='integer'),
    CHECK (returned_posts IS NULL OR typeof(returned_posts)='integer'),
    CHECK (returned_top_comments IS NULL OR typeof(returned_top_comments)='integer'),
    CHECK (returned_replies IS NULL OR typeof(returned_replies)='integer'),
    CHECK (started_at IS NULL OR finished_at IS NULL OR finished_at>=started_at)
);

CREATE TABLE raw_artifacts (
    raw_artifact_id     INTEGER PRIMARY KEY,
    attempt_id          INTEGER NOT NULL REFERENCES collection_attempts(attempt_id),
    artifact_type       TEXT NOT NULL CHECK (artifact_type IN
                        ('search_response','detail_response','error_response')),
    file_path           TEXT NOT NULL UNIQUE,
    content_type        TEXT,
    file_size_bytes     INTEGER CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    sha256              TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
    saved_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    storage_status      TEXT NOT NULL DEFAULT 'saved'
                        CHECK (storage_status IN ('saved','missing','corrupted')),
    protection_status   TEXT
    -- Never embed authentication headers or plaintext bearer tokens here.
    ,CHECK (attempt_id IS NULL OR typeof(attempt_id)='integer'),
    CHECK (file_size_bytes IS NULL OR typeof(file_size_bytes)='integer'),
    CHECK (saved_at IS NULL OR (typeof(saved_at)='text' AND length(saved_at)=24 AND saved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(saved_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',saved_at) IS saved_at AND date(saved_at,'+0 days') IS substr(saved_at,1,10)))
);

-- ============================================================
-- E. Frozen research dataset membership (3 tables)
-- ============================================================
CREATE TABLE dataset_versions (
    dataset_id          INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL REFERENCES research_projects(project_id),
    dataset_name        TEXT NOT NULL,
    version             TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','frozen')),
    selection_rules_json TEXT CHECK (selection_rules_json IS NULL OR json_valid(selection_rules_json)),
    source_run_id      INTEGER NOT NULL REFERENCES collection_runs(run_id),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    frozen_at           TEXT,
    manifest_sha256     TEXT CHECK (manifest_sha256 IS NULL OR length(manifest_sha256)=64),
    CHECK ((status='draft' AND frozen_at IS NULL AND manifest_sha256 IS NULL) OR
           (status='frozen' AND frozen_at IS NOT NULL AND manifest_sha256 IS NOT NULL)),
    UNIQUE (project_id, dataset_name, version)
    ,CHECK (project_id IS NULL OR typeof(project_id)='integer'),
    CHECK (source_run_id IS NULL OR typeof(source_run_id)='integer'),
    CHECK (created_at IS NULL OR (typeof(created_at)='text' AND length(created_at)=24 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(created_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',created_at) IS created_at AND date(created_at,'+0 days') IS substr(created_at,1,10))),
    CHECK (frozen_at IS NULL OR (typeof(frozen_at)='text' AND length(frozen_at)=24 AND frozen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(frozen_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',frozen_at) IS frozen_at AND date(frozen_at,'+0 days') IS substr(frozen_at,1,10))),
    CHECK (selection_rules_json IS NULL OR json_type(selection_rules_json)='object'),
    CHECK (json_type(selection_rules_json,'$.observation_cutoff_at') IS NULL OR (json_type(selection_rules_json,'$.observation_cutoff_at') IS 'text' AND (typeof(json_extract(selection_rules_json,'$.observation_cutoff_at'))='text' AND length(json_extract(selection_rules_json,'$.observation_cutoff_at'))=24 AND json_extract(selection_rules_json,'$.observation_cutoff_at') GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(json_extract(selection_rules_json,'$.observation_cutoff_at'),1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',json_extract(selection_rules_json,'$.observation_cutoff_at')) IS json_extract(selection_rules_json,'$.observation_cutoff_at') AND date(json_extract(selection_rules_json,'$.observation_cutoff_at'),'+0 days') IS substr(json_extract(selection_rules_json,'$.observation_cutoff_at'),1,10)))),
    CHECK (frozen_at IS NULL OR json_extract(selection_rules_json,'$.observation_cutoff_at') IS NULL OR json_extract(selection_rules_json,'$.observation_cutoff_at')<=frozen_at)
);

CREATE TABLE dataset_posts (
    dataset_post_id     INTEGER PRIMARY KEY,
    dataset_id          INTEGER NOT NULL REFERENCES dataset_versions(dataset_id),
    post_id             INTEGER NOT NULL REFERENCES posts(post_id),
    snapshot_id         INTEGER,
    inclusion_status    TEXT NOT NULL DEFAULT 'included'
                        CHECK (inclusion_status IN ('included','detail_unavailable','type_mismatch')),
    added_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (dataset_id, post_id),
    CHECK ((inclusion_status='included' AND snapshot_id IS NOT NULL) OR
           (inclusion_status IN ('detail_unavailable','type_mismatch') AND snapshot_id IS NULL)),
    FOREIGN KEY (snapshot_id, post_id)
        REFERENCES post_snapshots(snapshot_id, post_id)
    ,CHECK (dataset_id IS NULL OR typeof(dataset_id)='integer'),
    CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (snapshot_id IS NULL OR typeof(snapshot_id)='integer'),
    CHECK (added_at IS NULL OR (typeof(added_at)='text' AND length(added_at)=24 AND added_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(added_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',added_at) IS added_at AND date(added_at,'+0 days') IS substr(added_at,1,10)))
);

CREATE TABLE dataset_comments (
    dataset_comment_id  INTEGER PRIMARY KEY,
    dataset_id          INTEGER NOT NULL,
    post_id             INTEGER NOT NULL,
    comment_id          INTEGER,
    observation_id      INTEGER NOT NULL,
    added_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (dataset_id, post_id)
        REFERENCES dataset_posts(dataset_id, post_id),
    FOREIGN KEY (comment_id, post_id)
        REFERENCES comments(comment_id, post_id),
    FOREIGN KEY (observation_id, post_id)
        REFERENCES comment_observations(observation_id, post_id),
    FOREIGN KEY (observation_id, comment_id)
        REFERENCES comment_observations(observation_id, comment_id),
    UNIQUE (dataset_id, observation_id)
    ,CHECK (dataset_id IS NULL OR typeof(dataset_id)='integer'),
    CHECK (post_id IS NULL OR typeof(post_id)='integer'),
    CHECK (comment_id IS NULL OR typeof(comment_id)='integer'),
    CHECK (observation_id IS NULL OR typeof(observation_id)='integer'),
    CHECK (added_at IS NULL OR (typeof(added_at)='text' AND length(added_at)=24 AND added_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z' AND substr(added_at,1,4)>='0001' AND strftime('%Y-%m-%dT%H:%M:%fZ',added_at) IS added_at AND date(added_at,'+0 days') IS substr(added_at,1,10)))
);

-- ============================================================
-- Indexes for expected queries (FK columns are not auto-indexed by SQLite)
-- ============================================================
CREATE INDEX idx_collection_plans_project       ON collection_plans(project_id);
CREATE INDEX idx_collection_runs_plan           ON collection_runs(plan_id, started_at);
CREATE INDEX idx_search_requests_run            ON search_requests(run_id, status);
CREATE INDEX idx_search_hits_post               ON search_hits(post_id);
CREATE INDEX idx_search_hits_request            ON search_hits(search_request_id);
CREATE INDEX idx_search_hits_artifact           ON search_hits(raw_artifact_id);
CREATE INDEX idx_post_snapshots_post_time       ON post_snapshots(post_id, observed_at DESC);
CREATE INDEX idx_post_snapshots_attempt         ON post_snapshots(collection_attempt_id);
CREATE INDEX idx_post_media_snapshot            ON post_media(snapshot_id);
CREATE INDEX idx_comments_post_parent           ON comments(post_id, parent_comment_id);
CREATE INDEX idx_comment_observations_post_time ON comment_observations(post_id, observed_at DESC);
CREATE INDEX idx_comment_observations_comment   ON comment_observations(comment_id, observed_at DESC);
CREATE INDEX idx_comment_observations_attempt   ON comment_observations(collection_attempt_id);
CREATE INDEX idx_comment_observations_parent    ON comment_observations(parent_observation_id);
CREATE INDEX idx_collection_tasks_run_status    ON collection_tasks(run_id, status);
CREATE INDEX idx_collection_tasks_post          ON collection_tasks(post_id);
CREATE UNIQUE INDEX ux_collection_tasks_search_request ON collection_tasks(search_request_id)
    WHERE search_request_id IS NOT NULL;
CREATE INDEX idx_collection_attempts_task       ON collection_attempts(task_id, started_at);
CREATE INDEX idx_raw_artifacts_attempt          ON raw_artifacts(attempt_id);
CREATE INDEX idx_dataset_versions_project       ON dataset_versions(project_id);
CREATE INDEX idx_dataset_versions_run           ON dataset_versions(source_run_id);
CREATE INDEX idx_dataset_posts_post             ON dataset_posts(post_id);
CREATE INDEX idx_dataset_comments_post          ON dataset_comments(post_id);
CREATE INDEX idx_dataset_comments_comment       ON dataset_comments(comment_id);
CREATE UNIQUE INDEX ux_dataset_comments_identity
    ON dataset_comments(dataset_id, comment_id)
    WHERE comment_id IS NOT NULL;

-- ============================================================
-- Cross-table integrity & freeze guards (v0.2)
-- Application MUST use write transactions; these guards supplement, not replace,
-- adapter validation, storage permissions, manifest verification, and tests.
-- SQLite PRAGMA foreign_keys=ON is per-connection: enable on every connection.
-- ============================================================

-- Once any Run uses a Plan, preserve the exact keyword/sort/filter configuration.
CREATE TRIGGER trg_plan_lock_update BEFORE UPDATE ON collection_plans
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.plan_id<>OLD.plan_id;
  SELECT RAISE(ABORT,'Plan is locked after first Run') WHERE EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id);
END;
CREATE TRIGGER trg_plan_lock_delete BEFORE DELETE ON collection_plans
WHEN EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id)
BEGIN SELECT RAISE(ABORT,'Plan is locked after first Run'); END;
CREATE TRIGGER trg_plan_keyword_lock_insert BEFORE INSERT ON plan_keywords
WHEN EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=NEW.plan_id)
BEGIN
  SELECT RAISE(ABORT,'Protected plan_keywords conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM plan_keywords old WHERE (old.keyword_id=NEW.keyword_id) OR (old.plan_id=NEW.plan_id AND old.sequence_no=NEW.sequence_no) OR (old.plan_id=NEW.plan_id AND old.keyword=NEW.keyword)); SELECT RAISE(ABORT,'Plan keywords are locked'); END;
CREATE TRIGGER trg_plan_keyword_lock_update BEFORE UPDATE ON plan_keywords
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.keyword_id<>OLD.keyword_id;
  SELECT RAISE(ABORT,'Plan keywords are locked') WHERE EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id OR plan_id=NEW.plan_id);
END;
CREATE TRIGGER trg_plan_keyword_lock_delete BEFORE DELETE ON plan_keywords
WHEN EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id)
BEGIN SELECT RAISE(ABORT,'Plan keywords are locked'); END;
CREATE TRIGGER trg_plan_sort_lock_insert BEFORE INSERT ON plan_sort_methods
WHEN EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=NEW.plan_id)
BEGIN
  SELECT RAISE(ABORT,'Protected plan_sort_methods conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM plan_sort_methods old WHERE (old.sort_method_id=NEW.sort_method_id) OR (old.plan_id=NEW.plan_id AND old.sequence_no=NEW.sequence_no) OR (old.plan_id=NEW.plan_id AND old.sort_method=NEW.sort_method)); SELECT RAISE(ABORT,'Plan sort methods are locked'); END;
CREATE TRIGGER trg_plan_sort_lock_update BEFORE UPDATE ON plan_sort_methods
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.sort_method_id<>OLD.sort_method_id;
  SELECT RAISE(ABORT,'Plan sort methods are locked') WHERE EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id OR plan_id=NEW.plan_id);
END;
CREATE TRIGGER trg_plan_sort_lock_delete BEFORE DELETE ON plan_sort_methods
WHEN EXISTS (SELECT 1 FROM collection_runs WHERE plan_id=OLD.plan_id)
BEGIN SELECT RAISE(ABORT,'Plan sort methods are locked'); END;
CREATE TRIGGER trg_run_plan_ready BEFORE INSERT ON collection_runs
BEGIN
  SELECT RAISE(ABORT,'Protected collection_runs conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM collection_runs old WHERE (old.run_id=NEW.run_id));
  SELECT RAISE(ABORT,'Plan needs at least one keyword') WHERE NOT EXISTS
      (SELECT 1 FROM plan_keywords WHERE plan_id=NEW.plan_id);
  SELECT RAISE(ABORT,'Plan needs at least one sort method') WHERE NOT EXISTS
      (SELECT 1 FROM plan_sort_methods WHERE plan_id=NEW.plan_id);
  SELECT RAISE(ABORT,'MVP Plan must request image-text filter') WHERE NOT EXISTS
      (SELECT 1 FROM collection_plans p WHERE p.plan_id=NEW.plan_id
       AND json_extract(p.search_filters_json, '$.note_type')='图文');
END;
CREATE TRIGGER trg_run_identity_update BEFORE UPDATE ON collection_runs
WHEN NEW.run_id<>OLD.run_id OR NEW.plan_id<>OLD.plan_id
BEGIN SELECT RAISE(ABORT,'Run identity and Plan are immutable'); END;
CREATE TRIGGER trg_run_no_reopen BEFORE UPDATE ON collection_runs
WHEN OLD.status IN ('completed','partial','failed','cancelled')
BEGIN SELECT RAISE(ABORT,'Closed Run cannot change'); END;
CREATE TRIGGER trg_run_close_clean BEFORE UPDATE OF status ON collection_runs
WHEN NEW.status IN ('completed','partial','failed','cancelled') AND OLD.status NOT IN ('completed','partial','failed','cancelled')
BEGIN
  SELECT RAISE(ABORT,'Run finish precedes captured activity') WHERE NEW.finished_at IS NOT NULL AND (
    EXISTS (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON t.task_id=a.task_id
      WHERE t.run_id=NEW.run_id AND (a.started_at>NEW.finished_at OR a.finished_at>NEW.finished_at)) OR
    EXISTS (SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
      JOIN collection_tasks t ON t.task_id=a.task_id WHERE t.run_id=NEW.run_id AND s.observed_at>NEW.finished_at) OR
    EXISTS (SELECT 1 FROM comment_observations o JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id
      JOIN collection_tasks t ON t.task_id=a.task_id WHERE t.run_id=NEW.run_id AND o.observed_at>NEW.finished_at));
  SELECT RAISE(ABORT,'Run still has unfinished SearchRequests') WHERE EXISTS
     (SELECT 1 FROM search_requests WHERE run_id=NEW.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'Run still has unfinished Tasks') WHERE EXISTS
     (SELECT 1 FROM collection_tasks WHERE run_id=NEW.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'Run still has unfinished Attempts') WHERE EXISTS
     (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON a.task_id=t.task_id
      WHERE t.run_id=NEW.run_id AND a.status='running');
END;
CREATE TRIGGER trg_run_protect_delete BEFORE DELETE ON collection_runs
BEGIN SELECT RAISE(ABORT,'Run deletion is not allowed'); END;

-- SearchRequest is one keyword x sort logical query within a Run.
CREATE TRIGGER trg_search_request_insert BEFORE INSERT ON search_requests
BEGIN
  SELECT RAISE(ABORT,'Protected search_requests conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM search_requests old WHERE (old.search_request_id=NEW.search_request_id) OR (old.run_id=NEW.run_id AND old.keyword=NEW.keyword AND old.sort_method=NEW.sort_method) OR (old.run_id=NEW.run_id AND old.sequence_no=NEW.sequence_no));
  SELECT RAISE(ABORT,'Search filters differ from fixed Plan') WHERE
    EXISTS (SELECT 1 FROM collection_runs r JOIN collection_plans p ON p.plan_id=r.plan_id,
      json_each(p.search_filters_json) expected WHERE r.run_id=NEW.run_id AND expected.key<>'sort_by'
      AND NOT EXISTS (SELECT 1 FROM json_each(NEW.filters_json) actual
        WHERE actual.key=expected.key AND actual.type=expected.type AND actual.value IS expected.value))
    OR EXISTS (SELECT 1 FROM json_each(NEW.filters_json) actual WHERE actual.key<>'sort_by'
      AND NOT EXISTS (SELECT 1 FROM collection_runs r JOIN collection_plans p ON p.plan_id=r.plan_id,
        json_each(p.search_filters_json) expected WHERE r.run_id=NEW.run_id
          AND expected.key=actual.key AND expected.type=actual.type AND expected.value IS actual.value));
  SELECT RAISE(ABORT,'SearchRequest requires active Run') WHERE NOT EXISTS
     (SELECT 1 FROM collection_runs WHERE run_id=NEW.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'SearchRequest keyword/sort not present in Plan') WHERE NOT EXISTS
     (SELECT 1 FROM collection_runs r JOIN plan_keywords k ON k.plan_id=r.plan_id
      JOIN plan_sort_methods s ON s.plan_id=r.plan_id
      WHERE r.run_id=NEW.run_id AND k.keyword=NEW.keyword AND s.sort_method=NEW.sort_method);
END;
CREATE TRIGGER trg_search_request_update BEFORE UPDATE ON search_requests
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.search_request_id<>OLD.search_request_id;
  SELECT RAISE(ABORT,'SearchRequest cannot change after Run is closed') WHERE NOT EXISTS
     (SELECT 1 FROM collection_runs WHERE run_id=OLD.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'SearchRequest identity/config is immutable') WHERE
     NEW.run_id<>OLD.run_id OR NEW.sequence_no<>OLD.sequence_no
     OR NEW.keyword<>OLD.keyword OR NEW.sort_method<>OLD.sort_method
     OR NEW.request_method<>OLD.request_method
     OR NEW.filters_json IS NOT OLD.filters_json;
END;
CREATE TRIGGER trg_search_request_delete BEFORE DELETE ON search_requests
BEGIN SELECT RAISE(ABORT,'Do not delete SearchRequests'); END;

CREATE TRIGGER trg_task_insert BEFORE INSERT ON collection_tasks
BEGIN
  SELECT RAISE(ABORT,'Protected collection_tasks conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM collection_tasks old WHERE (old.task_id=NEW.task_id) OR (old.search_request_id=NEW.search_request_id) OR (old.run_id=NEW.run_id AND old.sequence_no=NEW.sequence_no));
  SELECT RAISE(ABORT,'Known video candidate must not start detail collection') WHERE NEW.task_type='feed_detail'
    AND EXISTS (SELECT 1 FROM search_hits h JOIN search_requests q ON q.search_request_id=h.search_request_id
      WHERE q.run_id=NEW.run_id AND h.post_id=NEW.post_id AND h.card_note_type='video')
    AND NOT EXISTS (SELECT 1 FROM search_hits h JOIN search_requests q ON q.search_request_id=h.search_request_id
      WHERE q.run_id=NEW.run_id AND h.post_id=NEW.post_id AND h.card_note_type='normal');
  SELECT RAISE(ABORT,'Search Task method/config differs from Request') WHERE NEW.task_type='search'
    AND ( (json_type(NEW.task_config_json,'$.request_method') IS NOT NULL
           AND json_extract(NEW.task_config_json,'$.request_method') IS NOT 'POST')
       OR (json_type(NEW.task_config_json,'$.filters') IS NOT NULL AND (
         json_type(NEW.task_config_json,'$.filters') IS NOT 'object'
         OR EXISTS (SELECT key,type,value FROM json_each(NEW.task_config_json,'$.filters')
           EXCEPT SELECT key,type,value FROM json_each((SELECT filters_json FROM search_requests WHERE search_request_id=NEW.search_request_id)))
         OR EXISTS (SELECT key,type,value FROM json_each((SELECT filters_json FROM search_requests WHERE search_request_id=NEW.search_request_id))
           EXCEPT SELECT key,type,value FROM json_each(NEW.task_config_json,'$.filters')))));
  SELECT RAISE(ABORT,'Task requires active Run') WHERE NOT EXISTS
      (SELECT 1 FROM collection_runs WHERE run_id=NEW.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'SearchTask references SearchRequest from different Run') WHERE
      NEW.task_type='search' AND NOT EXISTS
      (SELECT 1 FROM search_requests r WHERE r.search_request_id=NEW.search_request_id AND r.run_id=NEW.run_id);
END;
CREATE TRIGGER trg_task_update BEFORE UPDATE ON collection_tasks
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.task_id<>OLD.task_id;
  SELECT RAISE(ABORT,'Task cannot change after Run closed') WHERE NOT EXISTS
      (SELECT 1 FROM collection_runs WHERE run_id=OLD.run_id AND status IN ('pending','running'));
  SELECT RAISE(ABORT,'Task identity/config is immutable') WHERE
      NEW.run_id<>OLD.run_id OR NEW.task_type<>OLD.task_type
      OR NEW.search_request_id IS NOT OLD.search_request_id
      OR NEW.post_id IS NOT OLD.post_id OR NEW.task_config_json<>OLD.task_config_json
      OR NEW.sequence_no<>OLD.sequence_no;
END;
CREATE TRIGGER trg_task_delete BEFORE DELETE ON collection_tasks
BEGIN SELECT RAISE(ABORT,'Do not delete Tasks'); END;

CREATE TRIGGER trg_attempt_insert BEFORE INSERT ON collection_attempts
BEGIN
  SELECT RAISE(ABORT,'Protected collection_attempts conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM collection_attempts old WHERE (old.attempt_id=NEW.attempt_id) OR (old.task_id=NEW.task_id AND old.attempt_no=NEW.attempt_no));
  SELECT RAISE(ABORT,'Search Attempt must use POST; mismatches may only be recorded as failed/cancelled') WHERE
    NEW.status NOT IN ('failed','cancelled') AND NEW.http_method IS NOT 'POST'
    AND EXISTS (SELECT 1 FROM collection_tasks WHERE task_id=NEW.task_id AND task_type='search');
  SELECT RAISE(ABORT,'Attempt needs running Run') WHERE NOT EXISTS
      (SELECT 1 FROM collection_tasks t JOIN collection_runs r ON t.run_id=r.run_id
       WHERE t.task_id=NEW.task_id AND r.status='running');
END;
CREATE TRIGGER trg_attempt_update BEFORE UPDATE ON collection_attempts
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.attempt_id<>OLD.attempt_id;
  SELECT RAISE(ABORT,'Search Attempt must use POST; mismatches may only be recorded as failed/cancelled') WHERE
    NEW.status NOT IN ('failed','cancelled') AND NEW.http_method IS NOT 'POST'
    AND EXISTS (SELECT 1 FROM collection_tasks WHERE task_id=NEW.task_id AND task_type='search');
  SELECT RAISE(ABORT,'Attempt cannot change after Run closed') WHERE NOT EXISTS
      (SELECT 1 FROM collection_tasks t JOIN collection_runs r ON t.run_id=r.run_id
       WHERE t.task_id=OLD.task_id AND r.status='running');
  SELECT RAISE(ABORT,'Completed Attempt is immutable') WHERE OLD.status<>'running';
  SELECT RAISE(ABORT,'Attempt identity immutable') WHERE NEW.task_id<>OLD.task_id
      OR NEW.attempt_no<>OLD.attempt_no OR NEW.started_at<>OLD.started_at;
END;
CREATE TRIGGER trg_attempt_delete BEFORE DELETE ON collection_attempts
BEGIN SELECT RAISE(ABORT,'Do not delete Attempts'); END;

-- Identity exists globally; observed content goes in append-only Snapshots.
CREATE TRIGGER trg_post_identity_update BEFORE UPDATE ON posts
BEGIN SELECT RAISE(ABORT,'Post identity must be stable'); END;
CREATE TRIGGER trg_post_identity_delete BEFORE DELETE ON posts
BEGIN SELECT RAISE(ABORT,'Do not delete Post identities'); END;
CREATE TRIGGER trg_comment_identity_insert BEFORE INSERT ON comments
BEGIN
  SELECT RAISE(ABORT,'Protected comments conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM comments old WHERE (old.comment_id=NEW.comment_id) OR (old.comment_id=NEW.comment_id AND old.post_id=NEW.post_id) OR (old.post_id=NEW.post_id AND old.source_comment_id=NEW.source_comment_id));
  SELECT RAISE(ABORT,'Unknown platform comment ID belongs in observations only')
     WHERE NEW.source_comment_id IS NULL OR length(trim(NEW.source_comment_id))=0;
  SELECT RAISE(ABORT,'Comment parent must be a level-1 comment of the same Post')
     WHERE NEW.parent_comment_id IS NOT NULL AND NOT EXISTS
       (SELECT 1 FROM comments c WHERE c.comment_id=NEW.parent_comment_id
          AND c.post_id=NEW.post_id AND c.comment_level=1);
END;
CREATE TRIGGER trg_comment_identity_update BEFORE UPDATE ON comments
BEGIN SELECT RAISE(ABORT,'Comment identity is immutable'); END;
CREATE TRIGGER trg_comment_identity_delete BEFORE DELETE ON comments
BEGIN SELECT RAISE(ABORT,'Do not delete Comment identities'); END;

CREATE TRIGGER trg_hit_insert BEFORE INSERT ON search_hits
BEGIN
  SELECT RAISE(ABORT,'Protected search_hits conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM search_hits old WHERE (old.search_hit_id=NEW.search_hit_id) OR (old.collection_attempt_id=NEW.collection_attempt_id AND old.result_position=NEW.result_position));
  SELECT RAISE(ABORT,'Hit attempt does not match SearchRequest or task type') WHERE NOT EXISTS
      (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON a.task_id=t.task_id
       JOIN search_requests r ON r.search_request_id=NEW.search_request_id
       JOIN collection_runs run ON run.run_id=r.run_id
       WHERE a.attempt_id=NEW.collection_attempt_id AND t.task_type='search'
         AND t.search_request_id=NEW.search_request_id AND t.run_id=r.run_id
         AND run.status='running' AND a.status IN ('succeeded','partial'));
  SELECT RAISE(ABORT,'Hit platform mismatches Plan source') WHERE NOT EXISTS
      (SELECT 1 FROM posts p JOIN search_requests q ON q.search_request_id=NEW.search_request_id
       JOIN collection_runs r ON r.run_id=q.run_id JOIN collection_plans cp ON cp.plan_id=r.plan_id
       WHERE p.post_id=NEW.post_id AND p.source_platform=cp.source_platform);
  SELECT RAISE(ABORT,'Hit artifact is from another Attempt') WHERE NEW.raw_artifact_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM raw_artifacts ra WHERE ra.raw_artifact_id=NEW.raw_artifact_id
          AND ra.attempt_id=NEW.collection_attempt_id);
END;
CREATE TRIGGER trg_hit_update BEFORE UPDATE ON search_hits
BEGIN SELECT RAISE(ABORT,'SearchHit is append-only'); END;
CREATE TRIGGER trg_hit_delete BEFORE DELETE ON search_hits
BEGIN SELECT RAISE(ABORT,'SearchHit is append-only'); END;

CREATE TRIGGER trg_snapshot_insert BEFORE INSERT ON post_snapshots
BEGIN
  SELECT RAISE(ABORT,'Protected post_snapshots conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM post_snapshots old WHERE (old.snapshot_id=NEW.snapshot_id) OR (old.snapshot_id=NEW.snapshot_id AND old.post_id=NEW.post_id) OR (old.post_id=NEW.post_id AND old.collection_attempt_id=NEW.collection_attempt_id));
  SELECT RAISE(ABORT,'Engagement metadata must be valid JSON objects') WHERE
    (NEW.engagement_raw_json IS NOT NULL AND (NOT json_valid(NEW.engagement_raw_json) OR json_type(NEW.engagement_raw_json)<>'object')) OR
    (NEW.engagement_parse_json IS NOT NULL AND (NOT json_valid(NEW.engagement_parse_json) OR json_type(NEW.engagement_parse_json)<>'object'));
  SELECT RAISE(ABORT,'Raw engagement needs explicit parse state') WHERE EXISTS (
    SELECT 1 FROM json_each(NEW.engagement_raw_json) WHERE json_type(NEW.engagement_parse_json,'$.'||key) IS NOT 'object');
  SELECT RAISE(ABORT,'Invalid engagement raw metric') WHERE EXISTS (
    SELECT 1 FROM json_each(NEW.engagement_raw_json)
    WHERE key NOT IN ('likes','comments','favorites','shares') OR type NOT IN ('text','null'));
  SELECT RAISE(ABORT,'Invalid engagement parse metric') WHERE EXISTS (
    SELECT 1 FROM json_each(NEW.engagement_parse_json) e
    WHERE e.key NOT IN ('likes','comments','favorites','shares') OR e.type<>'object'
      OR (SELECT count(*) FROM json_each(e.value))<>3
      OR EXISTS (SELECT 1 FROM json_each(e.value) WHERE key NOT IN ('raw','value','precision'))
      OR json_type(e.value,'$.raw') IS NULL OR json_type(e.value,'$.value') IS NULL
      OR json_type(e.value,'$.precision') IS NOT 'text'
      OR json_type(e.value,'$.raw') NOT IN ('text','null')
      OR json_type(e.value,'$.value') NOT IN ('integer','null')
      OR NOT (
        (json_type(e.value,'$.raw')='null' AND json_type(e.value,'$.value')='null' AND json_extract(e.value,'$.precision')='missing')
        OR (json_type(e.value,'$.raw')='text' AND json_type(e.value,'$.value')='null' AND json_extract(e.value,'$.precision')='unparseable')
        OR (json_type(e.value,'$.raw')='text' AND json_type(e.value,'$.value')='integer' AND json_extract(e.value,'$.value')>=0
            AND json_extract(e.value,'$.precision') IN ('exact_display','approximate','lower_bound','approximate_lower_bound')))
      OR json_extract(e.value,'$.raw') IS NOT json_extract(NEW.engagement_raw_json,'$.'||e.key)
      OR json_extract(e.value,'$.value') IS NOT CASE e.key
        WHEN 'likes' THEN NEW.likes_count WHEN 'comments' THEN NEW.comments_count
        WHEN 'favorites' THEN NEW.favorites_count WHEN 'shares' THEN NEW.shares_count END);
  SELECT RAISE(ABORT,'Numeric engagement needs parse metadata') WHERE
    (NEW.likes_count IS NOT NULL AND json_type(NEW.engagement_parse_json,'$.likes') IS NOT 'object') OR
    (NEW.comments_count IS NOT NULL AND json_type(NEW.engagement_parse_json,'$.comments') IS NOT 'object') OR
    (NEW.favorites_count IS NOT NULL AND json_type(NEW.engagement_parse_json,'$.favorites') IS NOT 'object') OR
    (NEW.shares_count IS NOT NULL AND json_type(NEW.engagement_parse_json,'$.shares') IS NOT 'object');
  SELECT RAISE(ABORT,'Duplicate engagement JSON keys') WHERE
    (SELECT count(*) FROM json_each(NEW.engagement_raw_json))<>(SELECT count(DISTINCT key) FROM json_each(NEW.engagement_raw_json)) OR
    (SELECT count(*) FROM json_each(NEW.engagement_parse_json))<>(SELECT count(DISTINCT key) FROM json_each(NEW.engagement_parse_json));
  SELECT RAISE(ABORT,'Observation precedes Attempt') WHERE EXISTS (SELECT 1 FROM collection_attempts a WHERE a.attempt_id=NEW.collection_attempt_id AND NEW.observed_at<a.started_at);
  SELECT RAISE(ABORT,'Snapshot attempt wrong Post/Task/Run') WHERE NOT EXISTS
      (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON t.task_id=a.task_id
       JOIN collection_runs r ON r.run_id=t.run_id
       WHERE a.attempt_id=NEW.collection_attempt_id AND t.task_type='feed_detail'
       AND t.post_id=NEW.post_id AND r.status='running'
       AND a.status IN ('succeeded','partial'));
  SELECT RAISE(ABORT,'Snapshot platform mismatches Plan source') WHERE NOT EXISTS
      (SELECT 1 FROM posts p JOIN collection_attempts a ON a.attempt_id=NEW.collection_attempt_id
       JOIN collection_tasks t ON t.task_id=a.task_id JOIN collection_runs r ON r.run_id=t.run_id
       JOIN collection_plans cp ON cp.plan_id=r.plan_id
       WHERE p.post_id=NEW.post_id AND p.source_platform=cp.source_platform);
  SELECT RAISE(ABORT,'Snapshot artifact is from different Attempt') WHERE NEW.raw_artifact_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM raw_artifacts ra WHERE ra.raw_artifact_id=NEW.raw_artifact_id
          AND ra.attempt_id=NEW.collection_attempt_id);
END;
CREATE TRIGGER trg_snapshot_update BEFORE UPDATE ON post_snapshots
BEGIN SELECT RAISE(ABORT,'PostSnapshot is append-only'); END;
CREATE TRIGGER trg_snapshot_delete BEFORE DELETE ON post_snapshots
BEGIN SELECT RAISE(ABORT,'PostSnapshot is append-only'); END;
CREATE TRIGGER trg_media_insert BEFORE INSERT ON post_media
BEGIN
  SELECT RAISE(ABORT,'Protected post_media conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM post_media old WHERE (old.media_id=NEW.media_id) OR (old.snapshot_id=NEW.snapshot_id AND old.source_field=NEW.source_field AND old.position=NEW.position));
  SELECT RAISE(ABORT,'Media insertion needs active source Run') WHERE NOT EXISTS
     (SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
      JOIN collection_tasks t ON t.task_id=a.task_id JOIN collection_runs r ON r.run_id=t.run_id
      WHERE s.snapshot_id=NEW.snapshot_id AND r.status='running');
END;
-- Only an authorized local cache path may be populated once later. It is not an
-- edit to the fetched media observation; application must verify the saved file.
CREATE TRIGGER trg_media_update BEFORE UPDATE ON post_media
BEGIN
  SELECT RAISE(ABORT,'Media evidence is immutable except first local cache path') WHERE
    NOT (
      OLD.local_path IS NULL AND NEW.local_path IS NOT NULL AND length(trim(NEW.local_path))>0
      AND NEW.media_id=OLD.media_id AND NEW.snapshot_id=OLD.snapshot_id
      AND NEW.media_type=OLD.media_type AND NEW.source_field=OLD.source_field
      AND NEW.position=OLD.position
      AND NEW.source_url IS OLD.source_url
      AND NEW.thumbnail_url IS OLD.thumbnail_url
      AND NEW.media_raw_json IS OLD.media_raw_json
    );
END;
CREATE TRIGGER trg_media_delete BEFORE DELETE ON post_media
BEGIN SELECT RAISE(ABORT,'Media record is immutable'); END;

CREATE TRIGGER trg_comment_observation_insert BEFORE INSERT ON comment_observations
BEGIN
  SELECT RAISE(ABORT,'Protected comment_observations conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM comment_observations old WHERE (old.observation_id=NEW.observation_id) OR (old.collection_attempt_id=NEW.collection_attempt_id AND old.source_path=NEW.source_path) OR (old.observation_id=NEW.observation_id AND old.comment_id=NEW.comment_id) OR (old.observation_id=NEW.observation_id AND old.post_id=NEW.post_id));
  SELECT RAISE(ABORT,'Invalid canonical source_path') WHERE
    (NEW.comment_level=1 AND NOT (
      NEW.source_path='list['||CAST(CAST(substr(NEW.source_path,6,length(NEW.source_path)-6) AS INTEGER) AS TEXT)||']'
      AND CAST(substr(NEW.source_path,6,length(NEW.source_path)-6) AS INTEGER)>=0))
    OR (NEW.comment_level=2 AND NOT EXISTS (
      SELECT 1 FROM comment_observations p WHERE p.observation_id=NEW.parent_observation_id
      AND NEW.source_path=p.source_path||'.subComments['||
          CAST(CAST(substr(NEW.source_path,length(p.source_path)+14,
              length(NEW.source_path)-length(p.source_path)-14) AS INTEGER) AS TEXT)||']'
      AND CAST(substr(NEW.source_path,length(p.source_path)+14,
              length(NEW.source_path)-length(p.source_path)-14) AS INTEGER)>=0));
  SELECT RAISE(ABORT,'Raw parent ID differs from observed parent identity') WHERE
    NEW.parent_source_comment_id IS NOT NULL AND EXISTS (
      SELECT 1 FROM comment_observations p JOIN comments c ON c.comment_id=p.comment_id
      WHERE p.observation_id=NEW.parent_observation_id AND c.source_comment_id<>NEW.parent_source_comment_id);

  SELECT RAISE(ABORT,'Observation precedes Attempt') WHERE EXISTS (SELECT 1 FROM collection_attempts a WHERE a.attempt_id=NEW.collection_attempt_id AND NEW.observed_at<a.started_at);
  SELECT RAISE(ABORT,'Comment Observation attempt wrong Post/Task/Run') WHERE NOT EXISTS
      (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON t.task_id=a.task_id
       JOIN collection_runs r ON r.run_id=t.run_id
       WHERE a.attempt_id=NEW.collection_attempt_id AND t.task_type='feed_detail'
       AND t.post_id=NEW.post_id AND r.status='running'
       AND a.status IN ('succeeded','partial'));
  SELECT RAISE(ABORT,'Observation Comment identity/level mismatch') WHERE NEW.comment_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM comments c WHERE c.comment_id=NEW.comment_id
             AND c.post_id=NEW.post_id AND c.comment_level=NEW.comment_level);
  SELECT RAISE(ABORT,'Level-2 reply needs an observed level-1 parent from same Attempt and Post')
      WHERE NEW.comment_level=2 AND (NEW.parent_observation_id IS NULL OR NOT EXISTS
         (SELECT 1 FROM comment_observations parent
          WHERE parent.observation_id=NEW.parent_observation_id AND parent.post_id=NEW.post_id
            AND parent.collection_attempt_id=NEW.collection_attempt_id AND parent.comment_level=1));
  SELECT RAISE(ABORT,'Level-1 reply cannot have parent observation')
      WHERE NEW.comment_level=1 AND NEW.parent_observation_id IS NOT NULL;
  SELECT RAISE(ABORT,'Comment identity parent differs from observed parent') WHERE
      NEW.comment_id IS NOT NULL AND NEW.comment_level=2
      AND EXISTS (SELECT 1 FROM comments c WHERE c.comment_id=NEW.comment_id AND c.parent_comment_id IS NOT NULL)
      AND EXISTS (SELECT 1 FROM comment_observations po WHERE po.observation_id=NEW.parent_observation_id AND po.comment_id IS NOT NULL)
      AND NOT EXISTS (
        SELECT 1 FROM comments c JOIN comment_observations po ON po.observation_id=NEW.parent_observation_id
        WHERE c.comment_id=NEW.comment_id AND c.parent_comment_id=po.comment_id
      );
  SELECT RAISE(ABORT,'Observation artifact is from different Attempt') WHERE NEW.raw_artifact_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM raw_artifacts ra WHERE ra.raw_artifact_id=NEW.raw_artifact_id
          AND ra.attempt_id=NEW.collection_attempt_id);
END;
CREATE TRIGGER trg_comment_observation_update BEFORE UPDATE ON comment_observations
BEGIN SELECT RAISE(ABORT,'CommentObservation is append-only'); END;
CREATE TRIGGER trg_comment_observation_delete BEFORE DELETE ON comment_observations
BEGIN SELECT RAISE(ABORT,'CommentObservation is append-only'); END;

CREATE TRIGGER trg_artifact_insert BEFORE INSERT ON raw_artifacts
BEGIN
  SELECT RAISE(ABORT,'Protected raw_artifacts conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM raw_artifacts old WHERE (old.raw_artifact_id=NEW.raw_artifact_id) OR (old.file_path=NEW.file_path));
  SELECT RAISE(ABORT,'Artifact needs active Run') WHERE NOT EXISTS
      (SELECT 1 FROM collection_attempts a JOIN collection_tasks t ON t.task_id=a.task_id
       JOIN collection_runs r ON r.run_id=t.run_id WHERE a.attempt_id=NEW.attempt_id
       AND r.status='running');
END;
CREATE TRIGGER trg_artifact_update BEFORE UPDATE ON raw_artifacts
BEGIN SELECT RAISE(ABORT,'RawArtifact evidence is immutable'); END;
CREATE TRIGGER trg_artifact_delete BEFORE DELETE ON raw_artifacts
BEGIN SELECT RAISE(ABORT,'RawArtifact cannot be deleted'); END;

-- Dataset = single source Run, not copied content. A draft may accumulate members;
-- freezing is an UPDATE completed in one application transaction after calculating manifest.
CREATE TRIGGER trg_dataset_version_insert BEFORE INSERT ON dataset_versions
BEGIN
  SELECT RAISE(ABORT,'Protected dataset_versions conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM dataset_versions old WHERE (old.dataset_id=NEW.dataset_id) OR (old.project_id=NEW.project_id AND old.dataset_name=NEW.dataset_name AND old.version=NEW.version));
  SELECT RAISE(ABORT,'Dataset project differs from source Run project') WHERE NOT EXISTS
      (SELECT 1 FROM collection_runs r JOIN collection_plans p ON p.plan_id=r.plan_id
       WHERE r.run_id=NEW.source_run_id AND p.project_id=NEW.project_id);
  SELECT RAISE(ABORT,'Insert Dataset in draft first') WHERE NEW.status<>'draft';
END;
CREATE TRIGGER trg_dataset_version_update BEFORE UPDATE ON dataset_versions
BEGIN
  SELECT RAISE(ABORT,'type_mismatch requires real non-image evidence within cutoff') WHERE NEW.status='frozen'
    AND EXISTS (SELECT 1 FROM dataset_posts dp WHERE dp.dataset_id=NEW.dataset_id AND dp.inclusion_status='type_mismatch'
      AND NOT EXISTS (SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t ON t.task_id=a.task_id WHERE s.post_id=dp.post_id AND t.run_id=NEW.source_run_id
        AND s.note_type='video' AND s.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at))
      AND NOT EXISTS (SELECT 1 FROM search_hits h JOIN search_requests q ON q.search_request_id=h.search_request_id
        JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id WHERE h.post_id=dp.post_id
        AND q.run_id=NEW.source_run_id AND h.card_note_type='video' AND a.finished_at IS NOT NULL AND a.finished_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at)));
  SELECT RAISE(ABORT,'Dataset project differs from source Run project') WHERE NOT EXISTS
    (SELECT 1 FROM collection_runs r JOIN collection_plans p ON p.plan_id=r.plan_id WHERE r.run_id=NEW.source_run_id AND p.project_id=NEW.project_id);
  SELECT RAISE(ABORT,'Dataset references observation after cutoff') WHERE NEW.status='frozen' AND (
    EXISTS (SELECT 1 FROM dataset_posts dp JOIN post_snapshots s ON s.snapshot_id=dp.snapshot_id WHERE dp.dataset_id=NEW.dataset_id AND s.observed_at>coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at)) OR
    EXISTS (SELECT 1 FROM dataset_comments dc JOIN comment_observations o ON o.observation_id=dc.observation_id WHERE dc.dataset_id=NEW.dataset_id AND o.observed_at>coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at)));
  SELECT RAISE(ABORT,'Frozen Dataset is immutable') WHERE OLD.status='frozen';
  SELECT RAISE(ABORT,'Dataset cannot change identity, project or source Run') WHERE
      NEW.dataset_id<>OLD.dataset_id OR NEW.project_id<>OLD.project_id
      OR NEW.source_run_id<>OLD.source_run_id OR NEW.dataset_name<>OLD.dataset_name
      OR NEW.version<>OLD.version;
  SELECT RAISE(ABORT,'Unsupported Dataset status transition') WHERE
      NOT (OLD.status='draft' AND NEW.status IN ('draft','frozen'));
  SELECT RAISE(ABORT,'Cannot freeze Dataset before Run is terminal') WHERE NEW.status='frozen'
      AND NOT EXISTS (SELECT 1 FROM collection_runs r WHERE r.run_id=NEW.source_run_id
          AND r.status IN ('completed','partial','failed','cancelled'));
  SELECT RAISE(ABORT,'Cannot freeze with missing source Hit candidate') WHERE NEW.status='frozen'
      AND EXISTS (
        SELECT DISTINCT h.post_id FROM search_hits h JOIN search_requests q ON q.search_request_id=h.search_request_id
        JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
        WHERE q.run_id=NEW.source_run_id AND a.status IN ('succeeded','partial')
        EXCEPT
        SELECT dp.post_id FROM dataset_posts dp WHERE dp.dataset_id=NEW.dataset_id
      );
  SELECT RAISE(ABORT,'Cannot freeze with extra Dataset Post not found in source Run') WHERE NEW.status='frozen'
      AND EXISTS (
        SELECT dp.post_id FROM dataset_posts dp WHERE dp.dataset_id=NEW.dataset_id
        EXCEPT
        SELECT DISTINCT h.post_id FROM search_hits h JOIN search_requests q ON q.search_request_id=h.search_request_id
        JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
        WHERE q.run_id=NEW.source_run_id AND a.status IN ('succeeded','partial')
      );
  -- A selected Snapshot must be latest within the single source Run, not latest globally.
  SELECT RAISE(ABORT,'Dataset Post selected a non-latest source-Run Snapshot') WHERE NEW.status='frozen'
     AND EXISTS (
       SELECT 1 FROM dataset_posts dp JOIN post_snapshots s ON s.snapshot_id=dp.snapshot_id
       WHERE dp.dataset_id=NEW.dataset_id AND EXISTS (
         SELECT 1 FROM post_snapshots newer
           JOIN collection_attempts a ON a.attempt_id=newer.collection_attempt_id
           JOIN collection_tasks t ON t.task_id=a.task_id
         WHERE newer.post_id=dp.post_id AND t.run_id=NEW.source_run_id
           AND a.status IN ('succeeded','partial') AND newer.note_type='normal' AND newer.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at) AND
           (newer.observed_at>s.observed_at OR
             (newer.observed_at=s.observed_at AND newer.snapshot_id>s.snapshot_id))
       )
     );
  -- If a normal image-text Snapshot was obtained, do not mark it as missing.
  SELECT RAISE(ABORT,'Dataset Post has an available normal detail Snapshot') WHERE NEW.status='frozen'
      AND EXISTS (
       SELECT 1 FROM dataset_posts dp WHERE dp.dataset_id=NEW.dataset_id
        AND dp.inclusion_status IN ('detail_unavailable','type_mismatch') AND EXISTS (
         SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
           JOIN collection_tasks t ON t.task_id=a.task_id
         WHERE s.post_id=dp.post_id AND t.run_id=NEW.source_run_id
           AND a.status IN ('succeeded','partial') AND s.note_type='normal' AND s.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at)
        )
      );
  -- Valid included content must be image/text, not a video mixed into search results.
  SELECT RAISE(ABORT,'Included Dataset Post requires a normal detail type') WHERE NEW.status='frozen'
      AND EXISTS (SELECT 1 FROM dataset_posts dp JOIN post_snapshots s ON s.snapshot_id=dp.snapshot_id
                  WHERE dp.dataset_id=NEW.dataset_id AND dp.inclusion_status='included'
                    AND s.note_type IS NOT 'normal');
  -- Keep every observed anonymous reply. For stable comment IDs, keep one
  -- observation per identity and insist that it is the latest from this Run.
  SELECT RAISE(ABORT,'Dataset omitted stable source-Run comment identities') WHERE NEW.status='frozen'
      AND EXISTS (
       SELECT DISTINCT o.comment_id FROM comment_observations o
         JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id
         JOIN collection_tasks t ON t.task_id=a.task_id
         JOIN dataset_posts dp ON dp.dataset_id=NEW.dataset_id AND dp.post_id=o.post_id
        WHERE t.run_id=NEW.source_run_id AND o.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at) AND o.comment_id IS NOT NULL
       EXCEPT
       SELECT dc.comment_id FROM dataset_comments dc
        WHERE dc.dataset_id=NEW.dataset_id AND dc.comment_id IS NOT NULL
      );
  SELECT RAISE(ABORT,'Dataset omitted anonymous source-Run comment observations') WHERE NEW.status='frozen'
      AND EXISTS (
       SELECT o.observation_id FROM comment_observations o
         JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id
         JOIN collection_tasks t ON t.task_id=a.task_id
         JOIN dataset_posts dp ON dp.dataset_id=NEW.dataset_id AND dp.post_id=o.post_id
        WHERE t.run_id=NEW.source_run_id AND o.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at) AND o.comment_id IS NULL
       EXCEPT
       SELECT dc.observation_id FROM dataset_comments dc
         WHERE dc.dataset_id=NEW.dataset_id AND dc.comment_id IS NULL
      );
  SELECT RAISE(ABORT,'Dataset selected a non-latest comment Observation in its Run') WHERE NEW.status='frozen'
      AND EXISTS (
       SELECT 1 FROM dataset_comments dc
        JOIN comment_observations chosen ON chosen.observation_id=dc.observation_id
       WHERE dc.dataset_id=NEW.dataset_id AND dc.comment_id IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM comment_observations newer
           JOIN collection_attempts a ON a.attempt_id=newer.collection_attempt_id
           JOIN collection_tasks t ON t.task_id=a.task_id
          WHERE newer.comment_id=chosen.comment_id AND t.run_id=NEW.source_run_id AND newer.observed_at<=coalesce(json_extract(NEW.selection_rules_json,'$.observation_cutoff_at'),NEW.frozen_at)
            AND (newer.observed_at>chosen.observed_at OR
               (newer.observed_at=chosen.observed_at AND newer.observation_id>chosen.observation_id))
        )
      );
END;
CREATE TRIGGER trg_dataset_version_delete BEFORE DELETE ON dataset_versions
BEGIN SELECT RAISE(ABORT,'Dataset Version cannot be deleted'); END;
CREATE TRIGGER trg_dataset_post_insert BEFORE INSERT ON dataset_posts
BEGIN
  SELECT RAISE(ABORT,'Protected dataset_posts conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM dataset_posts old WHERE (old.dataset_post_id=NEW.dataset_post_id) OR (old.dataset_id=NEW.dataset_id AND old.post_id=NEW.post_id));
  SELECT RAISE(ABORT,'Dataset is frozen or missing') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d WHERE d.dataset_id=NEW.dataset_id AND d.status='draft');
  SELECT RAISE(ABORT,'Dataset Post is not a candidate in source Run') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d JOIN search_requests q ON q.run_id=d.source_run_id
       JOIN search_hits h ON h.search_request_id=q.search_request_id
       JOIN collection_attempts a ON a.attempt_id=h.collection_attempt_id
       WHERE d.dataset_id=NEW.dataset_id AND h.post_id=NEW.post_id
        AND a.status IN ('succeeded','partial'));
  SELECT RAISE(ABORT,'Dataset Snapshot from different Run') WHERE NEW.snapshot_id IS NOT NULL
      AND NOT EXISTS
       (SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t ON t.task_id=a.task_id
        JOIN dataset_versions d ON d.source_run_id=t.run_id
        WHERE d.dataset_id=NEW.dataset_id AND s.snapshot_id=NEW.snapshot_id AND s.post_id=NEW.post_id);
END;
CREATE TRIGGER trg_dataset_post_update BEFORE UPDATE ON dataset_posts
BEGIN
  SELECT RAISE(ABORT,'Primary identity is immutable') WHERE NEW.dataset_post_id<>OLD.dataset_post_id;
  SELECT RAISE(ABORT,'Dataset Post cannot change after freeze') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d WHERE d.dataset_id=OLD.dataset_id AND d.status='draft');
  SELECT RAISE(ABORT,'DatasetPost identity cannot change') WHERE NEW.dataset_id<>OLD.dataset_id OR NEW.post_id<>OLD.post_id;
  SELECT RAISE(ABORT,'Dataset Snapshot from different Run') WHERE NEW.snapshot_id IS NOT NULL
      AND NOT EXISTS
       (SELECT 1 FROM post_snapshots s JOIN collection_attempts a ON a.attempt_id=s.collection_attempt_id
        JOIN collection_tasks t ON t.task_id=a.task_id
        JOIN dataset_versions d ON d.source_run_id=t.run_id
        WHERE d.dataset_id=NEW.dataset_id AND s.snapshot_id=NEW.snapshot_id AND s.post_id=NEW.post_id);
END;
CREATE TRIGGER trg_dataset_post_delete BEFORE DELETE ON dataset_posts
BEGIN
  SELECT RAISE(ABORT,'Frozen Dataset Post cannot be deleted') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d WHERE d.dataset_id=OLD.dataset_id AND d.status='draft');
END;
CREATE TRIGGER trg_dataset_comment_insert BEFORE INSERT ON dataset_comments
BEGIN
  SELECT RAISE(ABORT,'Protected dataset_comments conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM dataset_comments old WHERE (old.dataset_comment_id=NEW.dataset_comment_id) OR (old.dataset_id=NEW.dataset_id AND old.comment_id=NEW.comment_id) OR (old.dataset_id=NEW.dataset_id AND old.observation_id=NEW.observation_id));
  SELECT RAISE(ABORT,'Dataset is frozen or missing') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d WHERE d.dataset_id=NEW.dataset_id AND d.status='draft');
  SELECT RAISE(ABORT,'Dataset Comment identity/Run/Post mismatch') WHERE NOT EXISTS
      (SELECT 1 FROM comment_observations o JOIN collection_attempts a ON a.attempt_id=o.collection_attempt_id
       JOIN collection_tasks t ON t.task_id=a.task_id JOIN dataset_versions d ON d.source_run_id=t.run_id
       JOIN dataset_posts dp ON dp.dataset_id=d.dataset_id AND dp.post_id=o.post_id
       WHERE d.dataset_id=NEW.dataset_id AND o.post_id=NEW.post_id AND o.observation_id=NEW.observation_id
       AND o.comment_id IS NEW.comment_id);
END;
CREATE TRIGGER trg_dataset_comment_update BEFORE UPDATE ON dataset_comments
BEGIN
  SELECT RAISE(ABORT,'Dataset Comment cannot be changed; remove and reinsert only during draft');
END;
CREATE TRIGGER trg_dataset_comment_delete BEFORE DELETE ON dataset_comments
BEGIN
  SELECT RAISE(ABORT,'Frozen Dataset Comment cannot be deleted') WHERE NOT EXISTS
      (SELECT 1 FROM dataset_versions d WHERE d.dataset_id=OLD.dataset_id AND d.status='draft');
END;

-- Conflict guards for tables without an original INSERT trigger.
CREATE TRIGGER trg_research_projects_insert_guard BEFORE INSERT ON research_projects
BEGIN
  SELECT RAISE(ABORT,'Protected research_projects conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM research_projects old WHERE (old.project_id=NEW.project_id));
END;
CREATE TRIGGER trg_collection_plans_insert_guard BEFORE INSERT ON collection_plans
BEGIN
  SELECT RAISE(ABORT,'Protected collection_plans conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM collection_plans old WHERE (old.plan_id=NEW.plan_id));
END;
CREATE TRIGGER trg_posts_insert_guard BEFORE INSERT ON posts
BEGIN
  SELECT RAISE(ABORT,'Protected posts conflict: no replacement of existing identity/evidence')
    WHERE EXISTS (SELECT 1 FROM posts old WHERE (old.post_id=NEW.post_id) OR (old.source_platform=NEW.source_platform AND old.source_post_id=NEW.source_post_id));
END;

CREATE TRIGGER trg_project_identity_update BEFORE UPDATE ON research_projects
WHEN NEW.project_id<>OLD.project_id
BEGIN SELECT RAISE(ABORT,'Primary identity is immutable'); END;

COMMIT;
PRAGMA foreign_key_check;
