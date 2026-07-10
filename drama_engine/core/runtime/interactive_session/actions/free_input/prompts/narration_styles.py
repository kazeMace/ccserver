"""续写风格 prompt 片段。

每种风格定义：
  - 输出格式说明（告诉 LLM 输出什么结构）
  - 写作指令（告诉 LLM 用什么语气/视角）
"""

# ── 平铺直叙：纯旁白描写 ──
PLAIN_NARRATION = {
    "output_format": """\
输出 JSON 中的 "narration" 字段为第三人称旁白叙述。
- 描写场景、动作、心理活动
- 不要写成对话格式
- 200字以内""",
    "schema": '{"narration": "旁白叙述文本", "choices": [...], "should_end": false, "ending_id": null}',
    "writing_style": "用第三人称，简洁生动地描写场景和人物动作。",
}

# ── 对话序列：逐句对话（类似 The Clause 的 dialogue_history）──
DIALOGUE_SEQUENCE = {
    "output_format": """\
输出 JSON 必须包含以下字段:
- "title": 本段剧情的标题（5字以内）
- "synopsis": 本段剧情大纲（一句话概括）
- "location": 场景地点名称（如 "咖啡厅"、"律所会议室"）
- "dialogue_history": 对话列表，每项格式 {"speaker": "角色名或narrator", "text": "台词", "location": "场景地点"}
  - 包含旁白（speaker: "narrator"）和角色对话
  - 保持对话自然简洁
  - 3-8 句
- "choices": 分支选项
- "should_end": false
- "ending_id": null""",
    "schema": '{"title": "...", "synopsis": "...", "location": "...", "dialogue_history": [{"speaker": "...", "text": "...", "location": "..."}], "choices": [...], "should_end": false, "ending_id": null}',
    "writing_style": "写成剧本式对话，每句台词简短有力，旁白用于描写动作和环境。",
}

# ── 混排：旁白 + 对话交替 ──
MIXED = {
    "output_format": """\
输出 JSON 同时包含 "narration"（旁白段落）和 "dialogue_history"（对话列表）。
- narration: 场景描写和心理活动（100字内）
- dialogue_history: 接续的对话（2-5句）""",
    "schema": '{"narration": "...", "dialogue_history": [{"speaker": "...", "text": "..."}], "choices": [...], "should_end": false, "ending_id": null}',
    "writing_style": "先用一段旁白铺设氛围，再展开角色对话推动情节。",
}

# 注册表：name → prompt 配置（仅登记已有组件实现的风格）
NARRATION_PROMPTS: dict[str, dict[str, str]] = {
    "plain_narration": PLAIN_NARRATION,
    "dialogue_sequence": DIALOGUE_SEQUENCE,
    "mixed": MIXED,
}
