# ccserver/builtins/tools/vision.py
"""
builtins.tools.vision — 视觉语义服务（describe / locate），依赖 model_engine。

取代旧 model_engine/media + aimodels/routing：
  - describe_image / describe_images / locate_element：上层视觉理解服务。
  - _resolve_vlm_adapter：选 VLM adapter+model（主模型支持图像→主 adapter；
    否则按 VLM 配置；否则按可用 API key 默认；都无则抛错）。

注意：本模块在 builtins 层，依赖 model_engine（合法的上层→下层依赖）。
"""

from __future__ import annotations

import os
import json
import re

from loguru import logger

from ccserver.model_engine import ModelAdapter
from ccserver.model_engine.client import LLMCaller


# 默认屏幕描述 prompt / system（逐字迁自 media/describe.py）
DEFAULT_IMAGE_DESCRIPTION_PROMPT = (
    "请详细描述当前屏幕上显示的所有内容，包括：\n"
    "1. 打开的应用或窗口\n"
    "2. 界面中的主要文字和元素\n"
    "3. 当前状态（如光标位置、选中内容等）\n"
    "用中文回答，尽量详细准确。"
)
DEFAULT_IMAGE_DESCRIPTION_SYSTEM = "你是一个屏幕内容分析专家，请详细描述截图中的内容。"


# ── VLM adapter 进程级缓存（非主-adapter 路径）────────────────────────────────
# 仅缓存「配置/默认」路径的结果（进程内 config/env 固定）。主-adapter 路径不缓存。
# 注意：本缓存无失效机制——若进程内 CCSERVER_VLM_* / OPENAI_API_KEY 等中途变化，
# 旧 adapter 会保留。生产中 config/env 进程级固定，故可接受；测试用 _clear_cache 隔离。
_cached_vlm: "tuple[ModelAdapter, str] | None" = None


def _resolve_vlm_adapter(main_model, main_adapter) -> "tuple[ModelAdapter, str]":
    """
    选择 VLM adapter + model（复刻旧 VLMRouter/媒体注册表语义）。

    1. 主 adapter 支持图像 → (主 adapter, 主 model)（旧 NATIVE）。
    2. 否则按 VLM 配置（CCSERVER_VLM_*）构建 → (配置 adapter, 配置 model)。
    3. 否则按可用 API key 默认：OPENAI→gpt-4o，其次 ANTHROPIC→claude-sonnet-4-6
       （与旧 auto_priority openai=10 < anthropic=20 一致）。
    4. 都无 → RuntimeError。

    Returns:
        (ModelAdapter, model_id)
    """
    # 1. 主模型支持图像：直接用主 adapter（不缓存，按调用方传入）
    if main_adapter is not None and getattr(main_adapter, "supports_image", False):
        assert main_model, "main_model required when using main_adapter for VLM"
        return main_adapter, main_model

    # 2/3. 非主路径：进程级缓存（配置/默认在进程内固定）
    global _cached_vlm
    if _cached_vlm is not None:
        return _cached_vlm

    from ccserver.configuration import get_process_config
    vlm = get_process_config().vlm

    # 2. 显式 VLM 配置（任一连接字段被设置）→ 经 ModelEndpoint+AdapterFactory 构建
    if vlm.api_key or vlm.base_url or vlm.provider:
        from ccserver.model_engine import ModelEndpoint, AdapterFactory
        ep = ModelEndpoint(
            model_id=vlm.model_id,
            api_type=vlm.api_type,
            provider=vlm.provider,
            base_url=vlm.base_url,
            api_key=vlm.api_key,
        ).resolve()
        adapter = AdapterFactory.build(ep)
        _cached_vlm = (adapter, vlm.model_id)
        logger.info("VLM adapter | source=config model={}", vlm.model_id)
        return _cached_vlm

    # 3. 默认：按可用 API key 选（优先 openai）
    if os.getenv("OPENAI_API_KEY"):
        from ccserver.model_engine.providers.openai_chat import OpenAIChatProvider
        _cached_vlm = (OpenAIChatProvider.from_config(), "gpt-4o")
        logger.info("VLM adapter | source=default-openai model=gpt-4o")
        return _cached_vlm
    if os.getenv("ANTHROPIC_API_KEY"):
        from ccserver.model_engine.providers.anthropic import get_default_provider
        _cached_vlm = (get_default_provider(), "claude-sonnet-4-6")
        logger.info("VLM adapter | source=default-anthropic model=claude-sonnet-4-6")
        return _cached_vlm

    raise RuntimeError(
        "VLM unavailable: 未配置 CCSERVER_VLM_* / OPENAI_API_KEY / ANTHROPIC_API_KEY"
    )


async def _describe_with_adapter(
    image_base64: str,
    prompt: str,
    adapter: ModelAdapter,
    model: str,
    max_tokens: int = 1000,
    system: str = "",
    image_mime: str = "image/png",
) -> str:
    """
    用指定 adapter+model 描述图像（核心，逐字迁自 media/describe.describe_image_with_model）。

    构建 Anthropic block 格式消息（R3 后 adapter 双接受 dict），经 L1 调用，拼接 TextBlock。
    """
    assert adapter is not None, "adapter is required for _describe_with_adapter"
    assert model is not None, "model is required for _describe_with_adapter"

    resolved_prompt = prompt or DEFAULT_IMAGE_DESCRIPTION_PROMPT
    resolved_system = system or DEFAULT_IMAGE_DESCRIPTION_SYSTEM

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": image_mime, "data": image_base64}},
                {"type": "text", "text": resolved_prompt},
            ],
        }
    ]

    logger.debug("_describe_with_adapter | model={} prompt_len={}", model, len(resolved_prompt))
    caller = LLMCaller(adapter, model=model, max_tokens=max_tokens)
    response = await caller.invoke(messages, system=resolved_system)

    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    result = "".join(text_parts)

    if not result:
        logger.warning("_describe_with_adapter 返回空文本 | model={}", model)
    else:
        logger.info("_describe_with_adapter 完成 | model={} result_len={}", model, len(result))
    return result


async def describe_image(
    image_base64: str,
    prompt: str = "",
    max_tokens: int = 1000,
    *,
    main_model=None,
    main_adapter=None,
    image_mime: str = "image/png",
) -> str:
    """描述单张图像：自动选 VLM adapter+model（_resolve_vlm_adapter）后调用核心。"""
    adapter, model = _resolve_vlm_adapter(main_model, main_adapter)
    return await _describe_with_adapter(
        image_base64=image_base64, prompt=prompt, adapter=adapter, model=model,
        max_tokens=max_tokens, image_mime=image_mime,
    )


async def describe_images(
    images,
    prompt: str = "",
    max_tokens: int = 1000,
    *,
    main_model=None,
    main_adapter=None,
) -> str:
    """描述多张图像，结果用 '\\n---\\n' 连接（迁自 provider.describe_images）。"""
    if not images:
        return ""
    adapter, model = _resolve_vlm_adapter(main_model, main_adapter)
    results = []
    for img in images:
        desc = await _describe_with_adapter(
            image_base64=img.get("base64", ""), prompt=prompt, adapter=adapter,
            model=model, max_tokens=max_tokens,
        )
        results.append(desc)
    return "\n---\n".join(results)


async def locate_element(
    image_base64: str,
    description: str,
    image_width: int,
    image_height: int,
    *,
    main_model=None,
    main_adapter=None,
) -> dict:
    """定位视觉元素，返回 JSON dict（迁自 provider.locate_element，含正则兜底）。"""
    adapter, model = _resolve_vlm_adapter(main_model, main_adapter)
    locate_prompt = (
        f"请在 {image_width}x{image_height} 像素的截图"
        f"中定位：{description}。"
        f"返回格式：{{\"found\": true/false, \"x\": 像素x坐标, \"y\": 像素y坐标, \"confidence\": 0-1之间的置信度, "
        f"\"element_bounds\": \"描述元素大小和位置\"}}。"
        f"只返回 JSON，不要其他内容。"
    )
    result_text = await _describe_with_adapter(
        image_base64=image_base64, prompt=locate_prompt, adapter=adapter,
        model=model, max_tokens=200,
    )
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', result_text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"found": False, "reason": "无法解析定位结果"}
