"""
registry — 全局模型能力注册表。

进程级单例，线程安全。所有 ProviderPlugin 在初始化时向此注册表登记自己支持的模型。

提供按 model_id 查询、按 provider 过滤、按 input_type 筛选等能力。
Agent、VLMRouter 等消费者通过此注册表判断模型的输入能力。
"""

from __future__ import annotations

from loguru import logger

from .model_info import ModelInfo
from .catalog import BUILTIN_MODEL_CATALOG


class ModelInfoRegistry:
    """
    全局模型能力注册表。

    内部使用 dict[str, ModelInfo] 存储，key 为 model_id。
    线程安全：Python GIL 保证了 dict 读写的基本安全性，
    但批量查询结果可能不是一致快照（可接受）。

    Usage:
        registry = get_registry()
        info = registry.get("deepseek-chat")          # 返回 ModelInfo 或 None
        supports = registry.supports("claude-sonnet-4-6", "image")  # True
        models = registry.list_by_input_type("image")  # 所有支持图像的模型
    """

    def __init__(self):
        """初始化空注册表。首次调用 get_registry() 时会自动加载内置目录。"""
        self._models: dict[str, ModelInfo] = {}

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(self, info: ModelInfo) -> None:
        """
        注册一个模型的能力描述。

        Args:
            info: ModelInfo 实例

        注意：如果 model_id 已存在，则覆盖（后者优先）。
        """
        assert info is not None, "ModelInfo must not be None"
        assert isinstance(info.model_id, str) and info.model_id, \
            f"model_id must be a non-empty string, got: {info.model_id!r}"

        existed = info.model_id in self._models
        self._models[info.model_id] = info

        if existed:
            logger.debug("ModelInfoRegistry 覆盖注册 | model_id={}", info.model_id)
        else:
            logger.debug("ModelInfoRegistry 注册 | model_id={} provider={} types={}",
                         info.model_id, info.provider, sorted(info.input_types))

    def register_bulk(self, infos: list[ModelInfo]) -> None:
        """
        批量注册模型。

        Args:
            infos: ModelInfo 列表
        """
        for info in infos:
            self.register(info)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get(self, model_id: str) -> ModelInfo | None:
        """
        根据 model_id 查询模型能力。

        Args:
            model_id: 模型唯一标识

        Returns:
            ModelInfo 或 None（未注册时）
        """
        assert isinstance(model_id, str), f"model_id must be str, got {type(model_id)}"
        return self._models.get(model_id)

    def list_by_provider(self, provider: str) -> list[ModelInfo]:
        """
        列出指定 provider 的所有已注册模型。

        Args:
            provider: provider id，如 "anthropic"、"zhipuai"

        Returns:
            ModelInfo 列表
        """
        assert isinstance(provider, str) and provider, \
            f"provider must be a non-empty string, got: {provider!r}"
        result = [info for info in self._models.values() if info.provider == provider]
        logger.debug("ModelInfoRegistry 按 provider 过滤 | provider={} count={}", provider, len(result))
        return result

    def list_by_input_type(self, input_type: str) -> list[ModelInfo]:
        """
        列出所有支持指定输入类型的模型。

        Args:
            input_type: 输入类型，如 "image"、"video"、"file"

        Returns:
            支持的模型列表
        """
        assert isinstance(input_type, str) and input_type, \
            f"input_type must be a non-empty string, got: {input_type!r}"
        result = [info for info in self._models.values() if info.supports(input_type)]
        logger.debug("ModelInfoRegistry 按 input_type 过滤 | type={} count={}", input_type, len(result))
        return result

    def list_by_input_type_with_provider(
        self, input_type: str, provider: str
    ) -> list[ModelInfo]:
        """
        列出指定 provider 下支持指定输入类型的所有模型。

        Args:
            input_type: 输入类型
            provider:   provider id

        Returns:
            符合条件的模型列表，按 priority 降序排列
        """
        result = [
            info for info in self._models.values()
            if info.supports(input_type) and info.provider == provider
        ]
        result.sort(key=lambda x: x.priority, reverse=True)
        return result

    def supports(self, model_id: str, input_type: str) -> bool:
        """
        判断指定模型是否支持指定输入类型。

        Args:
            model_id:   模型唯一标识
            input_type: 输入类型

        Returns:
            True 表示支持。如果 model_id 未注册，返回 False（未知模型默认不支持非文本输入）。
        """
        info = self._models.get(model_id)
        if info is None:
            logger.debug("ModelInfoRegistry 未知模型 | model_id={}", model_id)
            return False
        return info.supports(input_type)

    def list_all(self) -> list[ModelInfo]:
        """列出所有已注册模型。"""
        return list(self._models.values())

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _init_defaults(self) -> None:
        """
        加载内置模型目录。

        仅在首次调用 get_registry() 时执行一次。
        后续 ProviderPlugin 可以通过 register() 追加更多模型。
        """
        assert len(self._models) == 0, "_init_defaults should only be called once"
        self.register_bulk(BUILTIN_MODEL_CATALOG)
        logger.info("ModelInfoRegistry 初始化完成 | 内置模型总数={}", len(self._models))


# ── 进程级单例 ────────────────────────────────────────────────────────────────────

_registry_instance: ModelInfoRegistry | None = None


def get_registry() -> ModelInfoRegistry:
    """
    返回进程级单例 ModelInfoRegistry。

    首次调用时自动加载内置模型目录。
    后续 ProviderPlugin 通过 register() 追加更多模型。

    Returns:
        ModelInfoRegistry 单例实例
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ModelInfoRegistry()
        _registry_instance._init_defaults()
    return _registry_instance
