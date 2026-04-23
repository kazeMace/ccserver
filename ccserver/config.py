import os
from pathlib import Path

# ─── Model ────────────────────────────────────────────────────────────────────

MODEL = os.getenv("CCSERVER_MODEL", "claude-sonnet-4-6")

# ─── Provider ─────────────────────────────────────────────────────────────────

# LLM 提供商：anthropic、openai、openrouter、ollama、lmstudio、oneapi、volcano、generic
PROVIDER = os.getenv("CCSERVER_PROVIDER", "anthropic")

# 通用 OpenAI-compatible 后端配置（generic / oneapi 等）
CCSERVER_BASE_URL = os.getenv("CCSERVER_BASE_URL", "")
CCSERVER_API_KEY = os.getenv("CCSERVER_API_KEY", "")

# ─── Context compaction ───────────────────────────────────────────────────────

THRESHOLD = int(os.getenv("CCSERVER_THRESHOLD", "60000"))   # chars/4 ≈ tokens
KEEP_RECENT = int(os.getenv("CCSERVER_KEEP_RECENT", "20"))   # tool results to keep untruncated

# ─── Agent loop limits ────────────────────────────────────────────────────────

MAIN_ROUND_LIMIT = int(os.getenv("CCSERVER_MAIN_ROUNDS", "100"))
SUB_ROUND_LIMIT = int(os.getenv("CCSERVER_SUB_ROUNDS", "30"))
MAX_DEPTH = int(os.getenv("CCSERVER_MAX_DEPTH", "5"))        # max agent nesting depth

# ─── Paths ────────────────────────────────────────────────────────────────────

# 项目工作空间根目录。
# server.py 部署时必须通过 CCSERVER_PROJECT_DIR 显式指定；
# tui.py 本地运行时默认使用当前工作目录。
PROJECT_DIR   = Path(os.getenv("CCSERVER_PROJECT_DIR", ".")).resolve()

# 全局配置目录，存放跨项目共享的 skills/agents/hooks/commands/sessions
GLOBAL_CONFIG_DIR = Path(os.getenv("CCSERVER_GLOBAL_CONFIG_DIR", str(Path.home() / ".ccserver")))

# sessions 和 db 属于全局，跟随 GLOBAL_CONFIG_DIR
SESSIONS_BASE = Path(os.getenv("CCSERVER_SESSIONS_DIR", str(GLOBAL_CONFIG_DIR / "sessions")))
DB_PATH       = Path(os.getenv("CCSERVER_DB_PATH",      str(GLOBAL_CONFIG_DIR / "ccserver.db")))

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR   = Path(os.getenv("CCSERVER_LOG_DIR", str(GLOBAL_CONFIG_DIR / "logs")))
LOG_LEVEL = os.getenv("CCSERVER_LOG_LEVEL", "DEBUG")

# ─── Storage backend ─────────────────────────────────────────────────────────

# "file"（默认）、"sqlite" 或 "mongo"
STORAGE_BACKEND = os.getenv("CCSERVER_STORAGE_BACKEND", "file")

# ─── MongoDB（仅 STORAGE_BACKEND=mongo 时生效）────────────────────────────────

MONGO_URI = os.getenv("CCSERVER_MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("CCSERVER_MONGO_DB",  "ccserver")

# ─── Redis 缓存（仅 STORAGE_BACKEND=mongo 时生效）────────────────────────────

REDIS_URL        = os.getenv("CCSERVER_REDIS_URL",        "redis://localhost:6379")
REDIS_CACHE_SIZE = int(os.getenv("CCSERVER_REDIS_CACHE_SIZE", "100"))
REDIS_TTL        = int(os.getenv("CCSERVER_REDIS_TTL",        "86400"))  # 秒，默认 24h

# ─── Agent Team ───────────────────────────────────────────────────────────────

# 是否启用 Agent Team 功能；True 表示支持 team 抽象、mailbox 协议、SendMessageTool 等
CCSERVER_USER_AGENT_TEAM = os.getenv("CCSERVER_USER_AGENT_TEAM", "false").lower() in ("true", "1", "yes")

# ─── Prompt Lib ───────────────────────────────────────────────────────────────

PROMPT_LIB = os.getenv("CCSERVER_PROMPT_LIB", "cc_reverse:v2.1.81")

# ─── Debug recording ─────────────────────────────────────────────────────────

# 设置此目录后，每次 agent loop 会将每轮的 system/messages/tools 记录到该目录下的 jsonl 文件
# 不设置则不记录
RECORD_DIR = os.getenv("CCSERVER_RECORD_DIR")

# ─── System prompt ────────────────────────────────────────────────────────────

# 启动时注入的额外 system prompt 文件路径（可选）
SYSTEM_FILE    = os.getenv("CCSERVER_SYSTEM_FILE")
# True: 追加到 workflow 末尾；False: 替换 workflow
APPEND_SYSTEM  = os.getenv("CCSERVER_APPEND_SYSTEM", "false").lower() == "true"
