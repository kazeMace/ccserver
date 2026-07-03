"""Validation tests for Castloop branching-story import samples.

These tests keep the imported sample DSL files aligned with the compiler.
这些测试用于保证 Castloop 分支剧情样例始终能被当前 DSL 编译器接受。
"""

from pathlib import Path
import logging

import yaml

from drama_engine.core.dsl.compiler import YamlCompiler


LOGGER = logging.getLogger(__name__)
SAMPLE_DIR = (
    Path(__file__).resolve().parents[2]
    / "drama_engine"
    / "scripts"
    / "fixed_flow"
    / "adventure"
    / "castloop_samples"
)


def _sample_paths() -> list[Path]:
    """Return real generated sample files, excluding macOS sidecar files.

    返回真实样例文件列表，过滤 macOS 可能生成的旁路文件。
    """
    paths = sorted(
        path
        for path in SAMPLE_DIR.glob("castloop_*.yaml")
        if not path.name.startswith("._")
    )
    assert paths, "Castloop sample YAML files should exist."
    return paths


def test_castloop_import_samples_compile() -> None:
    """Generated Castloop samples should validate and compile.

    生成的 Castloop 样例必须通过 DSL 校验并成功编译。
    """
    compiler = YamlCompiler()

    for path in _sample_paths():
        LOGGER.info("Validating Castloop import sample: %s", path)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))

        errors = compiler.validate(doc)
        assert errors == []

        script = compiler.compile_doc(doc)
        assert script.flow.initial == "node_001"
        assert "end" in script.flow.states
        assert len(script.flow.scenes) > 0
