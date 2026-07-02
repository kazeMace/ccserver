"""Shared action timeout policy and default-submission decisions."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


# Timeout policies / 超时策略
TIMEOUT_SKIP = "skip"
TIMEOUT_ABSTAIN = "abstain"
TIMEOUT_RANDOM_CANDIDATE = "random_candidate"
TIMEOUT_AI_TAKEOVER = "ai_takeover"
TIMEOUT_MODERATOR_REQUIRED = "moderator_required"

# Action kinds / 动作类型
ACTION_KIND_SPEECH = "speech"
ACTION_KIND_VOTE = "vote"
ACTION_KIND_NIGHT_ACTION = "night_action"
ACTION_KIND_STRUCTURED = "structured"

# Default timeout seconds / 默认超时秒数
DEFAULT_ACTION_TIMEOUT_SECONDS = 120

# Deadline watcher interval seconds / deadline watcher 检查间隔
DEADLINE_WATCH_INTERVAL_SECONDS = 1

__all__ = [
    "ACTION_KIND_NIGHT_ACTION",
    "ACTION_KIND_SPEECH",
    "ACTION_KIND_STRUCTURED",
    "ACTION_KIND_VOTE",
    "DEADLINE_WATCH_INTERVAL_SECONDS",
    "DEFAULT_ACTION_TIMEOUT_SECONDS",
    "TIMEOUT_ABSTAIN",
    "TIMEOUT_AI_TAKEOVER",
    "TIMEOUT_MODERATOR_REQUIRED",
    "TIMEOUT_RANDOM_CANDIDATE",
    "TIMEOUT_SKIP",
    "ActionTimeoutResolver",
    "ActionTimeoutResult",
    "TimeoutPolicy",
]


@dataclass(slots=True)
class TimeoutPolicy:
    """Timeout policy configuration shared by runtime action owners."""

    speech: str = TIMEOUT_SKIP
    vote: str = TIMEOUT_ABSTAIN
    night_action: str = TIMEOUT_MODERATOR_REQUIRED
    structured: str = TIMEOUT_ABSTAIN
    default_seconds: float | None = None
    hard_timeout_seconds: float | None = None

    def policy_for_kind(self, kind: str) -> str:
        """Return timeout policy for an action kind.

        未知 kind 按 structured 处理，便于 service facade 兼容 generic/custom
        action，同时保持固定流程游戏的 speech/vote/night_action 精确策略。
        """
        mapping = {
            ACTION_KIND_SPEECH: self.speech,
            ACTION_KIND_VOTE: self.vote,
            ACTION_KIND_NIGHT_ACTION: self.night_action,
            ACTION_KIND_STRUCTURED: self.structured,
        }
        return mapping.get(kind, self.structured)

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "TimeoutPolicy":
        """Build TimeoutPolicy from YAML/runtime config."""
        assert isinstance(config, dict), f"config 必须是 dict，实际：{type(config)}"
        return cls(
            speech=config.get("speech", TIMEOUT_SKIP),
            vote=config.get("vote", TIMEOUT_ABSTAIN),
            night_action=config.get("night_action", TIMEOUT_MODERATOR_REQUIRED),
            structured=config.get("structured", TIMEOUT_ABSTAIN),
            default_seconds=config.get("default_seconds"),
            hard_timeout_seconds=config.get("hard_timeout_seconds"),
        )


@dataclass(slots=True)
class ActionTimeoutResult:
    """Default action data produced by timeout resolution."""

    source: str
    data: dict[str, Any] | None
    text: str
    validated: bool = True
    validation_error: str = ""


class ActionTimeoutResolver:
    """Resolve timeout policy into a default action result."""

    def resolve(self, request: Any, policy: str) -> ActionTimeoutResult | None:
        """Return default action result for a timeout policy.

        `moderator_required` intentionally returns None because the caller must
        keep the request pending and notify the host.
        """
        assert request is not None, "request 不能为空"
        assert policy, "policy 不能为空"
        candidates = list(getattr(request, "candidates", None) or [])

        if policy == TIMEOUT_MODERATOR_REQUIRED:
            return None

        if policy == TIMEOUT_SKIP:
            return ActionTimeoutResult(
                source="timeout_default",
                data=None,
                text="",
            )

        if policy == TIMEOUT_ABSTAIN:
            return ActionTimeoutResult(
                source="timeout_default",
                data={"action": False, "target": None},
                text="弃权（超时）",
            )

        if policy == TIMEOUT_RANDOM_CANDIDATE:
            if candidates:
                chosen = random.choice(candidates)
                return ActionTimeoutResult(
                    source="timeout_default",
                    data={"target": chosen},
                    text=f"随机选择 {chosen}（超时）",
                )
            return ActionTimeoutResult(
                source="timeout_default",
                data={"action": False},
                text="弃权（超时，无候选）",
            )

        if policy == TIMEOUT_AI_TAKEOVER:
            return ActionTimeoutResult(
                source="ai",
                data={"action": False, "target": None},
                text="AI 接管（超时）",
            )

        return ActionTimeoutResult(
            source="timeout_default",
            data=None,
            text="",
        )
