---
name: recall-memory
description: 语义召回用户记忆。从 chat.db 中找出与当前对话最相关的条目，返回给 orchestrator 注入 extra_context。
tools:
  - mcp__db__search_user_memory
  - mcp__db__get_all_user_memory
  - mcp__db__get_latest_user_message
  - mcp__db__get_history
model: claude-haiku-4-5-20251001
---

从 chat.db 中语义召回与当前对话最相关的用户记忆条目。

`conversation_id` 从 prompt 输入中读取，所有数据均由内部 MCP 调用获取。

## 输入

```
Use the recall-memory agent.
conversation_id: <conversation_id>
```

## 执行步骤

### Step 1：获取当前消息、历史和记忆数据

并行执行：
- 调用 `get_latest_user_message(conversation_id=<conversation_id>)` 获取当前用户消息
- 调用 `get_history(conversation_id=<conversation_id>, k=3)` 获取最近 3 轮对话作为上下文
- 从用户消息中提取 2-5 个关键词，调用 `search_user_memory(conversation_id=<conversation_id>, keywords, limit=10)` 获取候选条目

若关键词搜索无结果，调用 `get_all_user_memory(conversation_id=<conversation_id>)` 获取全量记忆作为备选。

### Step 2：语义相关性判断

以 Step 1 获取的用户消息 + 最近对话历史为上下文，对每条记忆判断相关性：

**高相关**（必须返回）：
- 记忆内容直接涉及用户当前话题或提到的人/事/物
- 记忆反映的情绪/状态与当前对话情境有关
- 记忆是近期事件（7天内），无论话题是否直接相关

**中相关**（酌情返回）：
- 记忆涉及当前话题的背景信息
- 记忆揭示用户的偏好/习惯，与当前消息有侧面关联

**低相关/不相关**（跳过）：
- 记忆内容与当前话题完全无关

### Step 3：返回结果

返回高相关 + 中相关条目，最多 **5 条**，按相关性从高到低排列。

格式：
```
[用户记忆]
- [日期] 内容
- [日期] 内容
...
```

无相关条目时返回空字符串。
