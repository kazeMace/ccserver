"""
factory — constructs Agent instances for external callers.

AgentFactory.create_root() is the single entry point for building a root agent.
Spawning child agents from within the loop is handled by Agent.spawn_child().
"""

from pathlib import Path

from loguru import logger

from .session import Session
from .emitters import BaseEmitter
from .managers.tools import ToolManager, ExtraToolLoader
from .builtins.tools import BTAgent
from .agent import Agent, AgentContext
from .model_engine import ModelAdapter, AdapterFactory
from .prompt_engine import PromptEngine


# ─── AgentFactory ─────────────────────────────────────────────────────────────


class AgentFactory:

    @staticmethod
    def create_root(
        session: Session,
        emitter: BaseEmitter,
        *,
        model: str | None = None,
        name: str = "orchestrator",
        adapter: ModelAdapter | None = None,
        language: str = "简体中文",
        prompt_version: str | None = None,
        system: str | None = None,
        append_system: bool = False,
        run_mode: str | None = None,   # None 时从 session.config.agent.run_mode 读取
        on_limit=None,                 # round limit 回调：async def handler(agent, last_text) -> str
        stream: bool = True,           # True=实时 emit token，False=非流式
        env_vars: dict[str, str] | None = None,
        agent_package: str | None = None,   # 文件夹 Agent Package 路径（spec §7）
    ) -> Agent:
        """
        构建根 agent。

        prompt_version 指定使用哪个提示词库，默认读取 config.agent.prompt_lib。
        agent_package 指定一个文件夹 Agent Package（含 agent.json），
        加载后用其 name/model/system 覆盖（编程式来源，仍继承 Process 底座）。
        system 为额外注入的 system 文本（如从 md 文件读取的内容）。
        """
        # Agent Package：从文件夹加载 AgentDef，用其 name/model/system 作为覆盖来源
        if agent_package:
            from .managers.agents import AgentLoader
            ad = AgentLoader.load_package(Path(agent_package))
            if ad is None:
                raise ValueError(f"无法加载 Agent Package: {agent_package}")
            if name == "orchestrator" and ad.name:
                name = ad.name
            if model is None and ad.model:
                model = ad.model
            if system is None and ad.system:
                system = ad.system

        lib_id = prompt_version or session.config.agent.prompt_lib
        permissions = session.config.permissions

        # model 默认取 session.config.model.model_id
        model = model or session.config.model.model_id

        injected_system = system if system else None

        # adapter 解析优先级：显式传入 > session.config.model（新配置系统）
        if adapter is None:
            try:
                endpoint = session.config.model.to_model_endpoint(model_id=model)
                resolved_adapter = AdapterFactory.build(endpoint)
            except Exception as e:
                raise ValueError(
                    f"Failed to create adapter from config: {e}"
                ) from e
        else:
            resolved_adapter = adapter

        # 由 PromptEngine 构建工具集
        engine = PromptEngine(lib_id)
        built_tools = engine.build_tools(session, resolved_adapter, permissions, emitter=emitter, model=model)

        tool_manager = ToolManager(
            session.project_root,
            session.tasks,
            permissions,
            tools=built_tools,
        )

        # 加载工程级 / 用户全局 extra tools（.ccserver/tools/ 和 ~/.ccserver/tools/）
        extra_loader = ExtraToolLoader.from_workdir(session.project_root)
        for extra_tool in extra_loader.load_all():
            tool_manager.register_custom_tool(extra_tool)

        all_tools = tool_manager.get_all_tools()
        tools = session.config.permissions.filter_tools(all_tools)

        # Agent 工具始终保留，不受 permissions 过滤
        agent_catalog = session.agents.build_catalog()
        bt_agent = BTAgent(agent_catalog=agent_catalog)
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
            round_limit=session.config.agent.main_round_limit,
            limit_strategy=session.config.agent.main_limit_strategy,
            on_limit_callback=on_limit,
            persist=True,
            prompt_version=lib_id,
            language=language,
            system=injected_system,
            append_system=append_system,
            run_mode=run_mode,  # None = 从 session.settings.run_mode 读取
            stream=stream,
            env_vars=env_vars,
        )

        # MCP schema 过滤后追加（__init__ 不持有 config，无法过滤，由此处补全）
        agent._schemas += session.config.permissions.filter_mcp_schemas(session.mcp.schemas())

        # 让 PromptEngine 对 schema 描述做后处理（如 cc_reverse 替换为 CC 原版描述）
        agent._schemas = engine.patch_tool_schemas(agent._schemas)

        # 将根 Agent 挂载到 Session，供外部（如 Monitor）查询根 Agent 信息
        session._root_agent = agent

        # 启动定时任务调度器（若尚未启动）
        cs = session.cron_scheduler
        if not cs.is_alive:
            cs.start()

        logger.info(
            "Root agent created | session={} model={} lib={} tools={} mcp_tools={}",
            session.id[:8], model, lib_id, list(tools.keys()),
            [s["name"] for s in agent._schemas if s["name"].startswith("mcp__")],
        )
        return agent
