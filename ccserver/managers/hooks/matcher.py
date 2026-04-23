"""
hook_matcher — Hook 的 matcher 表达式解析与求值。

Claude Code 的 matcher 支持两种写法：
  1. 简单匹配："Bash"、"Bash|Write"、"^Write.*"、"*"
  2. 完整表达式：tool == "Bash" && tool_input.command matches "git *"

本模块将 matcher 设计为可实例化的类，便于扩展、测试和复用。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger


class HookMatcher(ABC):
    """
    matcher 抽象基类。

    所有 matcher 实例必须实现 match(payload) -> bool。
    payload 是事件专属数据字典，如：
      {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    """

    @abstractmethod
    def match(self, payload: dict) -> bool:
        raise NotImplementedError


class AlwaysMatcher(HookMatcher):
    """无条件匹配（空字符串或 *）。"""

    def match(self, payload: dict) -> bool:
        return True


class LiteralMatcher(HookMatcher):
    """
    CC 的简单 matcher 语法：
      - 精确匹配："Bash"
      - 多值精确匹配："Bash|Write|Edit"
      - 正则匹配："^Write.*"（包含非 [a-zA-Z0-9_|] 字符时视为正则）
    """

    def __init__(self, pattern: str):
        self.pattern = pattern.strip()
        self._is_regex = not _MATCHER_LITERAL_PATTERN.match(self.pattern)
        self._regex: Optional[re.Pattern] = None
        if self._is_regex:
            try:
                self._regex = re.compile(self.pattern)
            except Exception as e:
                logger.warning("Invalid regex in LiteralMatcher: '{}' error={}", self.pattern, e)

    def match(self, payload: dict) -> bool:
        target = payload.get("tool_name", "")
        if not target:
            return False
        if self._regex:
            return bool(self._regex.search(target))
        parts = [p.strip() for p in self.pattern.split("|")]
        return target in parts


# ── Tokenizer ─────────────────────────────────────────────────────────────────


@dataclass
class _Token:
    kind: str
    value: Any


# ── ExpressionMatcher ─────────────────────────────────────────────────────────


class ExpressionMatcher(HookMatcher):
    """
    完整表达式 matcher。

    支持语法：
      - 字面量："string", 'string', 123, true, false
      - 字段访问：tool, tool_input.command, tool_output.output
      - 比较：==, !=, matches（正则匹配）, contains, startswith, endswith
      - 逻辑：&&, ||, !
      - 括号分组：(a && b)

    示例：
      tool == "Bash" && tool_input.command matches "git *"
      tool == "Write" && tool_input.file_path matches "\\.(md|txt)$" && !(tool_input.file_path matches "README\\.md|CLAUDE\\.md")
    """

    def __init__(self, expression: str):
        self.source = expression.strip()
        self._tokens = list(_tokenize(self.source))
        self._pos = 0

    def match(self, payload: dict) -> bool:
        self._pos = 0
        try:
            result = self._parse_or_expr(payload)
            # 消费完所有 token
            return bool(result)
        except Exception as e:
            logger.warning("ExpressionMatcher eval failed | expr={} error={}", self.source, e)
            return False

    # ── 递归下降解析器 ─────────────────────────────────────────────────────────

    def _peek(self) -> Optional[_Token]:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> Optional[_Token]:
        tok = self._peek()
        if tok:
            self._pos += 1
        return tok

    def _expect(self, kind: str, value: Optional[str] = None) -> _Token:
        tok = self._peek()
        if tok is None:
            raise ValueError(f"Unexpected end of expression, expected {kind}")
        if tok.kind != kind:
            raise ValueError(f"Expected {kind}, got {tok.kind}({tok.value})")
        if value is not None and tok.value != value:
            raise ValueError(f"Expected '{value}', got '{tok.value}'")
        return self._advance()

    def _parse_or_expr(self, payload: dict) -> bool:
        """|| 优先级最低"""
        left = self._parse_and_expr(payload)
        while self._peek() and self._peek().kind == "OP" and self._peek().value == "||":
            self._advance()
            right = self._parse_and_expr(payload)
            left = left or right
        return left

    def _parse_and_expr(self, payload: dict) -> bool:
        """&& 优先级高于 ||"""
        left = self._parse_unary_expr(payload)
        while self._peek() and self._peek().kind == "OP" and self._peek().value == "&&":
            self._advance()
            right = self._parse_unary_expr(payload)
            left = left and right
        return left

    def _parse_unary_expr(self, payload: dict) -> bool:
        """! 优先级高于 && ||"""
        if self._peek() and self._peek().kind == "OP" and self._peek().value == "!":
            self._advance()
            val = self._parse_unary_expr(payload)
            return not val
        return self._parse_primary_expr(payload)

    def _parse_primary_expr(self, payload: dict) -> bool:
        """基础表达式：括号分组 或 比较表达式"""
        if self._peek() and self._peek().kind == "LPAREN":
            self._advance()  # (
            val = self._parse_or_expr(payload)
            self._expect("RPAREN", ")")
            return val

        return self._parse_comparison(payload)

    def _parse_comparison(self, payload: dict) -> bool:
        """比较表达式：field OP value"""
        left_tok = self._advance()
        if left_tok is None:
            raise ValueError("Unexpected end of expression in comparison")

        # 左值只能是字段名或字面量
        left_val = _resolve_token(left_tok, payload)

        # 判断是否是二元比较运算符
        op_tok = self._peek()
        if op_tok is None or op_tok.kind != "COMP":
            # 不是比较表达式，直接返回布尔值（如单字段名或单字面量）
            return bool(left_val)

        self._advance()
        op = op_tok.value

        right_tok = self._advance()
        if right_tok is None:
            raise ValueError("Missing right side of comparison")
        right_val = _resolve_token(right_tok, payload)

        return _apply_operator(left_val, op, right_val)


def _tokenize(expr: str) -> list[_Token]:
    """
    将表达式字符串拆分为 token 列表。

    支持的 token 类型：
      STRING    — 双引号或单引号字符串
      NUMBER    — 整数
      BOOL      — true / false
      IDENT     — 标识符（字段名）
      COMP      — ==、!=、matches、contains、startswith、endswith
      OP        — &&、||、!
      LPAREN    — (
      RPAREN    — )
    """
    tokens: list[_Token] = []
    i = 0
    length = len(expr)

    while i < length:
        ch = expr[i]

        # 跳过空白
        if ch.isspace():
            i += 1
            continue

        # 字符串
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < length:
                if expr[j] == "\\" and j + 1 < length:
                    j += 2
                    continue
                if expr[j] == quote:
                    break
                j += 1
            raw = expr[i + 1:j]
            # 处理转义
            value = _unescape_string(raw, quote)
            tokens.append(_Token("STRING", value))
            i = j + 1
            continue

        # 数字
        if ch.isdigit():
            j = i
            while j < length and expr[j].isdigit():
                j += 1
            tokens.append(_Token("NUMBER", int(expr[i:j])))
            i = j
            continue

        # 标识符 / 关键字
        if ch.isalpha() or ch == "_":
            j = i
            while j < length and (expr[j].isalnum() or expr[j] in "_."):
                j += 1
            word = expr[i:j]
            lower = word.lower()
            if lower in ("true", "false"):
                tokens.append(_Token("BOOL", lower == "true"))
            elif lower in ("matches", "contains", "startswith", "endswith"):
                tokens.append(_Token("COMP", lower))
            elif lower in ("and", "or", "not"):
                # 把英文关键字也转成对应操作符
                if lower == "and":
                    tokens.append(_Token("OP", "&&"))
                elif lower == "or":
                    tokens.append(_Token("OP", "||"))
                elif lower == "not":
                    tokens.append(_Token("OP", "!"))
            else:
                tokens.append(_Token("IDENT", word))
            i = j
            continue

        # 双字符操作符
        two_char = expr[i:i + 2]
        if two_char in ("==", "!=", "&&", "||"):
            if two_char in ("==", "!="):
                tokens.append(_Token("COMP", two_char))
            else:
                tokens.append(_Token("OP", two_char))
            i += 2
            continue

        # 单字符操作符 / 括号
        if ch == "!":
            tokens.append(_Token("OP", "!"))
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token("LPAREN", "("))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token("RPAREN", ")"))
            i += 1
            continue

        # 未知字符，跳过
        logger.warning("ExpressionMatcher unknown char '{}' at pos {} in expr: {}", ch, i, expr)
        i += 1

    return tokens


def _unescape_string(s: str, quote: str) -> str:
    """处理字符串中的转义字符。"""
    result = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch == "n":
                result.append("\n")
            elif next_ch == "t":
                result.append("\t")
            elif next_ch == "r":
                result.append("\r")
            elif next_ch == "\\":
                result.append("\\")
            elif next_ch == quote:
                result.append(quote)
            else:
                result.append(next_ch)
            i += 2
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


# ── 求值辅助 ───────────────────────────────────────────────────────────────────


def _resolve_token(tok: _Token, payload: dict) -> Any:
    """
    将 token 解析为实际值。

    IDENT token 支持字段访问，如：
      - tool → payload["tool_name"]
      - tool_input.command → payload["tool_input"]["command"]
      - tool_output.output → payload["tool_response"]（因为我们用 tool_response 存输出）
    """
    if tok.kind in ("STRING", "NUMBER", "BOOL"):
        return tok.value

    if tok.kind == "IDENT":
        path = tok.value.split(".")
        # 映射顶层字段别名
        root = path[0]
        if root == "tool":
            value = payload.get("tool_name")
        elif root == "tool_input":
            value = payload.get("tool_input", {})
        elif root == "tool_output":
            # payload 里存的是 tool_response
            value = payload.get("tool_response", "")
        elif root == "tool_use_id":
            value = payload.get("tool_use_id", "")
        else:
            value = payload.get(root)

        # 继续 resolve 嵌套字段
        for key in path[1:]:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break
        return value

    return None


def _apply_operator(left: Any, op: str, right: Any) -> bool:
    """应用比较运算符。"""
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "matches":
        return _regex_matches(left, right)
    if op == "contains":
        left_str = str(left) if left is not None else ""
        right_str = str(right) if right is not None else ""
        return right_str in left_str
    if op == "startswith":
        left_str = str(left) if left is not None else ""
        right_str = str(right) if right is not None else ""
        return left_str.startswith(right_str)
    if op == "endswith":
        left_str = str(left) if left is not None else ""
        right_str = str(right) if right is not None else ""
        return left_str.endswith(right_str)
    return False


def _regex_matches(text: Any, pattern: Any) -> bool:
    """用正则表达式匹配。"""
    text_str = str(text) if text is not None else ""
    pattern_str = str(pattern) if pattern is not None else ""
    if not pattern_str:
        return False
    try:
        return bool(re.search(pattern_str, text_str))
    except Exception as e:
        logger.warning("Invalid regex in matcher matches: '{}' error={}", pattern_str, e)
        return False


# 用于 LiteralMatcher 判断是否是简单精确匹配
_MATCHER_LITERAL_PATTERN = re.compile(r"^[a-zA-Z0-9_|]+$")


# ── 工厂函数 ───────────────────────────────────────────────────────────────────


def build_matcher(matcher_expr: Optional[str]) -> HookMatcher:
    """
    根据 matcher 字符串构建对应的 Matcher 实例。

    优先尝试完整表达式解析；如果解析失败或明显是简单模式，降级为 LiteralMatcher。
    """
    expr = (matcher_expr or "").strip()
    if not expr or expr == "*":
        return AlwaysMatcher()

    # 如果完全匹配简单精确匹配字符集，直接用 LiteralMatcher
    if _MATCHER_LITERAL_PATTERN.match(expr):
        return LiteralMatcher(expr)

    # 否则尝试完整表达式解析
    try:
        # 先 tokenize 验证一下，如果成功则有复杂语法
        tokens = _tokenize(expr)
        # 如果 token 里只有 IDENT 或 STRING 而没有 COMP/OP/LPAREN，也不应该走 ExpressionMatcher
        has_complex = any(t.kind in ("COMP", "OP", "LPAREN") for t in tokens)
        if has_complex:
            return ExpressionMatcher(expr)
    except Exception:
        pass

    # 降级：如果是正则表达式（如 "^Write.*"），走 LiteralMatcher
    return LiteralMatcher(expr)
