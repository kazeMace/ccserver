"""
frontmatter — 解析 Markdown 文件头部的 YAML frontmatter。

用法：
    from ccserver.utils import parse_frontmatter

    meta, body = parse_frontmatter(text)
    # meta: dict，frontmatter 中的键值对
    # body: str，frontmatter 之后的正文内容

支持的 YAML 子集：
    key: value          # 字符串值
    key: 123            # 数字（返回 int 或 float）
    key: true           # 布尔值（true/false/yes/no，返回 bool）
    key:                # 列表（后续缩进 "- item" 行）
      - item1
      - item2
    key: item1, item2   # 内联列表（逗号分隔，返回 list[str]）
    key: |              # 多行字符串（后续缩进行拼接，返回 str）
      line1
      line2

不支持：嵌套 dict、引号括起来的复杂值等。

文件没有 frontmatter（不以 --- 开头）时，返回 ({}, 原始文本)。
frontmatter 格式错误时，返回 (None, 原始文本)，调用方应跳过该文件。
"""

import re


def parse(text: str) -> tuple[dict | None, str]:
    """
    解析 Markdown 文本头部的 YAML frontmatter。

    返回 (meta, body)：
      meta  — frontmatter 解析结果，dict；无 frontmatter 返回 {}；解析失败返回 None
      body  — frontmatter 之后的正文，已去除首尾空白
    """
    if not text.startswith("---"):
        return {}, text

    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return None, text

    meta = _parse_yaml_block(match.group(1))
    body = match.group(2).strip()
    return meta, body


def _parse_yaml_block(block: str) -> dict:
    """
    解析 frontmatter 块内容（--- 之间的文本），返回 dict。

    逐行处理，识别四种行：
      1. "key: value"  — 普通键值对
      2. "key:"        — 列表键，后续 "- item" 行归入该键
      3. "key: |"      — 多行字符串键，后续缩进行拼接为字符串
      4. "  - item"    — 列表项，追加到最近的列表键
    """
    meta: dict = {}
    current_list_key: str | None = None   # 当前正在收集列表项的键名
    current_block_key: str | None = None  # 当前正在收集多行字符串的键名

    for line in block.splitlines():
        stripped = line.strip()

        # 多行字符串收集中：缩进行追加，空行/注释/新 key 结束收集
        if current_block_key is not None:
            if line.startswith(" ") or line.startswith("\t"):
                meta[current_block_key] += stripped + "\n"
                continue
            else:
                # 结束多行收集，去掉末尾换行
                meta[current_block_key] = meta[current_block_key].rstrip("\n")
                current_block_key = None

        if not stripped or stripped.startswith("#"):
            current_list_key = None
            continue

        # 列表项：以 "- " 开头，且当前有列表键在收集中
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip()
            if item:
                meta[current_list_key].append(item)
            continue

        # 键值对：必须含冒号
        if ":" not in stripped:
            current_list_key = None
            continue

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        if raw_value == "|":
            # "key: |" → 多行字符串键，等待后续缩进行
            meta[key] = ""
            current_block_key = key
            current_list_key = None
        elif not raw_value:
            # "key:" 后无值 → 列表键，初始化为空列表，等待后续 "- item"
            meta[key] = []
            current_list_key = key
        else:
            # "key: value" → 解析值类型，结束列表收集
            current_list_key = None
            meta[key] = _parse_value(raw_value)

    # 文件末尾结束多行收集
    if current_block_key is not None:
        meta[current_block_key] = meta[current_block_key].rstrip("\n")

    return meta


def _parse_value(raw: str):
    """
    将字符串值解析为合适的 Python 类型。

    优先级：bool → int → float → 内联列表（含逗号） → str
    """
    # 布尔值
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False

    # 整数
    try:
        return int(raw)
    except ValueError:
        pass

    # 浮点数
    try:
        return float(raw)
    except ValueError:
        pass

    # 内联列表：含逗号、不像 URL、且每个元素都是单个 token（不含空格）
    # 避免把普通描述句（如 "Build X, Y and Z servers..."）误判为列表
    if "," in raw and "://" not in raw:
        items = [v.strip() for v in raw.split(",") if v.strip()]
        if len(items) > 1 and all(" " not in item for item in items):
            return items

    # 字符串
    return raw
