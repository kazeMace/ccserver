#!/usr/bin/env bash
# setup.sh — cc_for_chat 一键安装脚本
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "项目根目录：$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# 检测 Python
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "错误：未找到 python3，请先安装 Python 3 或激活 conda 环境后重试。"
    exit 1
fi
echo "Python 路径：$(which python3)"

# ---------------------------------------------------------------------------
# 安装依赖
# ---------------------------------------------------------------------------
echo ""
echo "==> 安装 web-search MCP 依赖..."
pip3 install -r "$PROJECT_ROOT/mcp_servers/web-search/requirements.txt"

echo ""
echo "==> 安装 memory MCP 依赖..."
pip3 install -r "$PROJECT_ROOT/mcp_servers/memory/requirements.txt"

echo ""
echo "==> 安装 chat-model MCP 依赖..."
pip3 install -r "$PROJECT_ROOT/mcp_servers/chat-model/requirements.txt"

# ---------------------------------------------------------------------------
# 赋予 hook 脚本执行权限
# ---------------------------------------------------------------------------
echo ""
echo "==> 设置 hook 脚本执行权限..."
chmod +x "$PROJECT_ROOT/.claude/hooks/get_conversation_id.py"

# ---------------------------------------------------------------------------
# 创建运行时目录
# ---------------------------------------------------------------------------
echo ""
echo "==> 创建 data/ 运行时目录..."
mkdir -p "$PROJECT_ROOT/data"

# ---------------------------------------------------------------------------
# 从模板生成 .mcp.json（已存在则跳过）
# ---------------------------------------------------------------------------
echo ""
if [ -f "$PROJECT_ROOT/.mcp.json" ]; then
    echo "==> .mcp.json 已存在，跳过生成。"
else
    echo "==> 从 .mcp.json.example 复制生成 .mcp.json..."
    cp "$PROJECT_ROOT/.mcp.json.example" "$PROJECT_ROOT/.mcp.json"
    echo "    已创建 .mcp.json"
    echo "    ⚠️  请编辑 .mcp.json，填入 OPENAI_BASE_URL / MODEL_NAME / OPENAI_API_KEY。"
fi

# ---------------------------------------------------------------------------
# 从模板生成 .claude/settings.local.json（已存在则跳过）
# ---------------------------------------------------------------------------
echo ""
if [ -f "$PROJECT_ROOT/.claude/settings.local.json" ]; then
    echo "==> .claude/settings.local.json 已存在，跳过生成。"
else
    echo "==> 从 .claude/settings.local.json.example 复制生成配置文件..."
    cp "$PROJECT_ROOT/.claude/settings.local.json.example" "$PROJECT_ROOT/.claude/settings.local.json"
    echo "    已创建 .claude/settings.local.json"
fi

# ---------------------------------------------------------------------------
# 完成
# ---------------------------------------------------------------------------
echo ""
echo "✅ 安装完成。"
echo ""
echo "后续步骤："
echo "  1. 编辑 .mcp.json → 填入 OPENAI_BASE_URL、MODEL_NAME、OPENAI_API_KEY"
echo "  2. 启动 Claude Code："
echo "     claude --append-system-prompt \"\$(cat roleplay_instruct.md)\""
