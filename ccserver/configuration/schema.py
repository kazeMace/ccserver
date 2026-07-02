"""
schema — ccserver 配置的单一真相源（typed config schema）。

设计要点（对应 spec §4）：
  - 纯 dataclass，嵌套分段；每段一个类，职责单一（SRP）。
  - 每个字段带 metadata={"desc": 中英文说明, "env": 对应环境变量名}，
    既是代码内注释，也是 doc_gen 自动生成配置文档的数据源。
  - 每段提供显式的 from_dict / as_dict（不使用反射魔法，新人易懂）。
  - 权限判定方法挂在 PermissionConfig 上；端点构造挂在 ModelConfig 上
    （从旧 ProjectSettings 迁移，行为对齐）。

作用域说明（见 spec §3）：
  CcServerConfig 是 SESSION 作用域解析后的完整对象；
  ProcessConfig（见 loader.py）只是用同一个类承载"进程级共享底座"。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ccserver.model_engine import ModelEndpoint


# ─── 默认路径 ────────────────────────────────────────────────────────────────
# 全局配置目录默认 ~/.ccserver，其下放 sessions / db / logs。
# 这里只计算一次，作为 InfraConfig 字段的默认值。

import tempfile as _tempfile

_DEFAULT_GLOBAL_DIR = str(Path.home() / ".ccserver")
_DEFAULT_TEMP_DIR = _tempfile.gettempdir()


# ─── 各段 dataclass ──────────────────────────────────────────────────────────


@dataclass
class ModelConfig:
    """主 LLM 连接配置。对应旧 config.MODEL / PROVIDER / BASE_URL / API_KEY。"""

    model_id: str = field(
        default="claude-sonnet-4-6",
        metadata={"desc": "主模型 ID / main model id", "env": "CCSERVER_MODEL"},
    )
    api_type: Optional[str] = field(
        default=None,
        metadata={"desc": "API 协议类型（anthropic-messages 等）", "env": "CCSERVER_API_TYPE"},
    )
    provider: Optional[str] = field(
        default="anthropic",
        metadata={"desc": "提供商标识 / provider", "env": "CCSERVER_PROVIDER"},
    )
    base_url: Optional[str] = field(
        default=None,
        metadata={"desc": "API 端点 URL", "env": "CCSERVER_BASE_URL"},
    )
    api_key: Optional[str] = field(
        default=None,
        metadata={"desc": "API 密钥（可入文件，也可环境变量覆盖）", "env": "CCSERVER_API_KEY"},
    )

    @classmethod
    def from_dict(cls, data: dict) -> "ModelConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            model_id=data.get("model_id", cls.model_id),
            api_type=data.get("api_type", None),
            provider=data.get("provider", "anthropic"),
            base_url=data.get("base_url", None),
            api_key=data.get("api_key", None),
        )

    def as_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "api_type": self.api_type,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": self.api_key,
        }

    def to_model_endpoint(self, model_id: Optional[str] = None) -> "ModelEndpoint":
        """
        构造 ModelEndpoint（供 AdapterFactory.build 使用）。

        新格式字段（api_type/base_url/api_key）直接用；provider 辅助推断。
        model_id 参数可覆盖本配置的 model_id。
        """
        from ccserver.model_engine import ModelEndpoint

        resolved = model_id or self.model_id
        assert resolved, "未指定 model_id"
        return ModelEndpoint(
            model_id=resolved,
            api_type=self.api_type,
            provider=self.provider,
            base_url=self.base_url,
            api_key=self.api_key,
        )


@dataclass
class VlmConfig:
    """视觉模型配置（ScreenFind 等）。对应旧 config.VLM_*。"""

    model_id: str = field(
        default="claude-sonnet-4-6",
        metadata={"desc": "视觉模型 ID / VLM model id", "env": "CCSERVER_VLM_MODEL"},
    )
    api_type: Optional[str] = field(
        default=None, metadata={"desc": "VLM API 协议类型", "env": ""}
    )
    provider: Optional[str] = field(
        default=None, metadata={"desc": "VLM 提供商", "env": "CCSERVER_VLM_PROVIDER"}
    )
    base_url: Optional[str] = field(
        default=None, metadata={"desc": "VLM 端点 URL", "env": "CCSERVER_VLM_BASE_URL"}
    )
    api_key: Optional[str] = field(
        default=None, metadata={"desc": "VLM API 密钥", "env": "CCSERVER_VLM_API_KEY"}
    )
    priority: str = field(
        default="", metadata={"desc": "VLM autoPriority 排序覆盖", "env": "CCSERVER_VLM_PRIORITY"}
    )

    @classmethod
    def from_dict(cls, data: dict) -> "VlmConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            model_id=data.get("model_id", cls.model_id),
            api_type=data.get("api_type", None),
            provider=data.get("provider", None),
            base_url=data.get("base_url", None),
            api_key=data.get("api_key", None),
            priority=data.get("priority", ""),
        )

    def as_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "api_type": self.api_type,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "priority": self.priority,
        }


@dataclass
class AgentBehaviorConfig:
    """agent 行为配置。对应旧 config.PROMPT_LIB / *_ROUND_LIMIT / MAX_DEPTH 等。"""

    prompt_lib: str = field(
        default="cc_reverse:v2.1.81",
        metadata={"desc": "提示词库 ID / prompt lib", "env": "CCSERVER_PROMPT_LIB"},
    )
    language: str = field(
        default="简体中文", metadata={"desc": "system prompt 语言", "env": ""}
    )
    main_round_limit: int = field(
        default=100,
        metadata={"desc": "根 agent 最大轮次", "env": "CCSERVER_MAIN_ROUNDS"},
    )
    sub_round_limit: int = field(
        default=30,
        metadata={"desc": "子 agent 最大轮次", "env": "CCSERVER_SUB_ROUNDS"},
    )
    max_depth: int = field(
        default=5,
        metadata={"desc": "agent 最大嵌套深度", "env": "CCSERVER_MAX_DEPTH"},
    )
    run_mode: str = field(
        default="auto", metadata={"desc": "运行模式 auto/interactive", "env": ""}
    )
    stream: bool = field(
        default=True, metadata={"desc": "是否实时 emit token", "env": ""}
    )
    main_limit_strategy: str = field(
        default="last_text", metadata={"desc": "round limit 兜底策略", "env": ""}
    )
    inject_system_file: Optional[str] = field(
        default=None,
        metadata={"desc": "启动注入的额外 system 文件路径", "env": "CCSERVER_INJECT_SYSTEM_FILE"},
    )
    append_system: bool = field(
        default=False,
        metadata={"desc": "注入 system 追加(True)还是替换(False)", "env": "CCSERVER_APPEND_SYSTEM"},
    )

    @classmethod
    def from_dict(cls, data: dict) -> "AgentBehaviorConfig":
        data = data if isinstance(data, dict) else {}
        out = cls()
        for f in fields(cls):
            if f.name in data:
                setattr(out, f.name, data[f.name])
        # run_mode 合法性兜底
        if out.run_mode not in ("auto", "interactive"):
            out.run_mode = "auto"
        return out

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class CompactionConfig:
    """上下文压缩配置。对应旧 config.THRESHOLD / KEEP_RECENT。"""

    threshold: int = field(
        default=120000,
        metadata={"desc": "压缩触发阈值(chars/4≈tokens)", "env": "CCSERVER_THRESHOLD"},
    )
    keep_recent: int = field(
        default=20,
        metadata={"desc": "保留不截断的最近 tool result 数", "env": "CCSERVER_KEEP_RECENT"},
    )

    @classmethod
    def from_dict(cls, data: dict) -> "CompactionConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            threshold=data.get("threshold", cls.threshold),
            keep_recent=data.get("keep_recent", cls.keep_recent),
        )

    def as_dict(self) -> dict:
        return {"threshold": self.threshold, "keep_recent": self.keep_recent}


@dataclass
class ToolConfig:
    """工具开关配置。对应旧 settings.toolConfig / enabledMcpjsonServers / userAgentTeam。"""

    tool_config: dict = field(
        default_factory=dict, metadata={"desc": "内置工具专属配置字典", "env": ""}
    )
    enabled_mcp_servers: Optional[list] = field(
        default=None, metadata={"desc": "允许连接的 MCP server 列表（None=不限制）", "env": ""}
    )
    user_agent_team: bool = field(
        default=False,
        metadata={"desc": "是否启用 Agent Team 功能", "env": "CCSERVER_USER_AGENT_TEAM"},
    )

    @classmethod
    def from_dict(cls, data: dict) -> "ToolConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            tool_config=data.get("tool_config", {}) or {},
            enabled_mcp_servers=data.get("enabled_mcp_servers", None),
            user_agent_team=bool(data.get("user_agent_team", False)),
        )

    def as_dict(self) -> dict:
        return {
            "tool_config": self.tool_config,
            "enabled_mcp_servers": self.enabled_mcp_servers,
            "user_agent_team": self.user_agent_team,
        }

    def is_mcp_server_enabled(self, server_name: str) -> bool:
        """enabled_mcp_servers 为 None 时全部允许。"""
        if self.enabled_mcp_servers is None:
            return True
        return server_name in self.enabled_mcp_servers


# ─── 权限解析辅助（从旧 settings._parse_entries 迁移）────────────────────────


def _parse_entries(entries: list) -> tuple:
    """
    将 allow/deny 条目列表拆分为 (tools: set, commands: dict)。
      "WebSearch"        → tools
      "Bash(git:*)"      → commands["Bash"] = ["git"]
      "mcp__s__t"        → tools（整体作为工具名）
    """
    tools: set = set()
    commands: dict = {}
    for entry in entries or []:
        if "(" in entry:
            tool_name, _, rest = entry.partition("(")
            tool_name = tool_name.strip()
            cmd_prefix = rest.rstrip(")").strip().rstrip(":*").rstrip(":")
            if tool_name and cmd_prefix:
                commands.setdefault(tool_name, []).append(cmd_prefix)
        else:
            entry = entry.strip()
            if entry:
                tools.add(entry)
    return tools, commands


@dataclass
class PermissionConfig:
    """
    工具/命令权限配置。对应旧 settings.permissions + ProjectSettings 判定方法。

    存原始 allow/deny/ask 条目列表；判定时按需解析。
    语义（与旧实现对齐）：
      - 内置工具：deny 优先，未 deny 默认允许（不受 allow 约束）
      - MCP 工具：deny 优先 → allow 白名单 → allow 为空则默认允许
      - 命令：denied_commands 优先 → allowed_commands 白名单 → 默认允许
    """

    allow: list = field(
        default_factory=list, metadata={"desc": "授权条目（命令前缀/MCP 工具）", "env": ""}
    )
    deny: list = field(
        default_factory=list, metadata={"desc": "禁用条目（工具/命令黑名单，优先）", "env": ""}
    )
    ask: list = field(
        default_factory=list, metadata={"desc": "需运行时确认的工具", "env": ""}
    )

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            allow=list(data.get("allow", []) or []),
            deny=list(data.get("deny", []) or []),
            ask=list(data.get("ask", []) or []),
        )

    def as_dict(self) -> dict:
        return {"allow": list(self.allow), "deny": list(self.deny), "ask": list(self.ask)}

    # ── 判定方法 ──────────────────────────────────────────────────────────────

    def _allowed_tools(self) -> Optional[frozenset]:
        """allow 中的纯工具名集合；空则 None（不限制）。"""
        tools, _ = _parse_entries(self.allow)
        return frozenset(tools) if tools else None

    def _denied_tools(self) -> frozenset:
        tools, _ = _parse_entries(self.deny)
        return frozenset(tools)

    def denied_tool_set(self) -> frozenset:
        """公开访问：deny 中的纯工具名集合（供 spawn 子 agent 工具裁剪）。"""
        return self._denied_tools()

    def allowed_tool_set(self) -> Optional[frozenset]:
        """公开访问：allow 中的纯工具名集合；None=不限制。"""
        return self._allowed_tools()

    def is_tool_allowed(self, tool_name: str) -> bool:
        """内置工具：deny 优先，默认允许。"""
        return tool_name not in self._denied_tools()

    def is_mcp_tool_allowed(self, mcp_tool_name: str) -> bool:
        """MCP 工具：deny 优先 → allow 白名单 → 默认允许。"""
        if mcp_tool_name in self._denied_tools():
            return False
        allowed = self._allowed_tools()
        if allowed is None:
            return True
        return mcp_tool_name in allowed

    def is_command_allowed(self, tool_name: str, command: str) -> bool:
        """命令前缀：denied 优先 → allowed 白名单 → 默认允许。"""
        cmd = command.strip()
        _, allow_cmds = _parse_entries(self.allow)
        _, deny_cmds = _parse_entries(self.deny)
        for prefix in deny_cmds.get(tool_name, []):
            if cmd.startswith(prefix):
                return False
        tool_allowed = allow_cmds.get(tool_name)
        if not tool_allowed:
            return True
        return any(cmd.startswith(prefix) for prefix in tool_allowed)

    def ask_tools(self) -> frozenset:
        tools, _ = _parse_entries(self.ask)
        return frozenset(tools)

    def denied_command_prefixes(self, tool_name: str) -> list:
        """返回某工具的命令黑名单前缀列表（供错误信息展示）。"""
        _, deny_cmds = _parse_entries(self.deny)
        return deny_cmds.get(tool_name, [])

    def allowed_command_prefixes(self, tool_name: str):
        """返回某工具的命令白名单前缀列表；无则 None（不限制）。"""
        _, allow_cmds = _parse_entries(self.allow)
        return allow_cmds.get(tool_name)

    def filter_tools(self, tools: dict) -> dict:
        """过滤内置工具字典，剔除被 deny 的。"""
        return {name: t for name, t in tools.items() if self.is_tool_allowed(name)}

    def filter_mcp_schemas(self, schemas: list) -> list:
        """过滤 MCP schema 列表，只留允许的。"""
        return [s for s in schemas if self.is_mcp_tool_allowed(s["name"])]


@dataclass
class InfraConfig:
    """
    基础设施/部署配置（进程级）。对应旧 config 的存储/路径/日志/DB 段。

    路径字段在内存中存 Path（消费侧直接用，与旧 config 一致），
    as_dict 序列化为 str，from_dict 从 str 还原为 Path。
    """

    storage_backend: str = field(
        default="file",
        metadata={"desc": "存储后端 file/sqlite/mongo", "env": "CCSERVER_STORAGE_BACKEND"},
    )
    mongo_uri: str = field(
        default="mongodb://localhost:27017",
        metadata={"desc": "MongoDB URI", "env": "CCSERVER_MONGO_URI"},
    )
    mongo_db: str = field(
        default="ccserver", metadata={"desc": "MongoDB 库名", "env": "CCSERVER_MONGO_DB"}
    )
    redis_url: str = field(
        default="redis://localhost:6379",
        metadata={"desc": "Redis URL", "env": "CCSERVER_REDIS_URL"},
    )
    redis_cache_size: int = field(
        default=100, metadata={"desc": "Redis 缓存条数", "env": "CCSERVER_REDIS_CACHE_SIZE"}
    )
    redis_ttl: int = field(
        default=86400, metadata={"desc": "Redis TTL 秒", "env": "CCSERVER_REDIS_TTL"}
    )
    global_config_dir: Path = field(
        default_factory=lambda: Path(_DEFAULT_GLOBAL_DIR),
        metadata={"desc": "全局配置目录", "env": "CCSERVER_GLOBAL_CONFIG_DIR"},
    )
    sessions_base: Path = field(
        default_factory=lambda: Path(_DEFAULT_GLOBAL_DIR) / "sessions",
        metadata={"desc": "sessions 根目录", "env": "CCSERVER_SESSIONS_DIR"},
    )
    db_path: Path = field(
        default_factory=lambda: Path(_DEFAULT_GLOBAL_DIR) / "ccserver.db",
        metadata={"desc": "sqlite db 路径", "env": "CCSERVER_DB_PATH"},
    )
    log_dir: Path = field(
        default_factory=lambda: Path(_DEFAULT_GLOBAL_DIR) / "logs",
        metadata={"desc": "日志目录", "env": "CCSERVER_LOG_DIR"},
    )
    log_level: str = field(
        default="DEBUG", metadata={"desc": "日志级别", "env": "CCSERVER_LOG_LEVEL"}
    )
    record_dir: Optional[Path] = field(
        default=None,
        metadata={"desc": "调试记录目录（设置即启用）", "env": "CCSERVER_RECORD_DIR"},
    )
    temp_dir: Path = field(
        default_factory=lambda: Path(_DEFAULT_TEMP_DIR),
        metadata={"desc": "系统临时目录", "env": ""},
    )
    project_dir: Optional[Path] = field(
        default=None,
        metadata={"desc": "进程级项目根目录（可选）", "env": "CCSERVER_PROJECT_DIR"},
    )

    # 哪些字段是路径（用于 from_dict/as_dict 转换）
    _PATH_FIELDS = (
        "global_config_dir", "sessions_base", "db_path",
        "log_dir", "record_dir", "temp_dir", "project_dir",
    )

    @classmethod
    def from_dict(cls, data: dict) -> "InfraConfig":
        data = data if isinstance(data, dict) else {}
        out = cls()
        for f in fields(cls):
            if f.name in data:
                val = data[f.name]
                if f.name in cls._PATH_FIELDS and val is not None:
                    val = Path(val)
                setattr(out, f.name, val)
        return out

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name in self._PATH_FIELDS and val is not None:
                val = str(val)
            out[f.name] = val
        return out


# ─── 顶层聚合 ────────────────────────────────────────────────────────────────


@dataclass
class CcServerConfig:
    """
    SESSION 作用域解析后的完整配置对象（唯一真相源）。

    各段独立、职责单一；from_dict 逐段构造，未给字段用默认（部分覆盖友好）。
    """

    model: ModelConfig = field(default_factory=ModelConfig)
    vlm: VlmConfig = field(default_factory=VlmConfig)
    agent: AgentBehaviorConfig = field(default_factory=AgentBehaviorConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    infra: InfraConfig = field(default_factory=InfraConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "CcServerConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            model=ModelConfig.from_dict(data.get("model", {})),
            vlm=VlmConfig.from_dict(data.get("vlm", {})),
            agent=AgentBehaviorConfig.from_dict(data.get("agent", {})),
            permissions=PermissionConfig.from_dict(data.get("permissions", {})),
            tools=ToolConfig.from_dict(data.get("tools", {})),
            compaction=CompactionConfig.from_dict(data.get("compaction", {})),
            infra=InfraConfig.from_dict(data.get("infra", {})),
        )

    def as_dict(self) -> dict:
        return {
            "model": self.model.as_dict(),
            "vlm": self.vlm.as_dict(),
            "agent": self.agent.as_dict(),
            "permissions": self.permissions.as_dict(),
            "tools": self.tools.as_dict(),
            "compaction": self.compaction.as_dict(),
            "infra": self.infra.as_dict(),
        }
