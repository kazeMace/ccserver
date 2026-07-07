"""信息隔离层实现（架构文档 §14）。

统一接口：
    project_context(audience, purpose) -> dict

audience 形如：host | referee | recap | public | player:<id> | agent:<id> | plugin:<name>
purpose  形如：prompt | action_validation | referee | view | recap | html

原则：
- 默认不给完整上下文。
- Agent（player/agent audience）永远拿 actor view，看不到全局 state 与他人秘密。
- 授权**只看 audience 身份**，不看 purpose：只有受控内部角色 host / referee / recap
  能拿全局上下文。purpose 仅标注投影用途，不作为授权升级的钥匙——否则任何玩家
  只要把 purpose 传成 "referee" 就能开天眼（H1 缺口3）。
- 所有上下文投影都可测试。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 授权拿全局上下文的 audience（受控内部角色）。授权基于身份，与 purpose 无关。
_PRIVILEGED_AUDIENCES = ("host", "referee", "recap")


class KnowledgeFirewall:
    """按 audience + purpose 生成受限上下文投影。

    「什么算秘密」由 DSL 的 visibility.secret_attrs 声明驱动（见 VisibilityPolicy），
    不再硬编码。secret_attrs 为空即表示「无秘密、全部公开」。
    """

    def __init__(self, secret_attrs: tuple[str, ...] = ()) -> None:
        """初始化，指定视为秘密的属性名集合。

        参数：
          secret_attrs — 视为秘密、对他人隐藏的属性名集合。空集合表示无秘密。
        """
        self._secret_attrs = tuple(secret_attrs)

    def project_context(
        self,
        state: Any,
        audience: str,
        purpose: str,
        full_payload: dict[str, Any] | None = None,
        disclosed_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为指定 audience + purpose 生成受限上下文。

        参数：
          state        — 当前 engine.State（游戏事实）。
          audience     — host | public | player:<id> | agent:<id> | plugin:<name>。
          purpose      — prompt | action_validation | referee | view | recap | html。
          full_payload — 可选的完整运行时 payload（含 messages/patches 等），授权 audience
                         直接返回它；受限 audience 只从中取白名单片段。
          disclosed_facts — 可选的「该 actor 已被披露的动态事实」（fact_ref -> value），
                            由 DisclosureLedger.facts_for(actor) 提供，合成进受限视图的
                            self.disclosed。授权 audience 不需要（本就拿全量）。
        """
        assert audience, "audience 不能为空"
        assert purpose, "purpose 不能为空"
        if self._is_privileged(audience, purpose):
            # 授权：返回完整 payload（或至少完整 state 快照）。
            if full_payload is not None:
                return dict(full_payload)
            return {"state": state.snapshot() if state is not None else {}}

        # 受限：actor view —— 只给自己的属性 + 公共信息，隐藏他人秘密，并叠加已披露事实。
        actor = self._actor_of(audience)
        return self._actor_view(state, actor, purpose, disclosed_facts)

    def _is_privileged(self, audience: str, purpose: str) -> bool:
        """判断该 audience 是否授权拿全局上下文。

        授权**只看身份**：host / referee / recap 这三类受控内部角色（作为 audience）
        才拿全量。purpose 不再参与授权——玩家把 purpose 传成 "referee" 也不会升级
        （H1 缺口3）。参数 purpose 保留仅为接口兼容与用途标注。
        """
        return any(
            audience == priv or audience.startswith(priv + ":")
            for priv in _PRIVILEGED_AUDIENCES
        )

    def _actor_of(self, audience: str) -> str | None:
        """从 audience 解析 actor id（player:<id> / agent:<id>）。"""
        for prefix in ("player:", "agent:"):
            if audience.startswith(prefix):
                return audience[len(prefix):]
        return None

    def _actor_view(
        self,
        state: Any,
        actor: str | None,
        purpose: str,
        disclosed_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造 actor view：自己的完整属性 + 他人的公开属性 + 已披露的动态事实。"""
        facts = dict(disclosed_facts or {})
        if state is None:
            return {
                "audience_kind": "restricted",
                "purpose": purpose,
                "actor": actor,
                "self": {},
                "others": {},
                "disclosed": facts,
            }
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
            "self": self_attrs,      # 自己可见全部属性
            "others": others,        # 他人只见非秘密属性
            "game": public_game,
            "disclosed": facts,      # 已被披露的动态事实（如验人结果）
        }

    def _redact(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """从属性字典移除秘密属性。"""
        return {key: value for key, value in attrs.items() if key not in self._secret_attrs}


def build_default_knowledge_firewall() -> KnowledgeFirewall:
    """构建默认信息隔离层（无秘密声明，全部公开）。"""
    return KnowledgeFirewall()


def build_knowledge_firewall_from_policy(policy: Any) -> KnowledgeFirewall:
    """根据 VisibilityPolicy 构建信息隔离层。

    参数：
      policy — VisibilityPolicy 实例，或含 secret_attrs 属性的对象；None 时返回默认（无秘密）。
    """
    if policy is None:
        return KnowledgeFirewall()
    secret_attrs = tuple(getattr(policy, "secret_attrs", ()) or ())
    logger.debug("按 VisibilityPolicy 构建 KnowledgeFirewall，secret_attrs=%s", secret_attrs)
    return KnowledgeFirewall(secret_attrs=secret_attrs)


__all__ = [
    "KnowledgeFirewall",
    "build_default_knowledge_firewall",
    "build_knowledge_firewall_from_policy",
]
