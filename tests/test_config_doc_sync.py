"""
test_config_doc_sync — 配置参考文档与 schema 同步检查（防漂移）。

对应 plan Task E2 / spec §11。

策略（docs/ 在本仓库被 gitignore，故文档为本地生成物）：
  - 文档不存在 → 自动生成并通过（首次/全新检出）。
  - 文档已存在 → 与 render_reference() 比对，不一致即失败，提示重新生成。
另含完整性检查：schema 每个字段都必须出现在文档中。
"""

from dataclasses import fields

from ccserver.configuration.doc_gen import render_reference, write_reference, DOC_PATH, _SECTIONS


def test_doc_covers_all_schema_fields():
    """schema 每段每字段都应出现在生成文档里（防止新增字段漏登记）。"""
    text = render_reference()
    for section_key, section_cls, _title in _SECTIONS:
        for f in fields(section_cls):
            if f.name.startswith("_"):
                continue
            token = f"`{section_key}.{f.name}`"
            assert token in text, f"配置字段 {token} 未出现在生成文档中"


def test_doc_on_disk_in_sync():
    """磁盘文档若存在则必须与 schema 生成结果一致；不存在则生成。"""
    generated = render_reference()
    if not DOC_PATH.exists():
        write_reference()
        return
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config-reference.md 与配置 schema 不一致，"
        "请运行 `python -m ccserver.configuration.doc_gen` 重新生成。"
    )
