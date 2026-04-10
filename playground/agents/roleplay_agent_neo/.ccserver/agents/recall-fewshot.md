---
name: recall-fewshot
description: 语义召回 fewshot 示例。从 chat.db 中获取角色的全量 fewshot，从中选出与当前对话情境最匹配的 3-5 条，返回给 orchestrator 注入 extra_context。
tools:
  - mcp__db__get_session
  - mcp__db__get_fewshot
  - mcp__db__get_history
  - mcp__db__get_latest_user_message
model: claude-haiku-4-5-20251001
---

从 chat.db 中获取角色全量 fewshot，再从中语义筛选最匹配当前情境的示例。

`conversation_id` 从 prompt 输入中读取，所有数据均由内部 MCP 调用获取。

## 输入

```
Use the recall-fewshot agent.
conversation_id: <conversation_id>
```

## 执行步骤

### Step 1：并行获取所需数据

并行执行：
- 调用 `get_session(conversation_id=<conversation_id>)` 获取 `persona_name`，再调用 `get_fewshot(name=<persona_name>)` 获取全量 fewshot 文本
- 调用 `get_latest_user_message(conversation_id=<conversation_id>)` 获取当前用户消息
- 调用 `get_history(conversation_id=<conversation_id>, k=3)` 获取最近 3 轮对话作为情境参考
fewshot 为空 → 直接返回空字符串，结束。

解析所有示例块，每块格式为：
```
## [场景: xxx]
对方：...
你：...
```

### Step 2：判断当前情境类型

根据 Step 1 获取的用户消息 + 最近对话历史，判断当前情境属于哪类：

- 打招呼 / 开场
- 日常闲聊
- 情绪表达（开心/难过/吐槽/抱怨）
- 提问 / 求助
- 拒绝 / 回避场景（用户在请求角色做某事）
- 被夸 / 被评价
- 感兴趣话题（用户聊到角色可能感兴趣的领域）
- 追问 / 深入话题
- 沉默 / 冷处理
- 其他

### Step 3：从全量 fewshot 中筛选 3-5 条

匹配逻辑：
1. **精确匹配**：场景标签与判断的情境类型相同 → 优先选
2. **语义相近**：场景标签描述与当前情境有明显重叠 → 次选
3. **兜底**：若精确/语义匹配不足 3 条，从常见场景补充（日常打招呼、普通闲聊、情绪响应）

选取 **3-5 条**，覆盖不同情绪状态，避免全部来自同一场景。

### Step 4：返回结果

格式：
```
**【Few-shot 参考】**
以下是该角色的真实对话片段，请严格参考其风格：

对方：[原始消息]
你：[原始回复]

对方：[原始消息]
你：[原始回复]

...（共 3-5 条）
```

无匹配时返回空字符串。
