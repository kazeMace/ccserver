"""Controller action executor."""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input import FreeInputExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ControllerActionSpec
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller

logger = logging.getLogger(__name__)


def _infer_free_input_mode(free_input: dict[str, Any]) -> str:
    """从 free_input 配置推导执行模式。

    规则：
      - 有显式 mode → 直接使用
      - 无 mode + 有 generation 块 → grow_flow
      - 无 mode + 有 mapper + 无 generation → choose_mapping
      - 无 mode + 无 mapper + 无 generation → choose_mapping（默认）
    """
    if free_input.get("mode"):
        return str(free_input["mode"])
    if free_input.get("generation"):
        return "grow_flow"
    return "choose_mapping"


class ControllerActionExecutor:
    """Execute story-controller actions."""

    def __init__(self) -> None:
        """Initialize executor."""
        self._free_input = FreeInputExecutor()
        self._services = RuntimeServiceCaller()

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any] | None:
        """Execute controller action if enabled."""
        if not action.enabled or action.kind == "none":
            return None
        if action.kind == "narration":
            return self._narration(ctx, action)
        if action.kind == "cinematic":
            return await self._cinematic(ctx, action)
        if action.kind == "choice":
            return await self._choice(ctx, action)
        if action.kind == "free_text":
            return await self._free_text(ctx, action)
        raise ValueError(f"未知 controller_action.kind: {action.kind}")

    def _narration(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Emit a narration event."""
        event = {
            "kind": "interactive_controller_narration",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "controller": action.controller,
        }
        ctx.emit_public(event)
        return event

    async def _cinematic(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """播片式对话：逐条推送 dialogue_history，每条等玩家点击继续，最后出选项。

        流程：
        1. 有视频时跳过对话推送（视频已包含这些内容）
        2. 无视频时：逐条推送对话，每条创建 confirm pending 等玩家点击
        3. 对话播完 → 出 choices 选项等玩家选择
        """
        scene_id = ctx.current_scene_id
        scene_context = ctx.session_metadata.get("__cinematic_scene_context") or {}

        has_video = bool(ctx.state.get_attr("GAME", "__last_scene_had_video"))
        dialogue_history = scene_context.get("dialogue_history") or []

        if not has_video and dialogue_history:
            # 找到 human actor
            actor_name = self._default_controller_actor(ctx, "human")
            actor = None
            if actor_name and actor_name in ctx.cast.all_names():
                actor = ctx.cast.get(actor_name)

            for i, entry in enumerate(dialogue_history):
                if not isinstance(entry, dict):
                    continue
                speaker = entry.get("speaker", "narrator")
                text = entry.get("text", "")
                if not text:
                    continue

                # 推送当前对话行到消息流
                is_narrator = (speaker == "narrator")
                event = {
                    "kind": "interactive_controller_narration" if is_narrator else "interactive_message",
                    "runtime_type": "interactive_session",
                    "scene": scene_id,
                    "text": text,
                    "actor": speaker if not is_narrator else None,
                    "sender": speaker if not is_narrator else None,
                }
                sound_url = entry.get("sound_url")
                if sound_url:
                    event["sound_url"] = sound_url
                target = entry.get("target")
                if target:
                    event["target"] = target
                ctx.emit_public(event)

                # 每条对话后等玩家点击"下一句"（最后一条不等，直接出选项）
                is_last = (i == len(dialogue_history) - 1)
                if not is_last and actor is not None:
                    # 清除残留的 candidates，设置 confirm 类型请求
                    if hasattr(actor, "set_candidates"):
                        actor.set_candidates([])
                    if hasattr(actor, "set_action_request_hints"):
                        actor.set_action_request_hints(
                            kind="choose",
                            metadata={
                                "confirm": True,
                                "options": [{"id": "continue", "text": "继续"}],
                            },
                        )
                    await actor.act("继续", None)

        # 对话播完（或有视频跳过了对话）→ 出选项
        choices = list(action.choices or [])
        if choices:
            response = await self._controller_response(ctx, action, "请选择一个选项。")
            free_input = dict(action.free_input or {})
            if free_input.get("enabled"):
                free_input["choices"] = choices
                breakpoint()
                result = await self._free_input.execute(
                    ctx,
                    _infer_free_input_mode(free_input),
                    free_input,
                    response,
                )
            else:
                selected = self._selected_choice_from_response(choices, response)
                result = {
                    "kind": "choice",
                    "selected_choice": selected.get("id"),
                    "to": selected.get("to"),
                    "text": response.get("text", ""),
                }
            self._apply_choice_target(ctx, result)
            # 记录选择历史到 State（供剧情树使用）
            self._record_cinematic_choice(ctx, result)
            ctx.emit_public({
                "kind": "interactive_controller_choice",
                "runtime_type": "interactive_session",
                "scene": scene_id,
                "result": result,
            })
            return result

        # 无 choices，纯叙述完成
        return {"kind": "cinematic_complete", "scene": scene_id}

    async def _choice(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Choose one option through controller or fallback."""
        response = await self._controller_response(ctx, action, "请选择一个选项。")
        free_input = dict(action.free_input or {})
        if free_input.get("enabled"):
            free_input["choices"] = action.choices
            result = await self._free_input.execute(
                ctx,
                str(free_input.get("mode") or "choose_mapping"),
                free_input,
                response,
            )
        else:
            selected = self._selected_choice_from_response(action.choices, response)
            result = {
                "kind": "choice",
                "selected_choice": selected.get("id"),
                "to": selected.get("to"),
                "text": response.get("text", ""),
            }
        self._apply_choice_target(ctx, result)
        ctx.emit_public({
            "kind": "interactive_controller_choice",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "result": result,
        })
        return result

    async def _free_text(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Collect free text and execute its configured mode."""
        response = await self._controller_response(ctx, action, "请继续推动剧情。")
        free_input = dict(action.free_input or {})
        mode = str(free_input.get("mode") or "free_continue")
        if free_input.get("enabled", True):
            result = await self._free_input.execute(ctx, mode, free_input, response)
        else:
            result = {"kind": "free_text", "text": response.get("text", "")}
        ctx.emit_public({
            "kind": "interactive_controller_free_text",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "result": result,
        })
        return result

    async def _controller_response(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
        cue: str,
    ) -> dict[str, Any]:
        """Collect a controller response from human/agent/system/plugin fallback."""
        controller = dict(action.controller or {})
        controller_type = str(controller.get("type") or "none")
        if controller_type in {"human", "agent"}:
            actor_name = str(controller.get("agent_id") or controller.get("seat_id") or "")
            if not actor_name:
                actor_name = self._default_controller_actor(ctx, controller_type)
            if actor_name in ctx.cast.all_names():
                actor = ctx.cast.get(actor_name)

                # 从 State 读取 role 信息并注入到 actor profile（改动 3）
                self._ensure_actor_profile(ctx, actor, actor_name)

                if controller_type == "human":
                    self._prepare_human_controller_request(actor, action)
                return await actor.act(cue, None)
        if controller_type == "plugin":
            service_result = await self._services.call_async(
                ctx,
                controller,
                "controller",
                {**ctx.full_context_payload(), "cue": cue},
            )
            return {
                "actor": str(controller.get("name") or "plugin"),
                "text": str(service_result.get("text") or ""),
                "data": service_result.get("data"),
            }
        return {"actor": controller_type, "text": "(system controller)", "data": None}

    def _prepare_human_controller_request(self, actor: Any, action: ControllerActionSpec) -> None:
        """把 controller_action 的选择语义注入真人 pending request。

        前端只认识 ActionRequest → ReplyRequest；如果这里不写 metadata，
        controller_action.choice 会退化成普通文本输入，玩家看不到选项。
        """
        choices = list(action.choices or [])
        if hasattr(actor, "set_candidates"):
            actor.set_candidates([str(choice.get("id")) for choice in choices if choice.get("id") is not None])
        if not hasattr(actor, "set_action_request_hints"):
            return
        metadata: dict[str, Any] = {
            "options": [
                {
                    "id": str(choice.get("id") or ""),
                    "text": str(choice.get("text") or choice.get("id") or ""),
                    "desc": choice.get("desc"),
                    "disabled": bool(choice.get("disabled", False)),
                    "disabled_reason": choice.get("disabled_reason") or choice.get("cond"),
                }
                for choice in choices
                if choice.get("id") is not None
            ],
        }
        free_input = dict(action.free_input or {})
        if free_input.get("enabled"):
            metadata["free_input"] = True
        actor.set_action_request_hints(kind="choose" if choices else "free_text", metadata=metadata)

    async def continue_generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        previous_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Continue generated story beats after referee allows it."""
        return await self._free_input.continue_generated_beat(ctx, previous_result)

    def _default_controller_actor(
        self,
        ctx: InteractiveExecutionContext,
        controller_type: str,
    ) -> str:
        """Resolve a default actor for human or agent controller types."""
        names = ctx.cast.all_names()
        if not names:
            return ""
        if controller_type == "human":
            human_seats = set(ctx.session_metadata.get("human_seat_ids") or [])
            for name in names:
                actor = ctx.cast.get(name)
                if name in human_seats or getattr(actor, "is_human", False):
                    return str(name)
        return str(names[0])

    def _apply_choice_target(
        self,
        ctx: InteractiveExecutionContext,
        result: dict[str, Any],
    ) -> None:
        """Store requested next scene/state for flow executor."""
        target = result.get("to")
        if not target:
            return
        ctx.session_metadata["interactive_next_target"] = str(target)

    def _record_cinematic_choice(
        self,
        ctx: InteractiveExecutionContext,
        result: dict[str, Any],
    ) -> None:
        """记录玩家的剧情选择到 GAME 状态（供剧情树面板使用）。"""
        from drama_engine.core.engine import SetAttr
        current_node = ctx.state.get_attr("GAME", "__current_flow_node") or ""
        choice_id = result.get("selected_choice") or ""
        choice_text = result.get("text") or ""
        target = result.get("to") or ""
        if not current_node or not target:
            return
        # 追加选择记录
        history = list(ctx.state.get_attr("GAME", "choice_history") or [])
        history.append({
            "node": current_node,
            "choice_id": choice_id,
            "choice_text": choice_text,
            "to": target,
        })
        ctx.writer.apply(SetAttr("GAME", "choice_history", history))

    def _selected_choice_from_response(
        self,
        choices: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Select choice from structured response or text."""
        if not choices:
            return {}
        data = response.get("data")
        selected_id = None
        if isinstance(data, dict):
            selected_id = data.get("choose") or data.get("choice") or data.get("choice_id")
        if selected_id is not None:
            for choice in choices:
                if str(choice.get("id")) == str(selected_id):
                    return choice
        text = str(response.get("text") or "").lower()
        for choice in choices:
            choice_id = str(choice.get("id") or "").lower()
            choice_text = str(choice.get("text") or "").lower()
            if text and (choice_id in text or choice_text in text):
                return choice
        return choices[0]

    def _ensure_actor_profile(
        self,
        ctx: InteractiveExecutionContext,
        actor: Any,
        actor_name: str,
    ) -> None:
        """确保 actor 已设置 profile（从 State 读取 role 信息）。

        只对 AI actor 生效，且只在第一次调用时设置（避免重复）。
        """
        # 只处理 AI actor
        if not hasattr(actor, "controller_type") or actor.controller_type != "ai":
            return

        # 如果已经设置过 profile，跳过
        if hasattr(actor, "_profile") and actor._profile is not None:
            return

        # 从 State 读取该 seat 的 role
        role_name = ctx.state.get_attr(actor_name, "role")
        if not role_name:
            return

        # 从 GAME.roles 读取 role 详细信息
        roles = ctx.state.get_attr("GAME", "roles")
        if not roles or not isinstance(roles, dict):
            return

        role_data = roles.get(role_name)
        if not role_data:
            return

        # 构建 ActorProfile 并设置
        from drama_engine.core.engine.actors import ActorProfile
        profile = ActorProfile(
            role_name=role_name,
            role_display_name=role_data.get("display_name", role_name),
            persona=role_data.get("description", ""),
        )
        actor.set_actor_profile(profile)
        logger.info(
            "[ControllerActionExecutor] 为 actor=%s 设置 profile，role=%s",
            actor_name,
            role_name,
        )

