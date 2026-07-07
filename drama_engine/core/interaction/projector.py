"""InteractionProjector：内部事件/动作 → interaction.v1 对外契约（core，游戏无关）。

职责（docs/interaction_protocol_design.md 第五部分对接点）：
- event（SessionEventStore backlog 条目）→ InteractionMessage：内部 kind → 封闭 role。
- ActionRequest → ReplyRequest：字段一一对应，动作 kind → 封闭 primitive。
- 组装 InboxResponse（messages + cursor + pending + status），供 REST /inbox 使用。

只填封闭安全键（role / primitive）；开放键（widget / card / props）由 game_pack 的
projection_profile 富化（本文件不认识任何具体游戏）。per-seat 可见性由上游
GameInstance + KnowledgeFirewall 决定投影哪些事件，本投影器只做机械归一。
"""

from __future__ import annotations

from typing import Any

# 内部 event kind → 对外 role（§2.1 封闭 6 种）。未列出的走默认推断。
_KIND_TO_ROLE: dict[str, str] = {
    "session_assigned": "system",
    "session_started": "system",
    "session_ended": "referee",
    "session_failed": "system_meta",
    "interactive_scene_started": "system",
    "interactive_scene_completed": "system",
    "interactive_scene_skipped": "system",
    "interactive_message": "dialogue",
    "interactive_controller_narration": "narrator",
    "interactive_controller_free_text": "dialogue",
    "interactive_controller_choice": "system",
    "generated_beat": "narrator",
    "interactive_disclosure": "secret",
    "interactive_publication": "system",
    "interactive_broadcast": "system",
    "interactive_schedule_pushed": "system",
    "interactive_schedule_popped": "system",
    "interactive_schedule_merge": "system",
    "interactive_schedule_timeout": "system",
    "guardrail_flag": "system_meta",
    "rollback_applied": "system_meta",
    "control_announcement": "system",
}

# 动作 kind → 对外 primitive（§场景-2 封闭 8 种）。
_ACTION_KIND_TO_PRIMITIVE: dict[str, str] = {
    "speak": "text",
    "vote": "vote",
    "choose": "choice",
    "choose_many": "multi_choice",
    "action": "structured",
    "form": "form",
    "rating": "form",
    "free_text": "text",
    "choice": "choice",
    "generic": "text",
    # 注：narration/none 不创建 ActionRequest（不产生 reply_request），故不在此表；
    # observe 是"无 reply"状态，不是 reply.primitive 值（文档封闭 8 原语不含 observe）。
}


class InteractionProjector:
    """把内部事件与动作请求投影为 interaction.v1 对外对象。"""

    def project_event(self, event: dict[str, Any], self_seat: str | None = None) -> dict[str, Any]:
        """把一条内部事件投影成 InteractionMessage（dict 形式，供 JSON 返回）。

        参数：
          event     — SessionEventStore backlog 条目（带 seq/type/audience）。
          self_seat — 当前受众 seat（用于标记 sender.kind=human 的自己）。
        """
        assert isinstance(event, dict), "event 必须是 dict"
        kind = str(event.get("type") or event.get("kind") or "system")
        role = _KIND_TO_ROLE.get(kind) or self._infer_role(event)
        text = self._extract_text(event)
        sender = self._extract_sender(event, self_seat)
        scope = self._extract_scope(event)
        return {
            "seq": int(event.get("seq") or 0),
            "session_id": str(event.get("session_id") or ""),
            "ts": float(event.get("ts") or event.get("seq") or 0),
            "role": role,
            "sender": sender,
            "body": {
                "text": text,
                "style": self._extract_style(event, role),
                "cards": self._extract_cards(event),
            },
            "scope": scope,
            "phase": event.get("phase"),
            "scene_id": event.get("scene") or event.get("scene_name"),
            "reply_request": None,
        }

    def project_request(self, request: Any, profile: Any = None) -> dict[str, Any] | None:
        """把 ActionRequest 投影成 ReplyRequest（dict）。request 为 None 返回 None。

        profile（ProjectionProfile，可选）：game_pack 提供的投影档案，按 scene 富化开放键
        widget/props；缺省时只填封闭键，前端走 primitive 保底。
        """
        if request is None:
            return None
        kind = str(getattr(request, "kind", "generic") or "generic")
        primitive = _ACTION_KIND_TO_PRIMITIVE.get(kind, "text")
        metadata = getattr(request, "metadata", None) or {}
        # 请求创建期的语义提示（DSL free_input/多选/确认）经 metadata 传入，projector 据此
        # 产出 choice_or_text / multi_choice / confirm，避免只靠 kind 拿不到这些原语。
        if metadata.get("free_input") and primitive in {"choice", "text"}:
            primitive = "choice_or_text"
        if metadata.get("multi"):
            primitive = "multi_choice"
        presentation = "confirm" if metadata.get("confirm") else "default"
        # 开放键优先取 metadata（请求创建期已写入），否则由 profile 按 scene 富化。
        scene_id = str(getattr(request, "scene_name", "") or "")
        widget = metadata.get("widget")
        props = metadata.get("props")
        if profile is not None:
            widget = widget or profile.widget_for(scene_id)
            props = props or profile.props_for(scene_id)
        candidates = getattr(request, "candidates", None)
        options = None
        if candidates:
            # 完整 ReplyOption（§3）：id/text/desc/disabled/disabled_reason/meta。
            # game_pack 可通过 metadata["option_meta"][cid] 预置 emoji / 实时票数等语义参数。
            option_meta = metadata.get("option_meta") or {}
            options = [
                {
                    "id": str(c),
                    "text": str(c),
                    "desc": None,
                    "disabled": False,
                    "disabled_reason": None,
                    "meta": option_meta.get(str(c)),
                }
                for c in candidates
            ]
        schema = getattr(request, "schema", None)
        free_input = None
        if primitive in {"text", "choice_or_text"}:
            free_input = {"placeholder": str(getattr(request, "cue", "") or ""), "multiline": True, "hint": None}
        timeout_seconds = getattr(request, "timeout_seconds", None)
        return {
            "request_id": str(getattr(request, "request_id", "")),
            "primitive": primitive,
            # 开放键：优先 metadata，其次 game_pack 的 projection_profile 按 scene 富化。
            "widget": widget,
            "props": props,
            "prompt": str(getattr(request, "cue", "") or ""),
            "presentation": presentation,
            "options": options,
            "free_input": free_input,
            "schema": schema,
            "timeout_ms": int(timeout_seconds * 1000) if timeout_seconds else None,
            # multi_choice 的选择上下限来自 metadata（min/max_select），非多选恒为 1。
            "min_select": int(metadata.get("min_select", 1)) if primitive == "multi_choice" else 1,
            "max_select": int(metadata.get("max_select", len(options or []) or 1)) if primitive == "multi_choice" else 1,
            # skippable 是"可跳过本次交互"，与 allow_resubmit（可重复提交）语义不同，独立取。
            "skippable": bool(metadata.get("skippable", False)),
        }

    def build_inbox(
        self,
        events: list[dict[str, Any]],
        after: int,
        pending_request: Any,
        status: str,
        self_seat: str | None = None,
        phase: str | None = None,
        reset_from: int | None = None,
        profile: Any = None,
    ) -> dict[str, Any]:
        """组装 InboxResponse（§5）。

        events 是该受众已授权的全部 backlog（可见性已由上游过滤）；这里按 after 增量筛选、
        投影成 InteractionMessage、把 pending 挂到最后一条消息上。
        profile：game_pack 投影档案，富化 pending 的 widget/props（可选）。
        """
        fresh = [e for e in events if int(e.get("seq") or 0) > after]
        messages = [self.project_event(e, self_seat) for e in fresh]
        pending = self.project_request(pending_request, profile=profile)
        if pending is not None and messages:
            messages[-1]["reply_request"] = pending
        cursor = max([after, *[m["seq"] for m in messages]]) if messages else after
        return {
            "messages": messages,
            "cursor": cursor,
            "pending": pending,
            "phase": phase,
            "status": self._map_status(status, pending),
            "reset_from": reset_from,
        }

    # —— 内部提取辅助 ——

    def _infer_role(self, event: dict[str, Any]) -> str:
        """无显式映射时按字段推断 role。"""
        if event.get("actor") or event.get("sender"):
            return "dialogue"
        if event.get("audience") == "host":
            return "system_meta"
        return "system"

    def _extract_text(self, event: dict[str, Any]) -> str:
        """提取主文本。"""
        for key in ("text", "message", "result", "content"):
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _extract_style(self, event: dict[str, Any], role: str) -> str:
        """推断展示样式。"""
        if event.get("strong"):
            return "announcement"
        if role == "narrator":
            return "dramatic"
        if str(event.get("scope") or "").startswith("group:") or event.get("private"):
            return "whisper"
        return "normal"

    def _extract_sender(self, event: dict[str, Any], self_seat: str | None) -> dict[str, Any] | None:
        """提取发送者（§2.1 Sender：kind/id/name/emoji/role/dead），标记自己为 human。"""
        actor = event.get("actor") or event.get("sender") or event.get("seat_id")
        if not actor:
            return None
        actor = str(actor)
        kind = "human" if self_seat is not None and actor == self_seat else "agent"
        return {
            "kind": kind,
            "id": actor,
            "name": event.get("actor_name") or actor,
            "emoji": event.get("emoji"),
            "role": event.get("role_tag"),
            "dead": event.get("dead"),
        }

    def _extract_scope(self, event: dict[str, Any]) -> str:
        """提取 scope 显示名。"""
        scope = event.get("scope")
        if isinstance(scope, str) and scope:
            return scope
        if event.get("audience") == "private":
            return "private"
        return "public"

    def _extract_cards(self, event: dict[str, Any]) -> list[dict[str, Any]] | None:
        """提取富卡片为 RichCard（kind/variant/data）。variant 是同 kind 下的皮肤变体，
        用于 §9.2 降级链 card.variant→card.kind；缺省 None 时前端降级到 kind。"""
        view_kind = event.get("view_kind")
        if view_kind:
            return [{
                "kind": str(view_kind),
                "variant": event.get("view_variant") or event.get("variant"),
                "data": dict(event.get("data") or {}),
            }]
        cards = event.get("cards")
        if isinstance(cards, list):
            # 补齐每张卡的 variant 键（缺省 None），保证对外 RichCard schema 一致。
            return [{**c, "variant": c.get("variant")} if isinstance(c, dict) else c for c in cards]
        return None

    def _map_status(self, status: str, pending: dict[str, Any] | None) -> str:
        """内部 session_status → 对外 SessionStatus（§5）。"""
        if status in {"ended", "failed", "paused"}:
            return status
        if status in {"terminated"}:
            return "ended"
        # running / assigned / lobby：有 pending 就是 running（该你了），否则 waiting_others。
        return "running" if pending is not None else "waiting_others"


__all__ = ["InteractionProjector"]
