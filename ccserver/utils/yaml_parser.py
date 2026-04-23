"""
yaml_parser — 统一 YAML frontmatter 解析入口。

覆盖场景：
  - Agent / Skill / Command / Hook 定义文件（Markdown + YAML frontmatter）
  - 其他含 --- 包裹 YAML 块的文本

用法：
    from ccserver.utils import parse_frontmatter

    meta, body = parse_frontmatter(text)
    # meta: dict | None
    # body: str

行为：
  - 文本不以 --- 开头：返回 ({}, 原始文本)
  - frontmatter 格式错误（缺少闭合 ---）：返回 (None, 原始文本)
  - 解析成功：返回 (meta_dict, frontmatter 之后的正文)

更新记录：
  2025-04-11  由手写解析器迁移至 PyYAML (yaml.safe_load)，统一作为全项目唯一入口。
              原 frontmatter.py 被 yaml_parser.py 取代。
"""

import re

try:
    import yaml
except ImportError as _e:  # noqa: F841
    raise ImportError(
        "PyYAML is required for frontmatter parsing. "
        "Run: pip install pyyaml"
    )


def parse(text: str) -> tuple[dict | None, str]:
    """
    解析 Markdown 文本头部的 YAML frontmatter。

    返回 (meta, body)：
      meta — frontmatter 解析结果（dict）；无 frontmatter 返回 {}；解析失败返回 None。
      body — frontmatter 之后的正文，已去除首尾空白。
    """
    if not text.startswith("---"):
        return {}, text

    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return None, text

    try:
        meta = yaml.safe_load(match.group(1))
    except Exception:
        return None, text

    if not isinstance(meta, dict):
        meta = {}

    body = match.group(2).strip()
    return meta, body
