import os
import tempfile
from pathlib import Path

# ─── 临时目录 ─────────────────────────────────────────────────────────────────

# 系统临时目录，进程启动时计算一次。
# macOS 上 tempfile.gettempdir() 返回 /var/folders/...，/tmp 是其符号链接。
# 统一用此常量避免各模块重复调用，也避免 hardcode /tmp 在跨平台时出错。
TEMP_DIR: Path = Path(tempfile.gettempdir())

# ─── Model ────────────────────────────────────────────────────────────────────

MODEL = os.getenv("CCSERVER_MODEL", "claude-sonnet-4-6")

# VLM 模型：用于视觉工具（ScreenFind 等）的多模态推理，必须支持图像输入。
# 默认 claude-sonnet-4-6（支持多模态）；可通过环境变量单独配置，与主 MODEL 解耦。
VLM_MODEL = os.getenv("CCSERVER_VLM_MODEL", "claude-sonnet-4-6")

# VLM 专用 API Key 和 Base URL（用于 ScreenFind 等视觉工具，独立于主 model 的端点）。
# 未设置时 fallback 到 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL（与主 adapter 共用端点）。
VLM_API_KEY  = os.getenv("CCSERVER_VLM_API_KEY")   # None 表示未配置，使用主 key
VLM_BASE_URL = os.getenv("CCSERVER_VLM_BASE_URL")  # None 表示未配置，使用主 base_url

# VLM 路由配置（Phase 4）
# 显式指定 VLM provider（如 "zhipuai"、"qwen"、"anthropic"），未设置时自动按 autoPriority 选择
VLM_PROVIDER = os.getenv("CCSERVER_VLM_PROVIDER")  # None = 自动选择
# 覆盖 autoPriority 排序，数值越低越优先（通常不需要设置）
VLM_PRIORITY = os.getenv("CCSERVER_VLM_PRIORITY", "")  # "" 表示使用默认 autoPriority

# ─── Provider ─────────────────────────────────────────────────────────────────

# LLM 提供商：anthropic、openai、openrouter、ollama、lmstudio、oneapi、volcano、generic
PROVIDER = os.getenv("CCSERVER_PROVIDER", "anthropic")

# 通用 OpenAI-compatible 后端配置（generic / oneapi 等）
CCSERVER_BASE_URL = os.getenv("CCSERVER_BASE_URL", "")
CCSERVER_API_KEY = os.getenv("CCSERVER_API_KEY", "")

# Provider 专用 API Keys（Phase 2）
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")            # 通义千问
ZHIPUAI_API_KEY = os.getenv("ZHIPUAI_API_KEY", "")      # 智谱 GLM

# ─── Context compaction ───────────────────────────────────────────────────────

THRESHOLD = int(os.getenv("CCSERVER_THRESHOLD", "120000"))  # chars/4 ≈ tokens；调大避免普通对话频繁 compact
KEEP_RECENT = int(os.getenv("CCSERVER_KEEP_RECENT", "20"))   # tool results to keep untruncated

# ─── Agent loop limits ────────────────────────────────────────────────────────

MAIN_ROUND_LIMIT = int(os.getenv("CCSERVER_MAIN_ROUNDS", "100"))
SUB_ROUND_LIMIT = int(os.getenv("CCSERVER_SUB_ROUNDS", "30"))
MAX_DEPTH = int(os.getenv("CCSERVER_MAX_DEPTH", "5"))        # max agent nesting depth

# ─── Paths ────────────────────────────────────────────────────────────────────

# 项目工作空间根目录。
# tui.py 本地运行时默认使用当前工作目录；
# server.py 未设置时为 None，此时 Session 使用临时目录作为 project_root。
_PROJECT_DIR_ENV = os.getenv("CCSERVER_PROJECT_DIR")
PROJECT_DIR: Path | None = Path(_PROJECT_DIR_ENV).resolve() if _PROJECT_DIR_ENV else None

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

# ─── 注入 System Prompt（可选）────────────────────────────────────────────────

# 启动时注入的额外 system prompt 文件路径（设置即启用）
INJECT_SYSTEM_FILE = os.getenv("CCSERVER_INJECT_SYSTEM_FILE")
# True: 追加到 workflow 末尾；False: 替换 workflow
APPEND_SYSTEM      = os.getenv("CCSERVER_APPEND_SYSTEM", "false").lower() == "true"
