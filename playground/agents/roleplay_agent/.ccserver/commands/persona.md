# /persona — 切换仿真人角色

切换当前仿真人模型使用的人设（角色 system prompt）。

## 操作步骤

**如果用户未提供参数（直接输入 `/persona`）：**

1. 列出 `personas/` 目录下所有子目录（每个子目录即一个人设，排除非目录文件）
2. 告知用户当前激活的人设名称（读取 `data/current_persona_name.txt`，默认 `default`）
3. 提示用户输入 `/persona <名称>` 切换

**如果用户提供了名称（如 `/persona 小雨`）：**

1. 检查 `personas/<名称>/persona.md` 是否存在
2. **存在**：
   - 将名称写入 `data/current_persona_name.txt`（覆盖，只写名称字符串，不含换行）
   - 将 `personas/<名称>/persona.md` 的内容复制到 `data/current_persona.md`（覆盖写入）
   - 生成 session_id（格式：`<名称>_YYYYMMDD_HHMMSS`，使用当前时间），写入 `data/current_session_id.txt`（覆盖）
   - 创建 session 目录 `data/sessions/<session_id>/`（如不存在）
   - 在 session 目录中初始化空文件（仅当文件不存在时创建）：
     - `user_profile.json` — 内容为 `{}`
     - `user_memory.md` — 内容为 `# 用户记忆\n`
     - `persona_memory.md` — 内容为 `# 角色新设定\n`
     - `summary.md` — 内容为空字符串
   - 确认：「已切换到人设：<名称>」并展示 `personas/<名称>/persona.md` 的前 3 行预览
3. **不存在**：
   - 提示：「未找到人设 <名称>，是否创建新人设？」
   - 引导用户描述角色特征（姓名、年龄、性格、说话风格、背景等）
   - 根据描述生成标准格式的人设 prompt（参考下方格式）
   - 创建 `personas/<名称>/` 目录
   - 写入 `personas/<名称>/persona.md`
   - 写入名称到 `data/current_persona_name.txt`
   - 将 `personas/<名称>/persona.md` 的内容复制到 `data/current_persona.md`
   - 生成 session_id（格式：`<名称>_YYYYMMDD_HHMMSS`，使用当前时间），写入 `data/current_session_id.txt`（覆盖）
   - 创建 session 目录 `data/sessions/<session_id>/` 并初始化四个文件（同上）
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

## Session 目录结构

```
data/sessions/<session_id>/
├── user_profile.json   ← 用户画像（结构化槽位）
├── user_memory.md      ← 用户记忆（非结构化事件/情感细节）
├── persona_memory.md   ← 角色新设定（对话中动态引入，作为 persona 的补充）
└── summary.md          ← 对话摘要（历史压缩结果，每次覆盖写入）
```

chat.jsonl（对话记录）由 chat-model MCP 的 save_turn 工具自动写入同一目录。

## 注意

- 切换时将 persona 内容复制到 `data/current_persona.md`，编排器和 subagent 均从此文件读取；聊天中 slot-extract 对该文件的动态更新不会影响 `personas/<名称>/persona.md` 原文件
- `data/current_persona_name.txt` 仅用于定位 `personas/<名称>/fewshot.md`
- `data/current_session_id.txt` 用于定位当前 session 的数据目录
- 无需重启 Claude Code 或 MCP 服务
