"""跨模式组件基类定义。

定义 Free-Input Pipeline 中所有跨模式公用组件的抽象接口：
  - InputGuard: 输入校验（前置，在任何处理之前）
  - OutputGuard: 输出校验（后置，在生成内容之后）
  - Planner: 剧情规划（可选，在生成之前产出大纲）
  - ChoiceDesigner: 选项设计（可选，独立于内容生成）
  - AssetResolver: 资产匹配（可选，从预制资产池选图/选音）

所有组件支持 4 种执行后端（executor）：
  - builtin: 内置 Python 实现
  - llm: 调用 LLM
  - plugin: GamePack/用户注册的函数
  - http: 外部 HTTP 服务
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import (
    AssetMatch,
    GuardResult,
    PlanResult,
)


# ════════════════════════════════════════
# 输入守卫
# ════════════════════════════════════════


class InputGuard:
    """输入守卫基类。

    在玩家自由输入进入任何处理逻辑之前，校验输入的合法性。
    多个 InputGuard 按配置顺序串联执行，任一失败即中断流程。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化守卫。

        参数:
            config: 来自 DSL guards.input[].config 的配置
        """
        self._config = config

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验玩家输入。

        参数:
            payload: 待校验内容
                - text (str): 玩家输入的原始文本
                - characters (list[dict]): 剧本中定义的角色列表
                - scene_id (str): 当前场景 id
                - message_history (list): 最近的消息历史
            ctx: InteractiveExecutionContext

        返回:
            GuardResult 实例
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 输出守卫
# ════════════════════════════════════════


class OutputGuard:
    """输出守卫基类。

    在生成内容产出之后、呈现给玩家之前，校验内容的质量和合法性。
    多个 OutputGuard 按配置顺序串联执行。

    每个 OutputGuard 可配置 on_fail 策略：
      - retry: 重新调用生成（最多 max_retries 次）
      - fallback: 回退到保守/模板输出
      - reject: 直接拒绝，返回错误给玩家
      - skip: 跳过此 Guard，记录日志但继续执行
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化守卫。

        参数:
            config: 来自 DSL guards.output[].config 的配置
                - on_fail (str): 失败策略，默认 "reject"
                - max_retries (int): retry 模式下最大重试次数，默认 2
        """
        self._config = config

    @property
    def on_fail(self) -> str:
        """失败时的处理策略。"""
        return str(self._config.get("on_fail", "reject"))

    @property
    def max_retries(self) -> int:
        """retry 模式下最大重试次数。"""
        return int(self._config.get("max_retries", 2))

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验生成内容。

        参数:
            payload: 待校验的生成结果
                - narration (str): 叙述文本
                - dialogue_history (list[dict]): 对话列表
                - choices (list[dict]): 生成的选项
                - title (str): 标题
                - synopsis (str): 简介
            ctx: InteractiveExecutionContext

        返回:
            GuardResult 实例
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 规划器
# ════════════════════════════════════════


class Planner:
    """规划器基类。

    在主生成之前，产出标题/大纲/角色列表等结构化信息，
    指导后续 Generator 的生成方向，也为 AssetResolver 提供提示。

    Planner 是可选组件。未配置时 pipeline 跳过此阶段。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化规划器。

        参数:
            config: 来自 DSL generation.planner.config 的配置
                - generate_title (bool): 是否生成标题
                - generate_synopsis (bool): 是否生成简介
        """
        self._config = config

    async def plan(
        self,
        player_action: str,
        context: dict[str, Any],
        ctx: Any,
    ) -> PlanResult:
        """生成场景规划。

        参数:
            player_action: 玩家行动描述
            context: 当前剧情上下文
                - message_history (list): 消息历史
                - characters (list[dict]): 角色列表
                - state_snapshot (dict): 状态快照
            ctx: InteractiveExecutionContext

        返回:
            PlanResult 实例
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 选项设计器
# ════════════════════════════════════════


class ChoiceDesigner:
    """选项设计器基类。

    独立于内容生成，专门为生成的场景设计玩家可选动作。

    与 InteractionMode 的关系：
      - InteractionMode 决定结构：要不要 choices、要不要 free_input、要不要限时
      - ChoiceDesigner 决定内容：choices 具体是什么文字/态度

    当配置了 ChoiceDesigner 时：
      - Generator prompt 中不注入 choices 生成指令
      - 内容生成完毕后，由 ChoiceDesigner 独立生成 choices

    当未配置 ChoiceDesigner 时：
      - 保持现有行为（Generator 一次性输出剧情 + choices）
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化选项设计器。

        参数:
            config: 来自 DSL generation.choice_designer.config 的配置
                - count (int): 生成选项数量（默认 3）
                - attitudes (list[str]): 期望的态度分布
                    如 ["bold", "neutral", "cautious"]
        """
        self._config = config

    async def design_choices(
        self,
        narration: dict[str, Any],
        context: dict[str, Any],
        ctx: Any,
    ) -> list[dict[str, Any]]:
        """设计玩家选项。

        参数:
            narration: 已生成的剧情内容
                - narration (str): 叙述文本
                - dialogue_history (list[dict]): 对话
                - title (str): 标题
            context: 完整运行时上下文
                - message_history, characters, state_snapshot 等
            ctx: InteractiveExecutionContext

        返回:
            选项列表，每个选项为 dict：
            [
                {"id": "bold_move", "text": "大胆表白", "attitude": "bold"},
                {"id": "cautious", "text": "试探性暗示", "attitude": "neutral"},
                {"id": "retreat", "text": "假装没事离开", "attitude": "cautious"},
            ]
            必须字段: id, text
            可选字段: attitude, to, metadata
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 资产匹配器
# ════════════════════════════════════════


class AssetResolver:
    """资产匹配器基类。

    根据生成的剧情内容，从预制资产池中选择最匹配的资产。
    适用于需要配图/配音/选立绘状态的场景。

    AssetResolver 是可选组件。未配置时不进行资产匹配。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化资产匹配器。

        参数:
            config: 来自 DSL generation.asset_resolver.config 的配置
                - pool_ref (str): 资产池路径引用
                - roles (list[str]): 需要匹配的资产角色列表
        """
        self._config = config

    async def resolve(
        self,
        content: dict[str, Any],
        asset_pool: list[dict[str, Any]],
        ctx: Any,
    ) -> list[AssetMatch]:
        """从资产池中匹配资产。

        参数:
            content: 已生成的内容
                - narration (str): 叙述
                - characters_involved (list[str]): 涉及角色
                - mood (str): 情绪/氛围（如果 Planner 产出了）
                - title (str): 标题
            asset_pool: 可用资产列表，每个资产为 dict：
                [
                    {
                        "id": "bg_garden_night",
                        "tags": ["night", "garden", "romantic"],
                        "path": "assets/backgrounds/garden_night.webp",
                        "description": "月光下的花园",
                        "role": "background"
                    },
                    ...
                ]
            ctx: InteractiveExecutionContext

        返回:
            匹配结果列表（按匹配度排序，最佳在前）
        """
        raise NotImplementedError


__all__ = [
    "InputGuard",
    "OutputGuard",
    "Planner",
    "ChoiceDesigner",
    "AssetResolver",
]
