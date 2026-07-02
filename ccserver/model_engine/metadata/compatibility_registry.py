"""
compatibility_registry — 模型协议兼容性注册表。

进程级单例，按 (model_id, api_type) 查询对应的 ModelCompatibility。

查找优先级（从高到低）：
  1. 精确匹配 (model_id, api_type)   → 同一模型不同协议可能有差异
  2. 精确匹配 (model_id, None)        → 模型级默认，不区分协议
  3. 精确匹配 (None, api_type)        → 协议级默认，不区分模型
  4. 全局默认 ModelCompatibility()           → 标准接口行为兜底
"""

from __future__ import annotations

from loguru import logger

from .compatibility import ModelCompatibility
from .compatibility_catalog import BUILTIN_COMPATIBILITY_CATALOG


class CompatibilityRegistry:
    """
    模型协议兼容性注册表。

    内部以 (model_id, api_type) 为 key 的字典存储，其中任一字段可为 None
    表示"通配该维度"（对应查找优先级 2/3）。

    Usage:
        registry = get_compatibility_registry()
        compatibility = registry.get("deepseek-chat", "openai-completions")
        # → ModelCompatibility(supports_image_in_tool_result=False, ...)
    """

    def __init__(self):
        """初始化空注册表。首次调用 get_compatibility_registry() 时自动加载内置目录。"""
        # key: (model_id | None, api_type | None)
        self._entries: dict[tuple[str | None, str | None], ModelCompatibility] = {}

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(
        self,
        model_id: str | None,
        api_type: str | None,
        compatibility: ModelCompatibility,
    ) -> None:
        """
        注册一条兼容性条目。

        Args:
            model_id: 模型 ID，None 表示匹配该 api_type 下所有未单独配置的模型
            api_type: api_type，None 表示匹配该 model_id 下所有未单独配置的协议
            compatibility:   对应的 ModelCompatibility 实例
        """
        assert compatibility is not None, "ModelCompatibility must not be None"
        key = (model_id, api_type)
        existed = key in self._entries
        self._entries[key] = compatibility

        if existed:
            logger.debug("CompatibilityRegistry 覆盖注册 | model_id={} api_type={}", model_id, api_type)
        else:
            logger.debug("CompatibilityRegistry 注册 | model_id={} api_type={} supports_image_in_tool_result={}",
                         model_id, api_type, compatibility.supports_image_in_tool_result)

    def register_bulk(
        self,
        entries: list[tuple[tuple[str | None, str | None], ModelCompatibility]],
    ) -> None:
        """
        批量注册兼容性条目。

        Args:
            entries: [(model_id, api_type), ModelCompatibility] 元组列表
        """
        for (model_id, api_type), compatibility in entries:
            self.register(model_id, api_type, compatibility)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get(self, model_id: str | None, api_type: str | None) -> ModelCompatibility:
        """
        查询指定模型在指定协议下的兼容配置。

        查找优先级：
          1. (model_id, api_type)  精确匹配
          2. (model_id, None)      模型级默认
          3. (None, api_type)      协议级默认
          4. ModelCompatibility()         全局兜底

        Args:
            model_id: 模型 ID，None 时只按 api_type 查找
            api_type: api_type，None 时只按 model_id 查找

        Returns:
            匹配的 ModelCompatibility 实例，未找到时返回全局默认值
        """
        # 优先级 1：精确匹配
        if model_id and api_type:
            result = self._entries.get((model_id, api_type))
            if result is not None:
                logger.debug("CompatibilityRegistry 精确匹配 | model_id={} api_type={}", model_id, api_type)
                return result

        # 优先级 2：模型级默认（不区分协议）
        if model_id:
            result = self._entries.get((model_id, None))
            if result is not None:
                logger.debug("CompatibilityRegistry 模型级默认 | model_id={}", model_id)
                return result

        # 优先级 3：协议级默认（不区分模型）
        if api_type:
            result = self._entries.get((None, api_type))
            if result is not None:
                logger.debug("CompatibilityRegistry 协议级默认 | api_type={}", api_type)
                return result

        # 优先级 4：全局兜底
        logger.debug("CompatibilityRegistry 全局兜底 | model_id={} api_type={}", model_id, api_type)
        return ModelCompatibility()

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _init_defaults(self) -> None:
        """
        加载内置 compatibility 目录。

        仅在首次调用 get_compatibility_registry() 时执行一次。
        """
        assert len(self._entries) == 0, "_init_defaults should only be called once"
        self.register_bulk(BUILTIN_COMPATIBILITY_CATALOG)
        logger.info("CompatibilityRegistry 初始化完成 | 内置条目总数={}", len(self._entries))


# ── 进程级单例 ────────────────────────────────────────────────────────────────────

_registry_instance: CompatibilityRegistry | None = None


def get_compatibility_registry() -> CompatibilityRegistry:
    """
    返回进程级单例 CompatibilityRegistry。

    首次调用时自动加载内置 compatibility 目录。

    Returns:
        CompatibilityRegistry 单例实例
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = CompatibilityRegistry()
        _registry_instance._init_defaults()
    return _registry_instance
