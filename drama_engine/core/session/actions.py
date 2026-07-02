"""Session-scoped action request service.

第一版实现用于验证多 session 隔离：每局都有自己的 ActionRequestService，
因此同名 seat 的 pending request 不会互相覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from drama_engine.core.ports.timeout import (
    DEADLINE_WATCH_INTERVAL_SECONDS,
    TIMEOUT_AI_TAKEOVER,
    TIMEOUT_MODERATOR_REQUIRED,
    ActionTimeoutResolver,
    TimeoutPolicy,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ActionRequest",
    "ActionRequestService",
    "ActionRequestStore",
    "ActionSubmission",
    "_find_target_field",
    "_format_human_submission_text",
    "_validate_candidates",
]


@dataclass(slots=True)
class ActionRequest:
    """真人玩家待处理动作请求。"""

    request_id: str
    session_id: str
    seat_id: str
    cue: str
    kind: str = "generic"
    candidates: list[str] | None = None
    schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scene_name: str = ""
    scene_display_name: str = ""
    allow_resubmit: bool = False
    timeout_seconds: float | None = None
    deadline_at: float | None = None

    def __post_init__(self) -> None:
        assert self.request_id, "request_id 不能为空"
        assert self.session_id, "session_id 不能为空"
        assert self.seat_id, "seat_id 不能为空"
        assert self.cue, "cue 不能为空"


@dataclass(slots=True)
class ActionSubmission:
    """玩家提交结果。"""

    submission_id: str
    request_id: str
    session_id: str
    seat_id: str
    source: str
    data: dict[str, Any] | None = None
    text: str = ""
    validated: bool = True
    validation_error: str = ""

    def __post_init__(self) -> None:
        assert self.submission_id, "submission_id 不能为空"
        assert self.request_id, "request_id 不能为空"
        assert self.session_id, "session_id 不能为空"
        assert self.seat_id, "seat_id 不能为空"
        assert self.source, "source 不能为空"


class ActionRequestStore:
    """Shared in-memory storage for runtime action requests."""

    def __init__(self) -> None:
        self.requests: dict[str, Any] = {}
        self.current_request: dict[str, str] = {}
        self.submissions: dict[str, Any] = {}
        self.waiters: dict[str, asyncio.Future[Any]] = {}
        self.collect_models: dict[str, Any] = {}

    def cancel_waiters(self) -> None:
        """Cancel every pending waiter future."""
        for future in self.waiters.values():
            if not future.done():
                future.cancel()

    def clear_pending(self) -> None:
        """Clear current pending request pointers."""
        self.current_request.clear()

    def reset(self) -> None:
        """Clear all action request state."""
        self.cancel_waiters()
        self.requests.clear()
        self.current_request.clear()
        self.submissions.clear()
        self.waiters.clear()
        self.collect_models.clear()


class ActionRequestService:
    """单局动作请求服务。"""

    def __init__(
        self,
        session_id: str,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> None:
        assert session_id, "session_id 不能为空"
        self.session_id = session_id
        self._timeout_policy = timeout_policy or TimeoutPolicy()
        self._store = ActionRequestStore()
        self._requests = self._store.requests
        self._current_request = self._store.current_request
        self._submissions = self._store.submissions
        self._waiters = self._store.waiters
        self._collect_models = self._store.collect_models
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._deadline_task: asyncio.Task[Any] | None = None
        self._timeout_resolver = ActionTimeoutResolver()
        logger.info("[ActionRequestService] 初始化：session=%s", session_id)

    @property
    def is_running(self) -> bool:
        """Return whether the service action deadline watcher is active."""
        return self._started

    async def start(self) -> None:
        """启动 service action deadline watcher。"""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._started = True
        self._deadline_task = self._loop.create_task(self._deadline_watcher())
        logger.info("[ActionRequestService] deadline watcher 已启动：session=%s", self.session_id)

    async def stop(self) -> None:
        """停止 service action deadline watcher。"""
        self._started = False
        if self._deadline_task is not None:
            self._deadline_task.cancel()
            try:
                await self._deadline_task
            except asyncio.CancelledError:
                pass
            self._deadline_task = None
        logger.info("[ActionRequestService] deadline watcher 已停止：session=%s", self.session_id)

    def create_request(
        self,
        seat_id: str,
        cue: str,
        kind: str = "generic",
        candidates: list[str] | None = None,
        schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionRequest:
        """创建动作请求，并标记该 seat 当前 pending。"""
        assert seat_id, "seat_id 不能为空"
        assert cue, "cue 不能为空"
        request_metadata = dict(metadata or {})
        loop = self._get_or_create_loop()
        effective_timeout = request_metadata.get("timeout_seconds")
        if effective_timeout is None:
            effective_timeout = self._timeout_policy.default_seconds
        deadline_at = None
        if effective_timeout is not None:
            deadline_at = loop.time() + float(effective_timeout)
            request_metadata["timeout_seconds"] = effective_timeout
        request_id = str(uuid.uuid4())
        request = ActionRequest(
            request_id=request_id,
            session_id=self.session_id,
            seat_id=seat_id,
            cue=cue,
            kind=kind,
            candidates=candidates,
            schema=schema,
            metadata=request_metadata,
            scene_name=str(request_metadata.get("scene_name") or ""),
            scene_display_name=str(request_metadata.get("scene_display_name") or ""),
            allow_resubmit=bool(request_metadata.get("allow_resubmit", False)),
            timeout_seconds=effective_timeout,
            deadline_at=deadline_at,
        )
        self._requests[request_id] = request
        self._current_request[seat_id] = request_id
        collect_model = request_metadata.get("collect_model")
        if collect_model is not None:
            self._collect_models[request_id] = collect_model
        self._waiters[request_id] = loop.create_future()
        if deadline_at is not None:
            self._ensure_deadline_watcher(loop)
        logger.info(
            "[ActionRequestService] 创建请求：session=%s seat=%s request=%s",
            self.session_id,
            seat_id,
            request_id,
        )
        return request

    def get_current_request(self, seat_id: str) -> ActionRequest | None:
        """获取 seat 当前 pending 请求。"""
        assert seat_id, "seat_id 不能为空"
        request_id = self._current_request.get(seat_id)
        if request_id is None:
            return None
        return self._requests.get(request_id)

    async def submit(
        self,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None = None,
        text: str = "",
    ) -> ActionSubmission | None:
        """提交 seat 当前 pending 请求。"""
        assert seat_id, "seat_id 不能为空"
        assert source, "source 不能为空"
        request = self.get_current_request(seat_id)
        if request is None:
            logger.warning(
                "[ActionRequestService] 无 pending 请求：session=%s seat=%s",
                self.session_id,
                seat_id,
            )
            return None
        validation_error = ""
        validated_data = data
        collect_model = self._collect_models.get(request.request_id)
        if collect_model is not None and data is not None:
            try:
                assert isinstance(data, dict), f"data 必须是 dict，实际：{type(data)}"
                model_instance = collect_model(**data)
                validated_data = model_instance.model_dump()
            except Exception as exc:
                validation_error = f"schema 校验失败：{exc}"
        if validation_error == "" and request.candidates is not None and validated_data is not None:
            validation_error = _validate_candidates(validated_data, request.candidates)
        display_text = _format_human_submission_text(request, source, text)
        submission = ActionSubmission(
            submission_id=str(uuid.uuid4()),
            request_id=request.request_id,
            session_id=self.session_id,
            seat_id=seat_id,
            source=source,
            data=validated_data,
            text=display_text,
            validated=(validation_error == ""),
            validation_error=validation_error,
        )
        if validation_error:
            logger.info(
                "[ActionRequestService] 提交校验失败，保持 pending：session=%s seat=%s request=%s error=%s",
                self.session_id,
                seat_id,
                request.request_id,
                validation_error,
            )
            return submission
        self._submissions[request.request_id] = submission
        self._current_request.pop(seat_id, None)
        self._collect_models.pop(request.request_id, None)
        future = self._waiters.get(request.request_id)
        if future is not None and not future.done():
            future.set_result(submission)
            self._waiters.pop(request.request_id, None)
        logger.info(
            "[ActionRequestService] 提交完成：session=%s seat=%s request=%s",
            self.session_id,
            seat_id,
            request.request_id,
        )
        return submission

    async def wait_submission(self, request_id: str) -> ActionSubmission:
        """等待指定 request 的提交。"""
        assert request_id, "request_id 不能为空"
        future = self._waiters.get(request_id)
        assert future is not None, f"request 不存在或不可等待: {request_id}"
        return await future

    def dump(self) -> dict[str, Any]:
        """导出可持久化的动作请求快照。"""
        return {
            "session_id": self.session_id,
            "requests": [self._request_to_dict(request) for request in self._requests.values()],
            "current_request": dict(self._current_request),
            "submissions": [
                self._submission_to_dict(submission)
                for submission in self._submissions.values()
            ],
        }

    def load(self, data: dict[str, Any]) -> None:
        """从持久化快照恢复动作请求状态。"""
        assert isinstance(data, dict), "action snapshot 必须是 dict"
        self._store.reset()
        for item in data.get("requests") or []:
            request = self._request_from_dict(dict(item))
            self._requests[request.request_id] = request
        current = data.get("current_request") or {}
        assert isinstance(current, dict), "current_request 必须是 dict"
        self._current_request.update({
            str(seat_id): str(request_id)
            for seat_id, request_id in current.items()
        })
        for item in data.get("submissions") or []:
            submission = self._submission_from_dict(dict(item))
            self._submissions[submission.request_id] = submission
        loop = self._get_or_create_loop()
        has_deadline = False
        for request_id in self._current_request.values():
            request = self._requests.get(request_id)
            if request is None:
                continue
            if request_id not in self._waiters:
                self._waiters[request_id] = loop.create_future()
            if request.timeout_seconds is not None:
                request.deadline_at = loop.time() + float(request.timeout_seconds)
                has_deadline = True
        if has_deadline:
            self._ensure_deadline_watcher(loop)

    def pending_summary(self) -> list[dict[str, Any]]:
        """返回 pending 请求摘要。"""
        result = []
        for seat_id, request_id in self._current_request.items():
            request = self._requests.get(request_id)
            if request is None:
                continue
            result.append({
                "session_id": self.session_id,
                "seat_id": seat_id,
                "request_id": request_id,
                "kind": request.kind,
                "cue": request.cue,
                "deadline_at": request.deadline_at,
                "timeout_seconds": request.timeout_seconds,
            })
        return result

    def cancel_all(self) -> None:
        """取消本局所有等待中的请求。"""
        self._started = False
        if self._deadline_task is not None:
            self._deadline_task.cancel()
            self._deadline_task = None
        self._store.cancel_waiters()
        self._store.clear_pending()
        logger.info("[ActionRequestService] 已取消所有请求：session=%s", self.session_id)

    async def expire_request(self, request_id: str, policy_override: str | None = None) -> ActionSubmission | None:
        """让一个 pending 请求过期，并按 timeout policy 生成默认提交。"""
        assert request_id, "request_id 不能为空"
        request = self._requests.get(request_id)
        if request is None:
            logger.warning("[ActionRequestService] 过期请求不存在：session=%s request=%s", self.session_id, request_id)
            return None
        if request_id in self._submissions:
            return self._submissions[request_id]
        policy = policy_override or self._timeout_policy.policy_for_kind(request.kind)
        if policy == TIMEOUT_MODERATOR_REQUIRED:
            logger.info(
                "[ActionRequestService] 请求过期但等待主持人处理：session=%s request=%s",
                self.session_id,
                request_id,
            )
            return None
        submission = self._build_timeout_submission(request, policy)
        self._submissions[request_id] = submission
        self._current_request.pop(request.seat_id, None)
        self._collect_models.pop(request_id, None)
        future = self._waiters.get(request_id)
        if future is not None and not future.done():
            future.set_result(submission)
        self._waiters.pop(request_id, None)
        logger.info(
            "[ActionRequestService] 请求超时自动提交：session=%s seat=%s request=%s policy=%s",
            self.session_id,
            request.seat_id,
            request_id,
            policy,
        )
        return submission

    @staticmethod
    def _request_to_dict(request: ActionRequest) -> dict[str, Any]:
        """把动作请求转成 JSON 安全字典。"""
        metadata = dict(request.metadata or {})
        metadata.pop("collect_model", None)
        return {
            "request_id": request.request_id,
            "session_id": request.session_id,
            "seat_id": request.seat_id,
            "cue": request.cue,
            "kind": request.kind,
            "candidates": list(request.candidates) if request.candidates is not None else None,
            "schema": dict(request.schema) if request.schema is not None else None,
            "metadata": metadata,
            "scene_name": request.scene_name,
            "scene_display_name": request.scene_display_name,
            "allow_resubmit": request.allow_resubmit,
            "timeout_seconds": request.timeout_seconds,
            "deadline_at": request.deadline_at,
        }

    @staticmethod
    def _request_from_dict(data: dict[str, Any]) -> ActionRequest:
        """从 JSON 安全字典恢复动作请求。"""
        return ActionRequest(
            request_id=str(data.get("request_id") or ""),
            session_id=str(data.get("session_id") or ""),
            seat_id=str(data.get("seat_id") or ""),
            cue=str(data.get("cue") or ""),
            kind=str(data.get("kind") or "generic"),
            candidates=list(data["candidates"]) if data.get("candidates") is not None else None,
            schema=dict(data["schema"]) if data.get("schema") is not None else None,
            metadata=dict(data.get("metadata") or {}),
            scene_name=str(data.get("scene_name") or ""),
            scene_display_name=str(data.get("scene_display_name") or ""),
            allow_resubmit=bool(data.get("allow_resubmit", False)),
            timeout_seconds=data.get("timeout_seconds"),
            deadline_at=data.get("deadline_at"),
        )

    @staticmethod
    def _submission_to_dict(submission: ActionSubmission) -> dict[str, Any]:
        """把动作提交转成 JSON 安全字典。"""
        return {
            "submission_id": submission.submission_id,
            "request_id": submission.request_id,
            "session_id": submission.session_id,
            "seat_id": submission.seat_id,
            "source": submission.source,
            "data": submission.data,
            "text": submission.text,
            "validated": submission.validated,
            "validation_error": submission.validation_error,
        }

    @staticmethod
    def _submission_from_dict(data: dict[str, Any]) -> ActionSubmission:
        """从 JSON 安全字典恢复动作提交。"""
        return ActionSubmission(
            submission_id=str(data.get("submission_id") or ""),
            request_id=str(data.get("request_id") or ""),
            session_id=str(data.get("session_id") or ""),
            seat_id=str(data.get("seat_id") or ""),
            source=str(data.get("source") or ""),
            data=data.get("data"),
            text=str(data.get("text") or ""),
            validated=bool(data.get("validated", True)),
            validation_error=str(data.get("validation_error") or ""),
        )

    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """返回当前 event loop，优先使用 running loop。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._loop = loop
        return loop

    def _ensure_deadline_watcher(self, loop: asyncio.AbstractEventLoop) -> None:
        """确保 timeout watcher 已运行。"""
        if self._started and self._deadline_task is not None and not self._deadline_task.done():
            return
        if not loop.is_running():
            return
        self._started = True
        self._deadline_task = loop.create_task(self._deadline_watcher())

    async def _deadline_watcher(self) -> None:
        """周期性检查 service action pending request 的 deadline。"""
        try:
            while self._started:
                await asyncio.sleep(DEADLINE_WATCH_INTERVAL_SECONDS)
                await self._check_deadlines()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ActionRequestService] deadline watcher 异常：session=%s error=%s", self.session_id, exc)
        finally:
            self._started = False

    async def _check_deadlines(self) -> None:
        """检查并处理已过期的 service action request。"""
        loop = self._get_or_create_loop()
        now = loop.time()
        expired: list[tuple[str, str | None]] = []
        for request_id, request in list(self._requests.items()):
            if request_id in self._submissions:
                continue
            if self._current_request.get(request.seat_id) != request_id:
                continue
            if request.deadline_at is None or now < request.deadline_at:
                continue
            policy = self._timeout_policy.policy_for_kind(request.kind)
            hard_timeout = self._timeout_policy.hard_timeout_seconds
            if policy == TIMEOUT_MODERATOR_REQUIRED and hard_timeout is not None:
                if now >= request.deadline_at + float(hard_timeout):
                    expired.append((request_id, TIMEOUT_AI_TAKEOVER))
                    continue
            expired.append((request_id, None))
        for request_id, policy_override in expired:
            await self.expire_request(request_id, policy_override=policy_override)

    def _build_timeout_submission(self, request: ActionRequest, policy: str) -> ActionSubmission:
        """根据 timeout policy 构造默认提交。"""
        result = self._timeout_resolver.resolve(request, policy)
        if result is None:
            logger.warning("[ActionRequestService] timeout resolver 未返回结果，降级为空提交：%s", policy)
            source = "timeout_default"
            data = None
            text = ""
            validated = True
            validation_error = ""
        else:
            source = result.source
            data = result.data
            text = result.text
            validated = result.validated
            validation_error = result.validation_error
        return ActionSubmission(
            submission_id=str(uuid.uuid4()),
            request_id=request.request_id,
            session_id=self.session_id,
            seat_id=request.seat_id,
            source=source,
            data=data,
            text=text,
            validated=validated,
            validation_error=validation_error,
        )


def _format_human_submission_text(request: Any, source: str, text: str) -> str:
    """Prefix human submission text with a stable player/scene label."""
    value = str(text or "").strip()
    if source != "human":
        return value
    if value.startswith("【"):
        return value
    scene_label = getattr(request, "scene_display_name", "") or getattr(request, "scene_name", "") or "操作"
    if not value:
        value = "已提交"
    return f"【{getattr(request, 'seat_id', '') or '玩家'}｜{scene_label}】{value}"


def _validate_candidates(data: dict[str, Any], candidates: list[str]) -> str:
    """Validate selected vote/choose/target values against candidates."""
    assert isinstance(data, dict), f"data 必须是 dict，实际：{type(data)}"
    assert isinstance(candidates, list), f"candidates 必须是 list，实际：{type(candidates)}"
    if not candidates:
        return ""
    target_field = _find_target_field(data)
    if target_field is None:
        return ""
    selected = data.get(target_field)
    if target_field == "target" and data.get("action") is False:
        return ""
    if target_field == "targets":
        if not isinstance(selected, list):
            return "targets 字段必须是列表"
        invalid = [item for item in selected if item not in candidates]
        if invalid:
            candidate_text = "、".join(str(item) for item in candidates)
            return f"targets 包含非法候选 {invalid!r}，可选目标：{candidate_text}"
        return ""
    if selected in candidates:
        return ""
    candidate_text = "、".join(str(item) for item in candidates)
    return f"{target_field}={selected!r} 不在候选集中，可选目标：{candidate_text}"


def _find_target_field(data: dict[str, Any]) -> str | None:
    """Find the candidate-target field in submitted action data."""
    for field_name in ("vote", "choose", "target", "targets"):
        if field_name in data:
            return field_name
    return None
