# Drama Engine

`drama_engine` 是 AgentParty 的核心游戏引擎，用来搭建可扩展的多 Agent + 真人派对游戏。新版本设计以 `runtime.type: interactive_session` 为主线，把一局游戏拆成可声明、可校验、可运行、可展示的几个层次：YAML DSL 描述玩法，runtime 选择执行模型，runner 推进一局 session，公共组件负责条件、候选人、效果、输入、事件、视图和记忆。

这个项目的目标不是只跑一个固定狼人杀脚本，而是沉淀一套可复用的互动游戏基础设施。狼人杀、卧底、阿瓦隆、UNO、德州扑克、文字冒险、群聊讨论、动态剧情，都应该能在同一套 session、actor、action、event、view 和 runtime 边界下扩展。

## 核心定位

- **DSL 驱动**：用 YAML 声明游戏元信息、角色、玩家、可见域、流程、场景、条件、效果、发布内容和 runtime。
- **interactive_session 主线**：新版本统一使用 `interactive_session` 表达玩法流程、群聊讨论、动态剧情和人机混合互动。
- **runtime 可插拔**：通过 `runtime.type` 选择执行模型，旧执行模型保留为已有能力和迁移背景。
- **多 Agent + 真人协作**：同一局游戏里可以同时存在 AI 玩家、真人玩家、系统主持、隐藏内部 agent 和外部 plugin。
- **派对游戏优先**：核心抽象围绕“多人互动、私密信息、公开讨论、行动提交、裁判结算、主持人视角、玩家视角、观众视角”设计。
- **公共组件复用**：条件判断、候选人解析、效果执行、值解析、动作服务、事件发布、记忆存储和视图投影都作为横切能力复用。
- **面向扩展**：新增游戏不应该复制一套运行框架，而是在 DSL、runtime、execution model、policy、projector 或 plugin 上扩展。

## 架构总览

```text
YAML DSL
  -> DSL validator / compiler
  -> RuntimeSpec
  -> PartySessionRuntime
  -> Runner dispatch
  -> Execution model runner
  -> Action / Event / View / Memory ports
  -> Host / Player / Public frontend
```

主要分层：

| 层级 | 职责 |
| --- | --- |
| `application/` | 剧本目录、仓库、检查器、生成、试玩和插件编排。 |
| `core/dsl/` | YAML DSL 编译、schema、validator、registry、插件、条件、候选人、效果和值解析。 |
| `core/runtime_spec/` | 解析和注册 `runtime.type` 声明。 |
| `core/session/` | Web 多 session 容器、状态、事件、action、持久化、视图投影和生命周期。 |
| `core/runner/` | runner 基类、上下文、dispatch 和统一生命周期协议。 |
| `core/execution_models/` | 固定流程、群聊、动态剧情等具体执行模型。 |
| `core/runtime/interactive_session/` | 新一代互动会话 runtime，统一玩法流程、动态调度、自由输入和剧情生长能力。 |
| `core/ports/` | action、event、input、memory、timeout、view 等窄端口。 |
| `service/` | 普通玩家/主持人/观众 Web 服务。 |
| `admin_service/` | 剧本开发、校验、检查、试玩和发布控制台。 |

## DSL 机制

DSL 是游戏作者和运行引擎之间的契约。它负责把“游戏规则是什么”从 Python 运行代码中分离出来，让同一套 runtime 可以运行不同游戏。

典型 DSL 文件包含：

```yaml
meta:
  id: werewolf_v1_guard
  display_name: 12人狼人杀守卫局

runtime:
  type: interactive_session
  config: {}

roles: []
players: {}
scopes: []
initial_state: {}
flow:
  type: sequence
  scenes: []
scenes: {}
```

DSL 的重点能力：

- `meta` 描述剧本身份、名称、版本、标签和语言。
- `runtime` 声明脚本由哪一种执行模型解释。
- `roles` 和 `players` 描述角色、阵营、能力、人数和发牌。
- `scopes` 描述公开频道、私聊频道、阵营频道等消息可见域。
- `flow` 描述游戏流程，可以是固定顺序，也可以是状态机。
- `scenes` 描述一段玩法或剧情生命周期。
- `conditions` 使用统一条件组件表达 `when`、胜负判断和过滤逻辑。
- `effects` 通过公共效果执行器修改状态、发布消息或触发动作。
- `publication` 和 `views` 控制主持人、玩家、观众看到什么。
- `plugins` 承载机制型扩展，例如规则集、条件判断、动态生成、视图渲染和外部服务。

新版本 DSL 以 `drama_engine/docs/interactive_session_dsl_design.md` 为准。旧语法可以由 normalizer 兼容，但执行层只读取 canonical model，不在 executor 里散落 legacy 判断。

## Runtime 与 Runner

`runtime.type` 决定一份 DSL 交给哪种执行模型运行。它只是声明，不是运行中的实例。真正持有一局游戏资源的是 `PartySessionRuntime`。

新版本主路径：

| `runtime.type` | Runner | 适合场景 |
| --- | --- | --- |
| `interactive_session` | `InteractiveSessionRunner` | 统一表达多 Agent 玩法流程、人机混合派对游戏、开放群聊、动态剧情和自由输入。 |

已有执行模型：

| `runtime.type` | 说明 |
| --- | --- |
| `game_session` | 固定流程派对/桌游执行模型，是狼人杀、阿瓦隆、卡牌、棋类等已有脚本的基础。 |
| `group_chat` | 多 Agent 群聊执行模型，适合开放讨论和圆桌互动。 |
| `dynamic_story` | 动态剧情执行模型，适合文字冒险、DM 裁决、NPC 反应和世界记忆。 |

runner 的统一职责：

- `assign()`：绑定 session、加载脚本、创建 actor、初始化领域状态。
- `start()`：启动执行循环。
- `step()`：推进一小步，便于测试、试玩或调试。
- `terminate()`：终止运行。
- `status()`：返回运行状态。
- `summary()`：输出可检查的 session 摘要。

`PartySessionRuntime` 负责 session 生命周期和资源装配，runner 负责具体玩法推进。新增能力时不要让 runner 直接访问 service 私有对象，而是通过 `RunnerContext` 和 `core/ports/` 暴露的窄端口协作。

## Interactive Session 新版本

`interactive_session` 是新一代通用互动 runtime。它把“玩法型流程”和“剧情型流程”统一到同一套 scene 结构里，也是新游戏和新能力优先面向的设计。

核心 DSL：

```yaml
runtime:
  type: interactive_session

flow:
  type: state_machine
  initial: debate
```

标准 scene 结构：

```yaml
scenes:
  public_debate:
    type: scene
    scope: {}
    when: {}
    participants: {}
    schedule: {}
    participant_action: {}
    controller_action: {}
    resolution: {}
    publication: {}
    referee: {}
    hooks: {}
```

字段分工：

| 字段 | 职责 |
| --- | --- |
| `scope` | 当前 scene 默认消息域。 |
| `when` | scene 是否可执行。 |
| `participants` | 当前 scene 的参与者来源和筛选方式。 |
| `schedule` | 谁能互动、何时互动、互动几轮、是否动态插入子调度。 |
| `participant_action` | 参与者需要提交的动作或发言。 |
| `controller_action` | 剧情控制者、系统、agent 或 plugin 如何推动剧情。 |
| `resolution` | 汇总响应并写入状态。 |
| `publication` | scene 结束后向不同 audience 发布信息。 |
| `referee` | 裁判检查、结束判断和跳转裁定。 |
| `hooks` | 生命周期扩展点。 |

`interactive_session` 的关键设计：

- `sequence` 是 `state_machine` 的特例。
- 循环通过 state transition 表达，不单独制造一套循环语法。
- 新语法统一使用 `left / op / right`、`all`、`any`、`not`、`ref` 和 `count` 条件。
- 旧语法兼容集中在 normalizer，执行器只读取 canonical model。
- 动态剧情生长通过 patch journal 记录，不直接修改原始 DSL。
- 外部能力统一通过 runtime service、plugin、HTTP 或 inside agent 调用。

实现入口：

```text
drama_engine/core/runtime/interactive_session/
  compiler.py
  context.py
  models.py
  normalizer.py
  runner.py
  flow/
  scene/
  schedule/
  actions/
  referee/
  patch/
  services/
```

执行链路：

```text
InteractiveSessionRunner
  -> InteractiveSessionCompiler
  -> InteractiveSessionNormalizer
  -> FlowExecutor
  -> SceneExecutor
  -> ScheduleExecutor
  -> ParticipantActionExecutor
  -> ControllerActionExecutor
  -> RefereeExecutor
```

执行层只读取 canonical model：

- `InteractiveScript`
- `FlowSpec`
- `SceneSpec`
- `ScheduleSpec`
- `ParticipantActionSpec`
- `ControllerActionSpec`
- `RefereeSpec`

## 动态调度与剧情生长

`interactive_session` 把动态能力作为 runtime 原生能力，而不是临时补丁。

`schedule.dynamic` 通过 `schedule_patch` 表达：

- `push_schedule` 插入临时子调度。
- `pop_schedule` 返回父调度。
- 支持 public/private 临时 scope。
- `detector` 通过 runtime service 调用。
- `check_on` 支持 `after_message` 和 `after_round`。
- 子调度结束后可以通过 `merge_back` 把摘要或结果写回状态。
- patch journal 记录调度变化，保证动态过程可追踪。

`grow_flow` 通过 `flow_patch` 表达：

- runtime 保存 `base_flow`、`patch_journal` 和 `materialized_flow`。
- 原始 DSL 不会被直接修改。
- `flow_patch` 会先校验和 dry-run 编译，确认可以合成后才进入 journal。
- `add_scene.after` 可以向当前 sequence/state 插入新 scene。
- `add_transition` 可以扩展 state machine transition。
- `set_state` 可以作为 patch 写入状态，但必须声明明确写入路径。

`controller_action.free_input.mode` 支持：

| mode | 作用 |
| --- | --- |
| `choose_mapping` | 把自由文本映射到已有 choice。 |
| `branch_then_return` | 生成临时支线 scene，执行后回到指定 scene/state。 |
| `constrained_continue` | 在结局约束下生成剧情 beat。 |
| `free_continue` | 自由生成剧情 beat。 |
| `grow_flow` | 生成并应用 `flow_patch`。 |

外部能力统一走 runtime service：

```yaml
provider: plugin
name: map_free_text_to_choice
```

也可以使用 HTTP 或 inside LLM/Agent provider。未声明 `input` 时，runtime service 默认获得完整运行时上下文；声明 `input.include_*` 时，会发送收窄 payload。

## 公共组件

公共组件是扩展游戏时优先复用的基础设施：

| 组件 | 职责 |
| --- | --- |
| `ConditionEvaluator` | 统一判断 `when`、过滤条件、胜负条件和 hook 条件。 |
| `CandidateResolver` | 根据 players、participants、roles、state 或 plugin 解析候选目标。 |
| `EffectExecutor` | 执行状态写入、消息发布、计分、道具变化等效果。 |
| `ValueResolver` | 解析 `{ref: ...}`、常量、计数、路径和表达式值。 |
| `ActorRuntime` | 创建和管理 AI、真人、mock、system 等 actor。 |
| `InputBridge` | 把真人输入、AI 行动和 service action 连接起来。 |
| `RuntimeActionServiceRouter` | 统一创建、读取、提交和取消玩家动作。 |
| `EventPublisher` | 向 public、host、private backlog 发布结构化事件。 |
| `RuntimeMemoryStore` | 为群聊、动态剧情和 Agent 提供短期/长期记忆。 |
| `BaseViewProjector` | 输出 host/public/player 三类稳定视图。 |

扩展新游戏时，优先问两个问题：

1. 这个能力是不是已经能用公共组件表达？
2. 如果不能，它应该成为 DSL 组件、runtime policy、execution model，还是 plugin？

不要把规则散落在 service handler、前端页面或 runner 生命周期里。规则应尽量进入 DSL、policy、condition、effect、referee 或专门的领域组件。

## 可扩展游戏模型

新增游戏通常有三种路径。

### 1. 新增 DSL 剧本

适合规则可以由现有 runtime 表达的游戏，例如固定流程桌游、社交推理、卡牌流程。

放置位置：

```text
drama_engine/scripts/<runtime_or_category>/<domain>/<game>.yaml
```

需要补充：

- `meta`
- `runtime`
- `roles / players / scopes`
- `flow / scenes`
- `referee`
- focused DSL 校验测试或运行测试

### 2. 新增 Game Pack 或 Plugin

适合多个游戏共享同一套机制，例如骰子、牌堆、棋盘、经济资产、身份阵营、投票规则、结局选择。

优先放置在：

```text
drama_engine/core/dsl/components/
drama_engine/core/dsl/extensions/
drama_engine/core/dsl/game_packs/
drama_engine/application/script_plugins/
```

### 3. 新增 Runtime / Execution Model

适合现有执行模型无法正确表达的全新运行方式，例如实时开放世界、长期经营模拟、异步多人回合制、跨房间大型活动。

新增 runtime 的基本步骤：

1. 在 `core/runtime_spec/registry.py` 注册 `RuntimeSpec`。
2. 在 `core/execution_models/<runtime_name>/` 建立 `model.py`、`state.py`、`policy.py`、`loop.py`、`projector.py`、`domain_runtime.py`。
3. 在 `core/runner/dispatch.py` 注册 `runtime.type -> runner`。
4. 增加 DSL schema、validator 和脚本样例。
5. 增加 runner dispatch、生命周期、动作提交、事件发布和视图投影测试。

## 多 Agent + 真人派对游戏

一局派对游戏的核心对象不是“AI 对话”，而是“带身份、权限、可见域和行动约束的多人 session”。

典型运行链路：

1. 主持人从 catalog 创建一局游戏。
2. `SessionRegistry` 创建 `PartySessionRuntime`。
3. runtime 根据 DSL 顶层 `runtime.type` 分派 runner。
4. runner 通过 `InputBridge` 创建真人座位、AI 座位、系统角色或内部 agent。
5. scene/schedule/referee 推进游戏。
6. 玩家通过 action service 提交发言、投票、选择、技能、移动或自由文本。
7. AI actor 根据私密视角、公开信息、角色提示和记忆生成行动。
8. `EffectExecutor` 和领域 policy 修改状态。
9. `EventPublisher` 发布 public/host/private 事件。
10. projector 输出主持人、玩家、观众视图。

这个模型允许：

- 全 AI 自动对局。
- 真人参与，AI 补位。
- 真人主持，AI 玩家。
- AI 主持，真人玩家。
- 系统裁判 + plugin 机制。
- 隐藏 inside agent 做意图识别、剧情生成、结局选择或规则裁决。

## 本地运行

项目要求使用 conda 环境 `ccserver` 和 Python 3.12。

```bash
conda activate ccserver
```

运行测试：

```bash
conda run -n ccserver pytest tests/drama drama_engine/tests -q
```

查看 CLI：

```bash
conda run -n ccserver python -m drama_engine.cli --help
```

查看本地脚本运行入口：

```bash
conda run -n ccserver python drama_engine/run_script.py --help
```

## 开发约定

- 先读现有实现和对应文档，再修改代码。
- 新代码优先复用公共组件，不重建平行框架。
- 新增 DSL 语法时同步更新 `drama_engine/docs/` 下的当前文档。
- 新增 runtime 时通过 `RuntimeSpec` 和 runner dispatch 接入。
- 新增横切能力时优先抽成小而专的 port 或组件。
- runner 不直接依赖 FastAPI、token store、HTTP handler 或前端细节。
- service handler 只做参数解析、权限检查和调用应用服务，不写游戏规则。
- 文档默认写入 `drama_engine/docs/`；`docs` 和 `legacy_docs` 默认不提交 Git。

## 参考文档

新版本真相源：

- `drama_engine/docs/interactive_session_dsl_design.md`
- `drama_engine/docs/interactive_session_runtime_implementation.md`

背景文档：

- `drama_engine/docs/drama_architecture.md`
- `drama_engine/docs/drama_engine_directory_structure.md`
- `drama_engine/docs/dsl-syntax-guide.md`
- `drama_engine/docs/plan/interactive_session_full_scope.md`
