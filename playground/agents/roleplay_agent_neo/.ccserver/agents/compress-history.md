---
name: compress-history
description: 将传入的对话内容压缩为结构化摘要并保存到 chat.db。由 orchestrator 在 history_json 超过 20 对时触发，也可用户手动触发。
tools:
  - mcp__db__get_summary
  - mcp__db__get_all_messages
  - mcp__db__count_messages
  - mcp__db__save_summary
model: claude-haiku-4-5-20251001
---

从 chat.db 读取对话内容，增量压缩为结构化摘要并保存。
只关注 role=user 和 role=assistant 的消息，跳过其他。

`conversation_id` 从 prompt 输入中读取，所有数据均由内部 MCP 调用获取。

## 输入格式

```
Use the compress-history agent.
conversation_id: <conversation_id>
```

---

## 执行步骤

### Step 1 — 读取数据

并行执行：
- 调用 `get_summary(conversation_id=<conversation_id>)` 获取现有摘要（无则为 null）
- 调用 `count_messages(conversation_id=<conversation_id>)` 获取总轮数 N
- 调用 `get_all_messages(conversation_id=<conversation_id>)` 获取全量消息

取前 `N - 10` 轮消息作为待压缩内容（保留最近 10 轮不压缩），计算 `rounds_covered` 为 `"1-{N-10}"`。

---

### Step 2 — 生成合并摘要

将现有摘要（如有）与待压缩消息合并，生成更新摘要：

```markdown
## 对话摘要（累计至第 <结束轮> 轮）

**用户关键信息**:
- [所有轮次中用户透露的重要个人信息，去重合并]

**主要话题**:
- [话题1]: [简述讨论内容和结论]
- [话题2]: ...

**情感与氛围**:
[对话整体情绪基调、用户当前状态]

**未解决/待延续的话题**:
- [需要在后续对话中跟进的内容]

**重要细节备忘**:
- [其他值得记住的具体信息]
```

摘要控制在 **400 字以内**，旧摘要中已有的信息若无变化可简化表达。

---

### Step 2 — 保存摘要

调用 `save_summary(conversation_id=<conversation_id>, content=<新摘要>, rounds_covered=<轮次范围>)`。
每个 session 只保留一条摘要，重复调用自动覆盖。

---

### Step 3 — 返回

返回新摘要的完整内容。
