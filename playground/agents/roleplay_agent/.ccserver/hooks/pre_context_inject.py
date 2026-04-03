#!/usr/bin/env python3
"""
Pre-Context Injection Hook  (UserPromptSubmit)

在每轮对话开始前自动注入以下内容：
  1. 当前时间
  2. 用户画像（全量，sessions/<session_id>/user_profile.json）
  3. 用户记忆（相关性召回，sessions/<session_id>/user_memory.md）
  4. 角色新设定（全量，sessions/<session_id>/persona_memory.md）
  5. IR 编排指令重注入（每 REINJECT_EVERY 轮重注入 relay prompt）

注意：HC-1 历史压缩由 orchestrator 在 Step 3 自行判断触发，不在 hook 中处理。

Input  (stdin): JSON event — 包含 prompt, transcript_path
Output (stdout): JSON with additionalContext
"""

import json
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 用户记忆召回配置
MEMORY_RECALL_TOP_K = 5       # 最多召回条数
MEMORY_FALLBACK_K = 3         # 无命中时返回最近 N 条
SCORE_KEYWORD_HIT = 2         # 每个关键词命中加分
SCORE_WITHIN_7_DAYS = 2       # 7 天内加分
SCORE_WITHIN_30_DAYS = 1      # 30 天内加分

# IR（Instruction Reinject，编排指令重注入）
REINJECT_EVERY = 5

_HOOK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _HOOK_DIR.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
SESSION_ID_PATH = DATA_DIR / "current_session_id.txt"
RELAY_PROMPT_PATH = PROJECT_ROOT / "roleplay_instruct.md"


# ---------------------------------------------------------------------------
# Session 路径
# ---------------------------------------------------------------------------

def _get_session_dir() -> Path:
    session_id = "default"
    if SESSION_ID_PATH.exists():
        session_id = SESSION_ID_PATH.read_text(encoding="utf-8").strip() or "default"
    return SESSIONS_DIR / session_id


def _now_str() -> str:
    now = datetime.now()
    return now.strftime("%Y年%m月%d日 %H:%M:%S") + f" {_WEEKDAYS[now.weekday()]}"


# ---------------------------------------------------------------------------
# 用户画像（全量注入）
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _format_profile(profile: dict) -> str:
    if not profile:
        return ""
    lines = ["[用户画像]"]
    for key, data in profile.items():
        val = data.get("value", data) if isinstance(data, dict) else data
        lines.append(f"  · {key}: {val}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 用户记忆（关键词 + 时间加权召回）
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> list[str]:
    """从用户消息中提取关键词：去停用词后的中文词和英文词，长度 > 1。"""
    stopwords = {
        "我", "你", "他", "她", "它", "的", "了", "吗", "呢", "啊", "哦", "嗯",
        "是", "在", "有", "也", "都", "就", "和", "与", "或", "但", "不", "没",
        "这", "那", "什么", "怎么", "为什么", "怎样", "哪", "谁", "一个", "一",
        "很", "太", "真", "好", "大", "小", "多", "少", "还", "再", "已经",
    }
    # 提取长度 > 1 的中文词段和英文词
    tokens = re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]{2,}', text)
    return [t for t in tokens if t.lower() not in stopwords]


def _parse_memory_date(line: str) -> date | None:
    """从记忆行中解析日期，格式：- [YYYY-MM-DD] ..."""
    m = re.match(r'-\s*\[(\d{4}-\d{2}-\d{2})\]', line.strip())
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _recall_memories(memory_path: Path, query: str) -> str:
    """关键词 + 时间加权召回 user_memory.md 中的相关条目。"""
    if not memory_path.exists():
        return ""

    raw = memory_path.read_text(encoding="utf-8").strip()
    if not raw:
        return ""

    # 只取以 "- [" 开头的记忆条目行
    lines = [l for l in raw.splitlines() if l.strip().startswith("- [")]
    if not lines:
        return ""

    keywords = _extract_keywords(query)
    today = date.today()
    scored: list[tuple[int, str]] = []

    for line in lines:
        score = 0
        line_lower = line.lower()

        # 关键词命中得分
        for kw in keywords:
            if kw.lower() in line_lower:
                score += SCORE_KEYWORD_HIT

        # 时间加权得分
        mem_date = _parse_memory_date(line)
        if mem_date:
            delta = (today - mem_date).days
            if delta <= 7:
                score += SCORE_WITHIN_7_DAYS
            elif delta <= 30:
                score += SCORE_WITHIN_30_DAYS

        scored.append((score, line))

    # 取分数 > 0 的，按分数降序，最多 TOP_K 条
    relevant = sorted(
        [(s, l) for s, l in scored if s > 0],
        key=lambda x: x[0],
        reverse=True,
    )[:MEMORY_RECALL_TOP_K]

    # 兜底：无命中时返回最近 FALLBACK_K 条
    if not relevant:
        relevant = [(0, l) for l in lines[-MEMORY_FALLBACK_K:]]

    if not relevant:
        return ""

    result_lines = [l for _, l in relevant]
    return "[用户记忆]\n" + "\n".join(result_lines)


# ---------------------------------------------------------------------------
# 角色新设定（全量注入）
# ---------------------------------------------------------------------------

def _read_persona_memory(path: Path) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    lines = content.splitlines()
    body_lines = [l for l in lines if not l.startswith("# ")]
    body = "\n".join(body_lines).strip()
    if not body:
        return ""
    return f"[角色新设定]\n{body}"


# ---------------------------------------------------------------------------
# IR 重注入
# ---------------------------------------------------------------------------

def _load_transcript(transcript_path: str) -> list[dict]:
    messages = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
    except Exception:
        pass
    return messages


def _count_user_turns(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("type") in ("human", "user"))


def _count_completed_turns(messages: list[dict]) -> int:
    return max(0, _count_user_turns(messages) - 1)


def _should_reinject_relay(transcript_path: str) -> bool:
    if not transcript_path or not Path(transcript_path).exists():
        return False
    messages = _load_transcript(transcript_path)
    completed_turns = _count_completed_turns(messages)
    return completed_turns > 0 and (completed_turns % REINJECT_EVERY == 0)


def _load_relay_prompt() -> str:
    try:
        if RELAY_PROMPT_PATH.exists():
            return RELAY_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:
        sys.exit(0)

    current_prompt: str = event.get("prompt", "")
    transcript_path: str = event.get("transcript_path", "")

    session_dir = _get_session_dir()
    parts: list[str] = []

    # 1. 当前时间
    parts.append(f"[当前时间] {_now_str()}")

    # 2. 用户画像（全量）
    profile_text = _format_profile(_load_json(session_dir / "user_profile.json"))
    if profile_text:
        parts.append(profile_text)

    # 3. 用户记忆（相关性召回）
    user_memory_text = _recall_memories(session_dir / "user_memory.md", current_prompt)
    if user_memory_text:
        parts.append(user_memory_text)

    # 4. 角色新设定（全量）
    persona_memory_text = _read_persona_memory(session_dir / "persona_memory.md")
    if persona_memory_text:
        parts.append(persona_memory_text)

    # 5. IR：每 REINJECT_EVERY 轮重注入 relay 编排指令
    if _should_reinject_relay(transcript_path):
        relay_prompt = _load_relay_prompt()
        if relay_prompt:
            parts.append(
                f"[IR - 编排指令重注入，每{REINJECT_EVERY}轮自动触发]\n{relay_prompt}"
            )

    if not parts:
        sys.exit(0)

    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(parts),
        },
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
