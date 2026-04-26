"""
builtins/agents/base -- BaseAgentSpec 抽象基类。

所有内置 Agent 的规范基类。每个内置 Agent 都是一个继承此 ABC 的类，
定义类属性作为元数据。框架通过 registry.discover() 自动扫描、
实例化、转换为 AgentDef。

与 Claude Code AgentDefinition 的对应关系：
  name          -> agentType
  description   -> whenToUse
  tools         -> tools
  disallowed_tools -> disallowedTools
  model         -> model
  model_hint    -> model (快捷方式：haiku/sonnet/inherit)
  omit_claude_md -> omitClaudeMd
  permission_mode -> permissionMode
  isolation     -> isolation
  auto_background -> background
  max_turns     -> maxTurns
  round_limit   -> (已对齐)
  output_mode   -> output_mode
  color         -> color
  auto_approve_tools -> acceptEdits 语义
  is_teammate   -> is_teammate
  is_team_capable -> is_team_capable
  is_persistent -> is_persistent
  skills        -> skills
  mcp_servers   -> mcpServers
  hooks         -> hooks
"""

from abc import ABC
from pathlib import Path
from typing import ClassVar

from loguru import logger


class BaseAgentSpec(ABC):
    """
    内置 Agent 的规范基类。

    每个内置 Agent 都是一个继承此 ABC 的类，定义类属性作为元数据。
    框架通过 discover() 自动扫描、实例化、转换为 AgentDef。

    子类必须定义的类属性：
        name         str -- Agent 唯一标识，对应 subagent_type 参数值
        description  str -- 一句话描述，注入根 Agent 的 system prompt catalog

    子类可选的类属性（有默认值）：
        tools / disallowed_tools / model / model_hint / omit_claude_md /
        permission_mode / isolation / auto_background / max_turns /
        round_limit / output_mode / color / auto_approve_tools /
        is_teammate / is_team_capable / is_persistent / skills /
        mcp_servers / hooks
    """

    # ---- 强制类属性（子类必须定义）----
    name: ClassVar[str]
    description: ClassVar[str]

    # ---- 可选类属性（有默认值）----
    tools: ClassVar[list[str] | None] = None
    disallowed_tools: ClassVar[list[str] | None] = None
    model: ClassVar[str | None] = None
    model_hint: ClassVar[str | None] = None
    omit_claude_md: ClassVar[bool] = False
    permission_mode: ClassVar[str | None] = None
    isolation: ClassVar[str | None] = None
    auto_background: ClassVar[bool] = False
    max_turns: ClassVar[int | None] = None
    round_limit: ClassVar[int | None] = None
    output_mode: ClassVar[str | None] = None
    color: ClassVar[str | None] = None
    auto_approve_tools: ClassVar[bool] = False
    is_teammate: ClassVar[bool] = False
    is_team_capable: ClassVar[bool] = False
    is_persistent: ClassVar[bool] = False
    skills: ClassVar[list[str] | None] = None
    mcp_servers: ClassVar[list[str] | None] = None
    hooks: ClassVar[dict | None] = None

    # 系统提示文本文件路径（可选，默认为 prompts/{name}.md）
    _prompt_file: ClassVar[str | None] = None

    @classmethod
    def _prompt_path(cls) -> Path:
        """
        返回系统提示文件的路径。

        优先使用 _prompt_file 类属性指定的路径，
        否则默认查找 prompts/{name}.md。

        Returns:
            Path 对象，指向系统提示文件
        """
        if cls._prompt_file:
            return Path(__file__).parent / "prompts" / cls._prompt_file
        return Path(__file__).parent / "prompts" / f"{cls.name}.md"

    @classmethod
    def get_system_prompt(cls) -> str:
        """
        返回 Agent 的 system prompt。

        优先从 prompts/{name}.md 读取文件内容，文件不存在则返回空字符串。
        子类可覆盖此方法实现动态生成（如运行时注入变量）。

        Returns:
            system prompt 文本，或空字符串
        """
        path = cls._prompt_path()
        if path.exists():
            content = path.read_text(encoding="utf-8")
            logger.debug(
                "BaseAgentSpec system prompt loaded | name={} path={} chars={}",
                cls.name, path, len(content),
            )
            return content
        logger.warning(
            "BaseAgentSpec system prompt file not found | name={} path={}",
            cls.name, path,
        )
        return ""

    @classmethod
    def build_agent_def(cls) -> "AgentDef":
        """
        将类属性转换为 AgentDef 数据类。

        从配置文件（AgentConfig）读取 enabled 和 auto_approve_tools 等运行时配置，
        与类属性合并后构造 AgentDef。

        Returns:
            AgentDef 数据类实例
        """
        from .config import agent_config

        # 运行时配置（agents.json）
        runtime_cfg = agent_config().get_agent_config(cls.name)
        enabled = runtime_cfg.get("enabled", True)

        if not enabled:
            # 未启用，返回空 AgentDef（registry 会跳过）
            from ccserver.managers.agents.manager import AgentDef
            return AgentDef(
                name=cls.name,
                description=cls.description,
                system="",
                location=cls._prompt_path(),
                is_builtin=True,
            )

        # 运行时配置覆盖类属性
        auto_approve_tools = runtime_cfg.get("auto_approve_tools", cls.auto_approve_tools)
        default_model = runtime_cfg.get("default_model", cls.model)
        default_model_hint = runtime_cfg.get("model_hint", cls.model_hint)

        # 合并类属性 + 运行时配置
        from ccserver.managers.agents.manager import AgentDef
        return AgentDef(
            name=cls.name,
            description=cls.description,
            system=cls.get_system_prompt(),
            location=cls._prompt_path(),
            tools=cls.tools,
            disallowed_tools=cls.disallowed_tools,
            model=default_model,
            model_hint=default_model_hint,
            omit_claude_md=cls.omit_claude_md,
            permission_mode=cls.permission_mode,
            isolation=cls.isolation,
            auto_background=cls.auto_background,
            max_turns=cls.max_turns,
            round_limit=cls.round_limit,
            output_mode=cls.output_mode,
            color=cls.color,
            auto_approve_tools=auto_approve_tools,
            is_teammate=cls.is_teammate,
            is_team_capable=cls.is_team_capable,
            is_persistent=cls.is_persistent,
            skills=cls.skills,
            mcp=cls.mcp_servers,
            hooks=cls.hooks,
            is_builtin=True,
        )

    @classmethod
    def is_enabled(cls) -> bool:
        """
        检查此 Agent 是否在 agents.json 中启用。

        Returns:
            True 如果启用（默认启用）
        """
        from .config import agent_config
        return agent_config().is_enabled(cls.name)
