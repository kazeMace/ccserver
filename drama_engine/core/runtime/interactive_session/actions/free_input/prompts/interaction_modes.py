"""互动方式 prompt 片段。

每种互动方式定义 choices 的格式要求（注入 system prompt 告诉 LLM 生成什么样的选项）。
"""

# ── 分支选择：经典 AVG 选项（含自由输入 fallback）──
BRANCH_CHOICE = {
    "choices_instruction": """\
在 "choices" 字段中生成 {min}-{max} 个分支选项。
每个选项格式: {{"id": "唯一英文标识", "text": "选项文本"}}
- 选项应推动剧情向不同方向发展
- id 用小写下划线命名（如 accept_offer, refuse_and_leave）
- text 简短明确（10字以内）""",
    "requires_choices": True,
}

# ── 纯自由输入：无固定选项 ──
FREE_INPUT_ONLY = {
    "choices_instruction": """\
"choices" 字段设为空数组 []。
玩家将通过自由文本输入推动剧情。""",
    "requires_choices": False,
}

# ── 确认推进：只需点"继续" ──
CONFIRM_ADVANCE = {
    "choices_instruction": """\
"choices" 字段固定为: [{{"id": "continue", "text": "继续"}}]
玩家只需点击继续即可推进。""",
    "requires_choices": False,
}

# 注册表（仅登记已有组件实现的互动方式）
INTERACTION_PROMPTS: dict[str, dict[str, object]] = {
    "branch_choice": BRANCH_CHOICE,
    "free_input_only": FREE_INPUT_ONLY,
    "confirm_advance": CONFIRM_ADVANCE,
}
