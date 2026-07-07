# Drama Engine DSL 语法说明（新版）

> **目标读者**：想要自己搭建 DSL 脚本的开发者。
> **定位**：这份文档不是内部架构说明，而是面向开发者的实用搭建指南——告诉你每种字段怎么写、什么值合法、怎么组合，以及背后的设计意图。
> **基准**：本文以 `interactive_session` runtime 的新版 DSL 为唯一基准。旧版 `game_session` 字段（如 `turn_policy`、`performers`、`collect` 等）已删除，不再兼容。
> **版本**：对应 `interactive_session.v1` 协议版本。

---

## 目录

1. [基本约定](#1-基本约定)
2. [顶层结构总览](#2-顶层结构总览)
3. [meta — 剧本元信息](#3-meta--剧本元信息)
4. [runtime — 运行时声明](#4-runtime--运行时声明)
5. [params — 参数替换](#5-params--参数替换)
6. [roles — 角色定义](#6-roles--角色定义)
7. [players — 玩家与发牌](#7-players--玩家与发牌)
8. [scopes — 消息可见域](#8-scopes--消息可见域)
9. [initial_state — 初始状态](#9-initial_state--初始状态)
10. [flow — 流程编排](#10-flow--流程编排)
11. [scenes — 场景定义（核心）](#11-scenes--场景定义核心)
12. [scope — 消息域](#12-scope--消息域)
13. [when — 条件判断（全局通用）](#13-when--条件判断全局通用)
14. [evaluator — 条件求值器](#14-evaluator--条件求值器)
15. [participants — 参与者选择](#15-participants--参与者选择)
16. [schedule — 调度模式](#16-schedule--调度模式)
17. [dynamic — 动态子调度](#17-dynamic--动态子调度)
18. [participant_action — 参与者动作](#18-participant_action--参与者动作)
19. [controller_action — 剧情控制动作](#19-controller_action--剧情控制动作)
20. [free_input — 自由输入模式](#20-free_input--自由输入模式)
21. [resolution — 结算](#21-resolution--结算)
22. [publication — 发布与披露](#22-publication--发布与披露)
23. [referee — 裁判](#23-referee--裁判)
24. [hooks — 生命周期钩子](#24-hooks--生命周期钩子)
25. [effects — 效果清单](#25-effects--效果清单)
26. [candidates — 候选集](#26-candidates--候选集)
27. [response — 响应协议](#27-response--响应协议)
28. [visibility — 实体属性可见性](#28-visibility--实体属性可见性)
29. [guardrail — OOC 内容守卫](#29-guardrail--ooc-内容守卫)
30. [patch — 动态生长模型](#30-patch--动态生长模型)
31. [extensions — 领域扩展](#31-extensions--领域扩展)
32. [game_pack / rule_set — 游戏包与规则集](#32-game_pack--rule_set--游戏包与规则集)
33. [vocab / concepts — 词汇与概念](#33-vocab--concepts--词汇与概念)
34. [publish — 发布元信息](#34-publish--发布元信息)
35. [game_pack 机制清单](#35-game_pack-机制清单)
36. [ref 与状态路径速查](#36-ref-与状态路径速查)
37. [完整示例：狼人杀场景](#37-完整示例狼人杀场景)
38. [完整示例：文字冒险](#38-完整示例文字冒险)
39. [常见错误与校验规则](#39-常见错误与校验规则)

---

## 1. 基本约定

### 1.1 文件格式

- DSL 文件使用 **YAML** 格式。
- 顶层必须是**字典对象**（dict）。
- 实体 ID、角色名、scope 名、scene 名使用**稳定英文/下划线命名**，如 `seer_check`、`werewolf`、`public_room`。
- 展示文本（`display_name`、`description`、`cue`、`template`）可以使用中文。

### 1.2 新版语法唯一基准

本文所有示例使用新版语法：

- 条件统一使用 `left / op / right`
- 内置条件判断统一使用 `evaluator: builtin`
- 机制型能力统一优先使用 `evaluator: plugin`
- 剧情动态生长使用 **patch**，不直接修改原始 DSL
- scene 内部使用 `scope / participants / schedule / participant_action / controller_action / resolution / publication / referee / hooks`

**旧版字段已删除，出现时应视为语法错误**。删除清单：

| 旧字段 | 替代 |
| --- | --- |
| `turn_policy` | `schedule.mode` |
| `performers` | `participants` |
| `collect` | `response` |
| `effects[].condition` | `effects[].when` |
| `type`（scene 顶层） | `scene_type` 或删除 |
| `dialogue_policy` | `schedule` |
| `action_policy` | `participant_action` |
| `gate` | `when` |
| `interaction` | `participant_action` + `controller_action` |

### 1.3 编译入口

当前编译入口：

```python
# interactive_session runtime
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler

compiler = InteractiveSessionCompiler()
script = compiler.compile(path)          # 从 YAML 文件编译
script = compiler.compile_doc(doc)       # 从 YAML dict 编译
issues = compiler.validate(doc)          # 校验但不编译
```

---

## 2. 顶层结构总览

一份完整的 DSL 脚本顶层结构如下：

```yaml
# ─── 必填 ───
meta: {}                    # 剧本元信息
runtime: {}                 # 运行时声明
players: {}                 # 玩家与发牌
scenes: {}                  # 场景定义（核心）
flow: {}                    # 流程编排

# ─── 推荐 ───
roles: []                   # 角色定义
scopes: []                  # 消息可见域（interactive_session 用 scene.scope 替代）
initial_state: {}           # 初始状态
visibility: {}              # 实体属性可见性
guardrail: {}               # OOC 内容守卫

# ─── 可选 ───
params: {}                  # 参数替换
vocab: {}                   # 词汇表
concepts: {}                # 概念解释
referee: {}                 # 顶层裁判（scene 内也可声明）
triggers: []                # 事件触发器
extensions: {}              # 领域扩展
game_pack: {}               # 游戏包
rule_set: {}                # 规则集
plugins: []                 # 插件列表
publish: {}                 # 发布元信息
```

**`interactive_session` runtime 下，`scopes` 顶层声明已被 `scene.scope` 替代**。scene 内直接声明 scope，不再需要顶层 scopes 列表。

---

## 3. meta — 剧本元信息

`meta` 是脚本的基础元信息，用于展示、运行和 UGC 创作。

```yaml
meta:
  id: werewolf_v1_guard               # 稳定脚本 ID
  name: werewolf_v1_guard             # 机器可读名称
  display_name: 12人狼人杀守卫局       # 展示名称
  title: 12人狼人杀守卫局              # 标题（兼容字段）
  version: "1.0.0"                    # 版本号
  author: system                       # 作者
  description: 12人狼人杀，含守卫、预言家、女巫、猎人、警长竞选
  tags: [party, social-deduction]     # 标签
  locale: zh-CN                        # 语言地区
  license: MIT                         # 许可证
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 推荐 | 稳定脚本 ID，用于注册和引用 |
| `name` | string | 推荐 | 机器可读名称 |
| `display_name` | string | 推荐 | 展示名称 |
| `title` | string | 推荐 | 标题（与 display_name 含义相同，兼容保留） |
| `version` | string | 推荐 | 版本号，建议语义化版本 |
| `author` | string | 可选 | 作者 |
| `description` | string | 推荐 | 一句话说明 |
| `tags` | list[string] | 可选 | 分类标签 |
| `locale` | string | 可选 | 语言地区，如 `zh-CN`、`en-US` |
| `license` | string | 可选 | 许可证 |

校验规则：

- `meta` 必须是字典。
- `title`、`name`、`display_name` 至少存在一个。
- `tags` 若存在必须是字符串列表。

---

## 4. runtime — 运行时声明

`runtime` 声明这份 DSL 由哪类 runtime 解释执行。

```yaml
runtime:
  type: interactive_session
```

也可简写为：

```yaml
runtime: interactive_session
```

当前已注册 runtime 类型：

| type | 说明 | 当前状态 |
| --- | --- | --- |
| `interactive_session` | 多 agent / 人类参与的互动流程 Runtime，统一玩法型与剧情型流程 | 可运行 |
| `game_session` | 固定剧本/派对/桌游流程 Runtime（旧版，仍可运行） | 可运行 |
| `group_chat` | 多 Agent 群聊互动 Runtime | 已预留 |
| `dynamic_story` | 用户驱动动态剧情 Runtime | 已预留 |

校验规则：

- `runtime.type` 必须是已注册类型。
- `runtime.config` 若存在必须是字典。
- 不写 `runtime` 时默认 `{type: game_session}`。

**注意**：`schedule.mode: openchat` 只是 `interactive_session` 中 scene 的对话调度方式，不是 `runtime.type: group_chat`。

---

## 5. params — 参数替换

`params` 声明默认参数，编译前会进行 `{{param_name}}` 文本替换。

```yaml
params:
  total_players: 12
  game_name: 狼人杀

players:
  count: "{{total_players}}"
meta:
  display_name: "{{game_name}}守卫局"
```

规则：

- `params` 应是字典。
- `{{name}}` 引用的参数必须存在于 `params` 或运行时传入的参数覆盖字典。
- 参数替换发生在 YAML 解析前，数字位置需要注意引号。

---

## 6. roles — 角色定义

`roles` 是可分配给玩家的身份定义。

```yaml
roles:
  - name: seer                    # 角色唯一名称
    display_name: 预言家           # 展示名
    faction: good                  # 阵营
    brief: 每晚可以查验一名玩家的阵营  # 私密身份说明
    scopes: [self, whisper:seer]   # 角色默认订阅的可见域
    abilities:                     # 能力声明
      - name: seer_check
        display_name: 查验
        description: 查看目标阵营
        prompt: 你可以查验一名玩家的阵营。
    inventory:                     # 初始道具
      - item: antidote
        display_name: 解药
        description: 可以救人
        count: 1
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | 角色唯一名称 |
| `display_name` | string | 推荐 | 展示名 |
| `faction` | string | 推荐 | 阵营名 |
| `brief` | string | 可选 | 私密身份说明（只发给该角色本人） |
| `scopes` | list[string] | 可选 | 默认订阅的可见域 |
| `abilities` | list[dict] | 可选 | 能力声明 |
| `inventory` | list[dict] | 可选 | 初始道具 |

道具字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `item` | string | 是 | 道具名 |
| `display_name` | string | 是 | 展示名 |
| `description` | string | 是 | 说明 |
| `count` | int / "unlimited" | 推荐 | 数量 |

---

## 7. players — 玩家与发牌

`players` 定义玩家数量、席位 ID、展示名、初始属性和发牌方式。

### 7.1 shuffle 发牌（随机分配）

```yaml
players:
  count: 12
  ids:                                # 可选，不写时自动生成 Player_1..Player_N
    - Player_1
    - Player_2
  display_names:                      # 可选
    Player_1: 1号玩家
    Player_2: 2号玩家
  initial_attrs:                      # 每个玩家的初始属性
    alive: true
    seat_index: 0                     # 可选，用于发言排序
  casting:                            # 发牌配置
    type: shuffle
    distribution:
      werewolf: 4
      villager: 4
      seer: 1
      witch: 1
      hunter: 1
      guard: 1
```

### 7.2 fixed 发牌（指定角色）

```yaml
players:
  count: 3
  initial_attrs:
    alive: true
  casting:
    type: fixed
    assignment:
      Player_1: seer
      Player_2: werewolf
      Player_3: villager
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `count` | int | 是 | 玩家数量 |
| `ids` | list[string] | 可选 | 席位 ID，不写时自动生成 |
| `display_names` | dict | 可选 | 玩家展示名 |
| `initial_attrs` | dict | 推荐 | 每个玩家的初始属性 |
| `casting` | dict | 是 | 发牌配置 |

校验规则：

- `casting.type` 必须是 `shuffle` 或 `fixed`。
- shuffle: `distribution` 的 key 必须是已定义角色名，value 必须是整数，所有 value 之和应等于 `count`。
- fixed: `assignment` 的 value 必须是已定义角色名。

---

## 8. scopes — 消息可见域

> **注意**：在 `interactive_session` runtime 下，`scopes` 顶层声明已被 `scene.scope` 替代。
> scene 内直接声明 scope 对象，不再需要顶层 scopes 列表。
> 顶层 `scopes` 仅在 `game_session` runtime 下仍然必填。

`interactive_session` 下的 scope 直接写在 scene 内：

```yaml
scenes:
  day_discussion:
    scope:
      id: public_room
      visibility: public
```

`game_session` 下的顶层 scopes 声明（旧版，仍可用）：

```yaml
scopes:
  - name: public
    display_name: 公共频道
    members: all
  - name: werewolf
    display_name: 狼人夜聊
    members:
      filter:
        value: role
        equal: werewolf
  - name: self
    display_name: 私密频道
    members: self
```

scope 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` / `name` | string | 是 | scope 唯一名称 |
| `display_name` | string | 推荐 | 展示名 |
| `visibility` | string | 是 | `public` 或 `private` |
| `members` | string / dict | 是 | 成员规则 |

members 可用写法：

| 写法 | 说明 |
| --- | --- |
| `all` | 所有玩家 |
| `alive` | 所有 alive: true 的玩家 |
| `dead` | 所有 alive: false 的玩家 |
| `self` | 当前 actor 自己 |
| `[A, B, C]` | 固定成员列表 |
| `{filter: {...}}` | 使用条件筛选成员 |

---

## 9. initial_state — 初始状态

`initial_state` 定义游戏全局状态和实体初始属性。

```yaml
initial_state:
  GAME:
    round: 0
    day: 0
    sheriff: null
    current_deaths: []
    players: []                        # 自动填充为玩家列表
    dice_defs:                         # 骰子定义（game_pack dice 使用）
      attack:
        faces: ["hit", "miss"]
        weights: [0.3, 0.7]
      d20:
        sides: 20
      catan:
        faces: [0, 0, 1, 1, 2, 5]
    board_size: 40                     # 轨道大小（economy/track 使用）
```

规则：

- `initial_state` 应是字典。
- 顶层 key 可以是 `GAME` 或实体 ID。
- 游戏全局状态统一放在 `GAME` 下。

---

## 10. flow — 流程编排

`flow` 描述游戏流程，支持两种类型：`sequence` 和 `state_machine`。

**核心语义**：`sequence` 是 `state_machine` 的语法糖——编译后会被 lowering 成一个名为 `main` 的单状态 `state_machine`。

### 10.1 sequence — 顺序流程

```yaml
flow:
  type: sequence
  scenes:                    # 按顺序执行的 scene 列表
    - intro
    - first_choice
    - ending
```

### 10.2 state_machine — 状态机流程

```yaml
flow:
  type: state_machine
  initial: start             # 初始状态名，必须存在于 states

  states:
    start:
      scenes:                # 当前状态下执行的 scene 列表
        - intro
      transitions:           # 状态迁移规则
        - to: danger
          when:
            left: STORY.risk
            op: greater_than_equal
            right: 3

        - to: safe
          when:
            left: STORY.risk
            op: less_than
            right: 3

    danger:
      scenes:
        - danger_scene
      transitions: []

    safe:
      scenes:
        - safe_scene
      transitions: []

    end:
      scenes: []
      terminal: true          # 终止状态，不再迁移
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | `sequence` 或 `state_machine` |
| `initial` | string | state_machine 必填 | 初始状态名 |
| `states` | dict | state_machine 必填 | 状态定义 |
| `scenes` | list[string] | 是 | 当前状态/流程下的 scene 名称列表 |
| `transitions` | list[dict] | 可选 | 状态迁移规则 |
| `entry_effects` | list[effect] | 可选 | 进入状态时执行 |
| `exit_effects` | list[effect] | 可选 | 离开状态时执行 |
| `terminal` | bool | 可选 | 是否终止状态 |

### 10.3 loop — 循环

循环不是 sequence 的特殊能力，而是 state_machine transition 的自然结果：

```yaml
flow:
  type: state_machine
  initial: debate

  states:
    debate:
      scenes:
        - public_debate
      transitions:
        - to: debate           # 自环 → 循环
          when:
            left: GAME.vote_ready
            op: equal
            right: false

        - to: vote
          when:
            left: GAME.vote_ready
            op: equal
            right: true
```

校验规则：

- `transition.to` 必须引用已定义的 state。
- `transition.when` 若存在必须是条件字典。
- 无 `when` 的 transition 是 fallback。
- state_machine 不使用 `loop` 字段。

---

## 11. scenes — 场景定义（核心）

`scene` 是 `interactive_session` 中的一段玩法或剧情生命周期。**不等同于一次发言或一次对话**。

### 11.1 scene 标准结构

```yaml
scenes:
  intro:
    type: scene
    scope:                                    # 默认消息域
      id: public_room
      visibility: public

    when:                                     # scene 是否执行
      left: STORY.started
      op: equal
      right: true

    participants:                             # 参与者
      static: []

    schedule:                                 # 调度
      mode: none

    participant_action:                       # 参与者做什么
      kind: none

    controller_action:                        # 剧情控制者做什么
      enabled: true
      controller:
        type: system
      kind: narration

    resolution:                               # 结算
      effects: []

    publication:                              # 发布
      messages: []

    referee:                                  # 裁判
      enabled: false

    hooks: {}                                 # 生命周期钩子
```

字段职责一览：

| 字段 | 职责 |
| --- | --- |
| `scope` | 默认消息域，控制哪些消息进入哪些 agent 上下文 |
| `when` | scene 是否可执行（条件不满足则跳过整个 scene） |
| `participants` | 当前 scene 的参与者 |
| `schedule` | 参与者如何互动、谁能发言、是否允许动态子调度 |
| `participant_action` | 每个参与者执行的动作 |
| `controller_action` | 剧情控制者执行的动作 |
| `resolution` | 汇总结果并修改状态 |
| `publication` | scene 结束后向指定 audience 发布信息 |
| `referee` | 裁判检查、结束判断、跳转裁定 |
| `hooks` | 生命周期钩子（on_enter / on_exit / on_message 等） |

### 11.2 interactive_session 不需要人类

`interactive_session` 不要求一定有人类参与：

- 没有人类的狼人杀：所有 seat 由 agent 扮演，`controller_action.enabled: false`。
- 没有人类的剧情演绎：`controller.type` 可以是 `agent`、`system` 或 `plugin`。
- 有人类参与时，人类可以是某个 participant，也可以是剧情 controller。

---

## 12. scope — 消息域

`scope` 是消息域，不只是可见性——它决定「哪些消息进入哪些 agent 的上下文」。

### 12.1 public scope

```yaml
scope:
  id: public_room
  visibility: public
```

所有在该 scope 下的消息对所有参与者可见。

### 12.2 private scope

```yaml
scope:
  id: private_a_b
  visibility: private
  members: [A, B]
```

消息只对 members 可见。host 保留可观测事件。

### 12.3 scope 的三种语义位置

需要区分 scope 在三个不同位置的含义：

| 位置 | 含义 |
| --- | --- |
| `scene.scope` | scene 的默认消息域 |
| `schedule.dynamic` 生成的临时 scope | 动态子调度的消息域 |
| `publication.audience` | scene 结束后发布信息的目标 |

---

## 13. when — 条件判断（全局通用）

`when` 是全局通用条件组件，在所有位置都使用同一套语法。

### 13.1 简单条件（left / op / right）

```yaml
when:
  left: GAME.round
  op: greater_than_equal
  right: 1
```

这是**推荐写法**，所有新 DSL 必须使用。

### 13.2 引用操作数

左右两侧都可以是普通值，也可以是结构化 operand：

```yaml
when:
  left:
    ref: GAME.alive_players
  op: greater_than
  right: 3
```

### 13.3 计数操作数

```yaml
when:
  left:
    count:
      ref: GAME.players
      where:
        left: role
        op: equal
        right: wolf
  op: equal
  right: 0
```

含义：`GAME.players` 中 `role == wolf` 的数量是否等于 0。

### 13.4 复合条件

**all — 全部满足（AND）**

```yaml
when:
  all:
    - left: GAME.phase
      op: equal
      right: night
    - left: GAME.round
      op: greater_than_equal
      right: 2
```

**any — 任一满足（OR）**

```yaml
when:
  any:
    - left: STORY.affection
      op: greater_than_equal
      right: 5
    - left: STORY.trust
      op: greater_than_equal
      right: 3
```

**not — 否定（NOT）**

```yaml
when:
  not:
    left: PLAYER.alive
    op: equal
    right: true
```

### 13.5 操作符完整清单

| 操作符 | 说明 | 示例 |
| --- | --- | --- |
| `equal` | 等于 | `left: GAME.round, op: equal, right: 1` |
| `not_equal` | 不等于 | `left: actor, op: not_equal, right: GAME.sheriff` |
| `greater_than` | 大于 | `left: GAME.round, op: greater_than, right: 0` |
| `less_than` | 小于 | `left: STORY.risk, op: less_than, right: 3` |
| `greater_than_equal` | 大于等于 | `left: GAME.round, op: greater_than_equal, right: 1` |
| `less_than_equal` | 小于等于 | `left: GAME.score, op: less_than_equal, right: 10` |
| `in` | 左值在右侧集合中 | `left: actor, op: in, right: GAME.sheriff_candidates` |
| `not_in` | 左值不在右侧集合中 | `left: actor, op: not_in, right: GAME.dead_players` |
| `is_null` | 左值是否为空/null | `left: GAME.sheriff, op: is_null, right: true` |
| `not_null` | 左值是否非空 | `left: GAME.sheriff, op: not_null, right: true` |
| `contains` | 字符串包含 | `left: MESSAGE.text, op: contains, right: "离开"` |

**注意**：新 DSL 统一使用 `equal` / `not_equal`，不要使用旧版 `equals` / `not_equals`。

### 13.6 @ 逃逸

如果某个字符串长得像引用路径，但你需要它作为字面量，使用 `@` 前缀逃逸：

```yaml
when:
  left: GAME.target
  op: equal
  right: "@GAME.round"          # 这是一个字符串字面量 "GAME.round"，不是引用
```

### 13.7 when 的不同位置含义

| 位置 | 粒度 | 说明 |
| --- | --- | --- |
| `scene.when` | 整幕 | 不满足则跳过整个 scene |
| `participants.when` | 单个参与者 | 不满足则该参与者不进入 |
| `candidates.when` | 单个候选 | 不满足则该候选不可选 |
| `effects[].when` | 单个效果 | 不满足则该效果不执行 |
| `referee.rules[].when` | 单个规则 | 不满足则该裁判规则不触发 |
| `hooks[].when` | 单个钩子 | 不满足则该钩子动作不执行 |

---

## 14. evaluator — 条件求值器

条件判断统一抽象为 evaluator。有五种类型：

### 14.1 builtin — 内置条件

```yaml
when:
  evaluator: builtin
  condition:
    left: GAME.round
    op: greater_than_equal
    right: 1
```

默认就是 builtin，不写 `evaluator` 时等同于 `evaluator: builtin`。

### 14.2 code — 代码执行

```yaml
when:
  evaluator: code
  language: python              # 支持 python / shell / node / bun
  env:
    MAX_BEATS: "20"
  code: |
    result = state["GAME"]["round"] >= int(env["MAX_BEATS"])
```

规则：

- `language` 可以是 `python`、`shell`、`bun_js` 等。
- condition 代码只能返回布尔值，不允许直接修改 State。
- python 内联执行受限 helper（只能读取 state/env）。

### 14.3 http — HTTP 外部判断

```yaml
when:
  evaluator: http
  url: https://example.com/runtime/check
  method: POST
  headers:
    X-Scene: vote
  timeout_ms: 3000
  input:
    include_state: true
    include_players: true
    include_messages: true
```

`input` 声明控制发给外部服务的上下文片段：

| 字段 | 发送内容 |
| --- | --- |
| `include_state` | state 快照 |
| `include_players` | players 列表 |
| `include_participants` | 当前参与者列表 |
| `include_messages` | 当前消息列表 |
| `include_recent_messages` | 最近消息（支持 `recent_limit`） |
| `include_message` | 当前触发消息 |
| `include_story_summary` | STORY 状态摘要 |
| `include_responses` | 当前 responses |
| `include_patch_journal` | patch journal |
| `include_metadata` | 可序列化 metadata |

未声明 `input` 时默认发送完整上下文。

### 14.4 llm — LLM 语义判断

```yaml
when:
  evaluator: llm
  provider: inside              # 默认值，使用 ccserver 内部 agent
  semantic_id: judge_story_progress
  input:
    include_state: true
    include_recent_messages: true
```

`provider: inside` 表示通过 ccserver 内部 agent 能力执行。真实运行时未注入 `inside_agent/llm_client` 时，会回退到 actor/builtin fallback。

### 14.5 plugin — 机制型能力

```yaml
when:
  evaluator: plugin
  name: choose_ending_by_progress
  input:
    include_state: true
    include_story_summary: true
```

复杂机制优先实现为 plugin。plugin 可以内置 prompt、schema、fallback、校验逻辑，也可以内部调用 LLM、HTTP 或代码执行器。

---

## 15. participants — 参与者选择

参与者可以静态指定、条件筛选或通过 plugin 选择。

### 15.1 static — 固定参与者

```yaml
participants:
  static: [A, B, C, D]
```

### 15.2 filter — 条件筛选

```yaml
participants:
  filter:
    source: GAME.players
    where:
      left: alive
      op: equal
      right: true
```

含义：从 `GAME.players` 中筛选 `alive == true` 的玩家。

### 15.3 plugin — 机制选择

```yaml
participants:
  evaluator: plugin
  name: select_current_scene_participants
  input:
    include_state: true
    include_players: true
```

简写形式：

```yaml
participants:
  plugin: select_current_scene_participants
```

### 15.4 from_state — 从状态读取

```yaml
participants:
  from_state: GAME.sheriff_candidates
  ordered: true
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `static` | list[string] | 固定参与者列表 |
| `filter` | dict | 条件筛选 |
| `source` | string | 筛选来源（如 `GAME.players`） |
| `where` | condition | 筛选条件 |
| `from_state` | string | 从 State 路径读取列表 |
| `plugin` / `evaluator` | dict | plugin 选择 |
| `ordered` | bool | 是否保留顺序 |
| `min` | int | 人数不足时跳过本幕 |

---

## 16. schedule — 调度模式

`schedule` 负责参与者之间如何互动。它不决定"做什么"，只决定"谁在什么时候发言、执行几轮、是否允许中途插入动态子调度"。

### 16.1 基础 mode 清单

| mode | 说明 | 适用场景 |
| --- | --- | --- |
| `none` | 不调度 actor | 旁白、纯 controller scene |
| `single` | 单个 actor 行动 | 预言家查验、指定玩家行动 |
| `sequential` | 按顺序依次行动 | 白天发言、警长竞选 |
| `simultaneous` | 同时行动，全部完成后统一公布 | 投票、夜间行动 |
| `random_order` | 随机顺序行动 | 自由讨论 |
| `openchat` | 开放聊天，planner 决定下一位 | 群聊讨论、圆桌 |
| `loop_until` | 循环直到停止条件成立 | 多轮讨论、反复投票 |

### 16.2 none

```yaml
schedule:
  mode: none
```

### 16.3 single

```yaml
schedule:
  mode: single
  actor:                           # 指定唯一 actor
    ref: GAME.current_player       # 支持字面量或 {ref: ...}
```

### 16.4 sequential

```yaml
schedule:
  mode: sequential
  order:
    source: participants           # 来源：participants 或 GAME.xxx
    strategy: seat_order           # 排序策略：seat_order / reverse_seat_order
```

`strategy: seat_order` 优先使用 `seat_index` 排序；没有 `seat_index` 时使用玩家名末尾数字的自然顺序兜底。

### 16.5 simultaneous

```yaml
schedule:
  mode: simultaneous
  timeout_ms: 30000               # 超时时间，超时 actor 被取消
```

`simultaneous` 以协程并发收集所有 actor 响应。超过 `timeout_ms` 的 actor 会被取消，已完成的响应仍然进入 resolution 和 referee。

### 16.6 openchat

```yaml
schedule:
  mode: openchat
  actor: A                         # 首个发言者
  opening: A 先开场，然后由 planner 决定下一位
  planner:                         # 每轮后决定下一位
    evaluator: plugin
    name: plan_openchat_next
  max_turns: 12                    # 最多发言段数
  stop_when:                       # 停止条件
    evaluator: builtin
    condition:
      left: SCENE.ready_to_end
      op: equal
      right: true
```

`openchat` 是开放聊天调度：每次只让一个 actor 发言，发言后立即发布，下一轮前调用 planner 决定下一位 actor、下一段 cue 或是否停止。

没有 `planner` 时 runtime 使用稳定轮转 fallback。

### 16.7 loop_until

```yaml
schedule:
  mode: loop_until
  max_rounds: 5                    # 最多轮数
  stop_when:                       # 每轮后检查停止条件
    evaluator: plugin
    name: check_discussion_complete
```

### 16.8 schedule 常用字段速查

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `mode` | string | 调度模式 |
| `actor` | string / dict | single/openchat 的首个 actor |
| `order` | dict | 顺序策略 |
| `planner` | dict | openchat 每轮决定下一位的 evaluator |
| `opening` / `cue` | string | 首轮提示词 |
| `max_turns` | int | openchat 最多发言段数 |
| `max_rounds` | int | loop_until 最多轮数 |
| `timeout_ms` | int | 超时时间 |
| `stop_when` / `until` | condition | 停止条件 |

---

## 17. dynamic — 动态子调度

动态私聊、分组聊、指定两人对话不是新的 flow 节点，而是当前 `schedule` 的动态子调度能力。运行时根据发言即时生成 `schedule_patch`，执行后回到父 schedule。

```yaml
schedule:
  mode: openchat
  max_turns: 12

  dynamic:
    enabled: true
    check_on: after_message        # 什么时候运行 detector

    detector:                      # 检测是否需要插入子调度
      evaluator: plugin
      name: detect_schedule_request
      input:
        include_message: true
        include_state: true
        include_participants: true

    allowed:                       # 子调度约束
      modes: [single, sequential, openchat]
      participant_count:
        min: 2
        max: 4
      scope_visibility: [private, public]
      max_turns:
        default: 4
        max: 8

    patch:                         # patch 类型
      type: push_schedule
      return_to_parent: true

    merge_back:                    # 子调度结果如何回传
      mode: summary
      to: SCENE.dynamic_schedule_summary
```

### 17.1 check_on 触发时机

| 值 | 触发时机 |
| --- | --- |
| `after_message` | 每条 participant message 发布后 |
| `after_round` | 当前 schedule round 完成后 |

### 17.2 schedule_patch 示例

基于某个 agent 的发言，plugin 可以生成：

```yaml
schedule_patch:
  type: push_schedule              # 插入子调度
  mode: openchat
  participants: [B, C]
  scope:
    id: private_b_c
    visibility: private
    members: [B, C]
  max_turns: 4
```

子调度结束后 runtime 自动执行：

```yaml
schedule_patch:
  type: pop_schedule               # 弹出子调度，回到父 schedule
```

### 17.3 动态子调度能表达的场景

- A 指定 B 和 C 私聊
- A 分别和 B、C、D 对话
- B、C、D 临时开小组讨论
- 当前讨论被临时打断，子调度结束后返回主讨论

---

## 18. participant_action — 参与者动作

`participant_action` 表达 scene 中参与者要做什么。

### 18.1 基础 kind 清单

| kind | 说明 | 默认 response |
| --- | --- | --- |
| `speak` | 自由发言 | `mode: text` |
| `choose` | 选择目标 | `mode: structured, schema: choose` |
| `vote` | 投票 | `mode: structured, schema: vote` |
| `action` | 行动决策 | `mode: structured, schema: action` |
| `form` | 自定义表单 | `mode: structured, schema: custom` |
| `narration` | 旁白叙述 | `mode: none` |
| `none` | 不执行动作 | `mode: none` |

### 18.2 speak — 发言

```yaml
participant_action:
  kind: speak
  response:
    mode: text
```

### 18.3 vote — 投票

```yaml
participant_action:
  kind: vote
  target: required                 # none / optional / required
  candidates:                      # 投票候选
    filter:
      source: GAME.players
      where:
        left: alive
        op: equal
        right: true
  response:
    mode: structured
    schema: vote
    include_reason: true            # 是否要求理由字段
```

### 18.4 choose — 选择

```yaml
participant_action:
  kind: choose
  candidates:
    static:
      - id: protect
        text: 守护
      - id: inspect
        text: 查验
  response:
    mode: structured
    schema: choose
```

### 18.5 form — 自定义表单

```yaml
participant_action:
  kind: form
  response:
    mode: structured
    schema:
      fields:
        - name: target
          type: string
          required: true
        - name: reason
          type: string
          required: false
```

---

## 19. controller_action — 剧情控制动作

`controller_action` 用于剧情控制者推动 flow。

### 19.1 controller 类型

| type | 说明 |
| --- | --- |
| `human` | 人类玩家控制剧情 |
| `agent` | AI agent 控制剧情 |
| `system` | 系统自动推进 |
| `plugin` | plugin 机制控制 |
| `none` | 无 controller |

### 19.2 human controller — 选择分支

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: choice
  choices:
    - id: stay
      text: 留下来
      to: stay_scene             # 选项跳转目标 scene
    - id: leave
      text: 离开
      to: leave_scene
```

### 19.3 agent controller — 自由文本

```yaml
controller_action:
  enabled: true
  controller:
    type: agent
    agent_id: narrator           # agent 标识
  kind: free_text
```

### 19.4 plugin controller — 机制驱动

```yaml
controller_action:
  enabled: true
  controller:
    type: plugin
    name: auto_story_driver
  kind: free_text
```

### 19.5 system controller — 旁白

```yaml
controller_action:
  enabled: true
  controller:
    type: system
  kind: narration
```

---

## 20. free_input — 自由输入模式

自由输入模式放在 `controller_action.free_input` 中，用于处理人类玩家的自由发言。

### 20.1 choose_mapping — 自由发言归因到选项

用户可以自由发言，runtime 会将输入归因到已有选项。

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: choice
  choices:
    - id: stay
      text: 留下来
      to: stay_scene
    - id: leave
      text: 离开
      to: leave_scene
  free_input:
    enabled: true
    mode: choose_mapping
    mapper:
      evaluator: plugin
      name: map_free_text_to_choice
```

### 20.2 branch_then_return — 分支后返回

用户自由发言，runtime 派生一段支线，然后回到主线位置。

```yaml
free_input:
  enabled: true
  mode: branch_then_return
  generator:
    evaluator: plugin
    name: generate_temporary_branch
  return_to:
    type: scene                   # scene 或 state
    id: main_choice
```

### 20.3 constrained_continue — 有约束地继续

runtime 可以一段一段生成后续剧情，最终受预定义结局约束。

```yaml
free_input:
  enabled: true
  mode: constrained_continue
  ending:
    candidates: [good_end, bad_end, true_end]
    selector:
      evaluator: plugin
      name: choose_ending_by_progress
      input:
        include_state: true
        include_story_summary: true
  generator:
    evaluator: plugin
    name: generate_constrained_beat
```

### 20.4 free_continue — 自由继续

自由继续，不约束到预定义结局。

```yaml
free_input:
  enabled: true
  mode: free_continue
  generator:
    evaluator: plugin
    name: generate_free_beat
```

### 20.5 grow_flow — 动态生长

动态生长 flow。runtime 生成 patch，不修改原始 DSL。

```yaml
free_input:
  enabled: true
  mode: grow_flow
  patch_store: session            # patch 存储位置
  generator:
    evaluator: plugin
    name: generate_flow_patch
```

---

## 21. resolution — 结算

`resolution` 负责处理 action 或 schedule 产生的结果。

### 21.1 vote 结算

```yaml
resolution:
  selection:
    source: responses             # 来源：responses / controller_result / state
    field: vote                   # 投票字段名
    tie_policy: runoff            # 平票策略
  effects:
    - type: set_state
      path: GAME.last_vote_target
      value:
        ref: RESOLUTION.selected
```

`tie_policy` 可选值：

| 值 | 说明 |
| --- | --- |
| `alphabetical` | 按字母序取第一个 |
| `no_winner` | 平票时无胜者 |
| `all_tied` | 所有平票者并列 |
| `runoff` | 加赛决胜负 |

### 21.2 broadcast 结算

```yaml
resolution:
  effects:
    - type: broadcast
      scope:
        id: public_room
        visibility: public
      message:
        template: "{RESOLUTION.selected} 被投票选中。"
```

---

## 22. publication — 发布与披露

`publication` 表达 scene 结束后发布什么信息、发布给谁。

```yaml
publication:
  messages:                       # 公告消息
    - audience:
        scope: public_room
      content:
        template: "天亮了。"

  disclosures:                    # 私密披露
    - audience:
        players: [seer]
      content:
        ref: GAME.last_inspection_result

  views:                          # 结构化视图
    - id: private-summary
      kind: key-value
      audience:
        players: [seer]
      data:
        rows:
          - label: 查验结果
            value:
              ref: GAME.last_inspection_result
```

### 22.1 audience 路由规则

| audience 写法 | 路由 |
| --- | --- |
| `scope: public_room` | public sink |
| `players: [seer]` | private sink（私发给指定席位） |
| `seats: [Player_1]` | private sink |
| `private: true`（无明确 seat） | 只发给 host |

### 22.2 disclosures 与披露账本

`disclosures` 私发给具体席位时，会把「谁被告知了哪条事实」记入披露账本（DisclosureLedger）。`content.ref` 作为事实键，供 KnowledgeFirewall 后续为该席位合成 actor view。

这是「预言家下一轮 prompt 里带着验人结果」的实现方式。

---

## 23. referee — 裁判

`referee` 负责裁判检查、结束判断、跳转裁定和效果触发。

### 23.1 after_scene 检查

```yaml
referee:
  enabled: true
  check_on: [after_scene]
  rules:
    - when:
        left:
          count:
            ref: GAME.players
            where:
              left: role
              op: equal
              right: wolf
        op: equal
        right: 0
      result:
        end: villagers_win           # 裁判结果：结束游戏
```

### 23.2 after_message 检查

```yaml
referee:
  enabled: true
  check_on: [after_message]
  evaluator: plugin
  name: check_story_should_end
```

### 23.3 include / exclude 范围控制

```yaml
referee:
  enabled: true
  check_on: [after_scene]
  include: [vote_scene, debate_scene]     # 只在这些 scene 检查
  exclude: [intro_scene]                  # 排除这些 scene
  evaluator: plugin
  name: check_game_result
```

### 23.4 check_on 合法值

| 值 | 说明 |
| --- | --- |
| `after_scene` | scene 结束后 |
| `after_message` | 每条 participant message 发布后 |
| `after_round` | schedule round 完成后 |
| `after_generated_beat` | controller free input 生成剧情 beat 后 |

`dynamic.check_on` 只允许 `after_message` 和 `after_round`。

---

## 24. hooks — 生命周期钩子

hook 与 lifespan 结合，使用统一 condition。

```yaml
hooks:
  on_enter:
    - type: set_state
      path: STORY.entered_intro
      value: true

  on_message:
    - when:
        left: MESSAGE.text
        op: contains
        right: "离开"
      do:
        - type: set_state
          path: STORY.intent
          value: leave

  on_exit:
    - type: summarize
      to: STORY.scene_summary
      format: text                  # text 或 object
      include_raw: true             # format=object 时是否保留原始 responses
```

推荐支持的 hook：

| hook | 触发时机 |
| --- | --- |
| `on_enter` | scene 进入 |
| `on_exit` | scene 退出 |
| `on_message` | 每条 participant message |
| `on_before_action` | action 执行前 |
| `on_after_action` | action 执行后 |
| `on_referee_check` | referee 检查前 |
| `on_schedule_push` | 动态子调度 push |
| `on_schedule_pop` | 动态子调度 pop |

---

## 25. effects — 效果清单

effects 是按顺序执行的效果列表。每个 effect 必须是字典、必须有 `type`、可选 `when`。

**禁止使用 `condition`，必须用 `when`。**

### 25.1 状态写入类

#### set_state

```yaml
- type: set_state
  path: GAME.sheriff                # 写入路径 "ENTITY.attr"
  value:
    ref: winner                      # 支持字面量、ref、data.xxx 等
```

#### increment_state

```yaml
- type: increment_state
  path: GAME.round
  amount: 1
```

#### clear

```yaml
- type: clear
  path: GAME.current_deaths
```

### 25.2 集合类

```yaml
- type: add
  path: GAME.sheriff_candidates
  value:
    ref: actor

- type: remove
  path: GAME.sheriff_candidates
  value:
    ref: actor

- type: clear
  path: GAME.sheriff_candidates
```

### 25.3 玩家生死类

#### kill

```yaml
- type: kill
  target:
    ref: data.target               # 支持 winner / actor / data.xxx / 字面量
  cause: werewolf_kill            # 死亡原因
```

#### record_target

```yaml
- type: record_target
  path: GAME.werewolf_target
  source: data.target
```

#### record_current_deaths

```yaml
- type: record_current_deaths
  path: GAME.current_deaths
  causes: [werewolf_kill]          # 可选：只记录指定死因
```

### 25.4 道具类

#### consume_item

```yaml
- type: consume_item
  entity:
    ref: actor
  item: antidote
```

#### give_item

```yaml
- type: give_item
  entity:
    ref: actor
  item: antidote
  count: 1
```

### 25.5 发言顺序

```yaml
- type: build_speech_order
  path: GAME.speech_order
  anchor:
    ref: GAME.sheriff
  filter:
    left: alive
    op: equal
    right: true
  order: clockwise                  # clockwise / counterclockwise
```

### 25.6 关系类

```yaml
- type: set_relation
  relation: lovers
  source:
    ref: data.targets[0]
  target:
    ref: data.targets[1]
  bidirectional: true

- type: clear_relation
  relation: lovers
  source:
    ref: actor

- type: get_relations
  relation: lovers
  source:
    ref: actor
  path: GAME.actor_lovers
```

### 25.7 for_each

```yaml
- type: for_each
  items:
    ref: GAME.current_deaths
  as: item                          # 默认 item
  effects:
    - type: broadcast
      scope: public
      template: "{item} 出局。"
```

### 25.8 pending 效果

```yaml
- type: pending_add
  queue: lovers_death
  item:
    ref: actor

- type: pending_resolve
  queue: lovers_death
  as: item
  clear: true                       # 结算后清空队列
  effects:
    - type: kill
      target:
        ref: item
```

### 25.9 flow_set_next

```yaml
- type: flow_set_next
  state: day                        # 让状态机跳转到指定 state
```

### 25.10 broadcast

```yaml
- type: broadcast
  scope: public
  template: "昨夜死亡玩家：{GAME.current_deaths}"
```

模板支持的变量：

| 表达式 | 说明 |
| --- | --- |
| `{actor}` | 当前 actor |
| `{winner}` | 统计胜者 |
| `{selection_result.xxx}` | 统计结果字段 |
| `{item}` / `{item.xxx}` | for_each/pending 当前项 |
| `{GAME.xxx}` | GAME 状态 |
| `{data.target}` | 本幕响应 data.target |
| `{data.target.role}` | 先取 data.target 得到实体，再读该实体 role |

### 25.11 add_score / advance_turn

```yaml
- type: add_score
  team: good
  value: 1

- type: advance_turn
  filter:
    left: alive
    op: equal
    right: true
  order: clockwise
```

### 25.12 summarize

```yaml
- type: summarize
  to: STORY.scene_summary           # 写入路径 "ENTITY.attr"
  format: text                      # text 或 object
  include_raw: true                 # object 时是否保留原始 responses
```

### 25.13 rule_set_apply

```yaml
- type: rule_set_apply
  result_path: GAME.last_rule_result    # 可选：把结果写入 State
```

需要脚本顶层声明 `rule_set.plugin`。

### 25.14 game_pack 机制类效果

以下效果由 game_pack 机制注册，不在 core 内：

| effect type | 机制 | 说明 |
| --- | --- | --- |
| `roll_dice` | dice | 掷骰，结果写入 GAME.last_roll / GAME.last_rolls |
| `advance_on_track` | dice | 按掷骰在环形轨道上移动 |
| `grant_item` | inventory | 给某实体增加物品 |
| `use_item` | inventory | 消耗某实体的一个计数型物品 |
| `transfer_item` | inventory | 转移物品 |
| `draw_card` | cards | 从牌堆摸牌 |
| `play_card` | cards | 出牌到弃牌堆 |
| `board_place` | board | 在棋盘落子 |
| `credit` | economy | 加钱 |
| `debit` | economy | 扣钱 |
| `transfer` | economy | 转账 |
| `adjust_attr` | stats | 增量修改属性（支持上下限） |
| `tally_votes` | social | 统计票数 |
| `eliminate` | social | 标记出局 |
| `resolve_night` | social | 结算夜晚死亡 |

---

## 26. candidates — 候选集

`candidates` 定义动作/投票/选择的可选目标。

### 26.1 filter

```yaml
candidates:
  filter:
    source: GAME.players
    where:
      left: alive
      op: equal
      right: true
```

### 26.2 static

```yaml
candidates:
  static:
    - id: protect
      text: 守护
    - id: inspect
      text: 查验
```

### 26.3 from_state

```yaml
candidates:
  from_state: GAME.sheriff_candidates
```

### 26.4 extra — 追加额外候选

```yaml
candidates:
  filter:
    left: alive
    op: equal
    right: true
  extra: ["@NO_KILL"]              # 追加固定值
```

### 26.5 when — 逐候选过滤

```yaml
candidates:
  filter:
    left: alive
    op: equal
    right: true
  when:
    left: candidate                 # 当前候选目标
    op: not_equal
    right: actor                    # 当前行动者（不能选自己）
```

---

## 27. response — 响应协议

`response` 控制参与者响应的数据格式。

### 27.1 响应模式

| mode | 说明 |
| --- | --- |
| `none` | 不收集响应（旁白） |
| `text` | 自由文本 |
| `structured` | 结构化 JSON |
| `mixed` | 文本 + 结构化 |

### 27.2 内置 schema

| schema | 输出字段 |
| --- | --- |
| `vote` | `vote: string`，可带 `reason` |
| `choose` | `choose: string`，可带 `reason` |
| `action` | `action: bool`，可带 `target` 和 `reason` |
| `target` | `target: string`，可带 `reason` |
| `targets` | `targets: list[string]`，可带 `reason` |
| `rating` | `rating: int`，可带 `reason` |
| `move` | `move: dict` |
| `card_action` | `card_action: dict`，可带 `reason` |
| `custom` | 使用自定义 fields |

### 27.3 示例

```yaml
response:
  mode: structured
  schema: vote
  include_reason: true              # 要求 reason 字段
  cue: 请选择你要投票的对象。       # 提示文本
```

自定义 schema：

```yaml
response:
  mode: structured
  schema:
    fields:
      - name: target
        type: string
        required: true
      - name: action
        type: string
        required: true
```

---

## 28. visibility — 实体属性可见性

`visibility` 声明实体属性级可见性，替代旧的硬编码秘密属性。

```yaml
visibility:
  secret_attrs: [role, faction]     # 这些属性对他人隐藏；本人与 host/referee 可见
```

规则：

- 未声明（`secret_attrs` 为空）时默认全部公开。
- `secret_attrs` 若拼错（未在 state/players.initial_attrs 中出现），编译期记 warning 但不报错——兼容由 casting/game_pack 运行时动态分配的属性。
- 与 `scope`、`candidates`、`participants` 是四个正交维度，互不冲突。

**四正交维度速查**：

| 维度 | 解决的问题 | 声明位置 |
| --- | --- | --- |
| `scope` | 消息发给谁 | `scene.scope` |
| `visibility.secret_attrs` | 某属性对谁隐藏 | 顶层 `visibility` |
| `candidates` | 能对谁行动 | `participant_action.candidates` |
| `participants` | 谁在场 | `scene.participants` |

---

## 29. guardrail — OOC 内容守卫

判定 agent/玩家发言是否离题、出圈、泄密，在发言写入前按策略处理。可声明在顶层（全局）或 scene 内（覆盖全局）。

```yaml
guardrail:
  enabled: true
  checks: [in_character, on_topic, no_secret_leak]
  on_violation: rewrite                # 违规处理策略
  evaluator:
    kind: llm
    provider: inside
    min_confidence: 0.7
```

`on_violation` 四种策略：

| 策略 | 说明 |
| --- | --- |
| `block` | 拦截，不放行 |
| `rewrite` | 改写回场景内再放行 |
| `soft_warn` | 打标放行（host 可观测） |
| `pass_with_flag` | 记录放行（轻量版 soft_warn） |

`enabled: false` 时完全旁路，零开销。

与 KnowledgeFirewall 正交：firewall 管**流入**的信息（防开天眼），GuardRail 管**产出**的内容（防出圈泄密）。

---

## 30. patch — 动态生长模型

动态能力不修改原始 DSL。runtime 维护三层数据：

| 层 | 说明 |
| --- | --- |
| `base_flow` | 原始 DSL（不可变） |
| `patch_journal` | 运行时生成的 patch（append-only） |
| `materialized_flow` | base_flow + patch_journal 合成后的可执行 flow |

### 30.1 flow_patch — 添加场景

```yaml
flow_patch:
  type: add_scene
  scene:
    id: generated_escape_scene
    type: scene
    scope:
      id: story
      visibility: public
    schedule:
      mode: none
    participant_action:
      kind: none
    controller_action:
      enabled: true
      controller:
        type: human
      kind: free_text
      free_input:
        enabled: true
        mode: constrained_continue
        generator:
          evaluator: plugin
          name: generate_constrained_beat
```

### 30.2 schedule_patch — 插入/弹出子调度

```yaml
schedule_patch:
  type: push_schedule
  mode: openchat
  participants: [A, B]
  scope:
    id: private_a_b
    visibility: private
    members: [A, B]
  max_turns: 4
```

```yaml
schedule_patch:
  type: pop_schedule
```

校验规则：

- `add_transition` 只能连接已存在的 state。
- `add_scene.state` 如果显式声明，也必须是已存在 state。
- runtime 不会因为 patch 自动创建新的 state machine 节点。
- `push_schedule` 写入 journal 后，无论子调度是否抛错，runtime 都会写入配对的 `pop_schedule`。

---

## 31. extensions — 领域扩展

`extensions` 是顶层领域扩展声明，声明脚本需要哪些领域能力。

```yaml
extensions:
  board:
    enabled: true
    version: "0.1"
    config: {}
  cards:
    enabled: true
  dice: {}
  economy: {}
  story: {}
```

内置可声明的 domain extension：

| extension | 说明 |
| --- | --- |
| `board` | 棋盘/地图状态、坐标、移动和落子 |
| `cards` | 通用卡牌、手牌、牌堆、弃牌堆 |
| `dice` | 骰子、随机检定、可回放随机事件 |
| `economy` | 资源、货币、交易、经济状态 |
| `story` | 剧情、地点、任务、NPC、叙事状态 |

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled` | bool | 是否启用 |
| `version` | string | 期望版本 |
| `config` | dict | 扩展私有配置 |

**注意**：`extensions.cards` 不表示 UNO，`extensions.board` 不表示五子棋。具体游戏规则应通过 game_pack / rule_set 引入。

---

## 32. game_pack / rule_set — 游戏包与规则集

### 32.1 game_pack

```yaml
game_pack:
  plugin: builtin.party.free_discussion
  version: "0.1"
  config: {}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `plugin` | string | 是 | 游戏包插件 ID |
| `version` | string | 可选 | 版本 |
| `config` | dict | 可选 | 私有配置 |

### 32.2 rule_set

```yaml
extensions:
  board:
    enabled: true
rule_set:
  plugin: builtin.board.generic
  version: "0.1"
  config: {}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `plugin` | string | 是 | 规则集插件 ID |
| `version` | string | 可选 | 版本 |
| `config` | dict | 可选 | 私有配置 |

当前内置声明：

| 类型 | plugin | 说明 |
| --- | --- | --- |
| game_pack | `builtin.party.free_discussion` | 自由讨论/投票派对 |
| rule_set | `builtin.board.generic` | 通用棋盘规则接口 |
| rule_set | `builtin.cards.generic` | 通用卡牌规则接口 |
| rule_set | `builtin.story.generic` | 通用剧情规则接口 |

---

## 33. vocab / concepts — 词汇与概念

### 33.1 vocab

`vocab` 为角色、阵营、scope 等提供自然语言词汇。

```yaml
vocab:
  roles:
    werewolf: 狼人
    villager: 平民
  factions:
    good: 好人阵营
    evil: 狼人阵营
  scopes:
    public: 公共频道
```

### 33.2 concepts

`concepts` 解释 DSL 中出现的角色、能力、道具、阵营等概念，帮助 actor 理解游戏。

```yaml
concepts:
  roles:
    seer:
      display_name: 预言家
      description: 每晚可以查验一名玩家的阵营。
  factions:
    good:
      display_name: 好人阵营
      description: 消灭狼人后获胜。
  scopes:
    public:
      display_name: 公共频道
      description: 所有玩家可见。
  abilities:
    seer_check:
      display_name: 查验
      description: 查看目标阵营。
      prompt: 你可以查验一名玩家的阵营。
  items:
    antidote:
      display_name: 解药
      description: 女巫可以使用一次解药救人。
```

校验建议：

- `concepts.roles` 应覆盖 `roles[].name`。
- `concepts.factions` 应覆盖 `roles[].faction`。
- `concepts.abilities` 应覆盖 `roles[].abilities`。
- `concepts.items` 应覆盖 `roles[].inventory[].item`。

---

## 34. publish — 发布元信息

面向 UGC / marketplace 的发布元信息，不影响游戏运行。

```yaml
publish:
  id: party_test
  version: "0.1.0"
  visibility: private                 # private / unlisted / public
  tags: [party, test]
  required_extensions: [board]
  license: MIT
  homepage: https://example.com
  repository: https://example.com/repo.git
```

---

## 35. game_pack 机制清单

以下机制由 game_pack 注册，在 DSL 中通过 effect / condition 引用。

### 35.1 dice — 骰子

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `roll_dice` | `sides`/`count` | 标准骰（面数+个数），结果写入 GAME.last_roll(总和)/GAME.last_rolls(明细) |
| `roll_dice` | `faces`/`weights` | 自定义面值+加权概率 |
| `roll_dice` | `die`/`dice` | 引用 GAME.dice_defs 里的具名骰子 |
| `roll_dice` | `to` | 可选 "ENTITY.attr"，把总和累加到该属性 |
| `advance_on_track` | `track_size`/`actor`/`steps` | 按掷骰在环形轨道上移动 |

骰子定义写在 `initial_state.GAME.dice_defs`：

```yaml
initial_state:
  GAME:
    dice_defs:
      attack:
        faces: ["hit", "miss"]
        weights: [0.3, 0.7]
      d20:
        sides: 20
      catan:
        faces: [0, 0, 1, 1, 2, 5]
```

### 35.2 inventory — 背包/道具

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `grant_item` | `target`/`item`/`count`/`attrs` | 增加物品（计数型加数量/富属性型写入 items） |
| `use_item` | `target`/`item`/`count` | 消耗计数型物品 |
| `transfer_item` | `giver`/`receiver`/`item`/`count` | 转移物品 |

**条件**：

| condition | 说明 |
| --- | --- |
| `inventory.has_item` | 判断某实体是否拥有某物品 |

两种物品形态：

- **计数型**：存 `<entity>.inventory_<item>` = 数量或 "unlimited"
- **富属性型**：存 `<entity>.items` = {item: {attrs}}

### 35.3 stats — 角色面板

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `adjust_attr` | `target`/`attr`/`delta`/`min`/`max` | 增量修改属性，支持上下限夹取 |

**条件**：

| condition | 说明 |
| --- | --- |
| `stats.attr_at_least` | 判断某属性 >= 阈值 |
| `stats.attr_below` | 判断某属性 < 阈值 |

### 35.4 cards — 卡牌

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `draw_card` | `target`/`count` | 从牌堆摸牌 |
| `play_card` | `target`/`card` | 出牌到弃牌堆 |

**条件**：

| condition | 说明 |
| --- | --- |
| `cards.hand_empty` | 判断手牌是否为空 |

存储约定：牌堆 `GAME.deck`，弃牌堆 `GAME.discard`，手牌 `<entity>.hand`。

### 35.5 economy — 经济

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `credit` | `target`/`amount` | 加钱 |
| `debit` | `target`/`amount`/`allow_negative` | 扣钱（不足可触发破产） |
| `transfer` | `payer`/`payee`/`amount` | 转账 |

**条件**：

| condition | 说明 |
| --- | --- |
| `economy.bankrupt` | 判断是否破产 |

存储约定：现金 `<entity>.cash`。

### 35.6 social — 社交推理

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `tally_votes` | `field`/`to`/`tie` | 统计票数 |
| `eliminate` | `target`/`from` | 标记出局 |
| `resolve_night` | 自动读取 GAME.night_target / guard_target / witch_save | 结算夜晚死亡 |

**条件**：

| condition | 说明 |
| --- | --- |
| `social.faction_cleared` | 判断某阵营存活数是否为 0 |

### 35.7 board — 棋盘

**效果**：

| effect | 字段 | 说明 |
| --- | --- | --- |
| `board_place` | `cell`/`position`/`piece` | 在棋盘落子 |

**条件**：

| condition | 说明 |
| --- | --- |
| `board.connect_n` | 判断最近一手是否形成 n 连 |
| `board.cell_empty` | 判断指定坐标是否为空 |

存储约定：棋盘 `GAME.board`（dict: "row,col" -> 棋子）。

---

## 36. ref 与状态路径速查

| ref / 路径 | 说明 |
| --- | --- |
| `actor` | 当前行动者 ID |
| `actor.xxx` | 当前行动者的属性 |
| `candidate` | 当前候选目标 ID |
| `candidate.xxx` | 当前候选目标的属性 |
| `entity` | 当前 filter 检查的实体 ID |
| `entity.xxx` | 当前 filter 实体的属性 |
| `GAME.xxx` | 全局游戏状态 |
| `STORY.xxx` | 剧情状态（约定放在 GAME 下或单独 STORY 前缀） |
| `data.xxx` | 本幕第一条响应 data 字段 |
| `data.target.role` | 先取 data.target 得实体，再读属性 |
| `responses` / `responses.xxx` | 响应列表 |
| `winner` / `winner.xxx` | 投票/选择统计胜者 |
| `selection_result.xxx` | 统计结果字段 |
| `RESOLUTION.selected` | resolution 选出的结果 |
| `item` / `item.xxx` | for_each/pending 当前项 |
| `result` / `result.xxx` | http/llm evaluator 返回结果 |
| `MESSAGE.text` | 当前触发消息文本 |

---

## 37. 完整示例：狼人杀场景

```yaml
meta:
  id: werewolf_v1_guard
  name: werewolf_v1_guard
  display_name: 12人狼人杀守卫局
  version: "1.0.0"
  tags: [party, social-deduction]

runtime:
  type: interactive_session

roles:
  - name: werewolf
    display_name: 狼人
    faction: evil
  - name: villager
    display_name: 平民
    faction: good
  - name: seer
    display_name: 预言家
    faction: good
    brief: 每晚可以查验一名玩家的阵营
  - name: witch
    display_name: 女巫
    faction: good
    inventory:
      - item: antidote
        display_name: 解药
        description: 可以救人
        count: 1
  - name: hunter
    display_name: 猎人
    faction: good
  - name: guard
    display_name: 守卫
    faction: good

players:
  count: 12
  initial_attrs:
    alive: true
    seat_index: 0
  casting:
    type: shuffle
    distribution:
      werewolf: 4
      villager: 4
      seer: 1
      witch: 1
      hunter: 1
      guard: 1

visibility:
  secret_attrs: [role, faction]

guardrail:
  enabled: true
  checks: [in_character, on_topic, no_secret_leak]
  on_violation: rewrite
  evaluator:
    kind: llm
    provider: inside

initial_state:
  GAME:
    round: 0
    current_deaths: []

flow:
  type: state_machine
  initial: night

  states:
    night:
      scenes:
        - day_discussion
      transitions:
        - to: day
          when:
            left: GAME.phase
            op: equal
            right: night_end

    day:
      scenes:
        - day_discussion
        - day_vote
      transitions:
        - to: night
          when:
            left: GAME.phase
            op: equal
            right: night_start

scenes:
  day_discussion:
    type: scene
    scope:
      id: public_room
      visibility: public

    participants:
      filter:
        source: GAME.players
        where:
          left: alive
          op: equal
          right: true

    schedule:
      mode: openchat
      max_turns: 12
      dynamic:
        enabled: true
        check_on: after_message
        detector:
          evaluator: plugin
          name: detect_schedule_request
        allowed:
          modes: [single, sequential, openchat]
          participant_count:
            min: 2
            max: 4
          scope_visibility: [private]
          max_turns:
            default: 4
            max: 8
        patch:
          type: push_schedule
          return_to_parent: true
        merge_back:
          mode: summary
          to: SCENE.private_talk_summary

    participant_action:
      kind: speak
      response:
        mode: text

    controller_action:
      enabled: false

  day_vote:
    type: scene
    scope:
      id: public_room
      visibility: public

    participants:
      filter:
        source: GAME.players
        where:
          left: alive
          op: equal
          right: true

    schedule:
      mode: simultaneous
      timeout_ms: 30000

    participant_action:
      kind: vote
      target: required
      candidates:
        filter:
          source: GAME.players
          where:
            left: alive
            op: equal
            right: true
      response:
        mode: structured
        schema: vote
        include_reason: true

    controller_action:
      enabled: false

    resolution:
      selection:
        source: responses
        field: vote
        tie_policy: runoff
      effects:
        - type: set_state
          path: GAME.last_vote_target
          value:
            ref: RESOLUTION.selected

    referee:
      enabled: true
      check_on: [after_scene]
      rules:
        - when:
            left:
              count:
                ref: GAME.players
                where:
                  left: role
                  op: equal
                  right: werewolf
            op: equal
            right: 0
          result:
            end: villagers_win
```

---

## 38. 完整示例：文字冒险

```yaml
meta:
  id: text_adventure_rainy_night
  display_name: 雨夜旧宅
  version: "1.0.0"
  tags: [story, text-adventure]

runtime:
  type: interactive_session

players:
  count: 1
  ids: [Player_1]
  initial_attrs:
    alive: true

roles: []

visibility:
  secret_attrs: []                   # 无秘密，全部公开

initial_state:
  GAME:
    round: 0
  STORY:
    started: true
    risk: 0
    affection: 0

flow:
  type: sequence
  scenes:
    - intro
    - first_choice

scenes:
  intro:
    type: scene
    scope:
      id: story
      visibility: public
    participants:
      static: []
    schedule:
      mode: none
    participant_action:
      kind: none
    controller_action:
      enabled: true
      controller:
        type: system
      kind: narration
    publication:
      messages:
        - audience:
            scope: story
          content:
            text: "雨夜，你站在旧宅门口。"

  first_choice:
    type: scene
    scope:
      id: story
      visibility: public
    participants:
      static: []
    schedule:
      mode: none
    participant_action:
      kind: none
    controller_action:
      enabled: true
      controller:
        type: human
      kind: choice
      choices:
        - id: enter_house
          text: 进入旧宅
          to: enter_house_scene
        - id: leave
          text: 转身离开
          to: leave_scene
      free_input:
        enabled: true
        mode: choose_mapping
        mapper:
          evaluator: plugin
          name: map_free_text_to_choice

    referee:
      enabled: true
      check_on: [after_scene]
      evaluator: plugin
      name: check_story_should_end

concepts:
  scopes:
    story:
      display_name: 剧情频道
      description: 剧情叙事可见域
```

---

## 39. 常见错误与校验规则

### 39.1 必填字段缺失

| 缺失字段 | 级别 | 说明 |
| --- | --- | --- |
| `meta` | error | 必须有剧本元信息 |
| `runtime` | warning | 不写默认 game_session，interactive_session 场景应显式声明 |
| `players` | error | 必须有玩家定义 |
| `scenes` | error | 必须至少有一个 scene |
| `flow` | error | 必须有流程定义 |

### 39.2 引用完整性

| 错误 | 级别 | 说明 |
| --- | --- | --- |
| `scene.scope` 引用未定义 scope | error | scope 必须已定义 |
| `flow.transition.to` 引用未定义 state | error | state 必须已存在 |
| `casting.distribution` key 不是已定义 role | error | 角色名必须已定义 |
| `controller_action.choices[].to` 引用未定义 scene | error | scene 必须已存在 |

### 39.3 禁用字段

| 禁用字段 | 替代 | 说明 |
| --- | --- | --- |
| `effects[].condition` | `effects[].when` | 已删除 |
| `referee.conditions[].condition` | `when` | 已删除 |
| `scene.type`（顶层） | 删除或用 scene_type | 已删除 |
| `turn_policy` | `schedule.mode` | 已删除 |
| `performers` | `participants` | 已删除 |
| `collect` | `response` | 已删除 |

### 39.4 语义 lint 建议

| 建议 | 级别 | 说明 |
| --- | --- | --- |
| 使用 `left / op / right` 代替旧条件字段 | warning | 旧写法仅兼容历史脚本 |
| `python` 条件应改用 `evaluator: code` | warning | python 是 legacy |
| `when` 中读取状态建议显式 `{ref: ...}` | info | 风格建议 |
| `concepts` 应覆盖所有 roles/factions/scopes | warning | 帮助 actor 理解 |
| `visibility.secret_attrs` 声明是唯一事实来源 | info | 未声明时默认全部公开 |

### 39.5 check_on 值校验

| 位置 | 合法值 |
| --- | --- |
| `referee.check_on` | `after_scene` / `after_message` / `after_round` / `after_generated_beat` |
| `dynamic.check_on` | `after_message` / `after_round` |
| `hooks` 触发时机 | `on_enter` / `on_exit` / `on_message` / `on_before_action` / `on_after_action` / `on_referee_check` / `on_schedule_push` / `on_schedule_pop` |

---

> **文档维护约定**：每次修改 DSL 相关代码（compiler / normalizer / models / conditions / effects / candidates / game_packs）时，应同步更新本文档。
