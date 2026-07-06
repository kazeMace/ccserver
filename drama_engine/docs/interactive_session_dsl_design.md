# Interactive Session DSL Design

本文记录 `interactive_session` runtime 的新版 DSL 设计。本文中的所有示例都使用新版语法：

- 条件统一使用 `left / op / right`
- 内置条件判断统一使用 `evaluator: builtin`
- 机制型能力统一优先使用 `plugin`
- 剧情动态生长使用 patch，不直接修改原始 DSL
- scene 内部使用 `scope / participants / schedule / participant_action / controller_action / resolution / publication / referee / hooks`

## 1. 设计目标

`interactive_session` 用于统一表达两类互动内容：

1. 多 agent 参与的玩法型流程，例如狼人杀、桌游、社交推理、分组讨论。
2. 由人类、agent、system 或 plugin 推动的剧情型流程，例如文字冒险、galgame、动态分支剧情。

核心原则：

- `flow` 负责流程推进。
- `scene` 负责一段玩法或剧情生命周期。
- `schedule` 负责参与者之间如何互动。
- `participant_action` 负责参与者做什么。
- `controller_action` 负责剧情控制者如何推动剧情。
- `when / condition / referee / hook / plugin` 作为全局通用组件。

## 2. Runtime

```yaml
runtime:
  type: interactive_session
```

`interactive_session` 不要求一定有人类参与。

- 没有人类的狼人杀：所有 seat 都由 agent 扮演，`controller_action.enabled: false`。
- 没有人类的剧情演绎：`controller_action.controller.type` 可以是 `agent`、`system` 或 `plugin`。
- 有人类参与时，人类可以是某个 participant，也可以是剧情 controller。

## 3. Flow

`flow` 只有两种类型：

- `sequence`
- `state_machine`

`sequence` 是特殊的 `state_machine`，它只有一个 state，并按顺序执行 scenes。

### 3.1 Sequence

```yaml
flow:
  type: sequence
  scenes:
    - intro
    - first_choice
    - ending
```

### 3.2 State Machine

```yaml
flow:
  type: state_machine
  initial: start

  states:
    start:
      scenes:
        - intro
      transitions:
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
```

### 3.3 Loop

循环不是 `sequence` 的特殊能力，而是 `state_machine` transition 的自然结果。

```yaml
flow:
  type: state_machine
  initial: debate

  states:
    debate:
      scenes:
        - public_debate
      transitions:
        - to: debate
          when:
            left: GAME.vote_ready
            op: equal
            right: false

        - to: vote
          when:
            left: GAME.vote_ready
            op: equal
            right: true

    vote:
      scenes:
        - day_vote
```

## 4. Scene

`scene` 是一段玩法或剧情生命周期，不等同于一次发言或一次对话。

标准结构：

```yaml
scenes:
  intro:
    type: scene
    scope:
      id: public_room
      visibility: public

    when:
      left: STORY.started
      op: equal
      right: true

    participants:
      static: []

    schedule:
      mode: none

    participant_action:
      kind: none

    controller_action:
      enabled: false

    resolution:
      effects: []

    publication:
      messages: []

    referee:
      enabled: false

    hooks: {}
```

字段职责：

| 字段 | 职责 |
| --- | --- |
| `scope` | 默认消息域，控制哪些消息进入哪些 agent 上下文 |
| `when` | scene 是否可执行 |
| `participants` | 当前 scene 的参与者 |
| `schedule` | 参与者如何互动、谁能发言、是否允许动态子调度 |
| `participant_action` | 每个参与者执行的动作 |
| `controller_action` | 剧情控制者执行的动作 |
| `resolution` | 汇总结果并修改状态 |
| `publication` | scene 结束后向指定 audience 发布信息 |
| `referee` | 裁判检查、结束判断、跳转裁定 |
| `hooks` | 生命周期钩子 |

## 5. Condition And When

`when` 在所有位置都使用同一套 condition 组件。

### 5.1 Simple Condition

```yaml
when:
  left: GAME.round
  op: greater_than_equal
  right: 1
```

### 5.2 Reference Operand

左右两侧都可以是普通值，也可以是结构化 operand。

```yaml
when:
  left:
    ref: GAME.alive_players
  op: greater_than
  right: 3
```

### 5.3 Count Operand

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

### 5.4 Compound Condition

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

```yaml
when:
  not:
    left: PLAYER.alive
    op: equal
    right: true
```

## 6. Evaluator

条件判断统一抽象为 evaluator。

### 6.1 Builtin

```yaml
when:
  evaluator: builtin
  condition:
    left: GAME.round
    op: greater_than_equal
    right: 1
```

### 6.2 Code

```yaml
when:
  evaluator: code
  language: python
  env:
    DEBUG: "1"
  code: |
    result = state["GAME"]["round"] >= 1
```

`language` 可以支持 `python`、`shell`、`bun_js` 等运行环境。

### 6.3 HTTP

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

如果 `input` 未定义，runtime 默认发送完整上下文，包括 state、players、scene、messages、responses。
如果声明 `input.include_*`，runtime 会只发送声明的上下文片段，并解析显式字段中的
`{ref: ...}`：

| 字段 | 输入内容 |
| --- | --- |
| `include_state` | `state` 快照 |
| `include_players` | `players` 列表 |
| `include_participants` | 当前参与者列表 |
| `include_messages` | 当前消息/响应列表 |
| `include_recent_messages` | 最近消息，支持 `recent_limit` |
| `include_message` | 当前触发消息 |
| `include_story_summary` | `STORY` 状态摘要 |
| `include_responses` | 当前 responses |
| `include_patch_journal` | patch journal |
| `include_metadata` | 可序列化 metadata |

runtime 会维护本 session 的 message history。participant message、generated beat 等
会进入该历史；如果历史为空，`include_messages/include_recent_messages` 会回退到当前
`responses/last_responses`。

### 6.4 LLM

```yaml
when:
  evaluator: llm
  provider: inside
  semantic_id: judge_story_progress
  input:
    include_state: true
    include_recent_messages: true
```

`provider: inside` 是默认值，表示通过 ccserver 内部 agent 能力执行。
真实运行时未注入 `inside_agent/llm_client` 时，会尝试实例化隐藏 ccserver `Agent`
执行；dry-run 或内部 Agent 初始化失败时，才回退到 actor/builtin fallback。

### 6.5 Plugin

```yaml
when:
  evaluator: plugin
  name: choose_ending_by_progress
  input:
    include_state: true
    include_story_summary: true
```

复杂机制优先实现为 plugin。plugin 可以内置 prompt、schema、fallback、校验逻辑，也可以内部调用 LLM、HTTP 或代码执行器。

### 6.6 Runtime Interaction Protocol

runtime service、HTTP evaluator、LLM/inside evaluator 使用同一个可扩展交互协议。
协议版本固定写在 envelope 里，便于后续增加字段时保持兼容。

```yaml
protocol:
  name: interactive_session
  version: "1.0"
  schema: interactive_session.v1

call:
  id: optional_call_id
  name: plan_openchat_next
  purpose: openchat_planner
  provider: plugin
  endpoint: null
  hook: after_message
  runtime_type: interactive_session

input:
  # 由 input/include_* 或默认完整上下文生成

context:
  runtime_type: interactive_session
  state: {}
  players: []
  participants: []
  current_state: main
  current_scene: day_discussion
  last_responses: []
  messages: []
  patches: []
  metadata: {}
  base_flow: {}

metadata:
  current_state: main
  current_scene: day_discussion
```

字段约定：

| 字段 | 说明 |
| --- | --- |
| `protocol.schema` | 协议版本标识，当前为 `interactive_session.v1` |
| `call.purpose` | 本次调用目的，例如 `participants`、`controller`、`schedule_detector`、`openchat_planner`、`schedule_merge_back`、`story_generator`、`flow_patch_generator`、`condition_evaluator` |
| `call.provider` | `builtin`、`plugin`、`http`、`inside`、`llm` |
| `input` | 按 DSL `input` 物化后的入参；未声明 `input` 时使用完整上下文 |
| `context` | 完整可序列化运行时上下文，用于外部服务需要更多信息时读取 |
| `metadata` | 协议级扩展信息，只放可序列化数据，不放 Python 对象 |

HTTP runtime service 和 HTTP evaluator 会收到完整 envelope，同时保留历史兼容字段
`id/name/purpose/endpoint/input/context`。inside provider 未显式写 `prompt` 时，默认
把 envelope 编码为 JSON 交给 ccserver `Agent.run()`。

plugin runtime service 为了兼容已有 Python 插件，默认仍接收扁平 `input` payload。如果
插件希望直接接收统一 envelope，可以声明：

```yaml
planner:
  provider: plugin
  name: plan_openchat_next
  protocol: envelope
```

或：

```yaml
planner:
  provider: plugin
  name: plan_openchat_next
  envelope: true
```

所有 provider 的返回值都必须是 dict。常见返回字段如下：

| purpose | 常见返回字段 |
| --- | --- |
| `participants` | `participants`、`selected`、`members` |
| `controller` | `text`、`data` |
| `schedule_detector` | `patch` |
| `openchat_planner` | `next_speaker`、`cue`、`stop` |
| `schedule_merge_back` | `value` |
| `story_generator` | `text`、`beats` |
| `flow_patch_generator` | `patch`、`flow_patch` |
| `condition_evaluator` | `result`、`passed`、`ended`、`confidence` |

## 7. Scope

`scope` 是消息域，不只是可见性。

### 7.1 Public Scope

```yaml
scope:
  id: public_room
  visibility: public
```

### 7.2 Private Scope

```yaml
scope:
  id: private_a_b
  visibility: private
  members: [A, B]
```

需要区分三种 scope 相关语义：

- `scene.scope`：scene 默认消息域。
- `schedule.dynamic` 生成的临时 scope：动态子调度的消息域。
- `publication.audience`：scene 结束后发布信息的目标。

## 8. Participants

参与者可以静态指定，也可以通过条件筛选。

### 8.1 Static Participants

```yaml
participants:
  static: [A, B, C, D]
```

### 8.2 Filter Participants

```yaml
participants:
  filter:
    source: GAME.players
    where:
      left: alive
      op: equal
      right: true
```

### 8.3 Plugin Participants

```yaml
participants:
  evaluator: plugin
  name: select_current_scene_participants
  input:
    include_state: true
    include_players: true
```

也支持简写：

```yaml
participants:
  plugin: select_current_scene_participants
```

## 9. Schedule

`schedule` 负责参与者之间如何互动。它不决定“做什么”，只决定“谁在什么时候执行
`participant_action`、执行几轮、是否允许中途插入动态子调度”。

常用字段：

| 字段 | 作用 |
| --- | --- |
| `mode` | 调度模式，决定 actor 顺序和并发语义 |
| `actor` | `single/openchat` 的首个或指定 actor，支持字面量或 `{ref: ...}` |
| `order` | 顺序策略或 planner 简写 |
| `planner` | `openchat` 每轮后决定下一位、下一段 cue、是否停止的 runtime service |
| `opening` / `cue` | 首轮提示词 |
| `max_turns` | `openchat` 最多发言段数 |
| `max_rounds` | `loop_until` 或子调度最多轮数 |
| `timeout_ms` | 单次 actor 调用或一组 simultaneous 调用的超时时间 |
| `stop_when` / `until` | 每段或每轮后检查的停止条件 |
| `dynamic` | 根据发言临时 push/pop 子调度 |

基础 mode：

- `none`
- `single`
- `sequential`
- `simultaneous`
- `random_order`
- `openchat`
- `loop_until`

### 9.1 None

```yaml
schedule:
  mode: none
```

### 9.2 Single

```yaml
schedule:
  mode: single
  actor:
    ref: GAME.current_player
```

### 9.3 Sequential

```yaml
schedule:
  mode: sequential
  order:
    source: participants
    strategy: seat_order
```

`strategy: seat_order` 会优先使用玩家状态上的 `seat_index` 排序；没有 `seat_index`
时使用玩家名末尾数字的自然顺序兜底。`reverse_seat_order` 使用同一排序反向执行。

### 9.4 Simultaneous

```yaml
schedule:
  mode: simultaneous
  timeout_ms: 30000
```

`simultaneous` 会以协程并发收集所有 actor 响应。超过 `timeout_ms` 的 actor 会被取消，
已完成的响应仍然进入 resolution 和 referee。

`sequential`、`single`、`openchat` 会在每条 participant message 发布后立即执行
`hooks.on_message` 和 `referee.check_on: after_message`。如果 referee 返回终局或跳转，
runtime 不再请求后续 actor 发言。`simultaneous` 因为并发语义，只能在本批已完成响应
返回后逐条检查。

### 9.5 Openchat

```yaml
schedule:
  mode: openchat
  actor: A
  opening: A 先开场，然后由 planner 决定下一位。
  planner:
    evaluator: plugin
    name: plan_openchat_next
  max_turns: 12
  stop_when:
    evaluator: builtin
    condition:
      left: SCENE.ready_to_end
      op: equal
      right: true
```

`openchat` 是开放聊天调度。runtime 每次只让一个 actor 发言，发言后立即发布，并在下一轮前调用 `planner` 或 builtin 轮转策略决定下一位 actor、下一段 cue 或是否停止。

`openchat` 与 `dialogue_policy` 的关系是：在 `interactive_session` 中，`openchat`
是 `schedule.mode` 的一种，承担旧 `dialogue_policy.mode: openchat` 的运行语义。
它可以由一个 agent 或真人发言触发，再由 planner 设计开场、下一位发言者和停止时机。

### 9.6 Loop Until

```yaml
schedule:
  mode: loop_until
  max_rounds: 5
  stop_when:
    evaluator: plugin
    name: check_discussion_complete
```

## 10. Dynamic Schedule

动态私聊、分组聊、指定两人对话不是新的 flow 节点，也不是新的 scene，而是当前 `schedule` 的动态子调度能力。

运行时根据发言即时生成 `schedule_patch`，执行后回到父 schedule。

```yaml
schedule:
  mode: openchat
  max_turns: 12

  dynamic:
    enabled: true
    check_on: after_message

    detector:
      evaluator: plugin
      name: detect_schedule_request
      input:
        include_message: true
        include_state: true
        include_participants: true

    allowed:
      modes: [single, sequential, openchat]
      participant_count:
        min: 2
        max: 4
      scope_visibility: [private, public]
      max_turns:
        default: 4
        max: 8

    patch:
      type: push_schedule
      return_to_parent: true

    merge_back:
      mode: summary
      to: SCENE.dynamic_schedule_summary
```

`dynamic.check_on` 决定什么时候运行 `detector`：

| 值 | 触发时机 | `include_message` / `source_response` 形状 |
| --- | --- | --- |
| `after_message` | 每条 participant message 发布、`hooks.on_message` 与 `referee.check_on: after_message` 检查之后 | 当前单条 actor response，例如 `{actor, text, data}` |
| `after_round` | 当前 schedule round 完成后、`schedule.stop_when` 与下一轮 planner 之前 | `{kind: round_completed, data: {responses: [...]}, text: ""}` |

`single`、`sequential`、`simultaneous` 的 `after_round` 表示一轮 actor 集合执行完成。
`openchat` 每个 turn 只收集一个 actor，因此 `after_round` 表示一个开放聊天 turn 完成。

`after_message` 适合根据某个 actor 的发言即时插入子调度，例如“A 要求 B 和 C 开放聊天”。
`after_round` 适合等本轮公开发言都完成后再分析是否插入私聊、分组聊或补充讨论。

`after_generated_beat` 不属于 `schedule.dynamic.check_on`，它属于 `referee.check_on`，
用于 controller free input 生成剧情 beat 后逐段判断是否结束。

detector 会收到统一 runtime service payload。最常用的输入声明是：

```yaml
detector:
  provider: plugin
  name: detect_schedule_request
  input:
    include_message: true
    include_recent_messages: true
    include_state: true
    include_participants: true
```

detector 返回值必须包含 `patch`，或 actor response 的 `data.schedule_patch` 直接提供 patch。

基于某个 agent 的发言，plugin 可以生成：

```yaml
schedule_patch:
  type: push_schedule
  mode: openchat
  participants: [B, C]
  scope:
    id: private_b_c
    visibility: private
    members: [B, C]
  max_turns: 4
```

如果 `mode: openchat` 的 patch 没有显式声明 `scope`，runtime 默认创建 public scope；需要私聊或小组隐藏对话时必须显式设置 `scope.visibility: private` 和 `members`。

动态子调度的 `mode: openchat` 与父级 openchat 语义一致：

- 每次只收集一个 actor。
- `first_speaker` / `actor` 决定第一位发言者。
- `opening` / `cue` 决定开场提示。
- `planner` 可以在每段后返回 `next_speaker`、`cue` 或 `stop: true`。
- 没有 `planner` 时 runtime 使用稳定轮转 fallback。
- `timeout_ms` 和 `stop_when` 在子调度中同样生效。

子调度结束后 runtime 执行：

```yaml
schedule_patch:
  type: pop_schedule
```

这种设计可以表达：

- A 指定 B 和 C 私聊。
- A 分别和 B、C、D 对话。
- B、C、D 临时开小组讨论。
- 当前讨论被临时打断，子调度结束后返回主讨论。

## 11. Participant Action

`participant_action` 表达 scene 中参与者要做什么。

基础动作：

- `speak`
- `choose`
- `vote`
- `action`
- `form`
- `narration`
- `none`

### 11.1 Speak

```yaml
participant_action:
  kind: speak
  response:
    mode: text
```

### 11.2 Vote

```yaml
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
```

### 11.3 Choose

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

### 11.4 Form

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

## 12. Controller Action

`controller_action` 用于剧情控制者推动 flow。

controller 类型：

- `human`
- `agent`
- `system`
- `plugin`
- `none`

### 12.1 Human Controller

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
```

### 12.2 Agent Controller

```yaml
controller_action:
  enabled: true
  controller:
    type: agent
    agent_id: narrator
  kind: free_text
```

### 12.3 Plugin Controller

```yaml
controller_action:
  enabled: true
  controller:
    type: plugin
    name: auto_story_driver
  kind: free_text
```

## 13. Free Input Modes

自由输入模式放在 `controller_action.free_input` 中。

### 13.1 Choose Mapping

用户可以自由发言，但 runtime 会将输入归因到已有选项。

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

### 13.2 Branch Then Return

用户自由发言，runtime 派生一段支线，然后回到主线位置。

```yaml
free_input:
  enabled: true
  mode: branch_then_return
  generator:
    evaluator: plugin
    name: generate_temporary_branch
  return_to:
    type: scene
    id: main_choice
```

`return_to.type` 可以是 `scene` 或 `state`。

### 13.3 Constrained Continue

有约束地继续。runtime 可以一段一段生成后续剧情，也可以穿插用户交互，但最终受预定义结局或目标约束。

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

### 13.4 Free Continue

自由继续，不约束到预定义结局。

```yaml
free_input:
  enabled: true
  mode: free_continue
  generator:
    evaluator: plugin
    name: generate_free_beat
```

### 13.5 Grow Flow

动态生长 flow。runtime 生成 patch，不修改原始 DSL。

```yaml
free_input:
  enabled: true
  mode: grow_flow
  patch_store: session
  generator:
    evaluator: plugin
    name: generate_flow_patch
```

## 14. Referee

`referee` 负责裁判检查、结束判断、跳转裁定和效果触发。

### 14.1 Check After Scene

```yaml
referee:
  enabled: true
  check_on: after_scene
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
        end: villagers_win
```

### 14.2 Check After Message

```yaml
referee:
  enabled: true
  check_on: after_message
  evaluator: plugin
  name: check_story_should_end
```

### 14.3 Include And Exclude

```yaml
referee:
  enabled: true
  check_on: after_scene
  include: [vote_scene, debate_scene]
  exclude: [intro_scene]
  evaluator: plugin
  name: check_game_result
```

模式三中的结束条件属于 referee，只是检查粒度通常是 `after_message` 或 `after_generated_beat`。

## 15. Hooks

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
```

`summarize` 是内置 effect。默认写入文本摘要；如果需要结构化结果，可以声明：

```yaml
hooks:
  on_exit:
    - type: summarize
      to: STORY.scene_summary
      format: object
      include_raw: true
```

推荐支持的 hook：

- `on_enter`
- `on_exit`
- `on_message`
- `on_before_action`
- `on_after_action`
- `on_referee_check`
- `on_schedule_push`
- `on_schedule_pop`

## 16. Resolution

`resolution` 负责处理 action 或 schedule 产生的结果。

### 16.1 Vote Result

```yaml
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
```

`selection.source` 默认是 `responses`，也可以读取 `controller_result` 或状态/ref
来源。例如：

```yaml
resolution:
  selection:
    source: controller_result
    field: selected_choice
```

### 16.2 Broadcast

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

## 17. Publication

`publication` 表达 scene 结束后发布什么信息，发布给谁。

```yaml
publication:
  messages:
    - audience:
        scope: public_room
      content:
        template: "天亮了。"

  disclosures:
    - audience:
        players: [seer]
      content:
        ref: GAME.last_inspection_result

  views:
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

`messages` 默认公开发布；`disclosures` 默认按私有信息处理；`views` 复用同一
`audience` 路由规则。`audience.players` / `audience.seats` 会走 private sink，
`audience.scope` 或普通字符串会走 public sink，`private: true` 且没有明确 seat 时只发给 host。

## 18. Patch Model

动态能力不修改原始 DSL。

runtime 维护：

- `base_flow`：原始 DSL。
- `patch_journal`：运行时生成的 patch。
- `materialized_flow`：`base_flow` 与 `patch_journal` 合成后的可执行 flow。

### 18.1 Flow Patch

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

### 18.2 Schedule Patch

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

`add_transition` 只能连接已存在的 state。runtime 会在写入 patch journal 前校验
`from` 和 `to`，materializer 也不会隐式创建 state。

`add_scene.state` 如果显式声明，也必须是已存在 state；runtime 不会因为 patch
自动创建新的 state machine 节点。

## 19. Four Story Interaction Modes

### 19.1 Mode 1: Choose Mapping

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: choice
  choices:
    - id: apologize
      text: 道歉
      to: apologize_scene
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

### 19.2 Mode 2: Branch Then Return

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: free_text
  free_input:
    enabled: true
    mode: branch_then_return
    generator:
      evaluator: plugin
      name: generate_temporary_branch
    return_to:
      type: scene
      id: main_choice
```

### 19.3 Mode 3: Constrained Continue Or Free Continue

有约束地继续：

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: free_text
  free_input:
    enabled: true
    mode: constrained_continue
    ending:
      candidates: [good_end, bad_end, true_end]
      selector:
        evaluator: plugin
        name: choose_ending_by_progress
    generator:
      evaluator: plugin
      name: generate_constrained_beat
```

自由继续：

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: free_text
  free_input:
    enabled: true
    mode: free_continue
    generator:
      evaluator: plugin
      name: generate_free_beat
```

### 19.4 Mode 4: Grow Flow

```yaml
controller_action:
  enabled: true
  controller:
    type: human
  kind: free_text
  free_input:
    enabled: true
    mode: grow_flow
    patch_store: session
    generator:
      evaluator: plugin
      name: generate_flow_patch
```

## 20. Werewolf Style Scene Example

```yaml
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
```

## 21. Text Adventure Example

```yaml
runtime:
  type: interactive_session

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
```

## 22. Implementation Notes

建议实现时拆成以下模块：

1. `InteractiveSessionRunner`：runtime 入口，负责 flow 执行和 scene 生命周期。
2. `FlowExecutor`：执行 `sequence` 和 `state_machine`。
3. `SceneExecutor`：执行 scene 的 enter、schedule、action、resolution、publication、exit。
4. `ScheduleExecutor`：执行基础 schedule mode，并管理 `push_schedule` / `pop_schedule`。
5. `ControllerActionExecutor`：执行剧情控制能力和自由输入模式。
6. `ParticipantActionExecutor`：执行参与者动作收集和响应校验。
7. `RefereeExecutor`：统一处理 `check_on`、rules 和 evaluator。
8. `PatchJournal`：保存 runtime patch，不修改原始 DSL。
9. `PluginRegistry`：注册机制型 plugin。
10. `ConditionEvaluator`：统一分发 `builtin / code / http / llm / plugin`。

## 23. Migration Rule

新增 DSL 和新文档统一使用本文件中的新版语法。

迁移现有脚本时，需要做到：

- 所有条件表达式统一为 `left / op / right`。
- 所有内置条件判断统一为 `evaluator: builtin`。
- 所有复杂判断、生成、选择结局、检测意图优先使用 `evaluator: plugin`。
- 所有动态剧情生长都通过 patch journal 保存，不直接修改原始 DSL。
- 所有临时私聊、分组聊、指定对话都通过 `schedule.dynamic` 和 `schedule_patch` 表达。
