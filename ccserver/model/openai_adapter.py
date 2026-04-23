"""
openai_adapter — 基于 OpenAI SDK 的 ModelAdapter 实现。

支持所有 OpenAI-compatible API：OpenAI、OpenRouter、Ollama、LMStudio、OneAPI 等。
内部通过 translator.py 将 Anthropic 格式请求转换为 OpenAI 格式，
并将 OpenAI 响应还原为 Anthropic 对象。

超时与连接复用：
  自定义 httpx.AsyncClient，timeout=600s，keepalive_expiry=5，
  避免 MCP 长调用后连接被服务端关闭导致的 chunk read 错误。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger

from .adapter import ModelAdapter
from .translator import (
    anthropic_to_openai_messages,
    anthropic_to_openai_tools,
    openai_to_anthropic_message,
    _Message,
    _TextBlock,
    _ToolUseBlock,
)

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore


class OpenAIAdapter(ModelAdapter):
    """封装兼容 OpenAI 接口的异步客户端，实现 ModelAdapter 接口。"""

    def __init__(self, client):
        """
        初始化 OpenAIAdapter。

        Args:
            client: openai.AsyncOpenAI 或兼容该接口的异步客户端实例。
        """
        assert client is not None, "OpenAI client must not be None"
        self._client = client

    @classmethod
    def from_env(cls) -> "OpenAIAdapter":
        """从 OPENAI_API_KEY 环境变量创建默认实例。"""
        assert AsyncOpenAI is not None, "openai package is not installed"
        import os
        client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return cls(client)

    @classmethod
    def from_config(cls, base_url: str | None = None, api_key: str | None = None) -> "OpenAIAdapter":
        """根据 base_url 和 api_key 创建实例。"""
        assert AsyncOpenAI is not None, "openai package is not installed"
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "",
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return cls(client)

    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """非流式调用，返回模拟 Anthropic Message 的对象。"""
        openai_messages = anthropic_to_openai_messages(messages, system)
        openai_tools = anthropic_to_openai_tools(tools)

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if openai_tools is not None:
            params["tools"] = openai_tools
        if kwargs:
            params.update(kwargs)

        response = await self._client.chat.completions.create(**params)
        return openai_to_anthropic_message(response)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """流式调用，返回 OpenAIStreamWrapper。"""
        openai_messages = anthropic_to_openai_messages(messages, system)
        openai_tools = anthropic_to_openai_tools(tools)

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if openai_tools is not None:
            params["tools"] = openai_tools
        if kwargs:
            params.update(kwargs)

        return OpenAIStreamWrapper(self._client.chat.completions.create(**params))


class OpenAIStreamWrapper:
    """
    包装 OpenAI 流式响应，提供 text_stream 异步生成器和 get_final_message() 协程。

    OpenAI 的 tool_calls delta 按 index 分片到达，需在迭代时累加，
    流结束时对 arguments 做 json.loads() 组装成完整的 _ToolUseBlock。
    """

    def __init__(self, async_stream):
        self._async_stream = async_stream
        self._text_chunks: list[str] = []
        # index -> {"id": str, "name": str, "arguments": str}
        self._tool_calls: dict[int, dict[str, str]] = {}

    async def __aenter__(self):
        self._stream = await self._async_stream.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._async_stream.__aexit__(exc_type, exc_val, exc_tb)

    @property
    async def text_stream(self):
        """
        异步生成器，yield 所有文本片段。
        同时会在后台累加 tool_calls 的 delta。
        """
        async for chunk in self._stream:
            delta = chunk.choices[0].delta
            if delta.content:
                self._text_chunks.append(delta.content)
                yield delta.content

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in self._tool_calls:
                        self._tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.id:
                        self._tool_calls[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        self._tool_calls[idx]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        self._tool_calls[idx]["arguments"] += tc.function.arguments

    async def get_final_message(self) -> _Message:
        """流结束后，返回组装好的 _Message 对象。"""
        # 确保流已耗尽（如果外部没有迭代 text_stream）
        async for _ in self.text_stream:
            pass

        content: list[Any] = []
        if self._text_chunks:
            content.append(_TextBlock("".join(self._text_chunks)))

        for idx in sorted(self._tool_calls.keys()):
            tc = self._tool_calls[idx]
            arguments = tc.get("arguments", "") or "{}"
            try:
                input_dict = json.loads(arguments)
            except json.JSONDecodeError:
                logger.warning("Failed to parse streaming tool_call arguments as JSON: {}", arguments)
                input_dict = {"raw": arguments}
            content.append(_ToolUseBlock(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                input=input_dict,
            ))

        # OpenAI 流式响应不会在最后一个 chunk 给出 finish_reason，
        # 我们根据是否有 tool_calls 推断 stop_reason。
        stop_reason = "tool_use" if self._tool_calls else "end_turn"
        return _Message(content=content, stop_reason=stop_reason)
