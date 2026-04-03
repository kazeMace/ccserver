"""
settings — 读取项目目录和全局目录下的配置文件。

配置文件路径（按优先级从低到高）：
  ~/.ccserver/settings.json         全局配置，适用于所有项目
  <project>/.ccserver/settings.local.json  项目配置，覆盖全局

支持字段：
  permissions.allow        — 额外授权（Bash(cmd:*) 限制命令范围；mcp__server__tool 显式允许 MCP 工具）
  permissions.deny         — 黑名单，禁用指定工具或命令（优先级高于 allow）
  permissions.ask          — 需要运行时确认的工具列表（interactive 模式下弹出提示；auto 模式下直接拒绝）
  runMode                  — 运行模式："auto"（全自动）或 "interactive"（交互式），默认 "auto"
  enabledMcpjsonServers    — 允许连接的 MCP server 名称列表

allow/deny 条目格式：
  "Bash(git:*)"            → 命令执行权限（限制 Bash 可执行的命令前缀）
  "mcp__server__tool"      → MCP 工具使用权限（白名单，未列出的 MCP 工具不可用）
  "ToolName"               → 内置工具黑名单（仅在 deny 中有意义，allow 中对内置工具无效）

内置工具（Bash、Read、Write 等）默认全部允许，用 deny 禁用；
MCP 工具默认全部禁用，用 allow 显式开启（或 enabledMcpjsonServers 启用整个 server）。

runMode 说明：
  auto        — 全自动运行，不等待用户确认；permissions.ask 列表中的工具调用会被直接拒绝
  interactive — 交互模式；permissions.ask 列表中的工具调用会暂停并向用户请求确认

合并规则（全局 + 项目）：
  deny     = 全局 deny ∪ 项目 deny    （取并集，更严格）
  allow    = 项目 allow ?? 全局 allow  （项目存在则覆盖全局；None 表示不限制）
  ask      = 全局 ask ∪ 项目 ask       （取并集）
  runMode  = 项目 runMode ?? 全局 runMode ?? "auto"
  deny 优先于 allow
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger


def _parse_entries(entries: list[str]) -> tuple[frozenset[str], dict[str, list[str]]]:
    """
    将 allow/deny 条目列表拆分为两类：
      tools    — 纯工具名（无括号，非 mcp__ 前缀），如 "WebSearch"、"Bash"
      commands — 带括号的命令前缀（按工具名分组），如 {"Bash": ["git:", "pytest:"]}

    mcp__ 前缀的条目归入 tools（整体作为工具名匹配）。
    """
    tools: set[str] = set()
    commands: dict[str, list[str]] = {}

    for entry in entries:
        if "(" in entry:
            # 带括号：命令执行权限，如 "Bash(git:*)"
            tool_name, _, rest = entry.partition("(")
            tool_name = tool_name.strip()
            cmd_prefix = rest.rstrip(")").strip()
            # 去掉末尾的 :* 通配符，只保留命令前缀
            cmd_prefix = cmd_prefix.rstrip(":*").rstrip(":")
            if tool_name and cmd_prefix:
                commands.setdefault(tool_name, []).append(cmd_prefix)
        else:
            # 无括号：工具使用权限
            entry = entry.strip()
            if entry:
                tools.add(entry)

    return frozenset(tools), commands


class ProjectSettings:
    """
    合并后的配置，由全局配置和项目配置叠加而成。
    文件不存在时对应层不生效（全部允许）。

    工具使用权限：
      allowed_tools  — 白名单，None = 不限制；frozenset = 只允许列出的工具
      denied_tools   — 黑名单，始终有效（空集合 = 无黑名单），优先于白名单

    命令执行权限（针对 Bash 等工具内部命令字符串）：
      allowed_commands — 按工具名分组的命令前缀白名单，None = 不限制
      denied_commands  — 按工具名分组的命令前缀黑名单，始终有效
    """

    def __init__(
        self,
        allowed_tools: Optional[frozenset[str]],       # None = 不限制
        denied_tools: frozenset[str],                   # 空集合 = 无黑名单
        allowed_commands: Optional[dict[str, list[str]]],  # None = 不限制
        denied_commands: dict[str, list[str]],
        enabled_mcp_servers: Optional[list[str]],       # None = 不限制
        ask_tools: frozenset[str] = frozenset(),         # 需要运行时确认的工具
        run_mode: str = "auto",                          # "auto" 或 "interactive"
        main_round_limit: Optional[int] = None,          # None = 使用 config.MAIN_ROUND_LIMIT
        main_limit_strategy: str = "last_text",           # round limit 兜底策略
    ):
        self.allowed_tools = allowed_tools
        self.denied_tools = denied_tools
        self.allowed_commands = allowed_commands
        self.denied_commands = denied_commands
        self.enabled_mcp_servers = enabled_mcp_servers
        self.ask_tools = ask_tools
        self.run_mode = run_mode
        self.main_round_limit = main_round_limit
        self.main_limit_strategy = main_limit_strategy

    @classmethod
    def from_dirs(cls, project_root: Path) -> "ProjectSettings":
        """
        合并全局配置（~/.ccserver/settings.json）和项目配置
        （<project>/.ccserver/settings.local.json）。
        """
        global_path = Path.home() / ".ccserver" / "settings.json"
        project_path = project_root / ".ccserver" / "settings.local.json"
        return cls._merge(
            cls._load_file(global_path, label="global"),
            cls._load_file(project_path, label="project"),
        )

    @classmethod
    def _load_file(cls, path: Path, label: str) -> Optional[dict]:
        """读取单个配置文件，返回 dict 或 None（文件不存在/解析失败）。"""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("settings loaded | label={} path={}", label, path)
            return data
        except Exception as e:
            logger.error("Failed to parse settings | label={} path={} error={}", label, path, e)
            return None

    @classmethod
    def _merge(cls, global_data: Optional[dict], project_data: Optional[dict]) -> "ProjectSettings":
        """
        合并两层配置，返回 ProjectSettings 实例。

        deny  = 全局 deny ∪ 项目 deny（并集，更严格）
        allow = 项目 allow ?? 全局 allow（项目存在则覆盖；None 表示不限制）
        enabled_mcp_servers = 项目 ?? 全局
        """
        def get_list(data: Optional[dict], *keys) -> Optional[list]:
            if data is None:
                return None
            result = data
            for key in keys:
                if not isinstance(result, dict):
                    return None
                result = result.get(key)
            return result if isinstance(result, list) else None

        global_allow_raw = get_list(global_data, "permissions", "allow") or []
        global_deny_raw  = get_list(global_data, "permissions", "deny")  or []
        project_allow_raw = get_list(project_data, "permissions", "allow")
        project_deny_raw  = get_list(project_data, "permissions", "deny") or []

        # 解析各层条目
        global_allow_tools, global_allow_cmds = _parse_entries(global_allow_raw)
        global_deny_tools,  global_deny_cmds  = _parse_entries(global_deny_raw)
        project_deny_tools, project_deny_cmds = _parse_entries(project_deny_raw)

        # deny 取并集
        denied_tools = global_deny_tools | project_deny_tools
        denied_commands: dict[str, list[str]] = {}
        for tool, cmds in global_deny_cmds.items():
            denied_commands.setdefault(tool, []).extend(cmds)
        for tool, cmds in project_deny_cmds.items():
            denied_commands.setdefault(tool, []).extend(cmds)

        # allow：项目存在则覆盖全局，否则用全局，都没有则 None（不限制）
        if project_allow_raw is not None:
            allow_tools, allow_cmds = _parse_entries(project_allow_raw)
            allowed_tools: Optional[frozenset[str]] = allow_tools if allow_tools else None
            allowed_commands: Optional[dict[str, list[str]]] = allow_cmds if allow_cmds else None
        elif global_allow_raw:
            allowed_tools = global_allow_tools if global_allow_tools else None
            allowed_commands = global_allow_cmds if global_allow_cmds else None
        else:
            allowed_tools = None
            allowed_commands = None

        # enabled_mcp_servers：项目 ?? 全局
        enabled_mcp_servers = (
            get_list(project_data, "enabledMcpjsonServers")
            or get_list(global_data, "enabledMcpjsonServers")
        )

        # ask_tools：全局 ask ∪ 项目 ask（取并集）
        global_ask_raw  = get_list(global_data,  "permissions", "ask") or []
        project_ask_raw = get_list(project_data, "permissions", "ask") or []
        global_ask_tools, _  = _parse_entries(global_ask_raw)
        project_ask_tools, _ = _parse_entries(project_ask_raw)
        ask_tools = global_ask_tools | project_ask_tools

        # runMode：项目 ?? 全局 ?? "auto"
        def get_str(data: Optional[dict], key: str) -> Optional[str]:
            if data is None:
                return None
            val = data.get(key)
            return str(val) if val is not None else None

        def get_int(data: Optional[dict], key: str) -> Optional[int]:
            if data is None:
                return None
            val = data.get(key)
            try:
                return int(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        project_run_mode = get_str(project_data, "runMode")
        global_run_mode  = get_str(global_data,  "runMode")
        run_mode = project_run_mode or global_run_mode or "auto"
        # 只接受合法值，其他全部回退到 "auto"
        if run_mode not in ("auto", "interactive"):
            run_mode = "auto"

        # mainRoundLimit：项目 ?? 全局 ?? None（None 表示用 config 默认值）
        main_round_limit = (
            get_int(project_data, "mainRoundLimit")
            or get_int(global_data, "mainRoundLimit")
        )

        # mainLimitStrategy：项目 ?? 全局 ?? "last_text"
        main_limit_strategy = (
            get_str(project_data, "mainLimitStrategy")
            or get_str(global_data, "mainLimitStrategy")
            or "last_text"
        )

        logger.debug(
            "settings merged | allowed_tools={} denied_tools={} allowed_cmds={} denied_cmds={} mcp_servers={} ask={} run_mode={} main_round_limit={} main_limit_strategy={}",
            allowed_tools, denied_tools, allowed_commands, denied_commands, enabled_mcp_servers, ask_tools, run_mode, main_round_limit, main_limit_strategy,
        )
        return cls(
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            allowed_commands=allowed_commands,
            denied_commands=denied_commands,
            enabled_mcp_servers=enabled_mcp_servers,
            ask_tools=ask_tools,
            run_mode=run_mode,
            main_round_limit=main_round_limit,
            main_limit_strategy=main_limit_strategy,
        )

    # ── 工具使用权限判断 ──────────────────────────────────────────────────────

    def is_tool_allowed(self, tool_name: str) -> bool:
        """
        判断内置工具是否允许。
        决策顺序：deny 优先 → 默认允许。

        内置工具不受 allow 白名单约束——allow 条目（如 Bash(cmd:*)）只影响命令执行权限，
        不会把未列出的内置工具禁掉。要禁用内置工具请用 deny。
        """
        return tool_name not in self.denied_tools

    def is_mcp_tool_allowed(self, mcp_tool_name: str) -> bool:
        """
        判断 MCP 工具（mcp__server__tool 格式）是否允许。
        决策顺序：deny 优先 → allow 白名单 → 默认允许。

        MCP 工具保留白名单语义：allow 列表里显式列出的 mcp__ 条目才允许使用，
        未列出则默认不允许（allowed_tools 为 None 时全部允许）。
        """
        if mcp_tool_name in self.denied_tools:
            return False
        if self.allowed_tools is None:
            return True
        return mcp_tool_name in self.allowed_tools

    def is_mcp_server_enabled(self, server_name: str) -> bool:
        """判断 MCP server 是否启用。enabled_mcp_servers 为 None 时全部允许。"""
        if self.enabled_mcp_servers is None:
            return True
        return server_name in self.enabled_mcp_servers

    def filter_tools(self, tools: dict) -> dict:
        """过滤内置工具字典，只保留允许的工具。"""
        return {name: tool for name, tool in tools.items() if self.is_tool_allowed(name)}

    def filter_mcp_schemas(self, schemas: list[dict]) -> list[dict]:
        """过滤 MCP 工具 schema 列表，只保留允许的工具。"""
        return [s for s in schemas if self.is_mcp_tool_allowed(s["name"])]

    # ── 命令执行权限判断 ──────────────────────────────────────────────────────

    def is_command_allowed(self, tool_name: str, command: str) -> bool:
        """
        判断某工具的命令字符串是否允许执行。
        决策顺序：denied_commands 优先 → allowed_commands 白名单 → 默认允许

        tool_name: 工具名，如 "Bash"
        command:   实际命令字符串，如 "git status"
        """
        cmd = command.strip()

        # deny 优先：命中任何一个黑名单前缀则拒绝
        for prefix in self.denied_commands.get(tool_name, []):
            if cmd.startswith(prefix):
                return False

        # allow：None 表示不限制命令
        tool_allowed_cmds = self.allowed_commands.get(tool_name) if self.allowed_commands else None
        if tool_allowed_cmds is None:
            return True

        # 白名单：命中任何一个前缀则允许
        return any(cmd.startswith(prefix) for prefix in tool_allowed_cmds)
