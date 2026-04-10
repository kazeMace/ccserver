# CHANGELOG

本文件按时间倒序记录项目的所有重要变更。

格式规范：
- 每个版本条目包含：版本号、日期、会话 ID（如有）、变更分类
- 变更分类：`Added`（新增）、`Changed`（修改）、`Fixed`（修复）、`Removed`（删除）、`Docs`（文档）

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
