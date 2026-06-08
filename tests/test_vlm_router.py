"""
test_vlm_router — 测试 VLMRouter 和 FallbackChain。

覆盖：
- VLMRouter NATIVE 路由（主模型支持图像）
- VLMRouter TRANSCRIBE 路由（主模型不支持图像）
- FallbackChain 候选列表
- 优雅降级（无可用 provider 时）
"""
import pytest
from ccserver.model.routing.router import VLMRouter, RouteResult, resolve_vlm_route
from ccserver.model.routing.fallback import FallbackChain
from ccserver.model.anthropic_adapter import get_default_adapter
from ccserver.model.plugins.registry import get_provider_registry


class TestRouteResult:
    """测试 RouteResult 数据类。"""

    def test_native_strategy(self):
        """NATIVE 策略的 is_native / is_transcribe。"""
        rr = RouteResult(
            strategy="native",
            adapter=None,
            model="claude-sonnet-4-6",
            provider_id="native",
        )
        assert rr.is_native
        assert not rr.is_transcribe

    def test_transcribe_strategy(self):
        """TRANSCRIBE 策略的 is_native / is_transcribe。"""
        rr = RouteResult(
            strategy="transcribe",
            adapter=None,
            model="glm-5v-turbo",
            provider_id="zhipuai",
            priority=18,
        )
        assert rr.is_transcribe
        assert not rr.is_native

    def test_priority_field(self):
        """priority 字段默认值为 0。"""
        rr = RouteResult(strategy="native", adapter=None, model="m", provider_id="p")
        assert rr.priority == 0


class TestVLMRouter:
    """测试 VLMRouter 路由决策。"""

    @pytest.fixture(autouse=True)
    def setup_registries(self):
        """确保 provider 和 media registry 初始化。"""
        get_provider_registry()

    def test_native_route_for_image_capable_model(self):
        """Claude 支持 image → NATIVE 策略。"""
        adapter = get_default_adapter()
        router = VLMRouter(main_model="claude-sonnet-4-6", main_adapter=adapter)
        route = _sync(router.route())
        assert route.is_native
        assert route.strategy == "native"

    def test_transcribe_route_for_text_only_model(self):
        """DeepSeek 不支持 image → TRANSCRIBE 策略。"""
        router = VLMRouter(main_model="deepseek-chat", main_adapter=None)
        route = _sync(router.route())
        assert route.is_transcribe
        assert route.strategy == "transcribe"

    def test_model_without_registry_entry_defaults_to_transcribe(self):
        """未注册模型默认不支持图像 → TRANSCRIBE。"""
        router = VLMRouter(main_model="unknown-uncatalogued-model", main_adapter=None)
        route = _sync(router.route())
        assert route.is_transcribe

    def test_fallback_candidates_sorted_by_priority(self):
        """Fallback 候选按 priority 排序。"""
        router = VLMRouter(main_model="deepseek-chat", main_adapter=None)
        candidates = router.get_fallback_candidates()
        # 验证排序：priority 递增
        priorities = [c.priority for c in candidates]
        assert priorities == sorted(priorities)

    def test_fallback_candidates_exclude_unavailable(self):
        """不包含 API key 未配置的 provider（如 zhipuai 无 key）。"""
        import os
        if not os.getenv("ZHIPUAI_API_KEY"):
            router = VLMRouter(main_model="deepseek-chat", main_adapter=None)
            candidates = router.get_fallback_candidates()
            provider_ids = [c.provider_id for c in candidates]
            assert "zhipuai" not in provider_ids


class TestFallbackChain:
    """测试 FallbackChain。"""

    def test_candidate_count(self):
        """candidate_count 属性正确。"""
        candidates = [
            RouteResult(strategy="transcribe", adapter=None, model="m1", provider_id="p1"),
            RouteResult(strategy="transcribe", adapter=None, model="m2", provider_id="p2"),
        ]
        chain = FallbackChain(candidates)
        assert chain.candidate_count == 2

    def test_empty_candidates_raises(self):
        """空候选列表抛出 AssertionError。"""
        with pytest.raises(AssertionError):
            FallbackChain([])

    def test_execute_with_success(self):
        """第一个候选成功时返回结果。"""
        candidates = [
            RouteResult(strategy="transcribe", adapter=None, model="m1", provider_id="p1"),
        ]
        chain = FallbackChain(candidates)

        async def call_fn(route):
            return "success"

        result, used_route = _sync(chain.execute(call_fn))
        assert result == "success"
        assert used_route.provider_id == "p1"

    def test_execute_fallback_on_failure(self):
        """第一个候选失败时尝试第二个。"""
        call_count = [0]

        candidates = [
            RouteResult(strategy="transcribe", adapter=None, model="m1", provider_id="p1"),
            RouteResult(strategy="transcribe", adapter=None, model="m2", provider_id="p2"),
        ]
        chain = FallbackChain(candidates)

        async def call_fn(route):
            call_count[0] += 1
            if route.provider_id == "p1":
                raise RuntimeError("p1 failed")
            return "success from p2"

        result, used_route = _sync(chain.execute(call_fn))
        assert result == "success from p2"
        assert used_route.provider_id == "p2"
        assert call_count[0] == 2

    def test_execute_all_fail_raises(self):
        """所有候选都失败抛出 RuntimeError。"""
        candidates = [
            RouteResult(strategy="transcribe", adapter=None, model="m1", provider_id="p1"),
            RouteResult(strategy="transcribe", adapter=None, model="m2", provider_id="p2"),
        ]
        chain = FallbackChain(candidates)

        async def call_fn(route):
            raise RuntimeError(f"{route.provider_id} failed")

        with pytest.raises(RuntimeError, match="FallbackChain"):
            _sync(chain.execute(call_fn))


class TestResolveVlmRoute:
    """测试便捷函数 resolve_vlm_route()。"""

    @pytest.fixture(autouse=True)
    def setup_registries(self):
        """确保 registry 初始化。"""
        get_provider_registry()

    def test_resolve_native(self):
        """Claude → NATIVE。"""
        adapter = get_default_adapter()
        route = _sync(resolve_vlm_route(
            main_model="claude-sonnet-4-6",
            main_adapter=adapter,
        ))
        assert route.is_native

    def test_resolve_transcribe(self):
        """DeepSeek → TRANSCRIBE。"""
        route = _sync(resolve_vlm_route(main_model="deepseek-chat"))
        assert route.is_transcribe


def _sync(coro):
    """同步执行异步协程的辅助函数。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    return loop.run_until_complete(coro)
