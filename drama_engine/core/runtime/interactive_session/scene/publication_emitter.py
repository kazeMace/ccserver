"""Scene 发布/披露发射器（从 SceneExecutor 拆出，M3）。

职责单一：把 scene.publication 声明（messages / disclosures / views）按 audience 路由到
public / host / private 事件流，并把私发披露记入披露账本（供 KnowledgeFirewall 合成 actor view）。
SceneExecutor 只负责编排，「消息发给谁、怎么发、记不记账本」收敛到这里。

文本渲染（_render_cue）留在 SceneExecutor（scene cue 也用它），以回调 render_cue 注入。
"""

from __future__ import annotations

from typing import Any, Callable

from drama_engine.core.plugins import ViewContext
from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import SceneSpec


class PublicationEmitter:
    """把 scene.publication 发布到各受众，并记录披露账本。"""

    def __init__(self, render_cue: Callable[[InteractiveExecutionContext, Any], str]) -> None:
        """绑定文本渲染回调（复用 SceneExecutor 的 cue 渲染，避免重复模板逻辑）。"""
        assert callable(render_cue), "render_cue 必须可调用"
        self._render_cue = render_cue

    def drain_pending_broadcasts(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> None:
        """发布并清空 effects.broadcast 和 effects.emit_media 累积的待发内容。"""
        # 文本广播
        pending = ctx.state.get_attr("GAME", "__pending_broadcasts") or []
        if pending:
            ctx.writer.apply(SetAttr("GAME", "__pending_broadcasts", []))
            for item in pending:
                if not isinstance(item, dict):
                    continue
                audience = item.get("scope") or scene.scope.id
                text = str(item.get("template") or item.get("text") or "")
                if not text:
                    continue
                event = {
                    "kind": "interactive_broadcast",
                    "runtime_type": "interactive_session",
                    "scene": scene.id,
                    "audience": self._audience_label(audience, scene.scope.id),
                    "text": self._render_cue(ctx, text),
                }
                self._emit_to_audience(ctx, event, audience, scene.scope.id, private_default=False)
        # 多媒体投递
        media_pending = ctx.state.get_attr("GAME", "__pending_media") or []
        if media_pending:
            ctx.writer.apply(SetAttr("GAME", "__pending_media", []))
            for item in media_pending:
                if not isinstance(item, dict):
                    continue
                audience = item.get("scope") or scene.scope.id
                event = {
                    "kind": item.get("kind") or "video",
                    "runtime_type": "interactive_session",
                    "scene": scene.id,
                    "audience": self._audience_label(audience, scene.scope.id),
                    "data": {
                        "url": item.get("url") or "",
                        "title": item.get("title") or "",
                        "poster": item.get("poster") or "",
                        "subtitle_url": item.get("subtitle_url") or "",
                        "autoplay": bool(item.get("autoplay", False)),
                    },
                }
                self._emit_to_audience(ctx, event, audience, scene.scope.id, private_default=False)

    def publish(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> None:
        """Publish scene messages and disclosures."""
        publication = scene.publication or {}
        messages = publication.get("messages") or []
        for item in messages:
            if isinstance(item, str):
                text = self._render_cue(ctx, item)
                audience = scene.scope.id
            elif isinstance(item, dict):
                text = self._publication_text(ctx, item)
                audience = item.get("audience") or item.get("scope") or scene.scope.id
            else:
                continue
            event = {
                "kind": "interactive_publication",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": self._audience_label(audience, scene.scope.id),
                "text": text,
            }
            self._emit_to_audience(ctx, event, audience, default_scope=scene.scope.id, private_default=False)
        for item in publication.get("disclosures", []) or []:
            if not isinstance(item, dict):
                continue
            audience = item.get("audience") or item.get("scope") or scene.scope.id
            text = self._publication_text(ctx, item)
            event = {
                "kind": "interactive_disclosure",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": self._audience_label(audience, scene.scope.id),
                "text": text,
            }
            private_default = bool(item.get("private", True))
            self._emit_to_audience(
                ctx,
                event,
                audience,
                default_scope=scene.scope.id,
                private_default=private_default,
            )
            # 记录披露：把「这条事实被告知给哪些席位」写入披露账本，
            # 供 KnowledgeFirewall 后续为这些席位合成 actor view（如预言家验人结果）。
            self._record_disclosure(ctx, item, audience, scene.scope.id, private_default)
        for view in publication.get("views", []) or []:
            if not isinstance(view, dict):
                continue
            try:
                audience_spec = view.get("audience") or view.get("scope") or scene.scope.id
                audience_label = self._audience_label(audience_spec, scene.scope.id)
                projector_spec = {**view, "audience": audience_label}
                view_event = ctx.plugin_registry.project_view(
                    projector_spec,
                    ViewContext(
                        state=ctx.state,
                        scene_name=scene.id,
                        audience=str(audience_label),
                        mutation_log=ctx.state.mutation_log(),
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - publication failure should be visible.
                ctx.emit_host({
                    "kind": "interactive_session_warning",
                    "message": f"publication.views 投影失败: {exc}",
                    "scene": scene.id,
                })
                continue
            if view_event:
                self._emit_to_audience(
                    ctx,
                    view_event,
                    audience_spec,
                    default_scope=scene.scope.id,
                    private_default=bool(view.get("private") or view_event.get("private")),
                )

    def _publication_text(
        self,
        ctx: InteractiveExecutionContext,
        item: dict[str, Any],
    ) -> str:
        """Resolve publication text/template/ref content."""
        content = item.get("content") or item.get("message") or {}
        if isinstance(content, str):
            return self._render_cue(ctx, content)
        if not isinstance(content, dict):
            content = {}
        if "ref" in content:
            value = ctx.value_resolver.resolve(
                {"ref": content["ref"]},
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            return "" if value is None else str(value)
        text = (
            content.get("text")
            or content.get("template")
            or item.get("text")
            or item.get("template")
            or ""
        )
        return self._render_cue(ctx, text)

    def _emit_to_audience(
        self,
        ctx: InteractiveExecutionContext,
        event: dict[str, Any],
        audience: Any,
        default_scope: str,
        private_default: bool,
    ) -> None:
        """Route a publication event to public, host, or private players."""
        if isinstance(audience, dict):
            players = audience.get("players") or audience.get("seats")
            if isinstance(players, list) and players:
                for seat_id in players:
                    self._emit_private(ctx, str(seat_id), event)
                return
            scope_name = audience.get("scope") or audience.get("id") or default_scope
            visibility = audience.get("visibility")
            if visibility == "private" or private_default:
                members = audience.get("members") or []
                for seat_id in members:
                    self._emit_private(ctx, str(seat_id), event)
                if not members:
                    ctx.emit_host(event)
                return
            ctx.emit_public({**event, "audience": scope_name})
            return
        if private_default:
            ctx.emit_host(event)
            return
        ctx.emit_public({**event, "audience": audience or default_scope})

    def _emit_private(
        self,
        ctx: InteractiveExecutionContext,
        seat_id: str,
        event: dict[str, Any],
    ) -> None:
        """Emit a private event when the runtime provides a private sink."""
        if ctx.emit_private is not None:
            ctx.emit_private(seat_id, event)
            return
        ctx.emit_host({**event, "seat_id": seat_id})

    def _record_disclosure(
        self,
        ctx: InteractiveExecutionContext,
        item: dict[str, Any],
        audience: Any,
        default_scope: str,
        private_default: bool,
    ) -> None:
        """把一条 disclosure 的接收席位与事实值写入披露账本。

        只记录「私发给具体席位」的披露（这才是 firewall 需要合成到 actor view 的动态事实）；
        公开发布（进 public sink）无需记录，因为它本就人人可见。
        """
        recipients = self._private_recipients(audience, private_default)
        if not recipients:
            return
        content = item.get("content") or item.get("message") or {}
        fact_ref = ""
        if isinstance(content, dict) and content.get("ref"):
            fact_ref = str(content["ref"])
        if not fact_ref:
            fact_ref = f"disclosure:{self._audience_label(audience, default_scope)}"
        value = self._disclosure_value(ctx, item, content)
        for seat_id in recipients:
            ctx.record_disclosure(seat_id, fact_ref, value)

    def _private_recipients(self, audience: Any, private_default: bool) -> list[str]:
        """返回一条披露实际私发到的席位列表（公开发布返回空）。"""
        if isinstance(audience, dict):
            players = audience.get("players") or audience.get("seats")
            if isinstance(players, list) and players:
                return [str(seat_id) for seat_id in players]
            visibility = audience.get("visibility")
            if visibility == "private" or private_default:
                return [str(seat_id) for seat_id in (audience.get("members") or [])]
            return []
        return []

    def _disclosure_value(
        self,
        ctx: InteractiveExecutionContext,
        item: dict[str, Any],
        content: Any,
    ) -> Any:
        """解析披露的具体值：ref 取原始（结构化）值，否则取渲染文本。"""
        if isinstance(content, dict) and "ref" in content:
            return ctx.value_resolver.resolve(
                {"ref": content["ref"]},
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
        return self._publication_text(ctx, item)

    def _audience_label(self, audience: Any, default_scope: str) -> Any:
        """Return a compact audience label for event payloads."""
        if isinstance(audience, dict):
            return audience.get("scope") or audience.get("id") or audience.get("players") or default_scope
        return audience or default_scope


__all__ = ["PublicationEmitter"]
