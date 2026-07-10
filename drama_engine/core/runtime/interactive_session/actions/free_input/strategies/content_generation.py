"""内容生成策略（Content Generation）。

生成剧情内容的策略，用于以下模式：
  - branch_then_return: 临时支线生成
  - constrained_continue: 约束到预设结局的续写
  - free_continue: 完全自由续写

适用场景：
  - 文字冒险：根据玩家输入生成后续剧情
  - 互动小说：生成故事节拍（beat）
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)


class FixedTextContentGenerationStrategy(FreeInputStrategy):
    """固定文本内容生成（内置 fallback 实现）。

    当没有 LLM 或外部生成器时的兜底实现，返回固定文本。

    优点：无需外部依赖，适合 dry-run 测试
    缺点：无法真正生成动态内容
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """生成固定内容。

        参数:
            mode: branch_then_return / constrained_continue / free_continue
            spec: DSL free_input 配置，可能包含 generator.text 作为固定文本
            context: 包含 text（玩家输入）、state（游戏状态）等

        返回:
            {
                "text": 生成的文本内容,
                "beats": [{"text": "节拍文本"}] 节拍列表
            }
        """
        # 优先级：spec.generator.text > context.text > 默认文本
        generator_spec = spec.get("generator", {}) if isinstance(spec.get("generator"), dict) else {}
        text = str(generator_spec.get("text") or context.get("text") or "")

        if not text:
            text = "剧情继续向前推进。"

        return {
            "text": text,
            "beats": [{"text": text}],
        }


__all__ = ["FixedTextContentGenerationStrategy"]
