"""执行器基类与数据模型。

BaseExecutor: 所有 executor 的抽象基类
ExecutorRequest: 统一请求结构
ExecutorResponse: 统一响应结构
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutorRequest:
    """执行器统一请求。

    属性:
        purpose: 调用目的标识（planner/generator/guard/condition/mapper 等）
        payload: 业务层构造的数据
            - LLM: {"prompt": "完整prompt文本"}
            - Plugin: {"arg1": ..., "arg2": ...}
            - HTTP: request body dict
            - Code: {"state": ..., "env": ...}
        config: DSL 中 executor 级配置（model_name/api_key/url/name 等）
        context: 可选运行时上下文（session_metadata 等）
    """

    purpose: str
    payload: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] | None = None


class BaseExecutor(ABC):
    """执行器抽象基类。

    所有 executor（llm/plugin/http/code）必须实现此接口。
    Executor 是纯传输层：接收请求 → 调用外部 → 返回结果。
    不含任何业务逻辑，不关心 prompt 内容是什么、plugin 做什么。
    """

    @abstractmethod
    async def execute(self, request: ExecutorRequest) -> "ExecutorResponse":
        """执行一次请求。

        参数:
            request: 统一请求结构

        返回:
            ExecutorResponse

        异常:
            子类可抛出异常，由调用方处理降级逻辑。
        """
        ...


@dataclass
class ExecutorResponse:
    """执行器统一响应。

    属性:
        success: 是否执行成功
        data: 结构化结果（业务层直接使用）
        raw: 原始返回值（调试用）
        error: 错误信息（失败时填充）
    """

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    error: str | None = None
