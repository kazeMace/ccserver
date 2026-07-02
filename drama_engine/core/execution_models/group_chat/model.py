"""Group-chat execution model runner."""

from __future__ import annotations

import asyncio
from typing import Any

from drama_engine.core.execution_models.group_chat.domain_runtime import GroupChatDomainRuntime
from drama_engine.core.execution_models.group_chat.loop import GroupChatLoop
from drama_engine.core.execution_models.group_chat.policy import GroupChatPolicy
from drama_engine.core.execution_models.group_chat.state import GroupChatState
from drama_engine.core.session.state import SESSION_ASSIGNED, SESSION_ENDED, SESSION_RUNNING
from drama_engine.core.ports.memory import configure_runtime_memory_backend
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime_spec.registry import RuntimeSpec

class GroupChatRunner(BasicGameRunner):
    """Runnable group-chat runtime.

    The runner asks shared Cast actors to produce conversation messages. It
    keeps the same public runner protocol as other BasicGameRunner subclasses:
    ``assign()``, ``start()`` and ``reset_runtime_state()``.
    """

    def __init__(
        self,
        runtime: Any,
        declaration: RuntimeSpec,
        dry_run: bool = True,
    ) -> None:
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration, dry_run=dry_run)
        self._state: GroupChatState | None = None
        self._domain_runtime: GroupChatDomainRuntime | None = None
        self._loop: GroupChatLoop | None = None

    async def reset_runtime_state(self) -> None:
        """Cancel current task and clear transient state."""
        runtime_state = self.runtime_state
        if runtime_state.task is not None and not runtime_state.task.done():
            runtime_state.task.cancel()
            try:
                await runtime_state.task
            except asyncio.CancelledError:
                pass
        runtime_state.task = None
        self._state = None
        self._domain_runtime = None
        self._loop = None

    async def assign(self) -> None:
        """Prepare room state and move the web session to assigned."""
        session_state = self.session_state
        assert session_state.status == "lobby", (
            f"只有 lobby 状态可以 assign，当前: {session_state.status}"
        )
        self._state = self._build_state()
        config = self._load_runtime_config()
        configure_runtime_memory_backend(self.context.memory_store, config)
        self._domain_runtime = GroupChatDomainRuntime(
            state=self._state,
            policy=self._build_policy(config),
            memory_store=self.context.memory_store,
        )
        self.input_bridge.create_cast(
            actor_runtime=self.context.actor_runtime,
            player_names=list(self._state.participants),
            human_seat_ids=set(getattr(session_state, "human_seat_ids", set())),
            action_service=self.runtime.action_service,
            dry_run=self.dry_run,
            step_gate=self.step_gate,
        )
        session_state.metadata["runtime_type"] = "group_chat"
        session_state.metadata["group_chat"] = {
            "room_name": self._state.room_name,
            "topic": self._state.topic,
            "participants": list(self._state.participants),
            "max_rounds": self._state.max_rounds,
        }
        session_state.set_status(SESSION_ASSIGNED)
        self.event_publisher.public({"kind": "session_assigned", "runtime_type": "group_chat"})
        self.event_publisher.host({"kind": "session_assigned", "runtime_type": "group_chat"})

    async def start(self) -> None:
        """Start the group-chat actor event loop."""
        session_state = self.session_state
        assert session_state.status == SESSION_ASSIGNED, (
            f"只有 assigned 状态可以 start，当前: {session_state.status}"
        )
        assert self._state is not None, "start 前必须先 assign"
        session_state.set_status(SESSION_RUNNING)
        self.event_publisher.public({"kind": "session_started", "runtime_type": "group_chat"})
        self.event_publisher.host({"kind": "session_started", "runtime_type": "group_chat"})
        self.runtime_state.task = asyncio.create_task(self._run_chat())

    async def _run_chat(self) -> None:
        """Ask cast actors to produce transcript messages and end the session."""
        assert self._state is not None, "group chat state 不能为空"
        assert self._domain_runtime is not None, "group chat domain runtime 不能为空"
        cast = self.context.actor_runtime.cast
        assert cast is not None, "group_chat assign 后 Cast 不能为空"
        self._loop = GroupChatLoop(
            domain_runtime=self._domain_runtime,
            cast=cast,
            emit_public=self._emit_public,
            emit_views=self._emit_views,
        )
        try:
            await self._loop.run()
            self.session_state.metadata["group_chat"]["transcript_size"] = len(self._state.transcript)
            self.session_state.metadata["group_chat"]["transcript_summary"] = self._state.summary
            self.session_state.metadata["group_chat"]["transcript"] = list(self._state.transcript)
            self.session_state.set_status(SESSION_ENDED)
            self._emit_public({
                "kind": "session_ended",
                "runtime_type": "group_chat",
                "result": "group_chat_completed",
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - runtime errors must surface to host.
            self.session_state.set_status("failed")
            self.event_publisher.host({"kind": "session_failed", "error": str(exc)})

    def _build_state(self) -> GroupChatState:
        """Build room state from runtime config and seats."""
        config = self._load_runtime_config()
        room_name = str(config.get("room_name") or config.get("room") or "group-chat-room")
        topic = str(config.get("topic") or self._script_title() or "自由讨论")
        participants = list(config.get("participants") or self.session_state.seat_ids)
        assert participants, "group_chat participants 不能为空"
        max_rounds = int(config.get("max_rounds") or config.get("rounds") or 1)
        assert max_rounds > 0, "group_chat max_rounds 必须大于 0"
        return GroupChatState(
            room_name=room_name,
            topic=topic,
            participants=[str(item) for item in participants],
            max_rounds=max_rounds,
        )

    def _role_prompts(self) -> dict[str, str]:
        """Read optional per-participant speaking policy from runtime config."""
        config = self._load_runtime_config()
        prompts = config.get("role_prompts")
        if isinstance(prompts, dict):
            return {str(key): str(value) for key, value in prompts.items()}
        return {}

    def _build_policy(self, config: dict[str, Any]) -> GroupChatPolicy:
        """Build the configured default group-chat policy."""
        policy_spec = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        phases = policy_spec.get("discussion_phases") or config.get("discussion_phases")
        rules = policy_spec.get("room_rules") or config.get("room_rules")
        max_context_items = policy_spec.get("max_context_items", config.get("max_context_items", 3))
        return GroupChatPolicy(
            topic=self._state.topic,
            role_prompts=self._role_prompts(),
            memory_store=self.context.memory_store,
            max_context_items=int(max_context_items),
            discussion_phases=[str(item) for item in phases] if isinstance(phases, list) else None,
            room_rules=[str(item) for item in rules] if isinstance(rules, list) else None,
        )

    def _load_runtime_config(self) -> dict[str, Any]:
        """Read runtime.config from the YAML script."""
        return self.config_parser.runtime_config(
            script_path=self.session_state.script_path,
            declaration=self.declaration,
        )

    def _script_title(self) -> str:
        """Return script title when available."""
        return self.config_parser.script_title(self.session_state.script_path)

    def _emit_public(self, event: dict[str, Any]) -> None:
        """Append one event to public and host streams."""
        self.event_publisher.public(dict(event))
        self.event_publisher.host(dict(event))

    def _emit_views(self) -> None:
        """Emit ViewHost projections for current group-chat state."""
        assert self._domain_runtime is not None, "group chat domain runtime 不能为空"
        for event in self._domain_runtime.project_views():
            self._emit_public(event)

GroupChatExecutionModel = GroupChatRunner

__all__ = ["GroupChatExecutionModel", "GroupChatRunner"]
