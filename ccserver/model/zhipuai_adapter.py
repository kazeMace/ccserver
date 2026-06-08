"""
zhipuai_adapter — 基于智谱 zai-sdk 的 ModelAdapter 实现。

包装 zai.ZhipuAiClient，实现 ModelAdapter 接口。
将 Anthropic block 格式转换为 GLM 的 image_url/video_url/file_url 格式。
支持 thinking={"type": "enabled"} 参数自动注入。

使用方式：
    adapter = ZhipuAIAdapter(api_key="xxx")
    response = await adapter.create(model="glm-5v-turbo", messages=[...], max_tokens=1000)
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from .adapter import ModelAdapter
from .translator import _Message


class ZhipuAIAdapter(ModelAdapter):
    """
    智谱 GLM ModelAdapter。

    包装 zai.ZhipuAiClient 客户端，实现 ModelAdapter 接口。

    SDK 要求：pip install zai-sdk

    图像/视频/文件通过 Anthropic block 格式传入，内部自动转换为 GLM 格式。
    thinking 参数自动注入（模型支持时）。
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        """
        初始化 ZhipuAIAdapter。

        Args:
            api_key:  智谱 API Key
            base_url: API 端点（通常由 SDK 内置，无需指定）
        """
        assert api_key, "ZhipuAI api_key must not be empty"

        # zai-sdk 是可选依赖，延迟导入
        try:
            from zai import ZhipuAiClient
        except ImportError:
            raise ImportError(
                "zai-sdk is required for ZhipuAIAdapter. Install it with: pip install zai-sdk"
            )

        self._api_key = api_key
        self._client = ZhipuAiClient(api_key=api_key)

        logger.info("ZhipuAIAdapter 初始化完成 | base_url={}", base_url or "default")

    # ── ModelAdapter 接口实现 ─────────────────────────────────────────────────

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
        """
        非流式调用 GLM 模型。

        Args:
            model:      模型名，如 "glm-5v-turbo"、"glm-4.5"
            messages:   Anthropic 格式的消息列表
            max_tokens: 最大输出 token 数
            system:     system prompt
            tools:      GLM 暂不支持 tools，忽略
            **kwargs:   额外参数（如 thinking={"type": "enabled"}）

        Returns:
            类 Anthropic _Message 对象，包含 content 和 stop_reason
        """
        # 1. 转换消息格式（Anthropic block → GLM content 格式）
        glm_messages = _anthropic_to_glm_messages(messages, system)

        # 2. 判断是否注入 thinking 参数
        thinking = kwargs.pop("thinking", None)
        if thinking is None and _model_supports_thinking(model):
            thinking = {"type": "enabled"}

        # 3. 调用 GLM SDK
        params: dict[str, Any] = {
            "model": model,
            "messages": glm_messages,
        }
        # GLM SDK 不直接支持 max_tokens，需要通过 extra_body 或 max_tokens 参数
        # zai-sdk 0.2.2 支持 max_tokens 参数
        params["max_tokens"] = max_tokens

        if thinking is not None:
            params["thinking"] = thinking

        if kwargs:
            params.update(kwargs)

        logger.debug("ZhipuAIAdapter.create | model={} messages_count={} thinking={}",
                     model, len(glm_messages), thinking is not None)

        response = self._client.chat.completions.create(**params)

        # 4. 转换响应为 Anthropic 格式
        return _glm_response_to_anthropic(response)

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
        """
        流式调用 GLM 模型。

        返回 GLMStreamWrapper，提供 text_stream + get_final_message()。
        """
        glm_messages = _anthropic_to_glm_messages(messages, system)

        thinking = kwargs.pop("thinking", None)
        if thinking is None and _model_supports_thinking(model):
            thinking = {"type": "enabled"}

        params: dict[str, Any] = {
            "model": model,
            "messages": glm_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if thinking is not None:
            params["thinking"] = thinking
        if kwargs:
            params.update(kwargs)

        logger.debug("ZhipuAIAdapter.stream | model={}", model)

        return GLMStreamWrapper(self._client.chat.completions.create(**params))


# ── 消息格式转换：Anthropic → GLM ───────────────────────────────────────────────


def _anthropic_to_glm_messages(
    messages: list[dict],
    system: list[dict] | str | None = None,
) -> list[dict]:
    """
    将 Anthropic 格式的 messages 转换为 GLM (OpenAI 风格) 格式。

    转换规则：
    - Anthropic {"type": "text", "text": "..."} → {"type": "text", "text": "..."}
    - Anthropic {"type": "image", "source": {"type": "base64", "data": "..."}}
      → {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    - Anthropic {"type": "tool_use", ...} → 跳过（GLM 暂不支持 tools）
    - Anthropic {"type": "tool_result", ...} → {"type": "text", "text": "工具结果: ..."}
    - system prompt → messages 列表开头的 system 消息

    Args:
        messages: Anthropic 格式的消息列表
        system:   system prompt

    Returns:
        GLM (OpenAI 风格) 消息列表
    """
    glm_messages: list[dict] = []

    # 处理 system prompt
    if isinstance(system, list):
        system_texts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                system_texts.append(block.get("text", ""))
        if system_texts:
            glm_messages.append({
                "role": "system",
                "content": "\n".join(system_texts),
            })
    elif isinstance(system, str) and system:
        glm_messages.append({"role": "system", "content": system})

    # 处理每条消息
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "assistant":
            # assistant 消息：提取 text 部分（忽略 tool_use）
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                glm_messages.append({
                    "role": "assistant",
                    "content": "".join(text_parts) if text_parts else "",
                })
            elif isinstance(content, str):
                glm_messages.append({"role": "assistant", "content": content})

        elif role == "user":
            if isinstance(content, list):
                glm_content: list[dict] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")

                    if block_type == "text":
                        glm_content.append({
                            "type": "text",
                            "text": block.get("text", ""),
                        })
                    elif block_type == "image":
                        # Anthropic image → GLM image_url
                        source = block.get("source", {})
                        media_type = source.get("media_type", "image/png")
                        b64_data = source.get("data", "")
                        if b64_data:
                            glm_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{b64_data}",
                                },
                            })
                    elif block_type == "tool_result":
                        # tool_result → text（GLM 不支持原生 tool）
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            # 从多块 content 中提取 text
                            tool_texts = []
                            for tc in tool_content:
                                if isinstance(tc, dict) and tc.get("type") == "text":
                                    tool_texts.append(tc.get("text", ""))
                            tool_content = "\n".join(tool_texts)
                        glm_content.append({
                            "type": "text",
                            "text": f"[工具执行结果]\n{tool_content}",
                        })
                    elif block_type == "video":
                        # Anthropic 没有标准 video block，但预留支持
                        source = block.get("source", {})
                        url = source.get("data", "")
                        if url:
                            glm_content.append({
                                "type": "video_url",
                                "video_url": {"url": url},
                            })
                    else:
                        # 未知块类型，尝试作为 text 处理
                        pass

                if glm_content:
                    glm_messages.append({
                        "role": "user",
                        "content": glm_content,
                    })
            elif isinstance(content, str):
                glm_messages.append({"role": "user", "content": content})

        else:
            # 其他 role 原样传递
            glm_messages.append(dict(msg))

    return glm_messages


def _model_supports_thinking(model: str) -> bool:
    """
    判断 GLM 模型是否支持 thinking/推理能力。

    参考智谱文档，以下模型原生支持 thinking：
    - glm-5v-turbo（多模态 + 思考链）
    - glm-4.5（文本 + 思考链）
    """
    thinking_models = {
        "glm-5v-turbo",
        "glm-4.5",
        "glm-4",
        "glm-4-flash",
        "glm-4-plus",
    }
    return model in thinking_models


def _glm_response_to_anthropic(response) -> "_Message":
    """
    将 GLM ChatCompletion 响应转换为类 Anthropic _Message 对象。

    Args:
        response: zai SDK ChatCompletion 响应对象

    Returns:
        _Message 对象，包含 .content（列表）和 .stop_reason（字符串）
    """
    choice = response.choices[0]
    message = choice.message

    content: list[dict] = []

    # GLM 的 reasoning_content → Anthropic thinking block
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        content.append({
            "type": "thinking",
            "thinking": str(reasoning),
        })

    # GLM 的 content → Anthropic text block
    text = getattr(message, "content", None)
    if text:
        content.append({
            "type": "text",
            "text": str(text),
        })

    # 映射 finish_reason
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "stop":
        stop_reason = "end_turn"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    elif finish_reason == "tool_calls":
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"

    return _Message(content=content, stop_reason=stop_reason)


# ── GLM 流式包装器 ──────────────────────────────────────────────────────────────


class GLMStreamWrapper:
    """
    GLM 流式响应包装器。

    处理 GLM 流式响应中 reasoning_content（思考内容）和 content（最终回答）的增量。
    提供 text_stream 异步生成器和 get_final_message() 协程。
    """

    def __init__(self, stream_response):
        """
        Args:
            stream_response: 同步的 GLM stream 生成器
        """
        self._stream_response = stream_response
        self._reasoning_chunks: list[str] = []
        self._text_chunks: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    @property
    async def text_stream(self):
        """
        异步生成器，逐块产出文本内容。
        同时累积 reasoning_content 供 get_final_message() 使用。
        """
        for chunk in self._stream_response:
            delta = chunk.choices[0].delta

            # reasoning_content（思考链内容）
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                self._reasoning_chunks.append(str(reasoning))

            # content（最终回答）
            content = getattr(delta, "content", None)
            if content:
                self._text_chunks.append(str(content))
                yield str(content)

    async def get_final_message(self) -> _Message:
        """
        流结束后组装完整的 _Message 对象。

        Returns:
            _Message，包含 thinking block（如有）和 text block
        """
        # 确保流已耗尽
        try:
            async for _ in self.text_stream:
                pass
        except Exception:
            pass

        content: list[dict] = []

        # 组装 thinking block
        if self._reasoning_chunks:
            content.append({
                "type": "thinking",
                "thinking": "".join(self._reasoning_chunks),
            })

        # 组装 text block
        if self._text_chunks:
            content.append({
                "type": "text",
                "text": "".join(self._text_chunks),
            })

        return _Message(content=content, stop_reason="end_turn")
