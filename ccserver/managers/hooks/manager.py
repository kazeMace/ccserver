"""
hook_loader — 加载并执行 Hook。

支持两条注册路径（并列，优先级相同层内等价）：

  路径一：settings.json 的 hooks 字段（Claude Code / ccserver 格式）
    {
      "hooks": {
        "PreToolUse": [                       ← CC 写法
          { "matcher": "Bash",
            "hooks": [{ "type": "command", "command": "echo test" }] }
        ],
        "tool:call:before": [                 ← ccserver 标准名写法
          { "hooks": [{ "type": "command", "command": "python check.py" }] }
        ]
      }
    }

  路径二：.ccserver/hooks/<name>/ 目录（OpenClaw HOOK.md 格式）
    .ccserver/hooks/my-hook/
      ├── HOOK.md      ← YAML 前置参数声明 events、requires 等
      └── handler.ts   ← 执行体（bun 运行）
      或 handler.py    ← 执行体（python 运行）
      或 handler.sh    ← 执行体（bash 运行）

  路径三：settings.json 的 hooks.internal（OpenClaw 控制面板格式）
    用于控制 HOOK.md 目录的启用/禁用和注入环境变量，不是事件注册。

注意：
  - 旧风格 A/B/C（文件名=事件名、函数名=事件名、def main()）已废弃，不再支持
  - OpenClaw 写法（before_tool_call）只在 HOOK.md 的 events 字段里使用，
    不能作为 settings.json 的 key
  - 所有事件名在加载时统一规范化为 ccserver 标准名（冒号分隔，如 tool:call:before）

执行器类型（type 字段）：
  command — 执行 shell 命令，stdin/stdout JSON 协议
  bun     — 执行 .ts/.js 文件（通过 bun 运行）
  prompt  — 调用 LLM 评估（返回 {ok, reason}）
  agent   — 启动独立 Agent 验证
  http    — POST 到远程 URL

加载优先级（从高到低）：
  1. {project_root}/.ccserver/settings.local.json  → hooks 字段
  2. {project_root}/.ccserver/hooks/<dir>/         → HOOK.md 目录扫描
  3. ~/.ccserver/settings.json                     → hooks 字段
  4. ~/.ccserver/hooks/<dir>/                      → HOOK.md 目录扫描
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field

import httpx
from pathlib import Path
from typing import Optional

from loguru import logger

from .matcher import HookMatcher, build_matcher


# ── 事件名规范化映射表 ─────────────────────────────────────────────────────────
#
# 支持三种写法，都映射到 ccserver 标准名（冒号分隔格式）：
#   CC 写法（PascalCase）：    PreToolUse
#   ccserver 标准名：          tool:call:before
#   OpenClaw 写法（下划线）：  before_tool_call（只在 HOOK.md events 字段出现）
#   旧别名（下划线，向下兼容）：tool_call_before
#
# 注意：OpenClaw 写法不能用作 settings.json 的 key，只在 HOOK.md 里使用。

# ccserver 标准名 → 事件信息
# mode 字段说明：
#   modifying — 默认并行执行，按顺序聚合返回值（可阻断、可修改输入）
#   observing — 默认并行执行，返回值忽略
#   claiming  — 默认并行执行，按顺序检查，第一个 handled=True 即停止后续
#
# 每个事件可指定默认执行策略（execution + collect），并被 hook/matcher 级配置覆盖：
#   execution — parallel（并行） / serial（串行）
#   collect   — all（全部聚合） / first（只取第一个成功结果） / last（只取最后一个成功结果）
KNOWN_EVENTS: dict[str, dict] = {
    # 工具生命周期
    "tool:call:before":          {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
    "tool:call:after":           {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "tool:call:failure":         {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "tool:permission:request":   {"mode": "modifying", "phase": "p1", "execution": "parallel", "collect": "all"},
    "tool:permission:denied":    {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "tool:result:persist":       {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要存储层支持

    # 提示词 / LLM 生命周期
    "prompt:llm:input":          {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "prompt:llm:output":         {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "prompt:llm:error":           {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},  # LLM 调用失败
    "prompt:build:before":       {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要拦截点
    "prompt:model:before":       {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要拦截点
    "prompt:agent:before":       {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要拦截点

    # 消息生命周期
    "message:inbound:claim":     {"mode": "claiming",  "phase": "p1", "execution": "parallel", "collect": "first"},
    "message:inbound:received":  {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
    "message:outbound:sending":  {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "message:outbound:sent":     {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "message:notify":            {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "message:transcribed":       {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "message:preprocessed":      {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "message:dispatch:before":   {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要网关
    "message:write:before":      {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要存储层

    # 会话生命周期
    "session:start":             {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "session:end":               {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "session:reset:before":      {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "session:patch":             {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "session:config:change":     {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "session:instructions:load": {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "session:elicitation":       {"mode": "modifying", "phase": "p1", "execution": "parallel", "collect": "all"},
    "session:elicitation:result":{"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},

    # Agent 生命周期
    "agent:stop":                {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "agent:stop:failure":        {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "agent:bootstrap":           {"mode": "modifying", "phase": "p1", "execution": "parallel", "collect": "all"},
    "agent:compact:before":      {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "agent:compact:after":       {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "agent:limit":               {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},

    # 子 Agent 生命周期
    "subagent:spawning":         {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "subagent:spawned":          {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "subagent:ended":            {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "subagent:teammate:idle":    {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "subagent:task:created":     {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "subagent:task:completed":   {"mode": "observing", "phase": "p1", "execution": "parallel", "collect": "all"},
    "subagent:delivery:target":  {"mode": "modifying", "phase": "p2", "execution": "parallel", "collect": "all"},  # 需要路由层

    # 文件系统（需要 file watcher，二期）
    "fs:file:changed":           {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
    "fs:cwd:changed":            {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
    "fs:worktree:create":        {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
    "fs:worktree:remove":        {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},

    # MCP lifecycle（MCP server 连接）
    "mcp:connect:before":         {"mode": "modifying", "phase": "p0", "execution": "parallel", "collect": "all"},
    "mcp:connect:success":        {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    "mcp:connect:failure":        {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},
    # MCP 工具调用失败，payload 额外带 server_name，比 tool:call:failure 更具体
    "mcp:call:failure":           {"mode": "observing", "phase": "p0", "execution": "parallel", "collect": "all"},

    # Gateway（需要 IM 网关集成，二期）
    "gateway:startup":           {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
    "gateway:start":             {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
    "gateway:stop":              {"mode": "observing", "phase": "p2", "execution": "parallel", "collect": "all"},
}

# 所有合法的 ccserver 标准名（用于快速查找）
_STANDARD_EVENT_NAMES: set[str] = set(KNOWN_EVENTS.keys())

# 其他写法 → ccserver 标准名（加载时做规范化）
#
# 包含三类：
#   1. CC 写法（PascalCase）
#   2. OpenClaw 写法（下划线，用于 HOOK.md events 字段）
#   3. 旧别名（下划线，向下兼容）
_OTHER_NAMES_TO_STANDARD: dict[str, str] = {
    # CC 写法
    "PreToolUse":            "tool:call:before",
    "PostToolUse":           "tool:call:after",
    "PostToolUseFailure":    "tool:call:failure",
    "PermissionRequest":     "tool:permission:request",
    "PermissionDenied":      "tool:permission:denied",
    "UserPromptSubmit":      "message:inbound:received",
    "Notification":          "message:notify",
    "SessionStart":          "session:start",
    "Setup":                 "session:start",
    "SessionEnd":            "session:end",
    "InstructionsLoaded":    "session:instructions:load",
    "ConfigChange":          "session:config:change",
    "Elicitation":           "session:elicitation",
    "ElicitationResult":     "session:elicitation:result",
    "Stop":                  "agent:stop",
    "StopFailure":           "agent:stop:failure",
    "PreCompact":            "agent:compact:before",
    "PostCompact":           "agent:compact:after",
    "SubagentStart":         "subagent:spawning",
    "SubagentStop":          "subagent:ended",
    "TeammateIdle":          "subagent:teammate:idle",
    "TaskCreated":           "subagent:task:created",
    "TaskCompleted":         "subagent:task:completed",
    "FileChanged":           "fs:file:changed",
    "CwdChanged":            "fs:cwd:changed",
    "WorktreeCreate":        "fs:worktree:create",
    "WorktreeRemove":        "fs:worktree:remove",

    # OpenClaw 写法（只在 HOOK.md events 字段里使用）
    "before_tool_call":      "tool:call:before",
    "after_tool_call":       "tool:call:after",
    "tool_result_persist":   "tool:result:persist",
    "before_prompt_build":   "prompt:build:before",
    "before_model_resolve":  "prompt:model:before",
    "before_agent_start":    "prompt:agent:before",
    "llm_input":             "prompt:llm:input",
    "llm_output":            "prompt:llm:output",
    "llm_error":             "prompt:llm:error",
    "agent_end":             "agent:stop",
    "inbound_claim":         "message:inbound:claim",
    "message_received":      "message:inbound:received",
    "message_sending":       "message:outbound:sending",
    "message_sent":          "message:outbound:sent",
    "before_message_write":  "message:write:before",
    "before_dispatch":       "message:dispatch:before",
    "before_reset":          "session:reset:before",
    "session_start":         "session:start",
    "session_end":           "session:end",
    "before_compaction":     "agent:compact:before",
    "after_compaction":      "agent:compact:after",
    "subagent_spawning":     "subagent:spawning",
    "subagent_spawned":      "subagent:spawned",
    "subagent_ended":        "subagent:ended",
    "subagent_delivery_target": "subagent:delivery:target",
    "gateway_start":         "gateway:start",
    "gateway_stop":          "gateway:stop",
    # HOOK.md 里用冒号格式的 OpenClaw 写法
    "message:received":      "message:inbound:received",
    "message:sent":          "message:outbound:sent",
    "session:compact:before":"agent:compact:before",
    "session:compact:after": "agent:compact:after",
    "agent:bootstrap":       "agent:bootstrap",
    "gateway:startup":       "gateway:startup",
    "command:new":           "session:start",
    "command:reset":         "session:reset:before",

    # 旧别名（向下兼容，原 KNOWN_EVENTS 里的 alias 字段）
    "pre_message":           "message:inbound:received",
    "post_message":          "prompt:llm:output",
    "agent_stop":            "agent:stop",
    "tool_call_before":      "tool:call:before",
    "tool_call_after":       "tool:call:after",
    "tool_call_failure":     "tool:call:failure",
    "session_end":           "session:end",
    "subagent_ended":        "subagent:ended",
    "agent_compact_before":  "agent:compact:before",
    "agent_compact_after":   "agent:compact:after",
    "agent_limit":           "agent:limit",
}


def normalize_event_name(name: str) -> Optional[str]:
    """
    将任意写法的事件名规范化为 ccserver 标准名。
    已经是标准名则直接返回，无法识别则返回 None。

    接受：
      - ccserver 标准名：tool:call:before
      - CC 写法：PreToolUse
      - OpenClaw 写法：before_tool_call（HOOK.md events 字段）
      - 旧别名：tool_call_before
    """
    # 已经是标准名
    if name in _STANDARD_EVENT_NAMES:
        return name
    # 其他写法查映射表
    return _OTHER_NAMES_TO_STANDARD.get(name)


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """
    Hook 执行时注入的上下文，包含当前代理和会话的基本状态。
    通过 stdin JSON 传给外部进程，也直接传给内部 callback。
    """
    session_id: str
    workdir: Path
    project_root: Path
    depth: int            # 0 = 根代理，>0 = 子代理
    agent_id: str
    agent_name: Optional[str]
    is_orchestrator: bool = False   # depth == 0 时为 True


@dataclass
class HookResult:
    """
    modifying / claiming 类型事件的返回值。
    observing 事件不需要返回 HookResult（返回值被忽略）。

    字段用途说明：

      通用流程控制：
        block        — True 时阻断工具调用或消息处理（exit code 2 也会设置此字段）
        block_reason — 阻断原因，注入给 LLM 作为工具错误结果
        continue_    — False 时阻止 Agent 最终停止（让模型继续运行，Stop hook 专用）

      message:inbound:received 用：
        message            — 替换用户消息内容（None = 不修改）
        additional_context — 追加给模型的额外上下文（拼接到消息后）

      tool:call:before 用：
        updated_input  — 替换工具输入参数（None = 不修改）
        additional_context — 注入给 LLM 的工具调用前说明

      message:inbound:claim 用（claiming 模式）：
        handled — True 表示本 handler 已处理该消息，停止后续 handler

      tool:permission:request 用：
        permission_behavior — 权限决策：deny / ask / allow / passthrough
          deny        → 拒绝执行（优先级最高）
          ask         → 继续弹窗询问（interactive）或自动拒绝（auto）
          allow       → 直接允许执行，跳过询问（优先级高于 ask/passthrough）
          passthrough → 默认行为，不干预

      系统消息（任意事件）：
        system_message — 注入给 LLM 的警告/提示（不影响用户可见内容）
    """
    # 流程控制
    block: bool = False
    block_reason: str = ""
    continue_: bool = True      # False = 阻止 Agent 停止

    # 内容修改
    message: Optional[str] = None           # 替换用户消息
    additional_context: Optional[str] = None  # 追加上下文
    updated_input: Optional[dict] = None    # 替换工具输入
    system_message: Optional[str] = None   # 注入给 LLM 的系统消息

    # 权限决策（tool:permission:request）
    permission_behavior: str = "passthrough"

    # claiming 模式专用
    handled: bool = False


@dataclass
class HookEntry:
    """
    一个已注册的 hook 执行单元。

    每个 HookEntry 对应 settings.json 里一个 hooks 数组项，
    或 HOOK.md 目录里一个 handler 文件。
    """
    event: str          # ccserver 标准事件名（如 tool:call:before）
    executor: dict      # 执行器配置（type、command/script/url/prompt 等字段）
    matcher: HookMatcher  # 匹配模式（如 ExpressionMatcher("tool == 'Bash' && ...")）
    env: dict           # 额外注入的环境变量
    timeout: int        # 超时秒数
    source: str         # 来源描述（用于日志，如文件路径或 settings key）
    execution: str = "parallel"   # parallel（并行） / serial（串行）
    collect: str = "all"          # all（全部聚合） / first（第一个成功结果） / last（最后一个成功结果）


# ── 加载器 ────────────────────────────────────────────────────────────────────


class HookLoader:
    """
    从多个来源加载 hook，提供 emit / emit_void 执行接口。

    加载顺序（同事件下，序号小的先执行）：
      1. 项目级 settings.json（hooks 字段）
      2. 项目级 HOOK.md 目录扫描
      3. 全局 settings.json（hooks 字段）
      4. 全局 HOOK.md 目录扫描

    同一优先级层内，不同写法（CC 写法/ccserver 标准名）的条目
    在规范化后合并到同一 handler 列表，按声明顺序排列。
    """

    def __init__(self) -> None:
        # 事件名 → handler 列表（按加载顺序，先加载的先执行）
        self._handlers: dict[str, list[HookEntry]] = {}

        # OpenClaw 控制面板状态
        # hook 目录名 → {"enabled": bool, "env": dict}
        self._openclaw_control: dict[str, dict] = {}

        # 额外 HOOK.md 扫描目录（来自 hooks.internal.load.extraDirs）
        self._extra_hook_dirs: list[Path] = []

    @classmethod
    def from_dirs(
        cls,
        project_root: Path,
        global_config_dir: Optional[Path] = None,
        project_settings: Optional[dict] = None,
        global_settings: Optional[dict] = None,
    ) -> "HookLoader":
        """
        根据项目和全局目录构建 HookLoader。

        project_settings / global_settings 是已解析的 settings.json 内容（dict），
        由调用方（settings.py）传入，避免重复读文件。
        """
        loader = cls()
        global_dir = global_config_dir or (Path.home() / ".ccserver")

        # 先处理 OpenClaw 控制面板（影响后续 HOOK.md 目录加载）
        if project_settings:
            loader._apply_openclaw_control_panel(project_settings, label="project")
        if global_settings:
            loader._apply_openclaw_control_panel(global_settings, label="global")

        # 按优先级顺序加载（高优先级先加载，排在 handler 列表前面）
        # 1. 项目级 settings.json
        if project_settings:
            loader._load_from_settings(project_settings, label="project-settings")

        # 2. 项目级 HOOK.md 目录
        loader._load_hook_md_dirs(project_root / ".ccserver" / "hooks", label="project-hooks")

        # 3. 全局 settings.json
        if global_settings:
            loader._load_from_settings(global_settings, label="global-settings")

        # 4. 全局 HOOK.md 目录
        loader._load_hook_md_dirs(global_dir / "hooks", label="global-hooks")

        # 5. 内置 HOOK.md 目录（最低优先级）
        builtin_hooks_dir = Path(__file__).parent.parent.parent / "builtins" / "hooks"
        loader._load_hook_md_dirs(builtin_hooks_dir, label="builtin-hooks")

        # 6. extraDirs（与全局 HOOK.md 同级）
        for extra_dir in loader._extra_hook_dirs:
            loader._load_hook_md_dirs(extra_dir, label=f"extra-hooks:{extra_dir}")

        return loader

    # ── 路径一：settings.json 解析 ────────────────────────────────────────────

    def _load_from_settings(self, settings_data: dict, label: str) -> None:
        """
        解析 settings.json 里的 hooks 字段（CC / ccserver 格式）。

        支持的事件名 key：CC 写法（PreToolUse）或 ccserver 标准名（tool:call:before）。
        不支持 OpenClaw 写法（before_tool_call）——那是 HOOK.md 专用。

        格式：
          {
            "hooks": {
              "PreToolUse": [
                {
                  "matcher": "Bash",
                  "hooks": [
                    { "type": "command", "command": "echo test", "timeout": 10 }
                  ]
                }
              ]
            }
          }
        """
        hooks_data = settings_data.get("hooks")
        if not hooks_data or not isinstance(hooks_data, dict):
            return

        for key, matchers in hooks_data.items():
            # 跳过 OpenClaw 控制面板字段（由 _apply_openclaw_control_panel 处理）
            if key == "internal":
                continue

            # 规范化事件名
            event = normalize_event_name(key)
            if event is None:
                logger.warning("settings hooks: unknown event name '{}' | source={}", key, label)
                continue

            if not isinstance(matchers, list):
                logger.warning("settings hooks: '{}' value must be a list | source={}", key, label)
                continue

            for matcher_item in matchers:
                if not isinstance(matcher_item, dict):
                    continue

                matcher = matcher_item.get("matcher", "")
                hook_list = matcher_item.get("hooks", [])
                if not isinstance(hook_list, list):
                    continue

                for hook_cfg in hook_list:
                    if not isinstance(hook_cfg, dict):
                        continue

                    entry = self._build_entry_from_config(
                        event=event,
                        hook_cfg=hook_cfg,
                        matcher=matcher,
                        source=f"{label}[{key}]",
                        matcher_execution=matcher_item.get("execution"),
                        matcher_collect=matcher_item.get("collect"),
                    )
                    if entry:
                        self._register(entry)

    def _build_entry_from_config(
        self,
        event: str,
        hook_cfg: dict,
        matcher: str,
        source: str,
        matcher_execution: Optional[str] = None,
        matcher_collect: Optional[str] = None,
    ) -> Optional[HookEntry]:
        """
        从 settings.json 的一条 hook 配置构建 HookEntry。

        hook_cfg 必须有 type 字段，支持：command、bun、prompt、agent、http。
        策略优先级：hook 级 > matcher 级 > 事件默认
        """
        hook_type = hook_cfg.get("type")
        if not hook_type:
            logger.warning("hook config missing 'type' field | source={}", source)
            return None

        valid_types = {"command", "bun", "prompt", "agent", "http"}
        if hook_type not in valid_types:
            logger.warning("hook config unknown type '{}' | source={}", hook_type, source)
            return None

        timeout = hook_cfg.get("timeout", 30)
        env = hook_cfg.get("env") or {}

        # 策略解析：hook 级 > matcher 级 > 事件默认
        event_meta = KNOWN_EVENTS.get(event, {})
        default_exec = event_meta.get("execution", "parallel")
        default_coll = event_meta.get("collect", "all")

        execution = hook_cfg.get("execution") or matcher_execution or default_exec
        collect = hook_cfg.get("collect") or matcher_collect or default_coll

        if execution not in ("parallel", "serial"):
            execution = default_exec
        if collect not in ("all", "first", "last"):
            collect = default_coll

        return HookEntry(
            event=event,
            executor=dict(hook_cfg),   # 保存完整配置，执行时按 type 分发
            matcher=build_matcher(matcher),
            env=env,
            timeout=timeout,
            source=source,
            execution=execution,
            collect=collect,
        )

    # ── 路径二：HOOK.md 目录扫描 ──────────────────────────────────────────────

    def _load_hook_md_dirs(self, hooks_dir: Path, label: str) -> None:
        """
        扫描目录下所有包含 HOOK.md 的子目录，加载为 HookEntry。

        每个子目录是一个 hook，目录名是 hook 名称。
        """
        if not hooks_dir.exists() or not hooks_dir.is_dir():
            return

        for hook_dir in sorted(hooks_dir.iterdir()):
            if not hook_dir.is_dir():
                continue
            hook_md = hook_dir / "HOOK.md"
            if not hook_md.exists():
                continue
            self._load_hook_md_entry(hook_dir, hook_md, label=label)

    def _load_hook_md_entry(self, hook_dir: Path, hook_md: Path, label: str) -> None:
        """
        加载一个 HOOK.md 目录。

        先读 HOOK.md 的 YAML 前置参数，检查资格（requires），
        再找 handler 文件，按后缀决定执行器类型。
        """
        hook_name = hook_dir.name

        # 检查 OpenClaw 控制面板：如果该 hook 被显式禁用则跳过
        control = self._openclaw_control.get(hook_name, {})
        if control.get("enabled") is False:
            logger.debug("hook disabled by control panel | hook={} source={}", hook_name, label)
            return

        # 解析 HOOK.md 前置参数
        meta = self._parse_hook_md(hook_md)
        if meta is None:
            logger.warning("failed to parse HOOK.md | path={}", hook_md)
            return

        # 资格检查：requires.bins / requires.env
        if not self._check_requirements(meta, hook_name):
            return

        # 找 handler 文件（按优先级：.ts > .js > .py > .sh）
        handler_file = self._find_handler_file(hook_dir)
        if handler_file is None:
            logger.warning("no handler file found in hook dir | path={}", hook_dir)
            return

        # 确定执行器类型
        executor_type = _get_executor_type(handler_file)

        # 控制面板注入的额外环境变量
        extra_env = control.get("env") or {}

        # 注册到每个声明的事件
        events_raw = meta.get("events", [])
        if not events_raw:
            logger.warning("HOOK.md has no events declared | hook={} path={}", hook_name, hook_md)
            return

        for raw_event in events_raw:
            event = normalize_event_name(str(raw_event))
            if event is None:
                logger.warning("HOOK.md unknown event '{}' | hook={} path={}", raw_event, hook_name, hook_md)
                continue

            executor = {
                "type": executor_type,
                "script": str(handler_file),
                "export": meta.get("export", "default"),
            }

            event_meta = KNOWN_EVENTS.get(event, {})
            execution = meta.get("execution") or event_meta.get("execution", "parallel")
            collect = meta.get("collect") or event_meta.get("collect", "all")
            if execution not in ("parallel", "serial"):
                execution = "parallel"
            if collect not in ("all", "first", "last"):
                collect = "all"

            entry = HookEntry(
                event=event,
                executor=executor,
                matcher=build_matcher(""),    # HOOK.md 不支持 matcher，无条件触发
                env=extra_env,
                timeout=30,
                source=f"{label}/{hook_name}",
                execution=execution,
                collect=collect,
            )
            self._register(entry)
            logger.debug("hook loaded from HOOK.md | event={} hook={} type={} path={}",
                         event, hook_name, executor_type, handler_file)

    def _parse_hook_md(self, hook_md: Path) -> Optional[dict]:
        """
        解析 HOOK.md 的 YAML 前置参数（--- 之间的内容）。
        --- 之后的 Markdown 内容完全忽略（仅供人阅读）。

        返回 metadata.openclaw 字段的内容（dict），失败返回 None。
        """
        from ...utils import parse_frontmatter

        try:
            text = hook_md.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("failed to read HOOK.md | path={} error={}", hook_md, e)
            return None

        meta, _ = parse_frontmatter(text)
        if meta is None:
            logger.warning("HOOK.md frontmatter parse failed | path={}", hook_md)
            return None

        # metadata.openclaw 里的内容才是我们需要的
        metadata = meta.get("metadata")
        if isinstance(metadata, dict):
            openclaw = metadata.get("openclaw")
            if isinstance(openclaw, dict):
                return openclaw

        # 兼容：有些 HOOK.md 直接在顶层写字段（非标准但常见）
        if "events" in meta:
            return meta

        return {}

    def _check_requirements(self, meta: dict, hook_name: str) -> bool:
        """
        检查 requires 字段声明的运行条件。
        any 条件不满足时跳过加载（不报错，只记 debug 日志）。

        always: true 时跳过所有检查。
        """
        if meta.get("always"):
            return True

        requires = meta.get("requires") or {}

        # requires.bins：所有命令必须在 PATH 中存在
        for bin_name in requires.get("bins") or []:
            if not _check_bin_exists(bin_name):
                logger.debug("hook skipped: required binary '{}' not found | hook={}", bin_name, hook_name)
                return False

        # requires.anyBins：至少一个命令存在
        any_bins = requires.get("anyBins") or []
        if any_bins and not any(_check_bin_exists(b) for b in any_bins):
            logger.debug("hook skipped: none of anyBins {} found | hook={}", any_bins, hook_name)
            return False

        # requires.env：所有环境变量必须有值
        import os
        for env_name in requires.get("env") or []:
            if not os.environ.get(env_name):
                logger.debug("hook skipped: required env '{}' not set | hook={}", env_name, hook_name)
                return False

        # os 限制：当前系统必须在列表中
        allowed_os = meta.get("os") or []
        if allowed_os:
            import platform
            current_os = platform.system().lower()  # darwin / linux / windows
            # 标准化：win32 和 windows 都识别
            if current_os == "windows":
                current_os = "win32"
            if current_os not in [o.lower() for o in allowed_os]:
                logger.debug("hook skipped: os '{}' not in allowed list {} | hook={}",
                             current_os, allowed_os, hook_name)
                return False

        return True

    def _find_handler_file(self, hook_dir: Path) -> Optional[Path]:
        """
        在 hook 目录中查找 handler 文件。
        优先级：handler.ts > handler.js > handler.py > handler.sh
        """
        for suffix in [".ts", ".js", ".py", ".sh"]:
            candidate = hook_dir / f"handler{suffix}"
            if candidate.exists():
                return candidate
        return None

    # ── 路径三：OpenClaw 控制面板 ─────────────────────────────────────────────

    def _apply_openclaw_control_panel(self, settings_data: dict, label: str) -> None:
        """
        解析 settings.json 里的 hooks.internal 字段（OpenClaw 控制面板格式）。

        这不是事件注册路径，而是对 HOOK.md 目录的开关控制。
        必须在 _load_hook_md_dirs() 之前调用，否则控制不生效。

        支持的字段：
          hooks.internal.enabled          — 全局启用/禁用 HOOK.md 目录加载
          hooks.internal.entries.{name}   — 针对具体 hook 目录的控制
            .enabled                      — 是否加载该目录
            .env                          — 注入的额外环境变量
          hooks.internal.load.extraDirs   — 追加额外扫描目录

        旧版兼容（handlers 数组格式）：
          hooks.internal.handlers[].event / .module / .export
          等价于一个 HOOK.md 目录的注册，直接转换为 HookEntry
        """
        internal = settings_data.get("hooks", {}).get("internal")
        if not internal or not isinstance(internal, dict):
            return

        # 全局禁用
        if internal.get("enabled") is False:
            logger.debug("hooks.internal disabled globally | source={}", label)
            # 用特殊 key 标记全局禁用（_load_hook_md_dirs 检查此标记）
            self._openclaw_control["__disabled__"] = {"enabled": False}
            return

        # 逐条 entry 控制
        entries = internal.get("entries") or {}
        for hook_name, entry_cfg in entries.items():
            if not isinstance(entry_cfg, dict):
                continue
            self._openclaw_control[hook_name] = {
                "enabled": entry_cfg.get("enabled", True),
                "env": entry_cfg.get("env") or {},
            }

        # extraDirs
        extra_dirs = internal.get("load", {}).get("extraDirs") or []
        for d in extra_dirs:
            p = Path(d)
            if p.exists():
                self._extra_hook_dirs.append(p)
            else:
                logger.warning("hooks.internal.load.extraDirs: path not found '{}' | source={}", d, label)

        # 旧版 handlers 数组（向后兼容）
        handlers = internal.get("handlers") or []
        for handler_cfg in handlers:
            if not isinstance(handler_cfg, dict):
                continue
            raw_event = handler_cfg.get("event", "")
            module_path = handler_cfg.get("module", "")
            export_name = handler_cfg.get("export", "default")

            if not raw_event or not module_path:
                continue

            event = normalize_event_name(raw_event)
            if event is None:
                logger.warning("hooks.internal.handlers: unknown event '{}' | source={}", raw_event, label)
                continue

            script_path = Path(module_path)
            executor_type = _get_executor_type(script_path)

            entry = HookEntry(
                event=event,
                executor={"type": executor_type, "script": module_path, "export": export_name},
                matcher=build_matcher(""),
                env={},
                timeout=30,
                source=f"{label}/internal-handlers",
            )
            self._register(entry)
            logger.debug("hook loaded from internal handlers | event={} module={}", event, module_path)

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def _register(self, entry: HookEntry) -> None:
        """注册一个 HookEntry，追加到对应事件的 handler 列表末尾。"""
        if entry.event not in self._handlers:
            self._handlers[entry.event] = []
        self._handlers[entry.event].append(entry)

    # ── 执行 ──────────────────────────────────────────────────────────────────

    async def emit(self, event: str, payload: dict, ctx: HookContext) -> HookResult:
        """
        触发 modifying 或 claiming 类型事件。

        支持按 entry 的 execution / collect 策略灵活控制：
          execution=parallel — 所有 handler 一起启动
          execution=serial   — 前一个完成后再启动下一个
          collect=all        — 按顺序聚合所有结果（_merge_results）
          collect=first      — 只返回第一个成功的 HookResult
          collect=last       — 只返回最后一个成功的 HookResult

        默认与 Claude Code 保持一致（parallel + all）。
        """
        entries = self._get_matching_entries(event, payload)
        if not entries:
            return HookResult()

        event_mode = KNOWN_EVENTS.get(event, {}).get("mode", "observing")
        execution, collect = self._resolve_strategy(entries, event)

        results: list[tuple[HookEntry, Optional[HookResult]]] = []

        if execution == "serial":
            for entry in entries:
                try:
                    raw = await self._call_entry(entry, payload, ctx)
                    results.append((entry, raw))
                except Exception as e:
                    logger.error("hook error | event={} source={} error={}", event, entry.source, e)
                    results.append((entry, None))
        else:
            # parallel：同时启动，按 entries 顺序收集
            tasks = [self._call_entry(entry, payload, ctx) for entry in entries]
            completed = await asyncio.gather(*tasks, return_exceptions=True)
            for entry, raw in zip(entries, completed):
                if isinstance(raw, Exception):
                    logger.error("hook error | event={} source={} error={}", event, entry.source, raw)
                    results.append((entry, None))
                else:
                    results.append((entry, raw))

        # 根据 collect 策略返回结果
        if collect == "first":
            for entry, raw in results:
                if raw is not None:
                    return self._post_process_result(raw, event_mode, entry)
            return HookResult()

        if collect == "last":
            for entry, raw in reversed(results):
                if raw is not None:
                    return self._post_process_result(raw, event_mode, entry)
            return HookResult()

        # collect == "all"：顺序聚合
        result = HookResult()
        for entry, raw in results:
            result = _merge_results(result, raw)
            # modifying：block=True 时短路（ccserver 特色语义）
            if event_mode == "modifying" and result.block:
                logger.debug("hook blocked event | event={} source={}", event, entry.source)
                break
            # claiming：handled=True 时短路
            if event_mode == "claiming" and result.handled:
                logger.debug("hook claimed event | event={} source={}", event, entry.source)
                break
        return result

    async def emit_void(self, event: str, payload: dict, ctx: HookContext) -> None:
        """
        触发 observing 类型事件。

        支持 parallel / serial 策略控制。
        返回值始终被忽略。
        """
        entries = self._get_matching_entries(event, payload)
        if not entries:
            return

        execution, _ = self._resolve_strategy(entries, event)

        if execution == "serial":
            for entry in entries:
                try:
                    await self._call_entry(entry, payload, ctx)
                except Exception as e:
                    logger.error("hook error (void) | event={} source={} error={}", event, entry.source, e)
            return

        async def run_one(entry: HookEntry) -> None:
            try:
                await self._call_entry(entry, payload, ctx)
            except Exception as e:
                logger.error("hook error (void) | event={} source={} error={}", event, entry.source, e)

        await asyncio.gather(*[run_one(e) for e in entries])

    def _resolve_strategy(self, entries: list[HookEntry], event: str) -> tuple[str, str]:
        """确定一组 entries 的统一执行策略。以第一个 entry 为准，混合时发出警告。"""
        if not entries:
            event_meta = KNOWN_EVENTS.get(event, {})
            return event_meta.get("execution", "parallel"), event_meta.get("collect", "all")

        execution = entries[0].execution
        collect = entries[0].collect

        for entry in entries[1:]:
            if entry.execution != execution or entry.collect != collect:
                logger.warning(
                    "mixed hook strategies detected | event={} using first(entry.execution={}, entry.collect={})",
                    event, execution, collect,
                )
                break
        return execution, collect

    def _post_process_result(self, result: HookResult, event_mode: str, entry: HookEntry) -> HookResult:
        """first/last 收集时，按 event_mode 做最终短路标记检查（用于日志）。"""
        if event_mode == "modifying" and result.block:
            logger.debug("hook blocked event | event={} source={}", entry.event, entry.source)
        elif event_mode == "claiming" and result.handled:
            logger.debug("hook claimed event | event={} source={}", entry.event, entry.source)
        return result

    def _get_matching_entries(self, event: str, payload: dict) -> list[HookEntry]:
        """
        获取该事件下所有满足 matcher + if 条件的 handler。

        matcher 是 CC 格式的事件级筛选，支持：
          - 空字符串或 "*"：无条件匹配
          - 精确匹配："Bash"
          - 多值精确匹配："Write|Edit|Bash"
          - 正则匹配："^Write.*"（包含非 [a-zA-Z0-9_|] 字符时视为正则）

        if 是更细粒度的工具输入级筛选（权限规则语法），支持：
          - ToolName                 → 只匹配工具名
          - ToolName(pattern)        → 匹配工具名且输入符合 pattern
          - "Bash(git *)" 等通配符匹配
        """
        all_entries = self._handlers.get(event, [])
        if not all_entries:
            return []

        result = []
        for entry in all_entries:
            if not entry.matcher.match(payload):
                continue
            if not _match_if(entry.executor.get("if"), payload):
                continue
            result.append(entry)

        return result

    async def _call_entry(self, entry: HookEntry, payload: dict, ctx: HookContext) -> Optional[HookResult]:
        """
        根据执行器类型调用 handler，返回 HookResult 或 None。
        每个执行器都有 timeout 控制，超时视为非阻断错误。

        async: true 时 fire-and-forget，后台执行，不阻塞主流程，返回值被忽略。
        """
        is_async = entry.executor.get("async", False)
        if is_async:
            # fire-and-forget：启动后台任务，立刻返回 None
            asyncio.create_task(
                self._run_entry_with_error_logging(entry, payload, ctx)
            )
            return None

        return await self._run_entry_sync(entry, payload, ctx)

    async def _run_entry_sync(self, entry: HookEntry, payload: dict, ctx: HookContext) -> Optional[HookResult]:
        """同步执行 hook（非 async 模式），支持 timeout。"""
        exec_type = entry.executor.get("type")
        try:
            if exec_type == "command":
                return await asyncio.wait_for(
                    self._run_command(entry, payload, ctx),
                    timeout=entry.timeout,
                )
            elif exec_type == "bun":
                return await asyncio.wait_for(
                    self._run_bun(entry, payload, ctx),
                    timeout=entry.timeout,
                )
            elif exec_type == "http":
                return await asyncio.wait_for(
                    self._run_http(entry, payload, ctx),
                    timeout=entry.timeout,
                )
            elif exec_type == "prompt":
                return await asyncio.wait_for(
                    self._run_prompt(entry, payload, ctx),
                    timeout=entry.timeout,
                )
            elif exec_type == "agent":
                logger.debug("executor type 'agent' not yet implemented | source={}", entry.source)
                return None
            else:
                logger.warning("unknown executor type '{}' | source={}", exec_type, entry.source)
                return None

        except asyncio.TimeoutError:
            logger.error("hook timed out after {}s | event={} source={}", entry.timeout, entry.event, entry.source)
            return None

    async def _run_entry_with_error_logging(self, entry: HookEntry, payload: dict, ctx: HookContext) -> None:
        """包装 async hook，保证异常被捕获不传播。"""
        try:
            await self._run_entry_sync(entry, payload, ctx)
        except Exception as e:
            logger.error("async hook error | event={} source={} error={}", entry.event, entry.source, e)

    # ── command 执行器 ────────────────────────────────────────────────────────

    async def _run_command(self, entry: HookEntry, payload: dict, ctx: HookContext) -> HookResult:
        """
        执行 command 类型 hook：启动子进程，通过 stdin 传入 JSON payload，
        从 stdout 读取 JSON 结果。

        stdin payload 格式：基础字段（session_id、cwd 等）+ 事件专属字段（payload 参数）。
        stdout 格式：JSON，见 _parse_stdout_json()。
        exit code 语义：0 = 成功，2 = 阻断，其他非零 = 非阻断错误（只记日志）。
        """
        command = entry.executor.get("command", "")
        script = entry.executor.get("script", "")

        # HOOK.md 的 .py/.sh 文件没有 command 字段，只有 script 字段
        # 自动构建 command：python script.py 或 bash script.sh
        if not command and script:
            script_path = Path(script)
            if script_path.suffix == ".py":
                command = f"python {script}"
            elif script_path.suffix == ".sh":
                command = f"bash {script}"
            else:
                command = script

        if not command:
            logger.warning("command executor missing 'command' and 'script' field | source={}", entry.source)
            return HookResult()

        shell = entry.executor.get("shell", "bash")
        stdin_data = _build_stdin_payload(entry.event, payload, ctx)

        # 合并环境变量
        import os
        env = {**os.environ, **entry.env}
        logger.debug("hook env | CONVERSATION_ID={} command={}", os.environ.get("CONVERSATION_ID", "<missing>"), command)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(ctx.project_root),
                executable="/bin/bash" if shell == "bash" else None,
            )
        except Exception as e:
            logger.error("failed to start command hook | source={} error={}", entry.source, e)
            return HookResult()

        stdin_bytes = json.dumps(stdin_data, ensure_ascii=False).encode("utf-8")
        stdout_bytes, stderr_bytes = await proc.communicate(stdin_bytes)

        return _process_subprocess_output(
            returncode=proc.returncode,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            source=entry.source,
        )

    # ── bun 执行器 ────────────────────────────────────────────────────────────

    async def _run_bun(self, entry: HookEntry, payload: dict, ctx: HookContext) -> HookResult:
        """
        执行 bun 类型 hook：通过 bun 运行 .ts/.js 文件。

        使用框架内置的 bun_wrapper.ts 作为适配层，负责：
          1. 读取 stdin JSON payload（ccserver 格式）
          2. 转换为 OpenClaw 的 event 对象格式
          3. 调用用户的 handler 函数
          4. 把 handler 结果转换回 ccserver stdout JSON 格式

        这样用户的 handler.ts 可以直接使用 OpenClaw 的 API（event.messages.push 等），
        无需了解 ccserver 的 stdin/stdout 协议。
        """
        script = entry.executor.get("script", "")
        if not script:
            logger.warning("bun executor missing 'script' field | source={}", entry.source)
            return HookResult()

        script_path = Path(script)
        if not script_path.is_absolute():
            script_path = ctx.project_root / script_path
        if not script_path.exists():
            logger.warning("bun script not found | path={} source={}", script, entry.source)
            return HookResult()

        # bun_wrapper.ts 位于 ccserver/managers/hooks/ 目录下
        wrapper = Path(__file__).parent / "bun_wrapper.ts"
        if not wrapper.exists():
            logger.error("bun_wrapper.ts not found | expected={}", wrapper)
            return HookResult()

        export_name = entry.executor.get("export", "default")
        stdin_data = _build_stdin_payload(entry.event, payload, ctx)

        import os
        env = {
            **os.environ,
            **entry.env,
            "HOOK_SCRIPT": str(script_path.resolve()),
            "HOOK_EXPORT": export_name,
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                "bun", "run", str(wrapper),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(ctx.project_root),
            )
        except FileNotFoundError:
            logger.error("'bun' not found in PATH. Install bun to use TypeScript hooks.")
            return HookResult()
        except Exception as e:
            logger.error("failed to start bun hook | source={} error={}", entry.source, e)
            return HookResult()

        stdin_bytes = json.dumps(stdin_data, ensure_ascii=False).encode("utf-8")
        stdout_bytes, stderr_bytes = await proc.communicate(stdin_bytes)

        return _process_subprocess_output(
            returncode=proc.returncode,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            source=entry.source,
        )

    # ── http 执行器 ───────────────────────────────────────────────────────────

    async def _run_http(self, entry: HookEntry, payload: dict, ctx: HookContext) -> HookResult:
        """
        执行 http 类型 hook：将 stdin JSON payload 作为请求体发送到远程 URL，
        把响应 JSON 解析为 HookResult。

        executor 配置字段：
          - url     （必填）目标 URL
          - method  （选填，默认 POST）HTTP 方法
          - headers （选填）额外请求头字典
        """
        url = entry.executor.get("url", "")
        method = entry.executor.get("method", "POST")
        headers = entry.executor.get("headers") or {}
        if not url:
            logger.warning("http executor missing 'url' field | source={}", entry.source)
            return HookResult()

        data = _build_stdin_payload(entry.event, payload, ctx)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    json=data,
                    timeout=entry.timeout,
                )
                if response.status_code >= 400:
                    logger.warning(
                        "http hook returned error status | source={} status={} body={}",
                        entry.source,
                        response.status_code,
                        response.text[:200],
                    )
                    return HookResult()
                resp_data = response.json()
                if isinstance(resp_data, dict):
                    return _parse_stdout_json(resp_data, entry.source)
                return HookResult()
        except Exception as e:
            logger.error("http hook error | source={} error={}", entry.source, e)
            return HookResult()

    # ── prompt 执行器 ──────────────────────────────────────────────────────────

    async def _run_prompt(self, entry: HookEntry, payload: dict, ctx: HookContext) -> HookResult:
        """
        执行 prompt 类型 hook：调用 LLM 评估事件/输入，返回 HookResult。

        executor 配置字段：
          - prompt      （必填）提示词模板，支持 {tool_name}、{tool_input} 等占位符
          - model       （选填，默认 claude-3-5-sonnet-20241022）
          - system      （选填）系统提示
          - max_tokens  （选填，默认 1024）
        """
        prompt_template = entry.executor.get("prompt", "")
        if not prompt_template:
            logger.warning("prompt executor missing 'prompt' field | source={}", entry.source)
            return HookResult()

        model = entry.executor.get("model", "claude-3-5-sonnet-20241022")
        system = entry.executor.get("system", "")
        max_tokens = entry.executor.get("max_tokens", 1024)

        variables = {
            "tool_name": payload.get("tool_name", ""),
            "tool_input": json.dumps(payload.get("tool_input", {}), ensure_ascii=False),
            "tool_output": payload.get("tool_response", ""),
            "tool_use_id": payload.get("tool_use_id", ""),
            "event": entry.event,
            "agent_name": ctx.agent_name or "",
        }
        try:
            user_prompt = prompt_template.format(**variables)
        except Exception as e:
            logger.error("prompt template format failed | source={} error={}", entry.source, e)
            return HookResult()

        messages = [{"role": "user", "content": user_prompt}]
        try:
            from ccserver.model import get_adapter
            adapter = get_adapter()
            response = await adapter.create(
                model=model,
                system=system or None,
                messages=messages,
                max_tokens=max_tokens,
            )
            text = ""
            if response.content:
                text = response.content[0].text.strip()
        except Exception as e:
            logger.error("prompt llm call failed | source={} error={}", entry.source, e)
            return HookResult()

        if text.startswith("{"):
            try:
                data = json.loads(text)
                return _parse_stdout_json(data, entry.source)
            except Exception:
                pass

        return HookResult(system_message=text)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _get_executor_type(handler_file: Path) -> str:
    """根据文件后缀决定执行器类型。"""
    suffix = handler_file.suffix.lower()
    if suffix in (".ts", ".js"):
        return "bun"
    elif suffix == ".py":
        return "command"
    elif suffix == ".sh":
        return "command"
    return "command"


def _check_bin_exists(bin_name: str) -> bool:
    """检查命令是否在 PATH 中存在。"""
    try:
        result = subprocess.run(
            ["which", bin_name],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


def _build_stdin_payload(event: str, payload: dict, ctx: HookContext) -> dict:
    """
    构造传给子进程 stdin 的 JSON payload。

    基础字段来自 HookContext，事件专属字段来自 payload 参数。
    两者合并，基础字段不会被 payload 覆盖。
    """
    base = {
        "hook_event_name": event,
        "session_id": ctx.session_id,
        "cwd": str(ctx.workdir),
        "project_root": str(ctx.project_root),
        "depth": ctx.depth,
        "is_orchestrator": ctx.is_orchestrator,
        "agent_id": ctx.agent_id,
        "agent_name": ctx.agent_name,
    }
    # payload 里的字段追加进去（如 tool_name、tool_input 等）
    # 基础字段不会被覆盖
    merged = {**payload, **base}
    return merged


def _process_subprocess_output(
    returncode: int,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    source: str,
) -> HookResult:
    """
    解析子进程的退出码和输出，返回 HookResult。

    exit code 语义：
      0       — 成功，解析 stdout JSON
      2       — 阻断，stderr 内容作为 block_reason
      其他非零 — 非阻断错误，只记日志，流程继续
    """
    # exit code 2：阻断
    if returncode == 2:
        reason = stderr_bytes.decode("utf-8", errors="replace").strip()
        logger.debug("hook blocked (exit 2) | source={} reason={}", source, reason)
        return HookResult(block=True, block_reason=reason)

    # 其他非零：非阻断错误
    if returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace").strip()
        logger.warning("hook non-zero exit | source={} code={} stderr={}", source, returncode, err)
        return HookResult()

    # exit 0：解析 stdout JSON
    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not stdout_text:
        return HookResult()

    if not stdout_text.startswith("{"):
        # 不是 JSON，忽略（有些脚本会输出非结构化内容）
        return HookResult()

    try:
        data = json.loads(stdout_text)
        return _parse_stdout_json(data, source)
    except Exception as e:
        logger.warning("hook stdout not valid JSON | source={} error={}", source, e)
        return HookResult()


def _parse_stdout_json(data: dict, source: str) -> HookResult:
    """
    解析 hook 脚本的 stdout JSON，转换为 HookResult。

    支持 Claude Code 的 hookSpecificOutput 格式，也支持直接写顶层字段。

    完整格式参见设计文档第七节（stdout 输出统一超集格式）。
    """
    result = HookResult()

    # continue: false → 阻止 Agent 停止（Stop hook 专用）
    if data.get("continue") is False:
        result.continue_ = False

    # suppressOutput：忽略 stdout（不做特殊处理，已经读了）

    # hookSpecificOutput 里的字段
    hook_output = data.get("hookSpecificOutput") or {}

    # additionalContext：追加给 LLM 的上下文
    additional_context = hook_output.get("additionalContext", "")
    if additional_context:
        result.additional_context = additional_context

    # updatedInput：替换工具输入（tool:call:before 专用）
    updated_input = hook_output.get("updatedInput")
    if updated_input and isinstance(updated_input, dict):
        result.updated_input = updated_input

    # permissionDecision：权限决策（tool:permission:request 专用）
    permission = hook_output.get("permissionDecision", "")
    if permission in ("block", "deny"):
        result.block = True
        result.block_reason = hook_output.get("permissionDecisionReason", "blocked by hook")
        result.permission_behavior = "deny"
    elif permission in ("allow", "ask", "passthrough"):
        result.permission_behavior = permission

    # 也支持新格式 permissionBehavior（ccserver 原生字段）
    if "permissionBehavior" in hook_output:
        behavior = hook_output.get("permissionBehavior")
        if behavior in ("deny", "ask", "allow", "passthrough"):
            if behavior == "deny":
                result.block = True
                result.block_reason = hook_output.get("permissionBehaviorReason", "blocked by hook")
            result.permission_behavior = behavior

    # systemMessage：注入给 LLM 的系统消息
    system_msg = data.get("systemMessage", "")
    if system_msg:
        result.system_message = system_msg

    # handled：claiming 模式专用
    if hook_output.get("handled"):
        result.handled = True

    return result


def _merge_results(base: HookResult, new_result: Optional[HookResult]) -> HookResult:
    """
    合并两个 HookResult。按字段单独指定聚合规则：

      block          — first-wins：任一 True 即生效，不被后续覆盖
      block_reason   — 跟随第一个 block=True 的值
      continue_      — 粘性 False：任一 False 即生效
      message        — last-wins：最后一个非 None 值生效
      additional_context — concat：换行拼接所有非空值
      updated_input  — last-wins：最后一个非 None 值生效
      system_message — first-wins：第一个非空值生效
      permission_behavior — 优先级制：deny(3) > ask(2) > allow(1) > passthrough(0)
      handled        — first-wins：第一个 True 即生效（claiming 模式短路）
    """
    if new_result is None:
        return base

    result = HookResult(
        block=base.block,
        block_reason=base.block_reason,
        continue_=base.continue_,
        message=base.message,
        additional_context=base.additional_context,
        updated_input=base.updated_input,
        system_message=base.system_message,
        permission_behavior=base.permission_behavior,
        handled=base.handled,
    )

    # block：first-wins
    if not result.block and new_result.block:
        result.block = True
        result.block_reason = new_result.block_reason

    # continue_：粘性 False
    if not new_result.continue_:
        result.continue_ = False

    # message：last-wins（None 不覆盖）
    if new_result.message is not None:
        result.message = new_result.message

    # additional_context：concat（换行拼接）
    if new_result.additional_context:
        if result.additional_context:
            result.additional_context = result.additional_context + "\n" + new_result.additional_context
        else:
            result.additional_context = new_result.additional_context

    # updated_input：last-wins（None 不覆盖）
    if new_result.updated_input is not None:
        result.updated_input = new_result.updated_input

    # system_message：first-wins（base 已有则不覆盖）
    if not result.system_message and new_result.system_message:
        result.system_message = new_result.system_message

    # permission_behavior：优先级制 deny(3) > ask(2) > allow(1) > passthrough(0)
    _PERM_PRIORITY = {"deny": 3, "ask": 2, "allow": 1, "passthrough": 0}
    current_p = _PERM_PRIORITY.get(result.permission_behavior, 0)
    new_p = _PERM_PRIORITY.get(new_result.permission_behavior, 0)
    if new_p > current_p:
        result.permission_behavior = new_result.permission_behavior

    # handled：first-wins（claiming 模式）
    if new_result.handled:
        result.handled = True

    return result


def _match_if(if_rule: Optional[str], payload: dict) -> bool:
    """
    匹配 if 条件（权限规则语法）。

    格式：
      - ToolName                 -> 只匹配工具名
      - ToolName(pattern)        -> 匹配工具名且输入内容符合 pattern
      - ToolName:*               -> legacy 前缀语法

    示例：
      "Bash(git *)"  -> 匹配 Bash 工具，且命令以 "git " 开头或恰好是 "git"
      "Write(*.ts)"  -> 匹配 Write 工具，且文件路径匹配 *.ts
      "Read"         -> 匹配 Read 工具，不限制输入
    """
    if not if_rule:
        return True

    tool_name = payload.get("tool_name", "")
    if not tool_name:
        return True

    parsed = _parse_permission_rule(if_rule)
    if parsed is None:
        return True

    # 工具名必须匹配
    if parsed["tool_name"] != tool_name:
        return False

    # 没有 pattern，只匹配工具名
    if parsed["pattern"] is None:
        return True

    pattern = parsed["pattern"]

    # 根据工具类型取要匹配的字段
    if tool_name == "Bash":
        command = payload.get("tool_input", {}).get("command", "")
        return _match_bash_command(pattern, command)
    elif tool_name in ("Read", "Write", "Glob"):
        file_path = payload.get("tool_input", {}).get("file_path", "")
        return _match_wildcard_pattern(pattern, file_path)
    elif tool_name == "Edit":
        tool_input = payload.get("tool_input", {})
        # Edit 的 file_path 和 old_string 都参与匹配
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        return (
            _match_wildcard_pattern(pattern, file_path)
            or _match_wildcard_pattern(pattern, old_string)
        )
    elif tool_name == "Grep":
        grep_pattern = payload.get("tool_input", {}).get("pattern", "")
        return _match_wildcard_pattern(pattern, grep_pattern)

    # 其他工具：还没有准备专门的 matcher，默认放行（fail-safe）
    return True


def _parse_permission_rule(rule: str) -> Optional[dict]:
    r"""
    解析权限规则字符串，返回 {"tool_name": str, "pattern": Optional[str]}。

    处理转义：\( 和 \) 视为字面量括号。
    r"""
    # 先提取转义括号，临时替换掉
    placeholders = []
    temp = rule

    import re

    def replace_escaped(m: "re.Match") -> str:
        placeholders.append(m.group(0)[1])  # \) -> )
        return f"\x00{len(placeholders) - 1}\x00"

    temp = re.sub(r"\\([()])", replace_escaped, temp)

    # 找第一个未转义的 (
    if "(" not in temp:
        return {"tool_name": rule.strip(), "pattern": None}

    tool_name, _, rest = temp.partition("(")
    tool_name = tool_name.strip()

    if ")" not in rest:
        # 括号不匹配，回退到只匹配工具名
        return {"tool_name": rule.strip(), "pattern": None}

    pattern, _, _ = rest.partition(")")
    pattern = pattern.strip()

    # 还原转义括号
    for i, ch in enumerate(placeholders):
        pattern = pattern.replace(f"\x00{i}\x00", ch)

    # 空内容或 * 视为无条件匹配
    if pattern in ("", "*"):
        pattern = None

    return {"tool_name": tool_name.strip(), "pattern": pattern}


def _match_bash_command(pattern: str, command: str) -> bool:
    """
    匹配 Bash 命令。

    采用简化策略：
      - 取命令头部（第一个词），忽略前导变量赋值如 FOO=bar
      - 支持 legacy 前缀语法 "cmd:*" -> 转为 "cmd *"
      - 支持通配符 *（匹配任意字符序列）
      - "git *" 同时匹配 "git" 和 "git add"
    """
    if not command:
        return False

    # 处理 legacy 前缀语法 cmd:*
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"

    # 简化命令头：去掉前导 VAR=val 的变量赋值
    cmd = command.strip()
    while "=" in cmd.split()[0] if cmd else False:
        parts = cmd.split(None, 1)
        if len(parts) > 1:
            cmd = parts[1]
        else:
            cmd = ""
            break

    head = cmd.split()[0] if cmd else ""
    rest = cmd[len(head):].lstrip() if head else ""

    # "git *" 可以匹配 "git" 本身（尾部参数可选）
    if pattern.endswith(" *"):
        prefix = pattern[:-2]
        if head == prefix:
            return True
        # 继续走完整通配匹配

    return _match_wildcard_pattern(pattern, cmd)


def _match_wildcard_pattern(pattern: str, text: str) -> bool:
    r"""
    通配符匹配，只支持 * 作为任意字符序列的通配符。

    规则：
      - * 匹配零个或多个任意字符
      - \* 匹配字面量 *
      - \\ 匹配字面量 \
    r"""
    if pattern == "*":
        return True
    if not pattern:
        return text == ""

    # 把 pattern 转成正则表达式
    import re
    regex_parts = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            next_ch = pattern[i + 1]
            if next_ch == "*":
                regex_parts.append(re.escape("*"))
                i += 2
                continue
            elif next_ch == "\\":
                regex_parts.append(re.escape("\\"))
                i += 2
                continue
        elif ch == "*":
            regex_parts.append(".*")
            i += 1
            continue
        else:
            regex_parts.append(re.escape(ch))
        i += 1

    try:
        regex = re.compile("^" + "".join(regex_parts) + "$")
        return bool(regex.match(text))
    except Exception:
        return False
