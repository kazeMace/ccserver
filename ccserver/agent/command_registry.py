"""
agent/command_registry — Agent 层内置命令注册表。

内置命令（CommandDef.builtin=True）的前置逻辑不再用 if/elif 堆叠，
改为注册表 + 处理器函数，新增命令只需 @register("name") 装饰一次。

使用方式：
    from ccserver.agent.command_registry import get_handler
    handler = get_handler("clear")
    if handler:
        stdout = await handler(agent, args)

扩展方式（新增内置命令）：
    @register("my_cmd")
    async def _handle_my_cmd(agent: "Agent", args: str) -> str:
        # 前置逻辑
        return "stdout 输出（注入到消息 content.stdout）"
"""

from typing import Callable, Awaitable, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ccserver.agent import Agent


# 处理器函数类型：接收 (agent, args)，返回 stdout 字符串
BuiltinHandler = Callable[["Agent", str], Awaitable[str]]

# 注册表：命令名（不含 /）→ 处理器函数
_HANDLERS: dict[str, BuiltinHandler] = {}


def register(name: str):
    """
    装饰器：注册一个内置命令处理器。

    Args:
        name: 命令名称（不含 /，如 "clear"、"model"）

    Usage:
        @register("clear")
        async def _handle_clear(agent: "Agent", args: str) -> str:
            ...
    """
    def decorator(fn: BuiltinHandler) -> BuiltinHandler:
        assert name not in _HANDLERS, f"命令 '{name}' 已被注册，请检查是否重复"
        _HANDLERS[name] = fn
        logger.debug("Builtin command registered | name={}", name)
        return fn
    return decorator


def get_handler(name: str) -> BuiltinHandler | None:
    """
    按命令名查找处理器。

    Args:
        name: 命令名称（不含 /）

    Returns:
        处理器函数，不存在时返回 None
    """
    return _HANDLERS.get(name)


def list_builtin_names() -> list[str]:
    """返回所有已注册的内置命令名列表（不含 /）。"""
    return list(_HANDLERS.keys())


# ── 内置命令实现 ──────────────────────────────────────────────────────────────


@register("clear")
async def _handle_clear(agent: "Agent", args: str) -> str:
    """
    /clear — 清空当前 Agent 的消息历史。

    效果等同于开新对话，但不改变 Agent 实例（工具、模型等保持不变）。

    Args:
        agent: 当前 Agent 实例
        args:  命令参数（此命令不使用参数）

    Returns:
        空字符串（无 stdout 输出）
    """
    agent.context.messages.clear()
    # persist=True 时同步清空磁盘
    if getattr(agent, "persist", False):
        agent.session.rewrite_messages([])
    logger.info("Builtin /clear: messages cleared | agent_id={}", agent.context.agent_id[:8])
    return ""


@register("model")
async def _handle_model(agent: "Agent", args: str) -> str:
    """
    /model [model_id] — 查看或切换当前会话使用的 LLM 模型。

    /model         — 显示当前模型 ID
    /model <name>  — 切换到指定模型（本轮生效）

    Args:
        agent: 当前 Agent 实例
        args:  模型 ID（空时仅显示当前模型）

    Returns:
        当前或切换后的模型信息字符串（注入到消息 content.stdout）
    """
    current = agent.session.config.model.model_id
    if not args:
        return f"当前模型：{current}"

    new_model = args.strip()
    # 更新 session 级配置（只影响当前 session）
    agent.session.config.model.model_id = new_model
    logger.info(
        "Builtin /model: switched | from={} to={} agent_id={}",
        current, new_model, agent.context.agent_id[:8],
    )
    return f"已切换模型：{current} → {new_model}"
