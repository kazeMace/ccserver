"""信息隔离层（KnowledgeFirewall）。

`scope` 只解决「消息发给谁」；隐藏身份、剧本杀、DnD、综艺 AI 还需要解决「这个
actor / service / audience 在当前目的下允许看到哪些 state、history、role、secret」。
KnowledgeFirewall 提供统一投影接口 project_context(audience, purpose)。
"""

from drama_engine.core.visibility.knowledge_firewall import (
    KnowledgeFirewall,
    build_default_knowledge_firewall,
)

__all__ = ["KnowledgeFirewall", "build_default_knowledge_firewall"]
