"""交互展示组件实现。

每种展示方式负责：
  把 narration 内容 + controller_action 组装成最终 scene patch。
  决定 publication 结构（消息格式）和 scene context。

scene 骨架由 base.build_add_scene_patch 统一构造，各展示方式只计算
自己的 scope / publication / context 三个可变部分。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    PresentationComponent,
    build_add_scene_patch,
)


class CinematicPresentation(PresentationComponent):
    """播片式：dialogue_history 放 context，controller_action.kind=cinematic，逐句播放。"""

    def build_scene_patch(
        self,
        narration: dict[str, Any],
        controller_action: dict[str, Any],
        ctx: Any,
        scene_id: str,
    ) -> dict[str, Any]:
        # cinematic 模式：对话放 context.dialogue_history，由 cinematic controller 逐句推送
        dialogue = list(narration.get("dialogue_history") or [])
        narration_text = str(narration.get("narration") or "")

        # 如果有 narration，作为 narrator 行加到 dialogue 开头
        if narration_text:
            dialogue.insert(0, {"speaker": "narrator", "text": narration_text})

        # controller_action.kind 改为 cinematic
        ca = dict(controller_action)
        ca["kind"] = "cinematic"

        scope = self._config.get("scope") or {"id": "story", "visibility": "public"}

        # 构建 scene context（含标题、大纲、地点）
        scene_context: dict[str, Any] = {"dialogue_history": dialogue}
        title = narration.get("title")
        if title:
            scene_context["title"] = title
        synopsis = narration.get("synopsis")
        if synopsis:
            scene_context["synopsis"] = synopsis
        location = narration.get("location")
        if location:
            scene_context["locations"] = [{"name": location}]

        return build_add_scene_patch(
            scene_id,
            ctx,
            scope=scope,
            controller_action=ca,
            publication={"messages": []},
            context=scene_context,
            state=getattr(ctx, "current_state_id", None),
        )


class ChatFlowPresentation(PresentationComponent):
    """聊天流式：publication.messages 为消息列表，走气泡渲染。"""

    def build_scene_patch(
        self,
        narration: dict[str, Any],
        controller_action: dict[str, Any],
        ctx: Any,
        scene_id: str,
    ) -> dict[str, Any]:
        scope = self._config.get("scope") or {"id": "story", "visibility": "public"}
        scope_id = scope.get("id", "story") if isinstance(scope, dict) else "story"

        # 构造 publication messages
        messages = []
        # 旁白
        narration_text = str(narration.get("narration") or "")
        if narration_text:
            messages.append({
                "audience": {"scope": scope_id},
                "content": {"text": narration_text},
            })

        # 对话列表（如果有）
        for entry in narration.get("dialogue_history") or []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "")
            if text:
                messages.append({
                    "audience": {"scope": scope_id},
                    "content": {"text": text},
                    "sender": entry.get("speaker"),
                })

        # 如果没有任何内容，放一条默认
        if not messages:
            messages.append({
                "audience": {"scope": scope_id},
                "content": {"text": "剧情继续……"},
            })

        return build_add_scene_patch(
            scene_id,
            ctx,
            scope=scope,
            controller_action=controller_action,
            publication={"messages": messages},
            state=getattr(ctx, "current_state_id", None),
        )


class VisualNovelPresentation(PresentationComponent):
    """视觉小说式：单条 narration + 底部选项 dock。"""

    def build_scene_patch(
        self,
        narration: dict[str, Any],
        controller_action: dict[str, Any],
        ctx: Any,
        scene_id: str,
    ) -> dict[str, Any]:
        # 取旁白或对话合并为一段文本
        narration_text = str(narration.get("narration") or "")
        if not narration_text:
            dialogue = narration.get("dialogue_history") or []
            parts = [str(e.get("text") or "") for e in dialogue if isinstance(e, dict)]
            narration_text = "\n".join(parts) or "……"

        scope = self._config.get("scope") or {"id": "story", "visibility": "public"}
        scope_id = scope.get("id", "story") if isinstance(scope, dict) else "story"

        return build_add_scene_patch(
            scene_id,
            ctx,
            scope=scope,
            controller_action=controller_action,
            publication={
                "messages": [{
                    "audience": {"scope": scope_id},
                    "content": {"text": narration_text},
                }]
            },
            state=getattr(ctx, "current_state_id", None),
        )


__all__ = ["CinematicPresentation", "ChatFlowPresentation", "VisualNovelPresentation"]
