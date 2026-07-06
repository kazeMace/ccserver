"""信息隔离层实现（架构文档 §14）。

统一接口：
    project_context(audience, purpose) -> dict

audience 形如：host | public | player:<id> | agent:<id> | plugin:<name>
purpose  形如：prompt | action_validation | referee | view | recap | html

原则：
- 默认不给完整上下文。
- Agent（player/agent audience）永远拿 actor view，看不到全局 state 与他人秘密。
- 裁判、规则包、主持人（referee/host purpose 或 host audience）在授权下拿全局上下文。
- 所有上下文投影都可测试。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认视为「秘密」的属性名：不应投影给普通 actor / public。
_DEFAULT_SECRET_ATTRS = ("role", "faction", "identity", "word", "hand", "secret")

# 授权拿全局上下文的 audience 前缀 / purpose。
_PRIVILEGED_AUDIENCES = ("host",)
_PRIVILEGED_PURPOSES = ("referee", "recap")


class KnowledgeFirewall:
    """按 audience + purpose 生成受限上下文投影。"""

    def __init__(self, secret_attrs: tuple[str, ...] = _DEFAULT_SECRET_ATTRS) -> None:
        """初始化，指定视为秘密的属性名集合。"""
        self._secret_attrs = tuple(secret_attrs)

    def project_context(
        self,
        state: Any,
        audience: str,
        purpose: str,
        full_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为指定 audience + purpose 生成受限上下文。

        参数：
          state        — 当前 engine.State（游戏事实）。
          audience     — host | public | player:<id> | agent:<id> | plugin:<name>。
          purpose      — prompt | action_validation | referee | view | recap | html。
          full_payload — 可选的完整运行时 payload（含 messages/patches 等），授权 audience
                         直接返回它；受限 audience 只从中取白名单片段。
        """
        assert audience, "audience 不能为空"
        assert purpose, "purpose 不能为空"
        if self._is_privileged(audience, purpose):
            # 授权：返回完整 payload（或至少完整 state 快照）。
            if full_payload is not None:
                return dict(full_payload)
            return {"state": state.snapshot() if state is not None else {}}

        # 受限：actor view —— 只给自己的属性 + 公共信息，隐藏他人秘密。
        actor = self._actor_of(audience)
        return self._actor_view(state, actor, purpose)

    def _is_privileged(self, audience: str, purpose: str) -> bool:
        """判断该 audience / purpose 是否授权拿全局上下文。"""
        if any(audience == priv or audience.startswith(priv + ":") for priv in _PRIVILEGED_AUDIENCES):
            return True
        if purpose in _PRIVILEGED_PURPOSES:
            return True
        return False

    def _actor_of(self, audience: str) -> str | None:
        """从 audience 解析 actor id（player:<id> / agent:<id>）。"""
        for prefix in ("player:", "agent:"):
            if audience.startswith(prefix):
                return audience[len(prefix):]
        return None

    def _actor_view(self, state: Any, actor: str | None, purpose: str) -> dict[str, Any]:
        """构造 actor view：自己的完整属性 + 他人的公开属性。"""
        if state is None:
            return {"audience_kind": "restricted", "purpose": purpose, "self": {}, "others": {}}
        snapshot = state.snapshot()
        game = dict(snapshot.get("GAME") or {})
        # GAME 里也可能有秘密（如未公开的身份表），一并遮蔽。
        public_game = self._redact(game)
        self_attrs = dict(snapshot.get(actor) or {}) if actor else {}
        others: dict[str, Any] = {}
        for name, attrs in snapshot.items():
            if name in {"GAME", actor}:
                continue
            others[name] = self._redact(dict(attrs))
        return {
            "audience_kind": "restricted",
            "purpose": purpose,
            "actor": actor,
            "self": self_attrs,   # 自己可见全部属性
            "others": others,     # 他人只见非秘密属性
            "game": public_game,
        }

    def _redact(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """从属性字典移除秘密属性。"""
        return {key: value for key, value in attrs.items() if key not in self._secret_attrs}


def build_default_knowledge_firewall() -> KnowledgeFirewall:
    """构建默认信息隔离层。"""
    return KnowledgeFirewall()


__all__ = ["KnowledgeFirewall", "build_default_knowledge_firewall"]
