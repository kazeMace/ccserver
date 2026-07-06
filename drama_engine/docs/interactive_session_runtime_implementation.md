# Interactive Session Runtime Implementation

本文记录 `runtime.type: interactive_session` 的实现结构。

## 1. Package Layout

实现位于：

```text
drama_engine/core/runtime/interactive_session/
```

核心拆分：

```text
interactive_session/
  compiler.py
  context.py
  models.py
  normalizer.py
  runner.py

  flow/
    executor.py

  scene/
    executor.py
    scope.py

  schedule/
    executor.py
    modes.py
    dynamic.py

  actions/
    candidate_validation.py
    participant.py
    controller.py
    free_input.py
    response_models.py

  referee/
    executor.py

  patch/
    applier.py
    journal.py
    materializer.py
    validators.py

  services/
    inside_agent.py
    input builder 复用 core/dsl/components/service_input.py
    runtime_services.py
```

## 2. Runtime Entry

新增 runtime：

```yaml
runtime:
  type: interactive_session
```

接入点：

- `drama_engine/core/runtime_spec/registry.py`
- `drama_engine/core/runner/dispatch.py`

runner 类型：

```python
InteractiveSessionRunner
```

## 3. Canonical DSL Boundary

执行层只读取 canonical model：

- `InteractiveScript`
- `FlowSpec`
- `SceneSpec`
- `ScheduleSpec`
- `ParticipantActionSpec`
- `ControllerActionSpec`
- `RefereeSpec`

老语法兼容集中在：

```text
normalizer.py
```

执行器中不散落 legacy 判断。

## 4. Reused Components

当前复用现有组件：

- `ConditionEvaluator`
- `CandidateResolver`
- `EffectExecutor`
- `ValueResolver`
- `ActorRuntime`
- `InputBridge`
- `RuntimeMemoryStore`
- `EventPublisher`
- `State / StateWriter`

同时对共享组件做了新版语法增强：

- `evaluator: builtin`
- `evaluator: plugin` 支持 `name`
- `count.ref / count.where`
- selector 支持 `source / where`
- `participants` 支持 `static`、`filter`、`evaluator: plugin`、`plugin: name` 和 service-style evaluator

## 5. Execution Chain

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

## 6. Dynamic Schedule

`schedule.dynamic` 通过 `schedule_patch` 表达，不创建新 scene，也不修改 flow。

支持：

- `push_schedule`
- `pop_schedule`
- private/public 临时 scope
- 子调度结束后回到父调度
- patch journal 记录；`push_schedule` 后即使 actor/hook/referee 抛错，也会在 `finally`
  中写入对应 `pop_schedule`，避免 journal 留下未闭合子调度
- `detector` 通过 runtime service 调用
- `dynamic.check_on` 支持 `after_message` 和 `after_round`
- 父级 `mode: openchat` 也支持 `dynamic.check_on: after_round`；每个 openchat turn
  只收集一个 actor，因此一个 turn 即一个 round
- `dynamic.check_on: after_round` 在本轮 `referee.check_on: after_round` 未终止时才运行；
  referee 返回终局或跳转后不会再 push 子调度
- `dynamic.patch` 作为默认 patch 合并
- `dynamic.merge_back` 在子调度结束后生效，`mode: summary` 会写入 `merge_back.to` 指定的状态路径
- 子调度尊重 patch 的 `mode / max_turns / max_rounds`
- 子调度尊重 patch 的 `order / actor / planner / opening / first_speaker / timeout_ms / stop_when`
- 子调度的非 `openchat` mode 复用常规 `ScheduleModePlanner`，因此 `single` 会尊重
  `actor/first_speaker`，`sequential/random_order/loop_until` 会尊重对应 order 和 round
  语义；非 `openchat` patch 未声明 `max_rounds` 时会用 `max_turns` 作为兼容轮数
- 子调度 `mode: openchat` 每次只收集一个 actor，发言后调用 planner；planner
  可返回 `next_speaker`、`cue` 或 `stop: true`
- 子调度 `mode: openchat` 未显式声明 `scope.visibility` 时默认按公开聊天发布；显式 private/public scope 会覆盖默认值
- 子调度消息触发 `on_message/referee.after_message` 时，`responses` 使用父调度已收集
  responses 与当前子调度 responses 的合并视图
- `on_schedule_push` / `on_schedule_pop` 在 push/pop journal 写入当下触发

基础 schedule order 支持 `seat_order` 和 `reverse_seat_order`，优先读取 state
中的 `seat_index`，缺失时按玩家名数字后缀自然排序。

## 7. Flow Patch

`grow_flow` 通过 `flow_patch` 表达。

runtime 保存：

- `base_flow`
- `patch_journal`
- `materialized_flow`

原始 DSL 不会被修改。

运行时行为：

- `flow_patch` 先校验并 dry-run 编译，确认可由 `base_flow + patch_journal + candidate_patch` 合成
- 校验和预览成功后才进入 `PatchJournal`，失败不会污染 journal
- `FlowPatchApplier` 从 immutable `base_flow + patch_journal` 重新合成内存中的 compiled script
- `FlowMaterializer` 用同一套规则生成 summary 快照，summary 同时暴露 `base_flow` 与 `materialized_flow`
- `add_scene.after` 可以把新 scene 插入当前 sequence/state
- `add_transition` 可以扩展 state machine transition
- `add_transition` 必须连接已存在的 `from/to` state；校验和 materializer 都会拒绝隐式创建 state
- `add_scene.state` 如果显式声明，也必须引用已存在 state；runtime 不会通过 flow patch 隐式创建 state machine 节点
- `set_state` 可以作为 patch 写入状态，但必须声明 `path` 或 `entity/attr`，否则在 journal 前拒绝

## 8. Free Input Modes

`controller_action.free_input.mode` 支持：

- `choose_mapping`：把自由文本映射到已有 choice，默认内置 mapper，也可配置 `mapper: {provider: plugin, name: ...}`
- `branch_then_return`：生成临时支线 scene，写入 `branch_patch` 和 `flow_patch`，执行后按 `return_to` 回到 scene/state；该模式要求 flow patch 是 `add_scene`
- `constrained_continue`：生成受结局约束的剧情 beat，支持 `ending.selector`、`ending_selector`、`max_beats/max_turns`
- `free_continue`：生成自由剧情 beat，支持多 beat，并逐条触发 `after_generated_beat`
- `grow_flow`：生成并应用 `flow_patch`

所有外部能力统一走 runtime service：

```yaml
provider: plugin
name: map_free_text_to_choice
```

也支持：

```yaml
provider: http
endpoint: story_mapper
```

```yaml
type: llm
provider: inside
```

`provider: inside` 在 runtime service 和 async condition 路径中优先调用显式注入的
`inside_agent`、`llm_client` 或 `llm_provider`。未注入且不是 dry-run 时，会通过
ccserver `AgentFactory.create_root(...)` 创建隐藏内部 Agent；初始化失败或 dry-run
时才回退到当前 cast actor 或 deterministic builtin fallback。
遗留同步 condition 路径如果拿到 inside Agent，也会阻塞等待其 async `run()` 结果；
interactive_session runtime 自身优先使用协程路径。

`inside_agent`、`llm_client`、`llm_provider` 是 Python runtime 句柄，只在内部协程路径上传递，不会进入 HTTP body、prompt 默认 JSON 或 materialized payload。

当 HTTP/LLM/plugin/runtime service 未声明 `input` 时，会发送完整运行时上下文，
包括 state、players、participants、current scene/state、last responses、message history、
patch journal 和 metadata。
声明 `input.include_*` 时，`ServiceInputBuilder` 会生成收窄 payload，并解析显式
字段中的 `{ref: ...}`。

HTTP runtime service 和 HTTP external condition 在 async 路径中通过协程 offload
执行，不阻塞 event loop。

交互协议：

- HTTP runtime service 和 HTTP external condition 默认收到 `interactive_session.v1`
  envelope，且保留 `id/name/purpose/endpoint/input/context` 兼容字段。
- inside provider 未显式写 `prompt` 时，默认把同一个 envelope 编码为 JSON 后调用
  ccserver `Agent.run()`。
- plugin runtime service 默认保持历史扁平 payload；声明 `protocol: envelope` 或
  `envelope: true` 时接收同一个 envelope。
- envelope 固定包含 `protocol`、`call`、`input`、`context`、`metadata` 五个顶层字段。

脚本顶层 `plugins` 会在 runner assign 阶段加载。支持：

```yaml
plugins:
  - module: my_game.plugins.story
    register: register
  - runtime_services:
      map_free_text_to_choice:
        result:
          selected_choice: leave
  - conditions:
      story_should_end:
        result: true
```

## 9. Scene Lifecycle

scene executor 支持：

- `on_enter`
- `on_before_action`
- `on_message`
- `on_after_action`
- `on_referee_check`
- `on_schedule_push`
- `on_schedule_pop`
- `on_exit`

`sequential`、`single`、`openchat` 的 participant message 会逐条触发 `on_message`
和 `referee.check_on: after_message`；一旦 referee 返回结果，后续 actor 不再执行。
`simultaneous` 会等待并发批次完成后逐条检查已完成响应。
`referee.check_on: after_round` 会在每个 schedule round 后立即检查；`loop_until`
如果第一轮已经满足结束条件，不会继续执行后续 round。
`on_referee_check` 在每次 referee 检查前触发，包括 `after_scene`。

private scope 的 participant message 会发送给 scope members 的 private sink，同时保留
host 可观测事件；public scope 仍走 public sink。

内置 `summarize` effect 可以在 hook 中把当前 responses/controller_result 汇总到
状态路径，默认写入文本，`format: object` 时写入结构化摘要。

`publication` 支持：

- `messages`
- `disclosures`
- `views`

`views` 复用现有 `PluginRegistry.project_view()`。

## 10. Referee Result

`referee.rules[].result` 和直接 `referee.evaluator + referee.result` 都支持：

```yaml
result:
  effects:
    - type: set_state
      path: GAME.flag
      value: true
  jump: next_scene_or_state
```

终局：

```yaml
result:
  end: story_finished
```

`jump / to` 不会自动结束 session；它会设置下一步 flow target。
非终局 `jump / to / effects / set_state` 会继续检查后续 referee rules；只有 `end / end_session / message` 会返回终局结果。

## 11. Resolution

`resolution.selection` 支持：

- `source: responses`，默认从 participant responses 中计票
- `source: controller_result`，从 controller action 结果中取字段
- 状态/ref 来源，例如 `{ref: GAME.votes}`
- `tie_policy: no_winner / all_tied / runoff / alphabetical`

`tie_policy: runoff` 会写入 `RESOLUTION.needs_runoff` 与
`RESOLUTION.runoff_candidates`，并可通过 `runoff.to` 请求下一步 flow target。

## 12. Validation Scope

`runtime.type: interactive_session` 的静态校验由 `InteractiveSessionCompiler` 负责。
当前会在编译期拒绝未知 `schedule.mode`、未知 `dynamic.check_on` 和未知
`referee.check_on`，避免拼写错误在运行时静默失效。

`drama_engine/scripts/interactive_session/...` 是新版 DSL 示例脚本。`fixed_flow`、
`group_chat`、`dynamic_story` 和 `presets` 目录仍属于各自 runtime 的脚本，不强制迁移
为 `interactive_session`。

## 13. Candidate Validation

`ParticipantActionExecutor` 会在 runtime 层校验结构化结果：

- `vote`
- `choose`
- `target`
- `targets`

返回值必须落在 `candidates` 解析出的集合中。非法输出会重新提示 actor，最多重试 3 次。
必选目标类 action 如果 candidates 解析失败，会直接报错，不再静默放行。

## 14. Validation And CLI

`DslValidator` 会根据 `runtime.type` 分派：

- `interactive_session` -> `InteractiveSessionCompiler`
- 其他 runtime -> 原 `YamlCompiler`

CLI `simulate` 也支持 `interactive_session`，不会把新脚本交给旧 compiler。

## 15. New Example Scripts

新增新版 DSL 示例：

```text
drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml
drama_engine/scripts/interactive_session/deduction/dynamic_schedule_discussion.yaml
```

两个脚本全部使用新版语法。

## 16. Legacy Script Compatibility

旧 CLI/测试入口 `drama_engine/core/scripts/*.yaml` 作为兼容层保留，使用 symlink
指向 `drama_engine/scripts/fixed_flow/...` 中的真实脚本源文件，避免维护两份 DSL。
`drama_engine/core/presets/werewolf_9p_normal.preset.yaml` 保留为旧 preset 入口。

## 17. Verification

已增加测试：

```text
tests/drama/test_interactive_session_runtime.py
```

覆盖：

- runtime declaration / dispatch
- compiler 编译新版脚本
- `builtin` + `count.ref/where`
- dry-run runner assign/start/end
- dynamic schedule patch journal
- choice target immediate jump
- live `grow_flow` materialization
- candidate validation retry
- referee effects and jump
