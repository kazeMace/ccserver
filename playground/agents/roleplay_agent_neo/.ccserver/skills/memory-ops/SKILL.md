# memory-ops — 基于 chat.db 的记忆与画像操作规范

所有数据存储在 `chat.db`，通过 `mcp__db__*` 工具读写。每个 session 的数据以 `conversation_id` 隔离。

---

## 数据结构概览

| 数据类型 | 表 | 负责工具 |
|---------|-----|---------|
| 用户画像（持久属性） | `user_profile` | `update_profile` / `get_profile` / `clear_profile_slot` |
| 用户记忆（时效性事件） | `user_memory` | `add_user_memory` / `search_user_memory` / `get_all_user_memory` |
| 角色新设定 | `persona_memory` | `add_persona_memory` / `search_persona_memory` / `get_persona_memory` |
| 对话摘要 | `summaries` | `save_summary` / `get_summary` |

---

## 用户画像（user_profile）

持久属性槽位，只存稳定不变的信息。

### 读取
```
mcp__db__get_profile(conversation_id=<conversation_id>)
```
返回所有槽位的 JSON 对象。

### 新增/更新槽位
```
mcp__db__update_profile(conversation_id=<conversation_id>, slot_name=<槽位名>, value=<值>)
```

### 删除槽位（用户明确否定时）
```
mcp__db__clear_profile_slot(conversation_id=<conversation_id>, slot_name=<槽位名>)
```

### 格式化输出（供 extra_context 注入）
```
<user_profile>
[用户画像]
- 年龄：25
- 职业：设计师
- 城市：上海
</user_profile>
```

---

## 用户记忆（user_memory）

时效性内容：具体事件、情感状态、计划意图等不适合放槽位的信息。

### 追加一条记忆
```
mcp__db__add_user_memory(conversation_id=<conversation_id>, content=<内容>, memory_date=<YYYY-MM-DD>)
```

### 关键词搜索（语义召回）
```
mcp__db__search_user_memory(conversation_id=<conversation_id>, keywords=<空格分隔关键词>, limit=10)
```

### 获取全量记忆
```
mcp__db__get_all_user_memory(conversation_id=<conversation_id>)
```

### 格式化输出（供 extra_context 注入）
```
<user_memory>
[用户记忆]
- [2026-03-16] 最近压力很大，在备考研究生
- [2026-03-10] 不喜欢吃辣，对甜食有点上瘾
</user_memory>
```

---

## 角色新设定（persona_memory）

角色在对话中动态引入的新设定，作为 persona 原文的补充，按 `conversation_id` 隔离。

### 追加角色新设定
```
mcp__db__add_persona_memory(conversation_id=<conversation_id>, content=<内容>, memory_date=<YYYY-MM-DD 或 "聊天时">)
```

### 关键词搜索
```
mcp__db__search_persona_memory(conversation_id=<conversation_id>, keywords=<关键词>, limit=10)
```

### 获取全量设定
```
mcp__db__get_persona_memory(conversation_id=<conversation_id>)
```

### 格式化输出（供 extra_context 注入）
```
<persona_memory>
[角色新设定]
- [2026-03-16] 最近买了一台咖啡机，在家能做拿铁了
- [聊天时] 养了一只叫"豆豆"的猫
</persona_memory>
```

---

## 对话摘要（summaries）

compress-history 生成的压缩摘要，每个 session 只保留一条，覆盖写入。

### 写入摘要
```
mcp__db__save_summary(conversation_id=<conversation_id>, content=<摘要内容>, rounds_covered=<"1-20">)
```

### 读取摘要
```
mcp__db__get_summary(conversation_id=<conversation_id>)
```
返回 `{content, rounds_covered, updated_at}`，不存在时返回 null。

---

## 时机区分

| 操作 | 时机 | 负责方 |
|------|------|--------|
| 用户槽位提取（user_profile） | 每轮用户消息后自动判断 | profile-sync subagent |
| 用户记忆追加（user_memory） | 仅当用户透露了值得长期记住的具体事件/情感细节时 | profile-sync subagent |
| 角色新设定追加（persona_memory） | 仅当角色回复引入了新的个人设定事实时 | profile-sync subagent |
| 对话摘要 | N > 20 时由 orchestrator 在 Step 6 触发 | compress-history subagent |

**关键区别**：
- 槽位提取：每轮都判断，命中就写，成本低
- 记忆/设定追加：需要判断"值不值得长期保留"，宁缺毋滥
- 对话摘要：save_turn 完成后触发，覆盖写入，不阻塞输出

---

## 注意

- 所有操作通过 `mcp__db__*` 工具完成，不读写任何本地文件
- `conversation_id` 必须显式传入每个工具调用
- 时间使用 `YYYY-MM-DD` 格式；clone 模式下无法确定日期时用 `"聊天时"`
