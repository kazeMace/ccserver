"""
router — VLM 自动路由引擎。

核心决策逻辑：
1. 如果用户显式配置了 VLM_PROVIDER / VLM_MODEL → 使用指定配置
2. 如果主模型自身支持 image 输入 → NATIVE 策略（直接传 image block）
3. 否则 → TRANSCRIBE 策略（用 autoPriority 选最佳 MediaUnderstandingProvider）

OpenClaw 三层路由的 Python 实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ccserver.model.adapter import ModelAdapter
from ccserver.model.info.registry import get_registry as get_model_registry
from ccserver.model.media.registry import get_media_registry
from ccserver.model.media.base import MediaUnderstandingProvider


@dataclass
class RouteResult:
    """
    VLM 路由决策结果。

    Attributes:
        strategy:    路由策略："native"（原生视觉）或 "transcribe"（先转文字）
        adapter:     用于调用 LLM 的 ModelAdapter
        model:       要使用的模型名
        provider_id: provider id
        priority:    autoPriority（用于 fallback 排序）

    当 strategy == "native" 时，调用方直接向主模型发送 image block。
    当 strategy == "transcribe" 时，调用方先调用 VLM 将图像转为文字描述。
    """
    strategy: str
    adapter: ModelAdapter
    model: str
    provider_id: str
    priority: int = 0

    @property
    def is_native(self) -> bool:
        """主模型自己可以看图。"""
        return self.strategy == "native"

    @property
    def is_transcribe(self) -> bool:
        """需要外部 VLM 把图转成文字。"""
        return self.strategy == "transcribe"


class VLMRouter:
    """
    VLM 路由决策器。

    负责回答一个核心问题：当前的多模态请求该由谁处理？

    - 主模型（如 Claude 4）能自己看图 → NATIVE 策略
    - 主模型（如 DeepSeek）不能看图 → TRANSCRIBE 策略，从 MediaUnderstandingRegistry 选最佳 VLM

    Usage:
        router = VLMRouter(main_model="deepseek-chat", main_adapter=adapter)
        route = await router.route()
        if route.is_native:
            # 直接发 image block 给主模型
            pass
        else:
            # 用 route.adapter + route.model 做视觉描述
            text = await describe_image_with_model(img, adapter=route.adapter, model=route.model)
    """

    def __init__(
        self,
        main_model: str = "",
        main_adapter: ModelAdapter | None = None,
        vlm_config: dict | None = None,
    ):
        """
        初始化 VLM 路由器。

        Args:
            main_model:   主对话使用的模型名，如 "deepseek-chat"、"claude-sonnet-4-6"
            main_adapter: 主对话使用的 ModelAdapter
            vlm_config:   VLM 显式配置，格式：
                          {"provider": "zhipuai", "model": "glm-5v-turbo",
                           "api_key": "xxx", "base_url": "https://..."}
                          为 None 时使用自动路由
        """
        self._main_model = main_model
        self._main_adapter = main_adapter
        self._vlm_config = vlm_config or {}

        logger.debug("VLMRouter 初始化 | main_model={} vlm_config_provider={}",
                     main_model, self._vlm_config.get("provider", "auto"))

    async def route(self) -> RouteResult:
        """
        执行路由决策，返回 RouteResult。

        决策优先级：
        1. 显式 vlm_config 配置 → 直接使用指定 provider/model
        2. 主模型支持 image → NATIVE（主模型自己处理图像）
        3. 从 MediaUnderstandingRegistry 自动选择最佳 VLM → TRANSCRIBE

        Returns:
            RouteResult

        Raises:
            RuntimeError: 没有任何可用的 VLM provider
        """
        # ── 决策 1：显式 VLM 配置 ─────────────────────────────────────────
        if self._vlm_config.get("provider"):
            return await self._route_explicit_vlm()

        # ── 决策 2：主模型支持 image？→ NATIVE ────────────────────────────
        if self._main_model and self._model_supports_image(self._main_model):
            return self._route_native()

        # ── 决策 3：主模型不支持 image → TRANSCRIBE ────────────────────────
        return await self._route_transcribe()

    def get_fallback_candidates(self) -> list[RouteResult]:
        """
        获取所有 VLM fallback 候选（按 autoPriority 排序）。

        用于构建 FallbackChain：试用第一个，失败试第二个，以此类推。
        跳过无法创建 adapter 的 provider（如 API key 未配置）。

        Returns:
            排序后的 RouteResult 列表（可能为空）
        """
        media_registry = get_media_registry()
        sorted_providers = media_registry.get_sorted()

        if not sorted_providers:
            return []

        candidates: list[RouteResult] = []
        for mp in sorted_providers:
            try:
                candidates.append(self._build_route_for_media_provider(mp))
            except Exception as e:
                logger.debug("VLMRouter fallback 跳过 provider | provider={} reason={}", mp.provider_id, e)
                continue

        return candidates

    # ── 私有路由方法 ──────────────────────────────────────────────────────────

    async def _route_explicit_vlm(self) -> RouteResult:
        """使用显式 VLM 配置创建 RouteResult。"""
        provider_name = self._vlm_config.get("provider", "")
        model_name = self._vlm_config.get("model", "")

        logger.info("VLMRouter 使用显式配置 | provider={} model={}", provider_name, model_name)

        # 创建对应的 adapter
        from ccserver.model.plugins.registry import get_provider_registry
        plugin_registry = get_provider_registry()

        adapter = plugin_registry.create_adapter(
            provider_name,
            api_key=self._vlm_config.get("api_key"),
            base_url=self._vlm_config.get("base_url"),
        )

        return RouteResult(
            strategy="transcribe",
            adapter=adapter,
            model=model_name,
            provider_id=provider_name,
            priority=0,  # 显式配置 = 最高优先级
        )

    def _route_native(self) -> RouteResult:
        """主模型自身支持图像，直接使用主模型。"""
        assert self._main_adapter is not None, \
            "main_adapter is required when main model supports images"

        logger.info("VLMRouter 选择 NATIVE 策略 | model={}", self._main_model)

        return RouteResult(
            strategy="native",
            adapter=self._main_adapter,
            model=self._main_model,
            provider_id="native",
            priority=0,
        )

    async def _route_transcribe(self) -> RouteResult:
        """
        从 MediaUnderstandingRegistry 自动选择最佳 VLM。

        按 auto_priority 依次尝试每个已注册的 provider。
        如果某个 provider 没有配置 API key（adapter 创建失败），则跳过，
        自动尝试下一个优先级的 provider。

        Raises:
            RuntimeError: 所有已注册 provider 均不可用（无 API key 或无注册）
        """
        media_registry = get_media_registry()

        # 优先使用 VLM_PROVIDER 环境变量指定的 provider
        import os
        preferred = os.getenv("CCSERVER_VLM_PROVIDER")

        if preferred:
            # 显式指定 provider：只尝试这个
            best = media_registry.get_best_for_provider(preferred)
            if best is None:
                raise RuntimeError(
                    f"CCSERVER_VLM_PROVIDER 指定的 provider '{preferred}' 未注册。"
                    f"可用 provider：{media_registry.list_providers()}"
                )
            try:
                result = self._build_route_for_media_provider(best)
                logger.info("VLMRouter 使用指定 provider | provider={}", preferred)
                return result
            except Exception as e:
                raise RuntimeError(
                    f"CCSERVER_VLM_PROVIDER 指定的 provider '{preferred}' 不可用：{e}"
                ) from e

        # 自动模式：按 auto_priority 依次尝试
        sorted_providers = media_registry.get_sorted()
        if not sorted_providers:
            raise RuntimeError(
                "没有任何已注册的 MediaUnderstandingProvider。\n"
                "请确保以下任意环境变量已配置：\n"
                "  - ANTHROPIC_API_KEY\n"
                "  - OPENAI_API_KEY\n"
                "  - QWEN_API_KEY\n"
                "  - ZHIPUAI_API_KEY\n"
                "或设置 CCSERVER_VLM_PROVIDER 指定 VLM provider。"
            )

        errors = []
        for mp in sorted_providers:
            try:
                result = self._build_route_for_media_provider(mp)
                logger.info("VLMRouter 自动选择 TRANSCRIBE | provider={} priority={}",
                            mp.provider_id, mp.auto_priority)
                return result
            except Exception as e:
                logger.debug("VLMRouter 跳过 provider | provider={} reason={}", mp.provider_id, e)
                errors.append(f"  - {mp.provider_id}: {e}")
                continue

        # 所有 provider 均失败
        provider_list = "\n".join(errors) if errors else "（无已注册 provider）"
        raise RuntimeError(
            f"所有 VLM provider 均不可用（共 {len(sorted_providers)} 个）：\n"
            f"{provider_list}\n"
            f"请检查环境变量是否配置正确。"
        )

    def _build_route_for_media_provider(
        self, media_provider: MediaUnderstandingProvider
    ) -> RouteResult:
        """
        为指定的 MediaUnderstandingProvider 构建 RouteResult。

        从插件注册表获取对应的 ModelAdapter。

        Args:
            media_provider: MediaUnderstandingProvider 实例

        Returns:
            RouteResult
        """
        from ccserver.model.plugins.registry import get_provider_registry

        # 通过 ProviderRegistry 创建 adapter
        provider_registry = get_provider_registry()
        adapter = provider_registry.create_adapter(media_provider.provider_id)

        # 确定使用的模型
        # 优先级：vlm_config > VLM_MODEL 环境变量 > provider 默认 > main_model
        model_name = self._vlm_config.get("model", "")

        if not model_name:
            import os
            model_name = os.getenv("CCSERVER_VLM_MODEL", "")

        if not model_name:
            # 从 ModelInfoRegistry 获取该 provider 的最佳图像模型
            model_registry = get_model_registry()
            candidates = model_registry.list_by_input_type_with_provider(
                "image", media_provider.provider_id
            )
            if candidates:
                model_name = candidates[0].model_id  # 按 priority 排序，第一个最高

        if not model_name:
            model_name = self._main_model

        return RouteResult(
            strategy="transcribe",
            adapter=adapter,
            model=model_name,
            provider_id=media_provider.provider_id,
            priority=media_provider.auto_priority,
        )

    def _model_supports_image(self, model_id: str) -> bool:
        """
        查询 ModelInfoRegistry 判断模型是否支持图像输入。
        """
        model_registry = get_model_registry()
        info = model_registry.get(model_id)
        if info is None:
            logger.debug("VLMRouter 未知模型，默认不支持图像 | model={}", model_id)
            return False
        return info.supports_image


# ── 便捷函数 ────────────────────────────────────────────────────────────────────


async def resolve_vlm_route(
    main_model: str = "",
    main_adapter: ModelAdapter | None = None,
    vlm_config: dict | None = None,
) -> RouteResult:
    """
    便捷函数：创建 VLMRouter 并执行路由。

    Args:
        main_model:   主模型名
        main_adapter: 主 adapter
        vlm_config:   VLM 配置

    Returns:
        RouteResult
    """
    router = VLMRouter(
        main_model=main_model,
        main_adapter=main_adapter,
        vlm_config=vlm_config,
    )
    return await router.route()
