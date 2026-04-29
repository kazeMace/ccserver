"""
registry — MediaUnderstandingRegistry 全局注册表。

管理所有注册的 MediaUnderstandingProvider 实例。
按 auto_priority 排序，提供 get_best() 自动选择最佳 VLM provider。
"""

from __future__ import annotations

from loguru import logger

from .base import MediaUnderstandingProvider


class MediaUnderstandingRegistry:
    """
    全局媒体理解能力注册表。

    每个 provider 可以注册一个 MediaUnderstandingProvider，
    系统按 auto_priority 排序，自动选择最佳提供者。

    auto_priority 数值越低优先级越高：
    - openai=10, qwen=15, zhipuai=18, anthropic=20, google=30, volcano=40
    """

    def __init__(self):
        """初始化空注册表。"""
        # key: provider_id, value: MediaUnderstandingProvider
        self._providers: dict[str, MediaUnderstandingProvider] = {}

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(self, provider: MediaUnderstandingProvider) -> None:
        """
        注册一个媒体理解能力提供者。

        Args:
            provider: MediaUnderstandingProvider 实例

        Raises:
            AssertionError: provider 不符合协议要求
        """
        assert provider is not None, "provider must not be None"
        assert isinstance(provider.provider_id, str) and provider.provider_id, \
            f"provider_id must be non-empty string, got: {provider.provider_id!r}"

        key = provider.provider_id.lower()

        if key in self._providers:
            old = self._providers[key]
            logger.warning(
                "MediaUnderstandingRegistry 覆盖注册 | provider={} old_priority={} new_priority={}",
                key, old.auto_priority, provider.auto_priority,
            )

        self._providers[key] = provider
        logger.info(
            "MediaUnderstandingRegistry 注册 | provider={} auto_priority={}",
            key, provider.auto_priority,
        )

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get_by_provider(self, provider_id: str) -> MediaUnderstandingProvider | None:
        """
        根据 provider id 查找媒体理解提供者。

        Args:
            provider_id: provider id

        Returns:
            MediaUnderstandingProvider 或 None
        """
        return self._providers.get(provider_id.lower())

    def get_best(self) -> MediaUnderstandingProvider | None:
        """
        自动选择最佳（auto_priority 最低）的媒体理解提供者。

        Returns:
            最佳提供者；如果没有注册任何提供者则返回 None
        """
        if not self._providers:
            logger.debug("MediaUnderstandingRegistry 无可用提供者")
            return None

        # 按 auto_priority 升序排序（数值越低越优先），同优先级按 provider_id 字典序
        best = min(self._providers.values(), key=lambda p: (p.auto_priority, p.provider_id))
        logger.debug(
            "MediaUnderstandingRegistry 自动选择 | provider={} priority={}",
            best.provider_id, best.auto_priority,
        )
        return best

    def get_sorted(self) -> list[MediaUnderstandingProvider]:
        """
        按 auto_priority 升序返回所有提供者。

        用于 fallback 链：依次尝试，失败时自动换下一个。

        Returns:
            排序后的提供者列表
        """
        return sorted(
            self._providers.values(),
            key=lambda p: (p.auto_priority, p.provider_id),
        )

    def get_best_for_provider(self, preferred_provider: str) -> MediaUnderstandingProvider | None:
        """
        优先选择指定 provider，不可用时自动 fallback 到最佳。

        Args:
            preferred_provider: 偏好的 provider id

        Returns:
            MediaUnderstandingProvider 或 None
        """
        provider = self._providers.get(preferred_provider.lower())
        if provider is not None:
            logger.debug("MediaUnderstandingRegistry 使用指定 provider | provider={}", preferred_provider)
            return provider
        logger.debug("MediaUnderstandingRegistry 指定 provider 不可用，使用自动选择 | preferred={}", preferred_provider)
        return self.get_best()

    def list_providers(self) -> list[str]:
        """列出所有已注册的 provider id。"""
        return sorted(self._providers.keys())

    @property
    def count(self) -> int:
        """已注册的媒体理解提供者数量。"""
        return len(self._providers)


# ── 进程级单例 ────────────────────────────────────────────────────────────────────

_registry_instance: MediaUnderstandingRegistry | None = None


def get_media_registry() -> MediaUnderstandingRegistry:
    """
    返回进程级单例 MediaUnderstandingRegistry。

    首次调用时为空，ProviderPlugin 通过 register() 注册媒体理解能力。

    Returns:
        MediaUnderstandingRegistry 单例实例
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = MediaUnderstandingRegistry()
    return _registry_instance
