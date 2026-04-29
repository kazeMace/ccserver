"""
describe — 通用图像描述函数。

使用任意多模态 ModelAdapter + model 进行视觉理解。
这是所有 MediaUnderstandingProvider 的默认实现基础。
"""

from __future__ import annotations

from loguru import logger

from ccserver.model.adapter import ModelAdapter


# 默认的屏幕内容描述 prompt（中文）
DEFAULT_IMAGE_DESCRIPTION_PROMPT = (
    "请详细描述当前屏幕上显示的所有内容，包括：\n"
    "1. 打开的应用或窗口\n"
    "2. 界面中的主要文字和元素\n"
    "3. 当前状态（如光标位置、选中内容等）\n"
    "用中文回答，尽量详细准确。"
)

# 默认的屏幕内容描述 system prompt
DEFAULT_IMAGE_DESCRIPTION_SYSTEM = "你是一个屏幕内容分析专家，请详细描述截图中的内容。"


async def describe_image_with_model(
    image_base64: str,
    prompt: str = "",
    adapter: ModelAdapter | None = None,
    model: str | None = None,
    max_tokens: int = 1000,
    system: str = "",
    image_mime: str = "image/png",
) -> str:
    """
    通用图像描述：用指定的 ModelAdapter + model 进行视觉理解。

    使用 Anthropic block 格式构建请求，adapter 负责内部格式转换。
    所有支持多模态输入的 ModelAdapter 都可以使用此函数。

    Args:
        image_base64: PNG/JPEG base64 数据（不含 data:image/... 前缀）
        prompt:       自定义描述引导词，为空时使用默认 prompt
        adapter:      ModelAdapter 实例，None 时抛出异常
        model:        模型名称
        max_tokens:   输出最大 token 数
        system:       system prompt，为空时使用默认
        image_mime:   MIME 类型，如 "image/png"、"image/jpeg"

    Returns:
        图片的文本描述

    Raises:
        AssertionError: adapter 或 model 为 None
    """
    assert adapter is not None, "adapter is required for describe_image_with_model"
    assert model is not None, "model is required for describe_image_with_model"

    resolved_prompt = prompt or DEFAULT_IMAGE_DESCRIPTION_PROMPT
    resolved_system = system or DEFAULT_IMAGE_DESCRIPTION_SYSTEM

    # 构建请求消息（Anthropic block 格式）
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_mime,
                        "data": image_base64,
                    },
                },
                {
                    "type": "text",
                    "text": resolved_prompt,
                },
            ],
        }
    ]

    logger.debug("describe_image_with_model | model={} prompt_len={}", model, len(resolved_prompt))

    # 调用 LLM
    response = await adapter.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        system=resolved_system,
    )

    # 提取文本内容
    text_parts = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
        # 忽略 thinking、tool_use 等非文本块

    result = "".join(text_parts)
    if not result:
        logger.warning("describe_image_with_model 返回空文本 | model={}", model)

    logger.info("describe_image_with_model 完成 | model={} result_len={}", model, len(result))
    return result
