# Agent 上帝类拆分设计文档 (2026-06-09)

> 对应重构计划任务 4。本文档**只做设计,不改代码**,经确认后再分步实施。
> 目标:把 `ccserver/agent.py` 的 `Agent` 类(2612 行、30+ 方法)按职责拆分,
> 满足 SRP/OCP/LOD,同时严格保证现有行为不变。

---

## 一、现状:Agent 是中枢编排者 + 5 个子系统的混合体

`Agent._loop()`(L1210)是真正的中枢:它顺序编排 6 件事,每件事目前都是
`Agent` 自己的方法,通过共享 `self.*` 状态耦合:

```
_loop()
 ├─ _drain_inbox_and_respond()   团队 inbox 收发      → 子系统①团队协作
 ├─ _maybe_compact() / _do_compact()  上下文压缩     → 子系统②压缩
 ├─ _call_llm_stream() / _call_llm_sync()  LLM 调用  → 子系统③LLM
 ├─ _handle_tools()              工具分发执行         → 子系统④工具
 └─ _on_limit() + _on_limit_*()  轮次上限兜底         → 子系统⑤限流
spawn_child / spawn_background / _spawn_teammate / _handle_agent → 子系统⑥派生
```

### 关键风险(为何不能简单"抽到独立类")
这些方法深度读写 `self` 的大量状态,直接抽取只是**把耦合搬家**:

| 共享状态 | 被哪些子系统读写 |
|---|---|
| `self.context`(messages/agent_id/depth/env_vars) | 全部 |
| `self.session`(hooks/event_bus/rewrite_messages) | 全部 |
| `self.emitter` | LLM/限流/工具/团队 |
| `self.adapter` / `self.model` / `self.system` / `self._schemas` | LLM/限流(summarize) |
| `self.state`(phase/round_num) + `self._set_phase()` | LLM/工具/限流/loop |
| `self.round_limit` / `self._continue_loop` | 限流 ↔ loop 双向 |
| `self._build_hook_ctx()` | 全部 |

因此拆分必须**先定义清晰的依赖契约**,让协作者通过显式入参拿到所需依赖,
而不是隐式共享整个 `Agent`。

---

## 二、设计原则:组合优先 + 显式依赖契约

`Agent` 退化为**协调者(orchestrator)**,持有 5 个协作对象。协作者**不反向持有
完整 Agent**,而是依赖一个最小契约(下称 `AgentRuntime`),只暴露它真正需要的能力。

```python
# 最小运行时契约(Protocol,不强制继承,降低耦合 = LOD)
class AgentRuntime(Protocol):
    context: AgentContext
    session: Session
    emitter: BaseEmitter
    adapter: ModelAdapter
    model: str
    system: list
    schemas: list
    state: AgentState
    def build_hook_ctx(self) -> HookContext: ...
    async def set_phase(self, phase: str) -> None: ...
```

> 这样每个协作者只看到 `AgentRuntime`,看不到彼此,符合迪米特法则(LOD)。
> `Agent` 实现 `AgentRuntime`,把 `self` 传给协作者即可(渐进迁移友好)。

---

## 三、5 个协作者的边界与接口

### ① LLMCaller — 封装 LLM 调用与消息净化
- 迁出方法:`_call_llm_stream`、`_call_llm_sync`、`_sanitize_messages`(静态)
- **顺带收益**:两个 `_call_llm_*` 当前 90% 重复(仅 token emit 差异),
  合并为 `call(stream: bool)`,消除重复(DRY)。
- 接口:
  ```python
  class LLMCaller:
      def __init__(self, rt: AgentRuntime): ...
      async def call(self, *, stream: bool) -> Response | None
      @staticmethod
      def sanitize_messages(messages: list) -> bool
  ```
- 依赖:rt.{adapter,model,system,schemas,session.hooks,session.event_bus,emitter,set_phase,build_hook_ctx}

### ② ToolDispatcher — 工具路由与执行
- 迁出方法:`_handle_tools`、`_handle_mcp_tool`、`_transcribe_image_result`、
  `_handle_send_message`、`_handle_ask_user`(工具语义部分)
- 注意:`_handle_agent`(派生子 agent)归入 SpawnManager,二者在 `_handle_tools`
  内部有调用关系,需通过 rt 或回调解耦。
- 接口:
  ```python
  class ToolDispatcher:
      def __init__(self, rt: AgentRuntime, tools: dict, spawn: "SpawnManager"): ...
      async def handle(self, blocks) -> tuple[list[dict], bool]  # (tool_results, trigger_compact)
  ```

### ③ SpawnManager — 子 agent / teammate 派生
- 迁出方法:`spawn_child`、`spawn_background`、`_spawn_teammate`、
  `_handle_agent`、`_resolve_model_hint`(静态)
- 接口:
  ```python
  class SpawnManager:
      def __init__(self, rt: AgentRuntime): ...
      def spawn_child(self, prompt, ...) -> Agent
      def spawn_background(self, ...) -> ...
      async def handle_agent_tool(self, task_input: dict) -> ToolResult
  ```

### ④ CompactStrategy — 上下文压缩
- 迁出方法:`_maybe_compact`、`_do_compact`
- `Agent.__init__` 已构造 `self.compactor`(CompactorFactory),本协作者是它的
  触发/编排层。
- 接口:
  ```python
  class CompactCoordinator:
      def __init__(self, rt: AgentRuntime, compactor): ...
      async def maybe_compact(self) -> None
      async def do_compact(self, reason: str) -> None
  ```

### ⑤ LimitPolicy — 轮次上限兜底(策略模式 → OCP)
- 迁出方法:`_on_limit`、`_on_limit_ask_user`、`_on_limit_graceful`、
  `_on_limit_summarize`、`_finish_with_last_text`
- **核心改造**:当前用 `if strategy == ...` 分派 5 种策略,改为策略注册表:
  ```python
  class LimitStrategy(Protocol):
      async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome

  _LIMIT_STRATEGIES: dict[str, LimitStrategy] = {}
  def register_limit_strategy(name): ...  # 装饰器,与 model/factory 同风格

  @register_limit_strategy("ask_user")
  class AskUserStrategy: ...
  ```
  新增策略零修改分派逻辑(OCP)。
- **难点**:`ask_user` 策略需要回写 `rt.round_limit += N` 和 `rt._continue_loop = True`,
  与 `_loop` 双向耦合。设计上改为**返回 `LimitOutcome`**(含 `continue_loop: bool`、
  `extra_rounds: int`、`final_text: str`),由 `_loop` 解读,消除回写耦合。

### 团队 inbox(`_drain_inbox_and_respond`)
- 暂**不**独立成第 6 个协作者:它与 `_loop` 轮次节奏强绑定,且已依赖
  `ccserver/team/*` 子模块。本轮先不动,降低风险;后续可评估抽 `TeamCoordinator`。

---

## 四、分步实施顺序(每步独立提交 + 跑测试验证行为不变)

> 纪律:每步前 `git commit`;每步后 `pytest tests/ -q` 对照基线,不得新增失败。
> 基线已知预先存在失败:test_factory x2、test_background_agent x2~3、
> test_shell_task_progress x1(与本任务无关)。

1. **Step 0**:引入 `AgentRuntime` Protocol + 让 `Agent` 标注实现它(纯加法,零行为变更)。
2. **Step 1**:抽 `LimitPolicy`(策略模式)。耦合面最清晰、收益(OCP)最直接,
   且改 `_loop` 仅一处(改读 `LimitOutcome`)。**风险最低,作为首块验证模板。**
3. **Step 2**:抽 `CompactCoordinator`(只 2 个方法,依赖窄)。
4. **Step 3**:抽 `LLMCaller`(合并两个 `_call_llm_*`,消除重复)。
5. **Step 4**:抽 `SpawnManager`。
6. **Step 5**:抽 `ToolDispatcher`(最大,依赖 SpawnManager,放最后)。
7. **Step 6**:`Agent` 收尾,`_loop` 成为纯编排,复核行数下降与可读性。

每步完成后预期 `Agent` 行数递减,最终从 2612 行降到约 600~800 行的协调者。

## 五、验收标准
- 全量测试无新增失败(对照第四节基线)。
- `agent.py` 行数显著下降;新增 `ccserver/agent/` 子包容纳协作者
  (`llm_caller.py` / `tool_dispatcher.py` / `spawn_manager.py` /
   `compact_coordinator.py` / `limit_policy.py` / `runtime.py`)。
- 关键路径手测:一次正常多轮对话、一次 round_limit 触发、一次工具调用、
  一次 spawn 子 agent,行为与重构前一致。

---

## 六、实施完成记录 (2026-06-09)

7 步全部完成,每步独立提交 + 全量回归对照基线(5 个预先存在失败,无新增)。

| Step | 内容 | 提交 |
|---|---|---|
| 0 | agent.py 转包 + AgentRuntime 契约 | b2539a7 |
| 1 | LimitPolicy 策略模式(OCP/LOD) | 3a73ef1 |
| 2 | CompactCoordinator | bee9ee5 |
| 3 | LLMCaller(合并 stream/sync,DRY) | 385da04 |
| 4 | SpawnManager | (Step4 commit) |
| 5 | ToolDispatcher | (Step5 commit) |
| 6 | 收尾:清理未用 import + 验收 | (本提交) |

### 最终包结构与行数
```
ccserver/agent/__init__.py        718  (Agent 协调者)
ccserver/agent/runtime.py          82  (AgentRuntime Protocol)
ccserver/agent/limit_policy.py    264
ccserver/agent/llm_caller.py      323
ccserver/agent/compact_coordinator.py  96
ccserver/agent/spawn_manager.py   719
ccserver/agent/tool_dispatcher.py 629
```
`Agent` 从 **2612 → 718 行(降幅 72.5%)**,退化为协调者:
`__init__` 装配 5 协作者 + `_loop` 编排 + run/run_stream/命令处理/inbox。

### 与设计的偏差(均为降风险的务实选择)
1. **保留公共委托 wrapper**:`spawn_child` / `spawn_background` / `_spawn_teammate`
   及 `_handle_tools` / `_handle_agent` 在 Agent 上保留薄委托方法,因它们是
   agent_scheduler 与多个测试直接调用的公共契约。实现已全部迁出。
2. **派生/工具委托回 rt.spawn_***:SpawnManager/ToolDispatcher 内部经 `rt.spawn_*`
   调用(而非自身),保持"spawn_background 使用父 Agent 的 spawn_child"契约,
   使外部 monkeypatch agent.spawn_child 仍生效(test_spawn_background_inherits_env_vars)。
3. **团队 inbox 未拆**:`_drain_inbox_and_respond` 与 _loop 轮次节奏强绑定,本轮保留
   在 Agent(设计第三节已说明)。
4. `_resolve_model_hint` 随 SpawnManager 迁出为 `resolve_model_hint` 静态方法。

### 验收结果
- 全量测试 735 passed / 5 failed(5 个均为预先存在,与本任务无关)。
- LimitPolicy 5 策略 + ask_user 继续/停止控制流:功能测试全过。
- factory + pipeline-node 两条创建路径:均正确装配 5 协作者。

