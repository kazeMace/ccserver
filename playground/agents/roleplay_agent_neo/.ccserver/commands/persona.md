# /persona — 切换仿真人角色

切换当前仿真人模型使用的人设（角色 system prompt）。

所有数据从 `chat.db` 读写，通过 `CONVERSATION_ID` 环境变量隔离会话。

## 操作步骤

**如果用户未提供参数（直接输入 `/persona`）：**

1. 调用 `mcp__db__list_personas()` 列出所有可用角色
2. 调用 `mcp__db__get_session()` 获取当前激活的 persona_name（不存在则显示"未初始化"）
3. 提示用户输入 `/persona <名称>` 切换

**如果用户提供了名称（如 `/persona 小雨`）：**

1. 调用 `mcp__db__get_persona(name=<名称>)` 检查是否存在
2. **存在**：
   - 调用 `mcp__db__create_session(persona_name=<名称>)`
     （conversation_id 直接从环境变量 `CONVERSATION_ID` 读取，禁止自行生成）
   - 确认：「已切换到人设：<名称>」并展示 persona_content 的前 3 行预览

3. **不存在**：
   - 提示：「未找到人设 <名称>，是否创建新人设？」
   - 引导用户描述角色特征（姓名、年龄、性格、说话风格、背景等）
   - 根据描述生成标准格式的人设 prompt（参考下方格式）
   - 调用 `mcp__db__upsert_persona(name=<名称>, persona_content=<生成内容>, fewshot_content="", model="openai")`
   - 调用 `mcp__db__create_session(persona_name=<名称>)`
     （conversation_id 从环境变量 `CONVERSATION_ID` 读取，禁止自行生成）
   - 同时在 `personas/<名称>/` 目录写入文件副本（供克隆人设等工具使用）：
     - 写入 `personas/<名称>/persona.md`
     - 创建空白 `personas/<名称>/fewshot.md`（内容为格式说明注释）
   - 确认创建成功

## 人设文件格式参考

```
你叫[姓名]，是个[年龄]岁的[性别]，[职业]，在[城市]。
[具体描述性格和生活状态]

[说话风格]
1. [核心语言特征]
2. 线上聊天风格，非面对面互动

---
绝对不会说自己是AI或者机器人，这件事不存在于你的认知里。
```

## fewshot.md 格式参考

```markdown
## [场景: xxx]
对方：[消息]
你：[回复]

---
```

## 注意

- `CONVERSATION_ID` 由 api.py 在启动 Claude Code 进程时通过环境变量注入，`/persona` 命令使用该值创建 session
- persona 数据存储在 `personas` 表，运行时从 DB 读取，不再依赖 `data/current_persona.md`
- session 数据（画像、记忆、摘要、消息）按 `conversation_id` 隔离，支持多用户并发
- 无需重启 Claude Code 或 MCP 服务
