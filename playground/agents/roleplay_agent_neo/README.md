# cc_for_chat

基于 Claude Code 的仿真人对话框架。以 Claude Code CLI 作为编排核心，通过多个 MCP server 实现记忆管理、个性化聊天、质量控制等能力，让 AI 像真实的人一样和你聊天。

## 使用方法
```bash
claude --append-system-prompt "$(cat roleplay_instruct.md)"
```

## 特性

- **多角色支持**：可配置多个角色人设（persona），运行时动态切换
- **持久化记忆**：自动提取并记忆用户信息（画像槽位 + 非结构化记忆）
- **角色记忆进化**：角色在对话中主动引入的新信息自动写入记忆库
- **Few-shot 语义召回**：每轮根据对话语义召回最匹配的示例，增强回复风格一致性
- **质量自动检测**：每轮回复由 quality-check agent 检测，OOC/重复等问题触发重试
- **联网搜索**：自动判断是否需要搜索，实时注入结果
- **话题引导**：自动分析对话状态，生成自然的话题延续建议
- **历史自动压缩**：对话超过 20 轮时后台触发压缩，维持上下文长度

## 架构

```
用户 ──→ Claude Code（编排核心）
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
  db MCP  chat-model  web-search
  (SQLite)  MCP         MCP
    │
    ├── user_profile   用户画像（结构化槽位）
    ├── user_memory    用户记忆（非结构化）
    ├── persona_memory 角色动态设定
    ├── messages       对话历史
    ├── summaries      历史压缩摘要
    └── personas       角色配置
```

Claude Code 本身不生成聊天内容，只负责调度。实际对话由 `chat-model` MCP server 调用仿真人语言模型（支持 Anthropic 或 OpenAI 兼容接口）完成。

## 每轮对话流程

```
Step 0  保存用户消息到 DB
   │
Step 1  并行执行（互不依赖）
   ├── web-search agent      — 判断是否需要搜索，返回结果 B
   └── profile-sync agent    — 提取用户信息更新画像 P（条件触发）
   │
Step 2  并行执行（依赖 Step 1 结果）
   ├── topic-suggest agent   — 生成话题引导建议 C
   ├── recall-memory agent   — 语义召回用户记忆 MEM
   ├── recall-fewshot agent  — 语义召回 few-shot 示例 F
   └── recall-persona agent  — 语义召回角色新设定 PER
   │
Step 3  构建 extra_context（F + P + MEM + PER + B + C）
        → conversation_chat()  调用仿真人模型生成回复
   │
Step 4  quality-check agent 检测回复质量
        → 不通过则重试（最多 3 次），最终降级到 rewrite_style
   │
Step 5  输出 D_final 给用户
        → save_turn() 保存到 DB
   │
Step 5.5  profile-sync persona 模式（后台）— 提取角色新设定
Step 6    历史压缩（N > 20 轮时后台触发）
```

## 目录结构

```
cc_for_chat/
├── mcp_servers/
│   ├── chat-model/     仿真人语言模型封装（conversation_chat / rewrite_style / save_turn）
│   ├── db/             SQLite 数据库操作（画像、记忆、摘要、角色管理）
│   ├── web-search/     DuckDuckGo 联网搜索
│   ├── weather/        天气查询
│   └── memory/         (可选) 轻量级文件记忆后端
├── personas/
│   ├── 小雨/           角色示例（persona.md + fewshot.md）
│   ├── 小北/
│   └── default/
├── .claude/
│   ├── agents/         子 agent 定义（quality-check、recall-*、topic-suggest 等）
│   ├── skills/         可调用技能（clone-persona、memory-ops 等）
│   ├── commands/       /persona 等指令
│   └── hooks/          get_conversation_id.py（自动注入 conversation_id）
├── docs/               设计文档
├── scripts/            init_db.py、clean_db.py
├── roleplay_instruct.md  编排核心系统提示词
├── setup.sh            一键安装脚本
└── requirements.txt    Python 依赖汇总
```

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/cc_for_chat.git
cd cc_for_chat
```

### 2. 安装依赖

```bash
bash setup.sh
```

### 3. 配置模型 API

编辑 `.mcp.json`，在 `chat-model` 的 `env` 中填入你的 Anthropic API 密钥和模型：

```json
{
  "API_TYPE": "anthropic",
  "ANTHROPIC_API_KEY": "sk-ant-xxx",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6"
}
```

### 4. 初始化数据库

```bash
python3 scripts/init_db.py
```

### 5. 启动

```bash
claude --append-system-prompt "$(cat roleplay_instruct.md)"
```

## 角色配置

角色文件位于 `personas/<角色名>/`，每个角色包含两个文件：

- `persona.md`：人设描述（性格、背景、说话风格等）
- `fewshot.md`：对话示例，用于风格对齐

运行时通过 `/persona <角色名>` 切换角色。

### 内置角色

项目内置两个示例角色，`init_db.py` 会自动将其写入数据库：

**小雨** — 25 岁上海 UI 设计师，性格开朗活泼，有话直说，养了一只橘猫"豆腐"。

**小北** — 24 岁北京运营，性格粘人热情，喜欢打游戏、逛 livehouse，养了一只橘猫"花卷"。

### 添加自定义角色

1. 在 `personas/` 下新建文件夹，例如 `personas/小明/`
2. 创建 `persona.md`，写入角色人设：

   ```
   你叫小明，是个 22 岁的大学生，喜欢打篮球和听说唱。
   [说话风格]
   1. 说话简短直接，偶尔用网络用语。每次只说一句话。
   2. 线上聊天风格，非面对面互动
   ```

3. （可选）创建 `fewshot.md`，写入对话示例用于风格对齐：

   ```
   对方：在吗
   你：在 怎了

   对方：你在干嘛
   你：打球刚回来
   ```

4. 在 `scripts/init_db.py` 的 `PERSONA_SEEDS` 列表中添加条目：

   ```python
   {"name": "小明", "model": "anthropic"},
   ```

5. 重新运行 `python3 scripts/init_db.py` 将角色写入数据库。

## 记忆系统

系统维护三种独立的持久化存储：

| 类型 | 说明 | 写入时机 |
|------|------|----------|
| 用户画像 | 结构化槽位（年龄、城市、爱好等） | 每轮对话后自动提取 |
| 用户记忆 | 非结构化事件/情感细节 | 检测到不适合放槽位的信息时 |
| 角色记忆 | 角色在对话中引入的新设定 | 角色回复含新事实时自动写入 |

所有记忆按 `conversation_id` 隔离，支持多用户/多会话并发。

## 依赖

- [Claude Code CLI](https://claude.ai/code)
- Python 3.8+
- `mcp`、`openai`、`anthropic`、`ddgs`（见 `requirements.txt`）

## License

MIT
