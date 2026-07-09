"""Hook 运行器 — 加载并执行 hooks/ 目录中的生命周期钩子。

每个 hook 文件声明 `async def handle(ctx: HookContext) -> None`，
由 HookRunner 在对应事件触发时调用。
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from drama_engine.core.script_loader.models import HookSpec

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HookContext:
    """Hook 执行上下文 — 传递给 hook handle 函数的参数。

    包含事件相关信息和运行时服务引用。
    """

    # 事件信息
    event: str
    payload: dict[str, Any] = field(default_factory=dict)

    # 运行时引用（由 HookRunner 在触发时注入）
    state: Any = None
    writer: Any = None
    cast: Any = None
    session_metadata: dict[str, Any] = field(default_factory=dict)

    # 可选：事件特定数据
    scene_id: str = ""
    actor_name: str = ""
    message: dict[str, Any] = field(default_factory=dict)


# Hook handler 类型签名
HookHandler = Callable[[HookContext], Awaitable[None]]


class HookRunner:
    """Hook 运行器 — 管理和执行生命周期钩子。

    使用方式：
        runner = HookRunner()
        runner.load_from_specs(hook_specs)
        await runner.trigger("on_session_start", ctx)
    """

    def __init__(self) -> None:
        # 事件名 → handler 列表（同一事件可有多个 handler）
        self._handlers: dict[str, list[HookHandler]] = {}

    def load_from_specs(self, specs: list[HookSpec]) -> None:
        """从 HookSpec 列表加载 hook handler。

        参数:
            specs: HookSpec 列表（由 DirectoryHookScanner 产出）
        """
        for spec in specs or []:
            try:
                handler = self._load_handler(spec)
                self._handlers.setdefault(spec.event, []).append(handler)
                logger.info("[HookRunner] 注册 hook: %s", spec.event)
            except Exception as e:
                logger.error(
                    "[HookRunner] 加载 hook 失败: event=%s, 错误: %s",
                    spec.event, e,
                )
                raise

    async def trigger(self, event: str, ctx: HookContext) -> None:
        """触发指定事件的所有 hook handler。

        参数:
            event: 事件名（如 "on_session_start"）
            ctx: Hook 执行上下文
        """
        handlers = self._handlers.get(event, [])
        if not handlers:
            return

        ctx.event = event
        for handler in handlers:
            try:
                await handler(ctx)
            except Exception as e:
                logger.error(
                    "[HookRunner] hook 执行异常: event=%s, 错误: %s",
                    event, e,
                )

    def has_hooks(self, event: str) -> bool:
        """检查某事件是否有注册的 hook。"""
        return bool(self._handlers.get(event))

    @property
    def registered_events(self) -> list[str]:
        """返回所有有注册 handler 的事件名。"""
        return [event for event, handlers in self._handlers.items() if handlers]

    def _load_handler(self, spec: HookSpec) -> HookHandler:
        """从 HookSpec 加载 handler 函数。"""
        if spec.source == "file" and spec.file_path:
            return self._load_from_file(spec.file_path)
        elif spec.source == "inline" and spec.code:
            return self._load_from_code(spec.code, spec.event)
        else:
            raise ValueError(f"无法加载 hook: source={spec.source}, event={spec.event}")

    def _load_from_file(self, file_path: Path) -> HookHandler:
        """从 .py 文件动态加载 handle 函数。"""
        module_name = f"_drama_hook_{file_path.stem}"
        module_spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert module_spec is not None, f"无法创建模块 spec: {file_path}"
        assert module_spec.loader is not None, f"模块 spec 无 loader: {file_path}"
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        handler = getattr(module, "handle", None)
        assert handler is not None, f"hook 文件 {file_path} 缺少 handle 函数"
        assert callable(handler), f"hook 文件 {file_path} 的 handle 不可调用"
        return handler

    def _load_from_code(self, code: str, event: str) -> HookHandler:
        """从内联代码编译 handler。"""
        namespace: dict[str, Any] = {}
        exec(compile(code, f"<hook:{event}>", "exec"), namespace)  # noqa: S102
        handler = namespace.get("handle")
        assert handler is not None, f"内联 hook 代码缺少 handle 函数 (event={event})"
        return handler


__all__ = ["HookContext", "HookHandler", "HookRunner"]
