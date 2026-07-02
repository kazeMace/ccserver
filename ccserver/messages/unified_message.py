"""
ccserver/messages/unified_message.py

统一输入消息类（会话历史的一条记录）。
零依赖：只依赖 dataclass、标准库、本包的 blocks 模块。

Unified input message class (one record in conversation history).
Zero external dependencies: only relies on dataclass, stdlib, and blocks module.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnifiedMessage:
    """
    统一输入消息（会话历史的一条记录）。
    Unified input message (one entry in conversation history).

    Fields:
        role:     消息角色，"user" | "assistant" | "system"
                  Message role: "user" | "assistant" | "system"
        content:  块列表，每个元素为 UnifiedBlock 的子类实例
                  List of blocks; each element is a UnifiedBlock subclass instance
        metadata: 透传的额外顶层字段（如 _ccserver_* 标记），不改变 JSONL 磁盘格式
                  Passthrough extra top-level fields (e.g. _ccserver_* markers),
                  does not alter the JSONL disk format

    to_dict 规则（保持现有 JSONL 兼容）：
    to_dict rules (maintains existing JSONL compatibility):
        - 单个 UnifiedCommandBlock → content 折叠为 {"_type": "command", ...}
          Single UnifiedCommandBlock → content collapsed to {"_type": "command", ...}
        - 其余 → content 为 list[dict]
          Otherwise → content is list[dict]
        - metadata 中的键展平到顶层
          Keys in metadata are flattened to top level
    """

    role: str                                    # "user" | "assistant" | "system"
    content: list = field(default_factory=list)  # list[UnifiedBlock]
    metadata: dict = field(default_factory=dict) # 透传标记，展平到 to_dict 顶层

    def to_dict(self) -> dict:
        """
        序列化为 wire dict，用于 storage/recorder/hooks 等边界。
        Serialize to a wire dict for use at storage/recorder/hooks boundaries.

        特殊规则 / Special rule:
            单个 UnifiedCommandBlock → content 为 {"_type":"command",...}（非 list），
            其余情况 content 恒为 list[dict]。
            Single UnifiedCommandBlock → content is {"_type":"command",...} (not a list);
            all other cases content is always list[dict].

        返回 / Returns:
            dict — 包含 role、content 以及 metadata 中所有键的字典。
                   Dict containing role, content, and all keys from metadata.
        """
        # 延迟导入，避免循环依赖（blocks → unified_message → blocks）
        # Deferred import to avoid circular dependency (blocks → unified_message → blocks)
        from .blocks import UnifiedCommandBlock

        # 特殊处理：单个 CommandBlock 折叠为 dict（保持 PromptLib.on_message 的 dict-facing 契约）
        # Special case: single CommandBlock is collapsed to dict
        # (to maintain PromptLib.on_message's dict-facing contract)
        if len(self.content) == 1 and isinstance(self.content[0], UnifiedCommandBlock):
            out = {"role": self.role, "content": self.content[0].to_dict()}
        else:
            # 其余情况：content 恒为 list，保持与磁盘 JSONL 格式一致
            # All other cases: content is always list, consistent with JSONL disk format
            out = {"role": self.role, "content": [b.to_dict() for b in self.content]}

        # metadata 中的键展平到顶层（如 _ccserver_team_new_task、task_id 等）
        # Flatten metadata keys to top level (e.g. _ccserver_team_new_task, task_id)
        out.update(self.metadata)
        return out
