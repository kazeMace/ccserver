"""约束相关 prompt 片段。

结局收束提示、章节引导等。
"""

# 结局收束提示模板（hint_at_depth 触发时注入 user prompt）
ENDING_HINT_TEMPLATE = """\
【收束提示】剧情即将接近尾声。请引导剧情向以下结局之一收束：
{ending_descriptions}
你可以设置 should_end=true 并指定 ending_id 来触发收束。"""
