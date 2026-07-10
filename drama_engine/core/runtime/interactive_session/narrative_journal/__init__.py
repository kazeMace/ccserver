"""叙事日志模块。

按 user_id + session_id 记录玩家的完整故事线，
统一记录预制节点跳转和生成节点，支持回溯和可视化。
"""

from drama_engine.core.runtime.interactive_session.narrative_journal.models import (
    ContentBlock,
    NarrativeNode,
    PlayerAction,
)
from drama_engine.core.runtime.interactive_session.narrative_journal.journal import (
    NarrativeJournal,
)
from drama_engine.core.runtime.interactive_session.narrative_journal.store import (
    NarrativeStore,
    JsonFileNarrativeStore,
)

__all__ = [
    "ContentBlock",
    "NarrativeNode",
    "PlayerAction",
    "NarrativeJournal",
    "NarrativeStore",
    "JsonFileNarrativeStore",
]
