"""
Recorder — 将 LLM 每轮调用的 input/output 追加写入 JSONL 文件。

仅当 CCSERVER_RECORD_DIR 环境变量已设置时生效。
每个 agent 一个文件，文件名为 {agent_id}.jsonl。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Recorder:
    """
    按 agent 维度记录每轮 LLM 调用的完整上下文。

    record_dir 为空字符串时，record() 是空操作。
    """

    def __init__(
        self,
        *,
        record_dir: str,
        agent_id: str,
        agent_name: str,
        depth: int,
        model: str,
        system: list,
        schemas: list,
    ):
        self.record_dir = record_dir
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.depth = depth
        self.model = model
        self.system = system
        self.schemas = schemas

        if record_dir:
            dir_path = Path(record_dir)
            dir_path.mkdir(parents=True, exist_ok=True)
            self._file = dir_path / f"{agent_id}.jsonl"
        else:
            self._file = None

    def record(
        self,
        round_num: int,
        input_messages: list,
        response_content: list[Any] | None = None,
        stop_reason: str | None = None,
    ):
        """
        将本轮 LLM 调用的完整 input/output 追加写入 record 文件。
        record_dir 未设置时直接返回。

        input_messages:   LLM 调用前的消息列表快照
        response_content: LLM 返回的 content 块列表（调用后填入）
        stop_reason:      LLM 返回的 stop_reason
        """
        if self._file is None:
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "depth": self.depth,
            "round": round_num,
            "model": self.model,
            "input": {
                "system": self.system,
                "messages": input_messages,
                "tools": self.schemas,
            },
            "output": {
                "content": response_content,
                "stop_reason": stop_reason,
            },
        }

        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
