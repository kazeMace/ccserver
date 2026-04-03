# CCServer Justfile
# 使用方式：just <recipe>
# 查看所有命令：just 或 just --list

set dotenv-load  # 自动加载 .env 文件

# 默认：列出所有可用命令
default:
    @just --list

# ─── 环境 ──────────────────────────────────────────────────────────────────────

# 安装生产依赖
install:
    uv pip install -r requirements.txt

# 安装开发依赖（含测试、格式化工具）
install-dev:
    uv pip install -r requirements.txt pytest ruff

# ─── 入口 ──────────────────────────────────────────────────────────────────────
# 入口关系：
#
#   server.py  — 后端服务（独立运行）
#   tui.py     — 直连 Agent（独立运行，无需 server.py）
#
#   clients/tui_http.py  ─┐
#   clients/gui.py        ├─ 需先启动 server.py
#

# [后端] 启动 HTTP API 服务器（SSE / WebSocket / HTTP，需设置 CCSERVER_PROJECT_DIR）
api:
    python server.py

# [本地] 终端直连 Agent，无需启动 server.py
tui:
    python tui.py

# [客户端] Gradio 图形界面，需先启动 server.py
gui:
    python clients/gui.py

# [客户端] HTTP 终端界面，测试 server.py 接口用，需先启动 server.py
tui-http:
    python clients/tui_http.py

# ─── 测试 ──────────────────────────────────────────────────────────────────────

# 运行所有测试
test:
    pytest tests/ -v

# 运行指定测试文件，例：just test-file tests/test_agent.py
test-file file:
    pytest {{file}} -v

# 运行指定关键字匹配的测试，例：just test-k "session"
test-k keyword:
    pytest tests/ -v -k "{{keyword}}"

# ─── 代码质量 ──────────────────────────────────────────────────────────────────

# 检查代码风格
lint:
    ruff check src/ tests/

# 自动修复风格问题
fmt:
    ruff format src/ tests/

# ─── Playground ────────────────────────────────────────────────────────────────

# 启动 roleplay_agent（需提前配置 .env 中的 CCSERVER_PROJECT_DIR）
play-roleplay:
    CCSERVER_PROJECT_DIR=./playground/agents/roleplay_agent python server.py


# 启动 web_search
play-websearch:
    CCSERVER_PROJECT_DIR=./playground/agents/web_search python server.py

# 启动 simple_roleplay_graph
play-chat:
    cd playground/graphs/simple_roleplay_graph && python server.py

# ─── 清理 ──────────────────────────────────────────────────────────────────────

# 清理 Python 缓存文件和编译产物
clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -name "*.pyc" -delete 2>/dev/null || true
    find . -name "*.pyo" -delete 2>/dev/null || true
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
    find . -name ".DS_Store" -delete 2>/dev/null || true

# 清理调试录制数据（records/）
clean-records:
    rm -rf records/
    @echo "records/ 已清理"

# ─── 信息 ──────────────────────────────────────────────────────────────────────

# 查看当前配置（从 .env 读取）
config:
    @echo "MODEL:          ${CCSERVER_MODEL:-claude-sonnet-4-6 (默认)}"
    @echo "PROJECT_DIR:    ${CCSERVER_PROJECT_DIR:-(未设置，tui 使用当前目录)}"
    @echo "STORAGE:        ${CCSERVER_STORAGE_BACKEND:-file (默认)}"
    @echo "PROMPT_LIB:     ${CCSERVER_PROMPT_LIB:-cc_reverse:v2.1.81 (默认)}"
    @echo "LOG_LEVEL:      ${CCSERVER_LOG_LEVEL:-DEBUG (默认)}"
    @echo "RECORD_DIR:     ${CCSERVER_RECORD_DIR:-(未设置，不录制)}"
