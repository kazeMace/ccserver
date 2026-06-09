# 设计原则审计与重构计划 (2026-06-09)

> 基于 SOLID(SRP/OCP/LSP/ISP/DIP)+ LOD 对 ccserver 核心包的审计。
> 重构顺序遵循 CLAUDE.md「胆大心细、做难而正确的事、重构前先 commit、不破坏已有功能」。

## 一、做得好的地方(保留)

1. **DIP/OCP 在 model / storage 层落实到位**:`ModelAdapter(ABC)`、`StorageAdapter(ABC)` 抽象基类,上层依赖抽象。
2. **分层合理**:emitters / channels / model / storage 目录边界清楚。
3. **测试基础**:tests/ 50 文件、约 1.1 万行,是重构的安全网。

## 二、待解决问题(按重构顺序 = 风险从低到高)

### 任务 1 🟠 `model/factory.py` if-elif 分派 → 注册表(违背 OCP)
- 现状:`if api_type == ANTHROPIC ... elif OPENAI ... elif ZHIPUAI ... elif VOLCANO`。
- 问题:每加供应商都要改工厂。抽象层已做对,工厂却硬编码。
- 方案:改注册表 `register(api_type, adapter_cls)` + 装饰器/显式注册;新增供应商零修改工厂。
- 风险:低。有抽象基础,改动局部。
- 验收:现有 4 个适配器全部可通过注册表创建;相关测试通过。

### 任务 2 🟠 `StorageAdapter` 29 方法胖接口 → 按领域拆分(违背 ISP)
- 现状:单 ABC 29 抽象方法,横跨 session / task / team / conversation / counter。
- 问题:file / mongo / sqlite 三个 adapter 被迫实现全部方法。
- 方案:拆 `SessionStore` / `TaskStore` / `TeamStore` 等细接口,adapter 按需组合实现。保留聚合接口兼容现有调用点。
- 风险:中低。需同步改三个 adapter 与调用点。
- 验收:三个 adapter 实现对应细接口;存储相关测试通过。

### 任务 3 🟡 `server.py` 1895 行聚合路由 → APIRouter 拆分(违背 SRP)
- 现状:session / team / channel / cron / agent-task / mailbox 路由全在一个文件。
- 方案:用 FastAPI `APIRouter` 按领域拆成多个路由模块,server.py 仅装配。
- 风险:低(机械拆分,逻辑不变)。
- 验收:所有路由路径不变;接口测试通过。

### 任务 4 🔴 `Agent` 上帝类 2612 行 → 职责拆分(违背 SRP/OCP/LOD)
- 现状:单 `Agent` 类 30+ 方法,承担 6 类职责:
  - 生命周期/状态机 / LLM 调用 / 工具分发 / 子 agent 派生 / 团队协作 / 压缩 / 限流。
- 方案(组合优先于继承,逐个职责剥离):
  - `LLMCaller` — stream/sync 调用 + 消息净化
  - `ToolDispatcher` — 工具路由执行(含 MCP、图像转写)
  - `SpawnManager` — spawn_child / spawn_background / teammate
  - `CompactStrategy` — 压缩策略
  - `LimitPolicy` — `_on_limit_*` 四变体做策略模式(OCP)
  - `Agent` 退化为协调者持有以上协作对象。
- 风险:高。务必逐项剥离 + 每步验证行为不变,先确认测试覆盖。
- 验收:Agent 行数显著下降;agent loop / streaming / spawn / team 相关测试全部通过。

## 三、执行纪律
- 每个任务开始前 `git commit` 当前状态(可回滚)。
- 每个任务完成后跑相关测试验证行为不变,再 commit。
- 环境:`conda activate ccserver`,python 3.12。
