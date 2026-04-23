# CHANGELOG

本文件按时间倒序记录项目的所有重要变更。

格式规范：
- 每个版本条目包含：版本号、日期、会话 ID（如有）、变更分类
- 变更分类：`Added`（新增）、`Changed`（修改）、`Fixed`（修复）、`Removed`（删除）、`Docs`（文档）

---

## [Unreleased] — 2026-04-23

**会话**: 输出控制重构 + TUI 交互增强 + 日志系统优化

### Changed

- `ccserver/emitters/filter.py` — `FilterEmitter` 重写，将旧的单一 `mode` 参数拆分为三个独立参数：
  - `verbosity: "verbose" | "final_only"` — 展示详细程度；`verbose` 透传全部事件，`final_only` 只透传 `done`/`error`
  - `stream: bool` — 控制 `token` 事件是否透传，默认 `True`
  - `interactive: bool` — 控制交互行为；`False` 时 `emit_ask_user` 直接返回 `""`，`emit_permission_request` 直接返回 `False`，不阻塞
  - 约束：`verbosity=final_only` 时强制 `interactive=False`
  - 移除旧 `streaming` / `interactive` 模式分支（已被新参数组合覆盖）

- `server.py` — `ChatRequest` 字段同步更新（`output_mode` + `run_mode` → `verbosity` + `stream` + `interactive`）；`_wrap_emitter` 签名对齐；WebSocket 端点从 payload 读取新三字段

- `tui.py` — 新增以下 slash 命令：
  - `/verbosity [verbose|final_only]` — 查看或切换展示详细程度
  - `/stream` — toggle token 流（on/off）
  - `/interactive` — toggle 交互模式（on/off）；`verbosity=final_only` 时提示无法开启
  - `/status` — 打印当前 session、model、workdir 及三个输出参数的值

- `clients/tui_http.py` — 同步上述 slash 命令；主循环从同步 `pt_prompt + asyncio.run` 改为全异步 `PromptSession.prompt_async()`

### Added

- `tui.py` / `clients/tui_http.py` — ESC 键绑定（`prompt_toolkit.KeyBindings`）：
  - 有正在运行的 agent/SSE task 时，ESC 调用 `task.cancel()`，打印 `⏹ 已中断`
  - 无 task 运行时，ESC 清空当前输入行

- `tui.py` / `clients/tui_http.py` — `/` 前缀自动补全（`prompt_toolkit.WordCompleter`）：
  - 输入 `/` 时弹出全部命令候选，`complete_while_typing=True` 边输边缩小列表

### Fixed

- `clients/tui_http.py` — `sys.stdout.write()` 错误传入 `flush=True` 关键字参数（`write` 不支持），拆分为 `write()` + `flush()`

- `ccserver/log.py` — 修复第三方子 logger（如 `mcp.server.db`）绕过 handler 导致原始 logging 格式漏出的问题：
  - 改为只在顶层 logger（`mcp`、`uvicorn`、`fastapi`）安装单例 `_InterceptHandler`，子 logger 通过 `propagate=True`（默认）自然冒泡，无需逐一注册动态创建的子 logger
  - 新增 `_prefix_to_tag()`：按最长前缀匹配规则将 `record.name` 映射为来源标签，子模块显示为 `TAG:suffix`（如 `MCP:server.db`），suffix 超出列宽时从末尾截取保留最有区分度的部分
  - `_INTERCEPT_LOGGERS` 从 8 条精确条目精简为 3 条前缀条目（`mcp` / `uvicorn` / `fastapi`）

- `server.py` — uvicorn 启动时传入 `log_config=None`，禁止 uvicorn 在启动后用 `dictConfig` 覆盖已安装的 loguru handler，修复 `uvicorn.access` 日志以原始 `INFO: 127.0.0.1 -` 格式漏出的问题

- `ccserver/emitters/__init__.py` / `ccserver/managers/agents/manager.py` — 修复 `VALID_MODES` 改名为 `VALID_VERBOSITY` 后未同步导致的 `ImportError`

---

## [0.3.0] — 2026-04-10

**会话**: Hook 系统重构与 roleplay_agent_neo 示例

### Added
- `ccserver/hooks/matcher.py` — 全新 HookMatcher 系统，支持 `LiteralMatcher`（精确/正则）与 `ExpressionMatcher`（完整布尔表达式），兼容 Claude Code 的 matcher 语法
- `ccserver/hooks/bun_wrapper.ts` — TypeScript hook 的 OpenClaw 兼容适配层
- `docs/hooks.md` — Hook 系统完整使用文档
- `playground/agents/roleplay_agent_neo/` — 新版角色扮演 Agent 示例，展示 `UserPromptSubmit` hook 注入 `CONVERSATION_ID` 的完整实践

### Changed
- `ccserver/hooks/loader.py` — 全面重构 Hook 加载与执行机制：
  - 废弃旧 A/B/C 风格（文件名/函数名/main 扫描），统一为 `settings.json hooks` + `HOOK.md` 目录两种注册方式
  - 新增 `parallel/serial` × `all/first/last` 执行策略，与 CC 行为对齐
  - 支持 `modifying` / `observing` / `claiming` 三种事件模式
  - 新增 `prompt` / `http` / `agent` 执行器类型
  - 事件名统一规范化为冒号分隔的 ccserver 标准名，兼容 CC 写法（PascalCase）和 OpenClaw 写法（下划线）
- `ccserver/agent.py` — `message:inbound:received`（`UserPromptSubmit`）hook 触发点提前到 `/` slash command 判断之前，确保 slash command 也能触发 hook
- `ccserver/settings.py` — `ProjectSettings` 新增 `build_hook_loader()`，将原始 settings dict 传给 `HookLoader`
- `ccserver/session.py` — `Session.__post_init__` 改用 `settings.build_hook_loader()` 构建 hook loader
- `server.py` — 启动时打印 `CONVERSATION_ID` 环境变量（调试用，可选）

### Fixed
- `ccserver/hooks/loader.py` — command hook 执行时 `cwd` 设为 `ctx.project_root`，保证相对路径命令以 `CCSERVER_PROJECT_DIR` 为基准
- `ccserver/hooks/loader.py` — bun hook 的 `script` 相对路径自动基于 `ctx.project_root` 解析，执行时也设置 `cwd`

---

## [0.2.0] — 2026-03-16

**会话**: Pipeline DAG 功能实现

### Added
- `src/pipeline/data.py` — NodeData 数据容器，节点间传递数据的 dict 包装
- `src/pipeline/node.py` — AgentNode（LLM 节点）、FunctionNode（函数节点）规格声明
- `src/pipeline/graph.py` — Pipeline 基类，含 DAG 容器 + 拓扑排序执行引擎；内置 _NullEmitter
- `src/pipeline/__init__.py` — 模块导出：Pipeline、AgentNode、FunctionNode、NodeData

### Changed
- `src/__init__.py` — 追加 pipeline 模块导出（1 行）
- `server.py` — ChatRequest 新增 `dag` / `pipeline_class` 字段；`/chat` 和 `/chat/stream` 支持 dag 模式分支；新增 `_PIPELINE_REGISTRY` 注册表和 `_get_pipeline_class()` 辅助函数

---

## [0.1.0] — 2026-03-16

**会话**: 初始化版本

### Added
- `src/agent.py` — Agent + AgentContext 核心循环，统一根代理/子代理逻辑
- `src/session.py` — Session、SessionManager（JSONL 持久化）、TodoManager、SkillLoader
- `src/factory.py` — AgentFactory.create_root() 根代理工厂
- `src/compactor.py` — 三级上下文压缩（micro / 阈值检测 / LLM 摘要）
- `src/utils.py` — BaseEmitter 事件系统 + SDK 工具函数
- `src/config.py` — 全局配置，支持 CCSERVER_* 环境变量覆盖
- `src/basic_tools/` — 内置工具集：Bash、Read、Write、Edit、Glob、Grep、Compact、Todo、LoadSkill、Task
- `server.py` — FastAPI 服务器，支持 HTTP 阻塞、SSE 流式、WebSocket 三种接口
- `tui.py` — 终端交互界面，支持会话管理命令

### Docs
- `README.md` — 项目功能概览、快速开始、模块说明、API 接口文档
- `CLAUDE.md` — Claude Code 开发参考指南（目录结构、核心概念、开发规范、Git 流程）
- `CHANGELOG.md` — 本文件，变更历史
- `VERSION` — 版本号文件，当前 0.1.0
