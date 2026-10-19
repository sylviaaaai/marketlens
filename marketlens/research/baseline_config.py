"""Fixed research design; comment targets are not storage limits."""
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
import math
import re

SUFFIXES = ("使用感受", "测评", "踩雷", "推荐", "种草", "回购", "盘点", "对比", "", "购买")
SORT_METHODS = ("综合", "最新", "最多点赞", "最多评论", "最多收藏")


@dataclass(frozen=True)
class DetailPacingPolicy:
    """Scheduler-only runtime settings; never alter an existing research Plan."""
    buffer_min_seconds: float = 0
    buffer_max_seconds: float = 10
    after_min_seconds: float = 5
    after_max_seconds: float = 10
    batch_enabled: bool = False
    batch_min_tasks: int = 15
    batch_max_tasks: int = 30
    batch_min_seconds: float = 60
    batch_max_seconds: float = 300

    def __post_init__(self):
        for low, high in ((self.buffer_min_seconds, self.buffer_max_seconds),
                          (self.after_min_seconds, self.after_max_seconds),
                          (self.batch_min_seconds, self.batch_max_seconds)):
            if not all(math.isfinite(v) for v in (low, high)) or not 0 <= low <= high:
                raise ValueError("Detail pacing requires finite nonnegative ordered bounds")
        if (type(self.batch_enabled) is not bool
                or type(self.batch_min_tasks) is not int or type(self.batch_max_tasks) is not int
                or not 1 <= self.batch_min_tasks <= self.batch_max_tasks):
            raise ValueError("Batch pacing requires positive ordered integer task bounds")

    def persisted(self):
        return asdict(self)


@dataclass(frozen=True)
class DetailRetryPolicy:
    """Runtime opt-in only: never part of the immutable research Plan."""
    max_attempts: int = 5
    min_seconds: float = 180
    max_seconds: float = 300

    def __post_init__(self):
        if (type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5
                or not all(math.isfinite(v) for v in (self.min_seconds, self.max_seconds))
                or not 30 <= self.min_seconds <= self.max_seconds <= 300):
            raise ValueError("Detail retry requires 1-5 attempts and 30-300 second bounds")

    def persisted(self):
        return asdict(self)


@dataclass(frozen=True)
class ReviewedTimeoutRecovery:
    """One explicit human decision, scoped to a single failed Attempt."""
    attempt_id: int
    reason: str
    evidence_file: Path
    server_request_finished: bool = False

    def __post_init__(self):
        if (type(self.attempt_id) is not int or self.attempt_id <= 0
                or not self.reason.strip() or len(self.reason) > 2000
                or self.server_request_finished is not True
                or re.search(r"(?i)bearer\s|(?:token|cookie|password|authorization|secret)\s*[:=]", self.reason)):
            raise ValueError("Review requires a specific Attempt, safe reason and explicit server-completion confirmation")


@dataclass(frozen=True)
class BaselineConfig:
    category: str = "粉底液"
    pilot: bool = True
    top_comment_target: int = 20
    reply_target_per_post: int = 10
    interval_seconds: float = 3.0
    max_attempts: int = 2
    media_directory: str = "media"
    media_max_attempts: int = 2
    media_interval_seconds: float = .5

    def __post_init__(self):
        if not self.category.strip() or self.interval_seconds < 0 or self.max_attempts not in (1, 2):
            raise ValueError("Invalid baseline configuration")
        if not self.pilot and (type(self.top_comment_target) is not int or self.top_comment_target not in (20, 25, 30)):
            raise ValueError("Formal comment target must be 20, 25 or 30")
        path = PurePosixPath(self.media_directory)
        if (not self.media_directory or path.is_absolute() or ".." in path.parts
                or "\\" in self.media_directory or ":" in self.media_directory or str(path) == "."
                or path.parts[0] in ("raw", "logs", "private_access", "exports")
                or self.media_max_attempts not in (1, 2) or self.media_interval_seconds < 0):
            raise ValueError("Invalid media configuration")

    @property
    def keywords(self):
        return tuple(self.category + s for s in (("", "测评") if self.pilot else SUFFIXES))

    @property
    def sorts(self):
        return SORT_METHODS[:2] if self.pilot else SORT_METHODS

    @property
    def combinations(self):
        return tuple((k, s) for k in self.keywords for s in self.sorts)

    @property
    def detail_limit(self):
        return 5 if self.pilot else None

    @property
    def detail_options(self):
        return {} if self.pilot else {"comment_target": self.top_comment_target}

    def persisted(self):
        result = {**asdict(self), "load_all_comments": False, "extra_comment_requests": 0,
                "comment_mode": "default_returned_only", "selection": "first_search_then_result_position"}

        if not self.pilot:
            result.update(load_all_comments=True, comment_mode="finite_dom_target",
                          comment_config={"max_comment_items": self.top_comment_target, "click_more_replies": False,
                                          "max_replies_threshold": 10, "scroll_speed": "normal"})
        else:
            for key in ("media_directory", "media_max_attempts", "media_interval_seconds"):
                result.pop(key)  # Preserve the exact historical Pilot plan contract.
        return result
