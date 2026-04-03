# CHANGELOG

本文件按时间倒序记录项目的所有重要变更。

格式规范：
- 每个版本条目包含：版本号、日期、会话 ID（如有）、变更分类
- 变更分类：`Added`（新增）、`Changed`（修改）、`Fixed`（修复）、`Removed`（删除）、`Docs`（文档）

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
