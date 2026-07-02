"""
loader — 扫描 .ccserver/agents/ 目录，加载 AgentDef。

Agent 定义文件格式（Markdown + frontmatter）：

    ---
    name: web-search
    description: 搜索网络并返回结构化摘要
    model: claude-haiku-4-5-20251001   # 可选，覆盖全局 MODEL
    ---

    # web-search

    你是一个专业的网络搜索 subagent...（system prompt 正文）

发现路径（按优先级从高到低）：
    {workdir}/.ccserver/agents/
    ~/.ccserver/agents/
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from typing import List, Tuple

from ...emitters.filter import VALID_VERBOSITY as _OUTPUT_MODES
from ...utils import parse_frontmatter

@dataclass
class AgentDef:
    """一个 agent 定义文件解析后的结构化表示。"""

    name: str           # agent 唯一标识，对应 Task 工具的 agent 参数
    description: str    # 一句话描述，注入编排 agent 的 system prompt
    system: str         # agent 的完整 system prompt（frontmatter 之后的正文）
    location: Path      # .md 文件的绝对路径
    model: str | None = None                  # 覆盖全局 MODEL；None = 使用全局配置
    tools: list[str] | None = None            # 允许的内置工具名白名单；None = 使用 CHILD_DEFAULT_TOOLS
    disallowed_tools: list[str] | None = None # 内置工具黑名单；从继承集合中剔除；None = 不额外禁用
    mcp: list[str] | None = None              # 允许的 mcp__server__tool 名列表；None = 不允许任何 MCP（必须显式列出）
    skills: list[str] | None = None           # 允许的 skill 名称列表；None = 无 skills（subagent 不注入 catalog）
    output_mode: str | None = None            # 输出模式；None 等同于 interactive（默认，完整事件流）
    is_teammate: bool = False                 # True = Teammate 角色，额外允许 Task 工具
    is_team_capable: bool = False             # True = 该 agent 支持 Agent Team 功能
    is_persistent: bool = False               # True = 永久驻留，完成后不自动清理
    round_limit: int | None = None            # 覆盖全局 SUB_ROUND_LIMIT；None = 使用全局默认值
    limit_strategy: str = "last_text"         # round limit 兜底策略（last_text/report/callback）

    # ---- 新增字段（对齐 Claude Code AgentDefinition）----
    model_hint: str | None = None             # 模型快捷方式："haiku" | "sonnet" | "inherit"
    omit_claude_md: bool = False              # 不加载 CLAUDE.md（省 token）
    permission_mode: str | None = None        # "dontAsk" | "acceptEdits" | "bubble" | ...
    isolation: str | None = None              # "worktree" | "remote"
    auto_background: bool = False             # True = 自动后台运行
    max_turns: int | None = None              # 最大回合数（语义同 round_limit）
    color: str | None = None                  # 终端显示颜色（"red" | "blue" | ...）
    auto_approve_tools: bool = False          # 工具调用自动批准（不询问用户）
    mcp_servers: list[str] | None = None      # 内置 Agent 专用的 MCP 服务器列表
    hooks: dict | None = None                 # 生命周期钩子配置
    is_builtin: bool = False                  # True = 由 AgentSpec（Python 类）注册的 AgentDef

    def overrides(self) -> dict:
        """
        把 AgentDef 字段映射成 CcServerConfig 的部分覆盖 dict（AGENT 作用域）。

        目前覆盖 model（其余如 tools/round_limit 由 spawn 的分层逻辑直接消费）。
        返回的 dict 可喂给 configuration.resolve_agent / deep_merge。
        """
        out: dict = {}
        if self.model:
            out.setdefault("model", {})["model_id"] = self.model
        return out


def _agent_def_from_meta(meta: dict, body: str, location: Path) -> "AgentDef | None":
    """
    从元数据 dict（.md frontmatter 或 agent.json）+ system 正文构建 AgentDef。

    供 AgentLoader._parse（单文件 .md）与 AgentLoader.load_package（文件夹包）共用，
    保证两种来源解析逻辑一致（DRY）。

    Args:
        meta:     元数据字典（含 name/description/model/tools 等）。
        body:     system prompt 正文。
        location: 来源文件路径（.md 或 agent.json），用于日志与 AgentDef.location。

    Returns:
        AgentDef；description 缺失时返回 None。
    """
    name = str(meta.get("name", location.parent.name)).strip()
    description = str(meta.get("description", "")).strip()
    if not description:
        logger.error("Missing description, skipping | path={}", location)
        return None

    raw_tools = _parse_str_or_list(meta.get("tools"))
    basic_tools, mcps = _devide_basic_tools_and_mcps(raw_tools)
    disallowed_tools = _parse_str_or_list(meta.get("disallowed_tools"))
    skills = _parse_str_or_list(meta.get("skills"))

    output_mode = meta.get("output_mode") or None
    if output_mode and output_mode not in _OUTPUT_MODES:
        logger.warning("Unknown output_mode={} in {}, ignoring", output_mode, location)
        output_mode = None

    def _as_bool(key: str) -> bool:
        return str(meta.get(key, "false")).strip().lower() in ("true", "1", "yes")

    is_teammate = _as_bool("is_teammate")
    is_team_capable = _as_bool("is_team_capable")
    is_persistent = _as_bool("is_persistent")
    omit_claude_md = _as_bool("omit_claude_md")
    auto_background = _as_bool("auto_background")
    auto_approve_tools = _as_bool("auto_approve_tools")

    def _as_int(key: str):
        raw = meta.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid {}={} in {}, ignoring", key, raw, location)
            return None

    round_limit = _as_int("round_limit")
    max_turns = _as_int("max_turns")
    limit_strategy = meta.get("limit_strategy", "last_text")
    model_hint = meta.get("model_hint") or None
    permission_mode = meta.get("permission_mode") or None
    isolation = meta.get("isolation") or None
    color = meta.get("color") or None
    mcp_servers = _parse_str_or_list(meta.get("mcp_servers"))
    hooks = meta.get("hooks") or None

    return AgentDef(
        name=name,
        description=description,
        system=body,
        location=location.resolve(),
        model=meta.get("model") or None,
        tools=basic_tools or None,
        disallowed_tools=disallowed_tools,
        mcp=mcps or None,
        skills=skills,
        output_mode=output_mode,
        is_teammate=is_teammate,
        is_team_capable=is_team_capable,
        is_persistent=is_persistent,
        round_limit=round_limit,
        limit_strategy=limit_strategy,
        model_hint=model_hint,
        omit_claude_md=omit_claude_md,
        permission_mode=permission_mode,
        isolation=isolation,
        auto_background=auto_background,
        max_turns=max_turns,
        color=color,
        auto_approve_tools=auto_approve_tools,
        mcp_servers=mcp_servers,
        hooks=hooks,
    )



class AgentLoader:
    """
    扫描多个目录发现 AgentDef，支持按名称查找。

    优先级：前面的目录优先级更高，同名 agent 只保留高优先级版本。
    """

    def __init__(self, *agents_dirs: Path):
        self.agents: dict[str, AgentDef] = {}
        for d in agents_dirs:
            self._scan(d)

    @classmethod
    def from_workdir(cls, workdir: Path, global_config_dir: Path | None = None) -> "AgentLoader":
        """根据工作目录自动构建标准扫描路径（project-level 优先于 user-level，内置最低）。"""
        global_dir = global_config_dir or Path.home() / ".ccserver"
        builtin_dir = Path(__file__).parent.parent.parent / "builtins" / "agents"
        instance = cls(
            workdir / ".ccserver" / "agents",
            global_dir / "agents",
            builtin_dir,
        )
        # 合并 Python AgentSpec 定义的内置 Agent
        instance._merge_builtin_specs()
        return instance

    def _merge_builtin_specs(self) -> int:
        """
        合并内置 AgentSpec（Python 类定义）到 agents 字典。

        AgentRegistry 自动扫描 ccserver.builtins.agents.specs 包，
        发现所有 BaseAgentSpec 子类并转换为 AgentDef。

        合并规则：
          - 内置 Agent 的优先级低于 project/user-level .md 文件
          - 如果同名 agent 已存在（由 .md 定义），保留 .md 版本
          - 内置 Agent 的 is_builtin=True

        Returns:
            合并的 Agent 数量
        """
        try:
            from ccserver.builtins.agents.registry import discover_builtin_agents
        except ImportError:
            logger.debug("Built-in agents package not available, skipping merge")
            return 0

        builtin_defs = discover_builtin_agents()
        merged_count = 0
        for name, agent_def in builtin_defs.items():
            if name in self.agents:
                # 已有同名 agent（project/user-level .md），跳过内置版本
                logger.debug(
                    "Built-in agent '{}' skipped: overridden by project/user-level definition",
                    name,
                )
                continue
            self.agents[name] = agent_def
            merged_count += 1
            logger.debug("Built-in agent merged | name={} builtin={}", name, agent_def.is_builtin)

        if merged_count:
            logger.info("Built-in agents merged | count={}", merged_count)
        return merged_count

    # ── 扫描 ─────────────────────────────────────────────────────────────────

    def _scan(self, agents_dir: Path):
        if not agents_dir.exists():
            return
        for md_file in sorted(agents_dir.glob("*.md")):
            agent = self._parse(md_file)
            if agent is None:
                continue
            if agent.name in self.agents:
                logger.warning(
                    "Agent name collision | name={} skipping {}",
                    agent.name, md_file,
                )
                continue
            self.agents[agent.name] = agent
            logger.debug("Agent loaded | name={} path={}", agent.name, md_file)

    def _parse(self, md_file: Path) -> AgentDef | None:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read agent file | path={} error={}", md_file, e)
            return None

        meta, body = parse_frontmatter(text)
        if meta is None:
            logger.error("Unparseable frontmatter, skipping | path={}", md_file)
            return None
        return _agent_def_from_meta(meta, body, md_file)

    @staticmethod
    def load_package(pkg_dir: Path) -> "AgentDef | None":
        """
        从文件夹 Agent Package 加载 AgentDef（spec §7）。

        约定：
          <pkg_dir>/agent.json  —— AgentDef 元数据（等价于 .md frontmatter 的 meta）
          <pkg_dir>/system.md   —— system prompt 正文（也可在 agent.json 内联 "system"）

        Returns:
            AgentDef，或 None（agent.json 缺失/无法解析时）。
        """
        pkg_dir = Path(pkg_dir)
        json_path = pkg_dir / "agent.json"
        if not json_path.exists():
            logger.error("Agent package 缺少 agent.json | dir={}", pkg_dir)
            return None
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Agent package agent.json 解析失败 | dir={} error={}", pkg_dir, e)
            return None
        if not isinstance(meta, dict):
            logger.error("Agent package agent.json 顶层必须是对象 | dir={}", pkg_dir)
            return None

        # system 正文：优先 agent.json 内联 "system"，否则读 system.md
        body = meta.get("system")
        if not body:
            sys_md = pkg_dir / "system.md"
            body = sys_md.read_text(encoding="utf-8") if sys_md.exists() else ""

        return _agent_def_from_meta(meta, body, json_path)

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> AgentDef | None:
        """按名称获取 AgentDef；不存在返回 None。"""
        return self.agents.get(name)

    def build_catalog(self) -> str:
        """
        生成可注入编排 agent system prompt 的 agent 目录描述块。
        格式与官方一致：- name: description (Tools: xxx)
        """
        if not self.agents:
            return ""
        lines = []
        for a in self.agents.values():
            tools_label = self._format_tools(a)
            lines.append(f"- {a.name}: {a.description} (Tools: {tools_label})")
        return "\n".join(lines)

    @staticmethod
    def _format_tools(a: "AgentDef") -> str:
        """将 agent 的工具列表格式化为可读字符串。"""
        from ccserver.builtins.tools.constants import CHILD_DEFAULT_TOOLS
        parts = []
        if a.tools is not None:
            parts.extend(a.tools)
        else:
            # tools=None 表示使用默认工具集
            parts.extend(sorted(CHILD_DEFAULT_TOOLS))
        if a.mcp:
            parts.extend(a.mcp)
        return ", ".join(parts)


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _parse_str_or_list(value) -> list[str] | None:
    """
    将 frontmatter 中的值统一转为字符串列表，或 None（未填）。
    支持：
      - YAML list（_parse_frontmatter 已解析为 list）
      - 逗号分隔字符串（单行值）
      - None / 空字符串 → None
    """
    if isinstance(value, list):
        result = [str(v).strip() for v in value if str(v).strip()]
        return result if result else None
    if isinstance(value, str) and value.strip():
        result = [v.strip() for v in value.split(",") if v.strip()]
        return result if result else None
    return None


def _devide_basic_tools_and_mcps(tools:List[str]) -> Tuple[List[str]]:
    basic_tools = []
    mcps = []
    if not tools or len(tools) == 0:
        return basic_tools, mcps
    for tool in tools:
        if tool.startswith("mcp__"):
            mcps.append(tool)
        else:
            basic_tools.append(tool)
    return basic_tools, mcps


