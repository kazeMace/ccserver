---
name: tmux
description: 使用 tmux 管理长时间运行的后台进程：启动 session、发送命令、读取输出、清理环境。
tags: [tmux, terminal, background-process]
---

# tmux 使用指南

## 何时使用 tmux

- 需要在后台持续运行的进程（dev server、长时间编译、监控脚本）
- 需要同时运行多个进程并分别查看输出
- 用户要求在特定 tmux session 中执行命令

**注意**：短时命令直接用 Bash 工具，不需要 tmux。

## 基本操作

### 创建和管理 session

```bash
# 创建新 session（后台运行）
tmux new-session -d -s my-session

# 列出所有 session
tmux list-sessions

# 检查 session 是否存在
tmux has-session -t my-session 2>/dev/null && echo "exists" || echo "not found"

# 杀掉 session
tmux kill-session -t my-session
```

### 在 session 中执行命令

```bash
# 发送命令到 session（异步，不等待结果）
tmux send-keys -t my-session "npm run dev" Enter

# 发送命令到指定 window
tmux send-keys -t my-session:0 "python server.py" Enter
```

### 读取输出

```bash
# 捕获当前 pane 内容
tmux capture-pane -t my-session -p

# 捕获并保存到文件
tmux capture-pane -t my-session -p > /tmp/output.txt

# 等待某个关键词出现（轮询读取）
for i in $(seq 1 30); do
    output=$(tmux capture-pane -t my-session -p)
    echo "$output" | grep -q "Server running" && break
    sleep 1
done
```

## 标准启动 dev server 流程

```bash
# 1. 检查 session 是否已存在，已存在则先杀掉
tmux has-session -t dev 2>/dev/null && tmux kill-session -t dev

# 2. 创建新 session 并启动服务
tmux new-session -d -s dev
tmux send-keys -t dev "cd /path/to/project && npm run dev" Enter

# 3. 等待启动完成
sleep 3
output=$(tmux capture-pane -t dev -p)
echo "$output"

# 4. 验证服务是否正常
curl -s http://localhost:3000/health
```

## 清理规范

任务完成后清理不再需要的 session：
```bash
tmux kill-session -t my-session
```

长期运行的进程（用户明确要保留的 server）不要主动 kill。
