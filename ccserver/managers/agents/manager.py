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

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from typing import List, Dict, Any, Optional, Tuple

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
    round_limit: int | None = None            # 覆盖全局 SUB_ROUND_LIMIT；None = 使用全局默认值
    limit_strategy: str = "last_text"         # round limit 兜底策略（last_text/report/callback）



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
        return cls(
            workdir / ".ccserver" / "agents",
            global_dir / "agents",
            builtin_dir,
        )

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

        # name 默认取文件名（去掉 .md）
        name = meta.get("name", md_file.stem).strip()
        description = meta.get("description", "").strip()
        if not description:
            logger.error("Missing description, skipping | path={}", md_file)
            return None

        # tools: 内置工具名白名单，不填则 None（使用 CHILD_DEFAULT_TOOLS）
        # mcp__* 前缀的条目会被自动拆分到 mcp 字段，tools 只存纯内置工具名
        raw_tools = _parse_str_or_list(meta.get("tools"))
        basic_tools, mcps = _devide_basic_tools_and_mcps(raw_tools)

        # disallowed_tools: 内置工具黑名单，不填则 None（不额外禁用）
        disallowed_tools = _parse_str_or_list(meta.get("disallowed_tools"))

        # skills: skill 名称列表，不填则 None（subagent 不注入 skills catalog）
        skills = _parse_str_or_list(meta.get("skills"))

        output_mode = meta.get("output_mode") or None
        if output_mode and output_mode not in _OUTPUT_MODES:
            logger.warning("Unknown output_mode={} in {}, ignoring", output_mode, md_file)
            output_mode = None

        # is_teammate: 声明为 Teammate 角色，额外允许 Task 工具
        is_teammate_raw = meta.get("is_teammate", "false")
        is_teammate = str(is_teammate_raw).strip().lower() in ("true", "1", "yes")

        # is_team_capable: 该 agent 是否支持 Agent Team 功能
        is_team_capable_raw = meta.get("is_team_capable", "false")
        is_team_capable = str(is_team_capable_raw).strip().lower() in ("true", "1", "yes")

        # round_limit: 覆盖全局 SUB_ROUND_LIMIT，不填则 None
        round_limit = None
        round_limit_raw = meta.get("round_limit")
        if round_limit_raw is not None:
            try:
                round_limit = int(round_limit_raw)
            except (TypeError, ValueError):
                logger.warning("Invalid round_limit={} in {}, ignoring", round_limit_raw, md_file)

        limit_strategy = meta.get("limit_strategy", "last_text")

        return AgentDef(
            name=name,
            description=description,
            system=body,
            location=md_file.resolve(),
            model=meta.get("model") or None,
            tools=basic_tools or None,
            disallowed_tools=disallowed_tools,
            mcp=mcps or None,
            skills=skills,
            output_mode=output_mode,
            is_teammate=is_teammate,
            is_team_capable=is_team_capable,
            round_limit=round_limit,
            limit_strategy=limit_strategy,
        )

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


