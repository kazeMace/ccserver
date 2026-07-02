"""Game matrix fixture tests.

这些 fixture 只验证 Party Game DSL Core 的表达边界，不实现具体游戏规则。
"""

from pathlib import Path

import pytest
import yaml

from drama_engine.core.dsl.compiler import YamlCompiler

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "game_matrix"


@pytest.mark.parametrize(
    "path",
    sorted(path for path in FIXTURE_DIR.glob("*.yaml") if not path.name.startswith("._")),
)
def test_game_matrix_fixture_validates_and_compiles(path: Path):
    """不同游戏类型的最小 DSL fixture 应可 validate + compile。"""
    compiler = YamlCompiler()
    doc = yaml.safe_load(path.read_text())

    errors = compiler.validate(doc)
    assert errors == [], f"{path.name} validation errors: {errors}"

    script = compiler.compile_doc(doc)
    assert script.runtime.type == "game_session"
    assert script.flow.scenes
