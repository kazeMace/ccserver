# tests/test_layer_isolation.py
"""tests/test_layer_isolation.py — OCP 隔离不变量：L1 不依赖具体 adapter/SDK。

验证 L1（LLMCaller）只依赖抽象接口（ModelAdapter ABC + 中性类型 + errors），
不得静态 import 任何具体 adapter 模块或第三方 LLM SDK。

This is a pure structural invariant test using AST parsing — no production
code is executed, just the import graph is checked.

设计原则：开闭原则（OCP）——L1 对扩展开放（新 adapter 无需改 L1），
对修改关闭（L1 不感知具体实现）。
Design principle: OCP — L1 is open for extension (new adapters need no L1 changes)
and closed for modification (L1 must not know concrete implementations).
"""

import ast
import pathlib


def _imported_modules(path: str) -> list:
    """解析一个 .py 文件，返回它 import 的所有模块名（ImportFrom.module + Import.names）。
    Parse a .py file and return all module names imported by it
    (both `from X import ...` and `import X` forms).

    Args:
        path: .py 文件路径（相对或绝对）。The path to the .py file (relative or absolute).

    Returns:
        模块名字符串列表。List of module name strings.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # `from ccserver.model_engine.errors import ...` → "ccserver.model_engine.errors"
            names.append(node.module)
        elif isinstance(node, ast.Import):
            # `import asyncio` → "asyncio"
            names.extend(alias.name for alias in node.names)
    return names


def test_l1_llmcaller_not_import_concrete_adapters_or_sdks():
    """L1 LLMCaller 只依赖抽象（adapter ABC + message 中性类型 + errors），
    不得 import 任何具体 adapter 或第三方 SDK。

    L1 LLMCaller must only depend on abstractions (adapter ABC, neutral message types, errors).
    It must NOT import any concrete adapter module or third-party LLM SDK.

    违反此规则意味着 L1 与某个具体 provider 紧耦合，违反 OCP。
    Violating this rule would tightly couple L1 to a specific provider, violating OCP.
    """
    mods = _imported_modules("ccserver/model_engine/client.py")

    # 禁止出现的模块名（精确子串匹配）
    # Forbidden module name fragments (substring match)
    # 注意：具体 adapter 现在在 model_engine/adapters/ 子包下（重构后），
    # 用 "adapters" 子串可覆盖新路径；保留旧扁平名和 SDK 名兜底。
    # Note: concrete adapters now live under model_engine/adapters/ subpackage (post-refactor).
    # The "adapters" fragment covers new paths; old flat names + SDK names are kept as fallback.
    forbidden = (
        "adapters",            # 具体 adapter 子包（新结构）/ concrete adapter subpackage (new structure)
        "anthropic_adapter",   # 旧扁平名兜底 / old flat name fallback
        "openai_adapter",      # 旧扁平名兜底 / old flat name fallback
        "anthropic",           # anthropic SDK（第三方）/ anthropic SDK (third-party)
        "openai",              # openai SDK（第三方）/ openai SDK (third-party)
        "translator",          # 已删除的旧翻译层 / deleted old translator layer
    )

    # 找出所有命中禁止名单的模块
    # Find all imported modules that match any forbidden fragment
    hits = [m for m in mods if any(f in m for f in forbidden)]

    assert not hits, (
        f"L1 不得依赖具体 adapter/SDK，发现违规 import：{hits}\n"
        f"L1 must not depend on concrete adapters/SDKs. Forbidden imports found: {hits}"
    )
