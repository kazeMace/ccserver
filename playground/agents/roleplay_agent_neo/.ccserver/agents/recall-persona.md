---
name: recall-persona
description: 语义召回角色新设定。从 chat.db 中找出与当前对话最相关的条目，合并进 persona 注入 extra_context。
tools:
  - mcp__db__search_persona_memory
  - mcp__db__get_persona_memory
  - mcp__db__get_latest_user_message
  - mcp__db__get_history
model: claude-haiku-4-5-20251001
---

从 chat.db 中语义召回与当前对话最相关的角色新设定条目。

`conversation_id` 从 prompt 输入中读取，所有数据均由内部 MCP 调用获取。

## 输入

```
Use the recall-persona agent.
conversation_id: <conversation_id>
```

## 执行步骤

### Step 1：获取当前消息、历史和设定数据

并行执行：
- 调用 `get_latest_user_message(conversation_id=<conversation_id>)` 获取当前用户消息
- 调用 `get_history(conversation_id=<conversation_id>, k=3)` 获取最近 3 轮对话作为上下文
- 从用户消息中提取关键词，调用 `search_persona_memory(conversation_id=<conversation_id>, keywords, limit=10)` 获取候选条目

若搜索无结果，调用 `get_persona_memory(conversation_id=<conversation_id>)` 获取全量设定。

### Step 2：相关性判断

以 Step 1 获取的用户消息 + 最近对话历史为上下文，对每条角色设定判断是否需要此刻激活：

**必须返回**：
- 设定内容与用户当前话题直接相关
- 设定涉及的人/物/地点被用户提及
- 设定反映角色的态度/偏好，与当前话题有关

**可选返回**（若总数不足 5 条则补充）：
- 近期设定（30天内），提供当前状态背景
- 揭示角色性格/习惯的设定，对自然回复有辅助

**不返回**：
- 与当前话题完全无关的设定
- 已在 persona.md 原文中覆盖的内容（重复）

### Step 3：返回结果

返回最多 **5 条**，按相关性排列。

格式：
```
[角色新设定]
- [日期/聊天时] 内容
- [日期/聊天时] 内容
...
```

无相关条目时返回空字符串。
