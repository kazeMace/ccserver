# ccserver Agent 生态架构图

> 可视化展示各层组件关系与数据流

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          用户请求入口                                        │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                   │
│  │  HTTP API   │    │   SSE API   │    │   Graph     │                   │
│  │ (非流式响应) │    │ (实时流式)  │    │  Pipeline   │                   │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘                   │
└─────────┼──────────────────┼──────────────────┼─────────────────────────────┘
          │                  │                  │
          ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AgentFactory.create_root()                         │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  stream=True (根 Agent)        │  stream=False (Graph 节点)        │   │
│   │  ├── 实时 emit token            │  ├── 只返回 done 事件              │   │
│   │  ├── 用户看到打字效果          │  ├── 用于 pipeline 流程           │   │
│   │  └── SSEEmitter               │  └── _NullEmitter / CollectEmitter │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Agent._loop()                                   │
│                                                                             │
│   ┌───────────────┐     ┌───────────────┐     ┌───────────────┐           │
│   │ AgentState    │     │  _call_llm    │     │ _handle_tools│           │
│   │ phase:        │     │  _stream()    │     │              │           │
│   │ - idle        │     │  或            │     │  • 工具执行   │           │
│   │ - running     │     │  _sync()       │     │  • 子 Agent  │           │
│   │ - llm_calling │     │                │     │    spawn    │           │
│   │ - tool_exec   │     │  ← 动态选择    │     │              │           │
│   │ - done        │     │                │     │              │           │
│   │ - error       │     └───────────────┘     └───────────────┘           │
│   └───────────────┘                                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
                    ▼                               ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────────┐
│        后台 Agent 框架           │   │        Task Manager                │
│                                 │   │                                     │
│  ┌───────────────────────────┐  │   │  ┌────────────┐  ┌────────────┐   │
│  │ BackgroundAgentHandle    │  │   │  │   Task      │  │ TaskStorage│   │
│  │ - agent_id               │  │   │  │ - id        │  │ (文件/DB)  │   │
│  │ - task_id (可选绑定)     │  │   │  │ - status    │  │            │   │
│  │ - state (引用Agent.state)│  │   │  │ - assigned  │  │            │   │
│  │ - inbox (收消息)         │  │   │  │   _agent_id │  │            │   │
│  │ - outbox (发消息)         │  │   │  │ - depends  │  │            │   │
│  └───────────────────────────┘  │   │  │ - output   │  └────────────┘   │
│                                 │   │  │   _summary │                    │
│  ┌───────────────────────────┐  │   │  └────────────┘                    │
│  │ AgentScheduler          │  │   │                                     │
│  │ - spawn() → handle      │  │   │  Task.bind_agent(agent_id)         │
│  │ - get(agent_id)        │  │   └─────────────────────────────────────┘
│  │ - list()                │  │
│  │ - cancel(agent_id)      │  │
│  └───────────────────────────┘  │
                                 │
│  ┌───────────────────────────┐  │
│  │ QueueEmitter            │  │
│  │ (asyncio.Queue)         │  │
│  │ - token / done / error  │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SessionAgentBus (同 Session)                       │
│                                                                             │
│      Agent A ──send──▶ SessionAgentBus ──forward──▶ Agent B                │
│                                                                             │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐            │
│   │ AgentMessage │ ──▶  │  _agents     │ ──▶  │   inbox      │            │
│   │ - id         │      │  {agent_id:  │      │   (Queue)    │            │
│   │ - from       │      │   Queue}     │      │              │            │
│   │ - to         │      │              │      │              │            │
│   │ - type       │      │  register()  │      │              │            │
│   │ - payload    │      │  unregister() │      │              │            │
│   └──────────────┘      └──────────────┘      └──────────────┘            │
│                                                                             │
│   支持：单播 (to_agent_id) / 广播 (to_agent_id="*")                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 数据流示例

### 示例 1: 用户发起请求（SSE 流式）

```
User → SSE API → AgentFactory.create_root(stream=True)
                           ↓
                    Agent(stream=True).run(message)
                           ↓
                    _loop() → _call_llm_stream()
                           ↓
                    实时 emit_token() → SSEEmitter → 客户端
                           ↓
                    tool_use → _handle_tools()
                           ↓
                    emit_tool_start/result
                           ↓
                    下一轮 LLM 或 emit_done → 客户端
```

### 示例 2: Graph Pipeline 节点

```
Graph.run() → AgentFactory.create_root(stream=False)
                        ↓
                  Agent(stream=False).run(prompt)
                        ↓
                  _loop() → _call_llm_sync()
                        ↓
                  无 token emit，直接收集 response
                        ↓
                  emit_done(final_text) → 返回给 Graph
                        ↓
                  NodeData(output_key=final_text)
```

### 示例 3: 后台 Agent + Task 绑定

```
用户/系统 → TaskManager.create(subject="分析代码")
                              ↓
                        Task(id="task-1", status="pending")
                              ↓
                        Session.scheduler.spawn(
                            prompt="分析代码...",
                            task_id="task-1"
                        )
                              ↓
                        BackgroundAgentHandle(agent_id="bg-001")
                              ↓
                        TaskManager.bind_agent("task-1", "bg-001")
                        Task(status="in_progress", assigned_agent_id="bg-001")
                              ↓
                        后台 Agent 异步运行...
                        handle.state.phase 可查询
                              ↓
                        完成 → TaskManager.complete("task-1", summary)
                        Task(status="completed", output_summary=summary)
```

### 示例 4: Agent 间通信

```
Agent A → session.agent_bus.send(
              AgentMessage(
                  from_agent_id="agent-A",
                  to_agent_id="agent-B",
                  type=REQUEST,
                  payload={"action": "分析这个"}
              )
          )
                ↓
          SessionAgentBus → _agents["agent-B"].inbox.put(message)
                ↓
          Agent B 的 _loop 在 tool_executing 阶段检查 inbox
          → 处理消息 → 返回响应 → agent_bus.send(..., to_agent_id="agent-A")
```

---

## 状态转移图

### AgentState 状态转移

```
                    ┌──────────────┐
                    │    idle     │ ← Agent 创建
                    └──────┬───────┘
                           │ run() 开始
                           ▼
                    ┌──────────────┐
          ┌─────────▶│  running    │
          │         └──────┬───────┘
          │                │ LLM 调用中
          │                ▼
          │         ┌──────────────┐
          │         │llm_calling  │ ← Phase 1
          │         └──────┬───────┘
          │                │ LLM 返回
          │                ▼
          │         ┌──────────────┐
          │         │tool_executing│ ← Phase 2 (tool_use)
          │         └──────┬───────┘
          │                │ 工具执行完
          │                ▼
          │         ┌──────────────┐
          │    ┌───│     done     │ ← 正常结束
          │    │   └──────────────┘
          │    │
          │    │ round_limit 到达
          │    ▼
          │   ┌─────────────────┐
          └──▶│ limit_reached   │ ← 轮次耗尽
              └─────────────────┘
              
              
          ┌──────────────┐
          │    error     │ ← LLM 调用失败
          └──────────────┘
          
          ┌──────────────┐
          │   cancelled   │ ← 被外部取消
          └──────────────┘
```

---

## 接口速查

| 接口 | 位置 | 用途 |
|------|------|------|
| `AgentFactory.create_root(..., stream=True/False)` | factory.py | 创建根 Agent |
| `agent.spawn_child(prompt)` | agent.py | 创建同步子 Agent |
| `agent.spawn_background(prompt, task_id)` | agent.py | 创建后台 Agent (Phase 2) |
| `agent.state` | agent.py | 查询运行时状态 |
| `session.scheduler.spawn(prompt, task_id)` | agent_scheduler.py | 后台调度 (Phase 2) |
| `session.agent_bus.send(message)` | agent_bus.py | Agent 间通信 (Phase 3) |
| `task_manager.bind_agent(task_id, agent_id)` | tasks/manager.py | Task 绑定 Agent (Phase 3) |