"""
async_compat — 异步/同步兼容工具函数。

提供 _maybe_await 函数，用于桥接同步代码与异步 StorageAdapter，
避免在 managers/tasks/manager.py 和 team/registry.py 中重复实现。

设计：
- 模块级单例 ThreadPoolExecutor（max_workers=1），
  避免高频 task/team 操作时反复创建销毁线程池。
- 优先尝试 asyncio.run（无运行中事件循环时），
  失败时回退到线程池桥接（避免嵌套事件循环错误）。
"""

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# 模块级单例线程池，用于桥接已存在事件循环时的协程执行
# max_workers=1 足够，因为该线程池仅用于运行 asyncio.run(coro)
_BRIDGE_POOL: ThreadPoolExecutor | None = None


def _get_bridge_pool() -> ThreadPoolExecutor:
    """延迟初始化并返回模块级单例 ThreadPoolExecutor。"""
    global _BRIDGE_POOL
    if _BRIDGE_POOL is None:
        _BRIDGE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="async_bridge")
    return _BRIDGE_POOL


def maybe_await(coro_or_result: Any) -> Any:
    """
    兼容同步与异步返回值：如果入参是协程，则运行至完成并返回结果；否则直接返回。

    使用场景：
      - StorageAdapter 可能是 sync（file/sqlite）或 async（mongo）实现
      - 调用方处于同步上下文，无法直接使用 await

    实现策略：
      1. 若已有运行中事件循环，用线程池在独立线程中运行 asyncio.run(coro)
      2. 若无运行中事件循环，直接用 asyncio.run(coro)（性能更好）

    Args:
        coro_or_result: 任意值；若是协程对象（inspect.isawaitable 为 True），则执行之。

    Returns:
        协程的返回值，或原值（若非协程）。
    """
    if not inspect.isawaitable(coro_or_result):
        return coro_or_result

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中事件循环，可直接 asyncio.run
        return asyncio.run(coro_or_result)

    # 已有运行中事件循环，在线程池中运行避免嵌套错误
    pool = _get_bridge_pool()
    future = pool.submit(asyncio.run, coro_or_result)
    return future.result()
