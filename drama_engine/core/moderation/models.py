"""OOC 内容守卫的数据模型 / GuardRail data models（架构文档 §14 扩展）。

GuardRail 与 KnowledgeFirewall 方向相反、职责正交：
  - KnowledgeFirewall 管「流入 actor 的信息」（输入过滤，防剧透/开天眼）。
  - GuardRail 管「actor 产出的内容」（输出审查，防出圈/离题/泄密）。

本模块只定义纯数据对象；判定逻辑在 guardrail.py，处理策略在 strategies.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GuardRailSpec:
    """OOC 守卫规格 / GuardRail spec（由 DSL guardrail 块编译）。

    字段：
      enabled        — 是否启用守卫。false 时完全旁路，零开销。
      checks         — 要检查的维度，如 ["in_character", "on_topic", "no_secret_leak"]。
                       这些名字会拼进判定 prompt，语义由具体 GuardRail 实现理解。
      on_violation   — 违规处理策略名：block | rewrite | soft_warn | pass_with_flag。
      executor       — 标识用哪种 GuardRail 实现：llm / plugin / http。默认 llm。
      min_confidence — 判定阈值（0~1），低于此值视为不确定，按 fallback 处理。
      config         — executor 级额外配置（如 model_name / url / plugin_name 等）。
    """

    enabled: bool = False
    checks: list[str] = field(default_factory=list)
    on_violation: str = "soft_warn"
    executor: str = "llm"
    min_confidence: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        allowed = {"block", "rewrite", "soft_warn", "pass_with_flag"}
        assert self.on_violation in allowed, (
            f"未知 guardrail.on_violation: {self.on_violation}，可选: {sorted(allowed)}"
        )
        assert all(isinstance(name, str) for name in self.checks), (
            "guardrail.checks 必须全部是字符串维度名"
        )


@dataclass(slots=True)
class GuardDecision:
    """守卫判定结果 / GuardRail decision。

    字段：
      violated — 是否判定为违规（离题/出圈/泄密）。
      reason   — 判定理由（供 host/审计展示；LLM 可选返回）。
    """

    violated: bool
    reason: str = ""


@dataclass(slots=True)
class GuardOutcome:
    """守卫处理结果 / GuardRail outcome，由 ViolationStrategy 产出。

    字段：
      allow    — 是否放行该发言进入投递链路。
      response — 最终要投递的发言（可能被策略改写过）；allow=False 时无意义。
      flagged  — 是否给该发言打了标记（供 host 观测）。
      note     — 处理说明（记入事件流 / 日志）。
    """

    allow: bool
    response: dict[str, Any]
    flagged: bool = False
    note: str = ""


__all__ = ["GuardRailSpec", "GuardDecision", "GuardOutcome"]
