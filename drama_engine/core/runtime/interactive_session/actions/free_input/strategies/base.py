"""自由输入策略基类（通用能力）。

所有自由输入策略的抽象接口。5种模式（choose_mapping / branch_then_return /
constrained_continue / free_continue / grow_flow）都可以有多种实现。

触发条件：
  - controller_action.kind == "choice"（有固定选项）
  - free_input.enabled == true（允许自由输入）
  - free_input.mode 决定用哪种策略

适用场景：
  - 文字冒险：自由输入映射到分支选项
  - 狼人杀投票：输入"投1号"映射到 vote_player_1
  - 桌游行动：输入"移动到红色格子"映射到 move_red
  - 任何需要自然语言输入的场景
"""

from __future__ import annotations

from typing import Any


class FreeInputStrategy:
    """自由输入策略基类。

    所有自由输入策略必须继承此类并实现 execute 方法。
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行自由输入策略。

        参数:
            mode: 策略模式名称（choose_mapping/branch_then_return/...）
            spec: DSL 配置（free_input 块的完整内容）
            context: 运行时上下文，包含:
                - text: 玩家自由输入的文本
                - choices: 可选分支列表（choose_mapping 需要）
                - state: 游戏状态快照
                - messages: 消息历史
                - ... 其他运行时数据

        返回:
            策略执行结果字典，格式根据 mode 不同而不同：
            - choose_mapping: {"selected_choice": id, "to": target, "confidence": float}
            - content_generation: {"text": str, "beats": list}
            - ending_selection: {"ending": str}
            - flow_patch_generation: {"patch": dict}
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 必须实现 execute 方法"
        )


__all__ = ["FreeInputStrategy"]
