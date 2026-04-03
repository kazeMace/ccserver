"""
factory — constructs Agent instances for external callers.

AgentFactory.create_root() is the single entry point for building a root agent.
Spawning child agents from within the loop is handled by Agent.spawn_child().
"""

from loguru import logger

from .config import MODEL, MAIN_ROUND_LIMIT, PROMPT_LIB
from .session import Session
from .core.emitter import BaseEmitter
from .tools import build_tools
from .tools.bt_agent import BTAgent
from .agent import Agent, AgentContext
from .model import ModelAdapter, get_default_adapter


# ─── AgentFactory ─────────────────────────────────────────────────────────────


class AgentFactory:

    @staticmethod
    def create_root(
        session: Session,
        emitter: BaseEmitter,
        *,
        model: str = MODEL,
        name: str = "orchestrator",
        adapter: ModelAdapter | None = None,
        language: str = "简体中文",
        prompt_version: str | None = None,
        system: str | None = None,
        append_system: bool = False,
        run_mode: str | None = None,   # None 时从 session.settings.run_mode 读取
        on_limit=None,                 # round limit 回调：async def handler(agent, last_text) -> str
    ) -> Agent:
        """
        构建根 agent。

        prompt_version 指定使用哪个提示词库，默认读取 config.PROMPT_LIB（来自环境变量）。
        system 为额外注入的 system 文本（如从 md 文件读取的内容）。
        """
        lib_id = prompt_version or PROMPT_LIB
        settings = session.settings

        injected_system = system if system else None
        resolved_adapter = adapter or get_default_adapter()
        all_tools = build_tools(
            session.project_root, session.tasks, settings,
            resolved_adapter._client,
        )
        agent_catalog = session.agents.build_catalog()
        bt_agent = BTAgent(agent_catalog=agent_catalog)
        tools = settings.filter_tools(all_tools)
        # Agent 工具始终保留，不受 permissions.allow 过滤
        tools[BTAgent.name] = bt_agent
        disabled_tools = {k: v for k, v in all_tools.items() if k not in tools}

        agent = Agent(
            session=session,
            adapter=resolved_adapter,
            emitter=emitter,
            tools=tools,
            disabled_tools=disabled_tools,
            context=AgentContext(
                name=name,
                messages=session.messages,
                depth=0,
            ),
            model=model,
            round_limit=settings.main_round_limit or MAIN_ROUND_LIMIT,
            limit_strategy=settings.main_limit_strategy,
            on_limit_callback=on_limit,
            persist=True,
            prompt_version=lib_id,
            language=language,
            system=injected_system,
            append_system=append_system,
            run_mode=run_mode,  # None = 从 session.settings.run_mode 读取
        )

        # MCP schema 过滤后追加（__init__ 不持有 settings，无法过滤，由此处补全）
        agent._schemas += settings.filter_mcp_schemas(session.mcp.schemas())

        # 让 prompt lib 对 schema 描述做后处理（如 cc_reverse 替换为 CC 原版描述）
        from ccserver.prompts_lib.adapter import get_lib
        agent._schemas = get_lib(lib_id).patch_tool_schemas(agent._schemas)

        logger.info(
            "Root agent created | session={} model={} lib={} tools={} mcp_tools={}",
            session.id[:8], model, lib_id, list(tools.keys()),
            [s["name"] for s in agent._schemas if s["name"].startswith("mcp__")],
        )
        return agent
