"""适配器层：把不同执行引擎（plugin/llm/http）适配成统一的 FreeInputStrategy 接口。

设计模式：Adapter Pattern
目的：屏蔽不同执行引擎的差异，统一调用接口
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)

logger = logging.getLogger(__name__)


class PluginStrategyAdapter(FreeInputStrategy):
    """Plugin 执行引擎适配器。

    从 PluginRegistry 获取用户注册的函数，适配成策略接口。

    适用场景：
      - Game Pack 注册的增强策略
      - 用户自定义 Python 函数
    """

    def __init__(self, plugin_registry, plugin_name: str, fallback_mode: str = "choose_mapping"):
        """初始化适配器。

        参数:
            plugin_registry: PluginRegistry 实例
            plugin_name: 插件名称（如 "story.semantic_choice_mapper"）
            fallback_mode: 当 plugin 未注册时，允许 fallback 的模式
        """
        assert plugin_registry is not None, "plugin_registry 不能为空"
        assert plugin_name, "plugin_name 不能为空"

        self._registry = plugin_registry
        self._name = plugin_name
        self._fallback_mode = fallback_mode

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """调用 plugin。

        参数:
            mode: 策略模式名称
            spec: DSL 配置
            context: 运行时上下文

        返回:
            plugin 函数的返回值
        """
        logger.debug("[PluginAdapter] 调用 plugin: name=%s", self._name)

        # 尝试通过 PluginRegistry 已注册的 runtime_service 调用
        if self._registry.has_runtime_service(self._name):
            result = self._registry.call_runtime_service(self._name, context)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                logger.warning("[PluginAdapter] plugin %s 返回 None", self._name)
                return {}
            return result

        # 未注册：只有 choose_mapping 模式支持 difflib fallback
        if self._fallback_mode == "choose_mapping":
            logger.debug("[PluginAdapter] plugin %s 未注册，使用内置 difflib fallback", self._name)
            return self._difflib_fallback(context)

        raise ValueError(
            f"plugin '{self._name}' 未在 PluginRegistry 中注册，"
            f"且模式 '{self._fallback_mode}' 不支持内置 fallback"
        )

    def _difflib_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """内置 difflib 文本匹配 fallback（仅 choose_mapping 模式）。

        参数:
            context: 运行时上下文（包含 text 和 choices）

        返回:
            匹配结果字典
        """
        from difflib import SequenceMatcher

        text = str(context.get("text") or "").lower()
        choices = list(context.get("choices") or [])
        if not choices:
            return {"selected_choice": None, "confidence": 0.0}

        best_choice = choices[0]
        best_score = -1.0
        for choice in choices:
            choice_id = str(choice.get("id") or "")
            choice_text = str(choice.get("text") or "")
            haystack = (choice_id + " " + choice_text).lower()
            score = SequenceMatcher(None, text, haystack).ratio()
            # 精确包含匹配加分
            if text and (choice_id.lower() in text or choice_text.lower() in text):
                score += 1.0
            if score > best_score:
                best_score = score
                best_choice = choice

        return {
            "selected_choice": best_choice.get("id"),
            "to": best_choice.get("to"),
            "confidence": round(min(best_score, 1.0), 3),
        }


class LLMStrategyAdapter(FreeInputStrategy):
    """LLM 执行引擎适配器。

    动态调用 LLM 生成策略结果。

    适用场景：
      - DSL 中指定 executor: llm
      - 需要语义理解的场景
    """

    def __init__(self, mode: str, llm_client, spec: dict[str, Any]):
        """初始化适配器。

        参数:
            mode: 策略模式名称（用于构造默认 prompt）
            llm_client: LLM 客户端（必须实现 complete_async 或 run/act 方法）
            spec: DSL 配置（包含 prompt/provider/model 等）
        """
        assert mode, "mode 不能为空"
        assert llm_client is not None, "llm_client 不能为空"

        self._mode = mode
        self._client = llm_client
        self._spec = spec

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """调用 LLM 生成结果。

        参数:
            mode: 策略模式名称
            spec: DSL 配置
            context: 运行时上下文

        返回:
            解析后的 LLM 返回值
        """
        # 构造 prompt
        prompt = self._build_prompt(context)

        logger.debug("[LLMAdapter] 调用 LLM: mode=%s prompt_length=%d", self._mode, len(prompt))

        # 调用 LLM（尝试多种接口）
        if hasattr(self._client, "complete_async"):
            response = await self._client.complete_async(prompt)
        elif hasattr(self._client, "run"):
            result = self._client.run(prompt)
            response = await result if inspect.isawaitable(result) else result
        elif hasattr(self._client, "act"):
            result = self._client.act(prompt, None)
            response = await result if inspect.isawaitable(result) else result
        elif callable(self._client):
            result = self._client({"mode": self._mode, "prompt": prompt, "context": context})
            response = await result if inspect.isawaitable(result) else result
        else:
            raise TypeError(
                f"llm_client 必须实现 complete_async/run/act 方法或是可调用对象，"
                f"当前类型: {type(self._client)}"
            )

        # 解析返回
        return self._parse_response(response)

    def _build_prompt(self, context: dict[str, Any]) -> str:
        """构造 LLM prompt。

        参数:
            context: 运行时上下文

        返回:
            格式化后的 prompt 字符串
        """
        # 1. 如果 DSL 中显式指定了 prompt 模板，使用它
        template = self._spec.get("prompt", "")
        if template:
            try:
                return template.format(**context)
            except KeyError as exc:
                logger.warning("[LLMAdapter] prompt 模板变量缺失: %s", exc)
                return template

        # 2. 否则根据 mode 生成默认 prompt
        if self._mode == "choose_mapping":
            choices_json = json.dumps(context.get("choices", []), ensure_ascii=False, indent=2)
            return f"""玩家输入: {context.get('text', '')}

可选分支:
{choices_json}

请选择玩家意图最接近的分支 id，返回 JSON 格式:
{{"selected_choice": "分支id", "confidence": 0.9}}"""

        elif self._mode in {"branch_then_return", "constrained_continue", "free_continue"}:
            return f"""玩家输入: {context.get('text', '')}

当前剧情: {context.get('state', {})}

请生成后续剧情，返回 JSON 格式:
{{"text": "剧情文本", "beats": [{{"text": "节拍文本"}}]}}"""

        elif self._mode == "grow_flow":
            return f"""玩家输入: {context.get('text', '')}

请生成一个新的场景节点，返回 JSON 格式的 flow patch:
{{"patch": {{"type": "add_scene", "scene": {{...}}}}}}"""

        else:
            # Fallback
            return f"Mode: {self._mode}\nContext: {json.dumps(context, ensure_ascii=False, indent=2)}"

    def _parse_response(self, response: Any) -> dict[str, Any]:
        """解析 LLM 返回值。

        参数:
            response: LLM 原始返回（可能是字符串、字典等）

        返回:
            解析后的字典
        """
        # 如果已经是字典，直接返回
        if isinstance(response, dict):
            return response

        # 如果是字符串，尝试解析 JSON
        if isinstance(response, str):
            try:
                # 提取 JSON 部分（去除 markdown 代码块标记）
                text = response.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

                return json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning("[LLMAdapter] JSON 解析失败: %s, 原始响应: %s", exc, response[:200])
                # Fallback: 包装成字典
                return {"text": response}

        # 其他类型，包装成字典
        logger.warning("[LLMAdapter] 未知响应类型: %s", type(response))
        return {"raw": response}


class HttpStrategyAdapter(FreeInputStrategy):
    """HTTP 执行引擎适配器。

    调用外部 HTTP 服务生成策略结果。

    适用场景：
      - 调用自建生成服务
      - 调用第三方 API
    """

    def __init__(self, mode: str, spec: dict[str, Any]):
        """初始化适配器。

        参数:
            mode: 策略模式名称
            spec: DSL 配置（包含 url/method/headers 等）
        """
        assert mode, "mode 不能为空"
        assert spec.get("url"), "HTTP adapter 需要指定 url"

        self._mode = mode
        self._url = spec.get("url")
        self._method = spec.get("method", "POST")
        self._headers = spec.get("headers", {})
        self._timeout = spec.get("timeout", 30)

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """调用 HTTP 服务。

        参数:
            mode: 策略模式名称
            spec: DSL 配置
            context: 运行时上下文

        返回:
            HTTP 响应的 JSON body
        """
        import aiohttp

        logger.debug(
            "[HttpAdapter] 调用 HTTP: method=%s url=%s",
            self._method,
            self._url,
        )

        # 构造请求 payload
        payload = {
            "mode": self._mode,
            "context": context,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    self._method,
                    self._url,
                    json=payload,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as response:
                    response.raise_for_status()
                    return await response.json()
        except aiohttp.ClientError as exc:
            logger.error("[HttpAdapter] HTTP 请求失败: %s", exc)
            raise RuntimeError(f"HTTP 策略调用失败: {exc}") from exc


__all__ = [
    "PluginStrategyAdapter",
    "LLMStrategyAdapter",
    "HttpStrategyAdapter",
]
