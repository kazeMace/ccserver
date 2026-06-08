# ccserver/utils/sdk.py 优化分析文档

## 1. 原文件问题梳理

| 问题类别 | 具体表现 | 影响 |
|---------|---------|------|
| **命名语义不清** | `_block_get`、`gen_uuid` 等命名过于简略；下划线前缀表示私有，却被多模块跨文件导入 | 新人难以一眼看懂函数用途；私有约定与实际使用矛盾 |
| **类型注解缺失** | 函数参数和返回值均无类型提示 | 静态检查工具（mypy / IDE）无法提供补全和报错 |
| **断言与日志不足** | 没有 `assert` 校验输入合法性；没有 `logger` 记录关键路径 | 出问题后难以快速定位；非法输入可能在下游才暴露 |
| **文档不完善** | 注释简短，缺少 Args / Returns / Raises 说明 | 不符合团队 CLAUDE.md 对"清晰注明参数、返回值、异常"的要求 |
| **代码风格不统一** | `datetime` 在函数内部 import；模块级无 `__all__` | 不符合 Python 惯用法，导出边界不清晰 |

## 2. 优化方案

### 2.1 命名重构

| 旧函数名 | 新函数名 | 优化理由 |
|---------|---------|---------|
| `_block_get` | `get_block_attr` | 去掉误导性的私有前缀；动词+名词结构更清晰 |
| `_normalize_content` | `normalize_content_blocks` | 明确操作对象是"内容块列表"，而非字符串或其他 |
| `gen_uuid` | `generate_message_id` | `gen` 是缩写；新名称说明用途是生成消息/调用 ID |
| `estimate_tokens` | —（保留） | 名称已足够表达含义，仅增强注释 |

### 2.2 代码结构增强

- 添加 `from __future__ import annotations` 支持延迟类型解析
- 添加 `import logging` 并在关键路径打印 `debug` 日志
- 将 `datetime` 的导入上移至模块顶部
- 添加 `__all__` 列表，明确控制模块公开接口
- 为每个函数补充完整的 docstring（Args / Returns / Raises）
- 添加必要的 `assert`：参数类型检查、非空检查

### 2.3 联动更新

由于函数名变更，需要同步修改所有引用点：

1. `ccserver/utils/__init__.py` — 调整导出名称
2. `ccserver/agent.py` — 5 处引用（导入 + `_loop` + `_handle_tools` + `AgentContext`）
3. `ccserver/compactor.py` — 4 处引用（导入 + `micro`）
4. `tests/test_utils.py` — 更新导入和测试函数体中的函数调用

## 3. 实现结果

- `ccserver/utils/sdk.py` 已重写，全部 4 个函数均具备完整 docstring、类型注解、断言和日志。
- 所有引用点已完成同步替换。
- `tests/test_utils.py` + `tests/test_compactor.py` 共 37 个测试用例全部通过。

## 4. 风险与兼容性说明

- **对外接口变更**：`_block_get` → `get_block_attr`、`_normalize_content` → `normalize_content_blocks`、`gen_uuid` → `generate_message_id`。
- 由于原函数名带下划线（`_block_get`、`_normalize_content`）或仅在项目内部使用，外部调用方受影响面极小。
- `tests/test_compactor.py` 仅依赖 `estimate_tokens`，无需修改。

## 5. 后续建议

1. **token 估算精度**：当前 `estimate_tokens` 使用 `len(str(messages)) // 4`，虽满足"廉价快速"需求，但后续可考虑接入 `tiktoken` 做更精确估算。
2. **未知 block 类型兜底**：`normalize_content_blocks` 对未知类型仅保留 `{"type": "unknown"}`，若 SDK 新增 `image` 等类型，需及时扩展分支。
3. **命名抽离**：`generate_message_id` 通用性较强，若后续其他模块也需要生成带时间戳的 UUID，可考虑将其抽离到更基础的 `utils/ids.py` 中。
