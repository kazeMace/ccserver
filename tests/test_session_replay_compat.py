# tests/test_session_replay_compat.py
"""旧格式 messages.jsonl 回放：wire_to_unified_message 能读现有磁盘格式；序列化稳定可 JSON 化。

迁移说明：
  旧测试使用 UnifiedMessage.from_dict（不存在），新 API 为 wire_to_unified_message。
  content 类型名已更新为带 "Unified" 前缀（UnifiedTextBlock 等）。

注意：to_dict 现在 content 恒为 list（单文本不塌缩成 str），所以对"旧 str content"行，
wire_to_unified_message→to_dict 会把 content 规范化为 list 形态（语义等价，codec 两种都接受）。
本测试验证：(a) 旧格式都能被 wire_to_unified_message 读入且不丢数据；(b) 规范化后仍可 JSON 序列化；
(c) 已是 list/command/metadata 的行 wire_to_unified_message→to_dict 完全往返不变。"""

import json
from ccserver.messages import (
    UnifiedMessage, wire_to_unified_message,
    UnifiedTextBlock as TextBlock,
    UnifiedToolResultBlock as ToolResultBlock,
    UnifiedCommandBlock as CommandBlock,
)


def test_legacy_str_content_reads_and_normalizes_to_list():
    """旧 str content → wire_to_unified_message 读入为 [UnifiedTextBlock]；to_dict 规范化为 list（语义等价）。"""
    legacy = {"role": "user", "content": "hello"}
    um = wire_to_unified_message(legacy)
    assert [type(b).__name__ for b in um.content] == ["UnifiedTextBlock"]
    assert um.content[0].text == "hello"
    out = um.to_dict()
    assert out == {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    json.dumps(out)  # JSON 可序列化


def test_legacy_list_blocks_roundtrip_stable():
    """已是 list 形态的旧行 wire_to_unified_message→to_dict 完全不变。"""
    legacy = {"role": "assistant", "content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"cmd": "ls"}}]}
    assert wire_to_unified_message(legacy).to_dict() == legacy


def test_legacy_tool_result_roundtrip_stable():
    legacy = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "file.txt", "is_error": False}]}
    um = wire_to_unified_message(legacy)
    assert isinstance(um.content[0], ToolResultBlock)
    assert um.to_dict() == legacy


def test_legacy_command_content_roundtrip_stable():
    legacy = {"role": "user", "content": {"_type": "command", "name": "clear",
                                          "args": "", "stdout": "", "body": ""}}
    um = wire_to_unified_message(legacy)
    assert isinstance(um.content[0], CommandBlock)
    assert um.to_dict() == legacy


def test_legacy_team_markers_preserved():
    """team 标记从顶层进 metadata，to_dict 展平还原（content 规范化为 list）。"""
    legacy = {"role": "user", "content": "do", "_ccserver_team_new_task": True, "task_id": "x"}
    um = wire_to_unified_message(legacy)
    assert um.metadata == {"_ccserver_team_new_task": True, "task_id": "x"}
    out = um.to_dict()
    assert out["_ccserver_team_new_task"] is True and out["task_id"] == "x"
    assert out["content"] == [{"type": "text", "text": "do"}]
    json.dumps(out)


def test_full_jsonl_lines_all_loadable():
    """模拟一整段旧 messages.jsonl：每行都能 wire_to_unified_message 且 to_dict 可 JSON 化，不抛。"""
    legacy_lines = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"},
                                          {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]},
        {"role": "user", "content": "do", "_ccserver_team_new_task": True, "task_id": "x"},
        {"role": "user", "content": {"_type": "command", "name": "clear", "args": "", "stdout": "", "body": ""}},
    ]
    for d in legacy_lines:
        um = wire_to_unified_message(d)
        json.dumps(um.to_dict())  # 不抛即通过
