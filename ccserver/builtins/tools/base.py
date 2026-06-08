"""
Tool base classes and parameter schema system.
Aligned with Claude Code's internal tool conventions.

Key conventions (from Claude Code):
- Tool names: PascalCase matching Claude Code (Read, Write, Edit, Bash, Glob, Grep)
- Descriptions: 3-4+ sentences, written from the LLM's perspective
- Params: named kwargs matching the LLM's input dict exactly
- Return: ToolResult(content, is_error) mirroring the Anthropic tool_result block
- Errors: is_error=True, never raise from run()
- run() receives only LLM-provided params; session dependencies injected via __init__
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


# ─── ToolResult ───────────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    """
    Return value of every tool.run() call.

    Maps directly to the Anthropic tool_result block:
        {
            "type": "tool_result",
            "tool_use_id": "...",
            "content": <self.content>,
            "is_error": <self.is_error>
        }

    content:   String or list sent back to the LLM.
               - str:  普通文本结果（绝大多数工具）
               - list: 多模态 content blocks（视觉工具，格式与 Anthropic API 对齐）
                       示例：[
                           {"type": "image", "source": {"type": "base64",
                            "media_type": "image/png", "data": "..."}},
                           {"type": "text", "text": "截图完成 1920x1080"}
                       ]
    is_error:  True signals to the LLM that the tool failed. The LLM will
               attempt to recover or report the problem rather than continuing
               as if the call succeeded.
    """

    # content 支持 str（普通工具）或 list（视觉/多模态工具），向后兼容
    content: str | list
    is_error: bool = False

    @classmethod
    def ok(cls, content: str) -> "ToolResult":
        return cls(content=content, is_error=False)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)

    @classmethod
    def multimodal(cls, blocks: list) -> "ToolResult":
        """
        构造多模态 ToolResult（视觉工具专用）。

        Args:
            blocks: Anthropic multimodal content blocks 列表，例如：
                    [{"type": "image", "source": {...}}, {"type": "text", "text": "..."}]

        Returns:
            ToolResult with list content.
        """
        assert isinstance(blocks, list) and len(blocks) > 0, "blocks 不能为空"
        return cls(content=blocks, is_error=False)

    def to_api_dict(self, tool_use_id: str) -> dict:
        """
        Render as an Anthropic tool_result content block.
        支持 content 为 str 或 list（多模态）。

        注意：image_thumbnail 是内部约定 block，不发送给 Anthropic API。
              过滤掉后，只保留 Anthropic 认识的 image / text block。
        """
        if isinstance(self.content, list):
            # 过滤掉内部使用的 image_thumbnail block（Anthropic API 不认识）
            api_content = [b for b in self.content if b.get("type") != "image_thumbnail"]
        else:
            api_content = self.content
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": api_content,
            "is_error": self.is_error,
        }

    # ── 多模态辅助属性 ────────────────────────────────────────────────────────

    @property
    def has_image(self) -> bool:
        """是否包含图像 block（视觉工具截图结果）。"""
        if isinstance(self.content, list):
            return any(b.get("type") == "image" for b in self.content)
        return False

    @property
    def content_text(self) -> str:
        """
        提取纯文本摘要。

        - str content：直接返回
        - list content：拼接所有 text block 的文字，无文字时返回占位符
        用于 emit_tool_result、日志、EventBus preview 等只需文本的场景。
        """
        if isinstance(self.content, str):
            return self.content
        parts = [b.get("text", "") for b in self.content if b.get("type") == "text"]
        return " | ".join(p for p in parts if p) or "[multimodal content]"

    def get_image_base64(self) -> str | None:
        """
        提取第一张图像的 base64 数据（完整分辨率）。

        Returns:
            base64 字符串，或 None（无图像时）。
        """
        if isinstance(self.content, list):
            for b in self.content:
                if b.get("type") == "image":
                    return b.get("source", {}).get("data")
        return None

    def get_thumbnail_base64(self) -> str | None:
        """
        提取缩略图 base64（视觉工具在 content 末尾附加的 thumbnail block）。

        约定：视觉工具在 content list 中通过 {"type": "image_thumbnail", ...} 附加缩略图，
        缩略图尺寸 ≤ 400px，用于 TUI/飞书等低带宽渠道预览。

        Returns:
            缩略图 base64，或 None（无缩略图时降级到完整图像）。
        """
        if isinstance(self.content, list):
            for b in self.content:
                if b.get("type") == "image_thumbnail":
                    return b.get("source", {}).get("data")
        # 无缩略图时返回完整图像（调用方自行决定是否使用）
        return None


# ─── ToolParam ────────────────────────────────────────────────────────────────

ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]


@dataclass
class ToolParam:
    """
    Describes a single input parameter.

    type:        JSON Schema primitive. Use "string" for paths, commands,
                 patterns. Use "integer" for counts, offsets. "boolean" for flags.
    description: Shown to the LLM. Be explicit about format, units, and
                 constraints. The LLM uses this to know what value to pass.
                 Examples:
                   Good: "Timeout in milliseconds. Default 120000 (2 minutes)."
                   Bad:  "Timeout."
    required:    False = LLM may omit this param. Provide a sensible default
                 in run() signature.
    enum:        Restricts the LLM to an exact set of values. Prefer enum over
                 a description saying "must be one of...".
    items:       For type="array", describes element schema.
                 e.g. {"type": "string"} or {"type": "object", "properties": {}}
    """

    type: ParamType
    description: str
    required: bool = True
    enum: list[Any] | None = None
    items: dict | None = None

    def to_property(self) -> dict:
        prop: dict = {"type": self.type, "description": self.description}
        if self.enum is not None:
            prop["enum"] = self.enum
        if self.items is not None and self.type == "array":
            prop["items"] = self.items
        return prop


# ─── BaseTool ─────────────────────────────────────────────────────────────────


class BuiltinTools(ABC):
    """
    Abstract base for all tools.

    ── Required class attributes ──────────────────────────────────────────────

    name (str)
        Matches Claude Code's capitalized convention: "Read", "Write", "Bash".
        This is the exact string sent to the Anthropic API and used by the LLM.
        Pattern: PascalCase for built-ins, snake_case for custom domain tools.
        Must be unique across all tools registered to an agent.

    description (str)
        3-4 sentences minimum. Written from the LLM's perspective — explain:
          1. What the tool does (one sentence)
          2. When to use it (vs. similar tools)
          3. Important constraints or warnings
          4. What the output looks like
        Claude Code example for Read:
          "Reads a file from the local filesystem. You can access any file
           directly by using this tool. Assume this tool is able to read all
           files on the machine. If the User provides a path to a file assume
           that path is valid. It is okay to read a file that does not exist;
           an error will be returned."

    params (dict[str, ToolParam])
        Ordered dict: param name → ToolParam. This is the single source of
        truth for the input_schema sent to the API. Param names must match
        the **kwargs keys in run() exactly.

    ── Required instance method ────────────────────────────────────────────────

    async def run(self, **kwargs) -> ToolResult
        Execute the tool. Receives exactly the named args the LLM provides.
        - Required params are guaranteed present (validated before run).
        - Optional params may be absent; use default values in the signature.
        - Never raise — return ToolResult.error("...") on failure.
        - Truncate large outputs; the LLM doesn't need raw megabytes.

    ── Optional ────────────────────────────────────────────────────────────────

    async def validate(self, **kwargs) -> ToolResult | None
        Pre-flight checks beyond required-param presence.
        Return ToolResult.error("...") to block execution, or None to proceed.
        Use for path-escape checks, value range guards, permission checks.

    ── Calling convention ──────────────────────────────────────────────────────

    Always call tools via __call__, not run() directly:

        result: ToolResult = await tool(**block.input)

    This goes through validate() → run() and guarantees ToolResult is always
    returned even if run() raises unexpectedly.

    ── Schema registration ─────────────────────────────────────────────────────

    Pass to_schema() output to the Anthropic API's tools list:

        tools = [t.to_schema() for t in tool_instances]
        client.messages.create(..., tools=tools)

    ── Concrete example ────────────────────────────────────────────────────────

    class BTEdit(BaseTool):
        name = "Edit"
        description = (
            "This is a tool for editing files. For moving or renaming files, "
            "use the Bash tool with the 'mv' command instead. "
            "To make a single edit, provide old_string and new_string. "
            "old_string must be unique within the file — if it appears multiple "
            "times, use replace_all=true or provide more surrounding context. "
            "The edit will fail if old_string is not found in the file."
        )
        params = {
            "file_path": ToolParam(
                type="string",
                description="The absolute path to the file to modify.",
            ),
            "old_string": ToolParam(
                type="string",
                description="The text to replace. Must match exactly, including whitespace.",
            ),
            "new_string": ToolParam(
                type="string",
                description="The replacement text.",
            ),
            "replace_all": ToolParam(
                type="boolean",
                description="Replace all occurrences of old_string. Default false.",
                required=False,
            ),
        }

        def __init__(self, workdir: Path):
            self.workdir = workdir

        async def run(
            self,
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> ToolResult:
            ...
    """

    name: str = ""
    description: str = ""
    params: dict[str, ToolParam] = {}

    # ── 元数据（可选，供权限过滤和 UI 展示使用）────────────────────────────────
    # risk:  工具的风险等级。
    #          "low"    — 只读、无副作用（Read、Glob、Grep、WebFetch）
    #          "medium" — 有副作用但可恢复（Write、Edit、Bash）
    #          "high"   — 不可逆或影响范围广（删除文件、网络请求、系统调用）
    #        settings.max_tool_risk 可用于过滤 risk 超出阈值的工具。
    # tags:  分类标签列表，供 UI 分组和按需过滤使用，例如 ["fs", "network", "input"]。
    risk: str = "medium"
    tags: list[str] = []

    # ── Schema ────────────────────────────────────────────────────────────────

    def to_schema(self) -> dict:
        """
        Generate the Anthropic-compatible tool definition.
        Pass directly into messages.create(tools=[...]).

        Output shape:
            {
                "name": "Bash",
                "description": "...",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "..."},
                        ...
                    },
                    "required": ["command"]
                }
            }
        """
        if not self.name:
            raise NotImplementedError(f"{type(self).__name__} must define `name`")
        if not self.description:
            raise NotImplementedError(f"{type(self).__name__} must define `description`")

        properties = {k: v.to_property() for k, v in self.params.items()}
        required = [k for k, v in self.params.items() if v.required]

        schema: dict = {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["input_schema"]["required"] = required
        return schema

    def to_disabled_schema(self) -> dict:
        """
        Generate a stub schema for a disabled tool.

        The tool name is preserved in the LLM's context so it knows the tool exists
        but is currently unavailable — this prevents the LLM from attempting to call
        a tool name it knows about from training but that isn't in the active tool list.

        The stub has no required parameters, so the LLM cannot accidentally invoke it.
        If the LLM calls it anyway, Agent._handle_tools() will return an error result.
        """
        return {
            "name": self.name,
            "description": f"（不可用）{self.name} 在当前上下文中已被禁用，请勿调用。",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        }

    # ── Execution ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """
        Execute the tool with LLM-provided named arguments.
        Return ToolResult.ok(output) or ToolResult.error(message).
        Never raise.
        """
        ...

    async def validate(self, **kwargs) -> ToolResult | None:
        """
        Optional pre-run validation. Return ToolResult.error() to block,
        or None to proceed. Default checks required params are present.
        """
        for name, param in self.params.items():
            if param.required and name not in kwargs:
                return ToolResult.error(f"Missing required parameter: '{name}'")
        return None

    async def __call__(self, **kwargs) -> ToolResult:
        """
        Validate → run. Always returns ToolResult.
        This is the only call site in Agent._handle_tools():

            result = await tool(**block.input)
            # result.content  → sent to LLM
            # result.is_error → flagged in tool_result block
        """
        error = await self.validate(**kwargs)
        if error is not None:
            return error
        try:
            return await self.run(**kwargs)
        except Exception as e:
            return ToolResult.error(f"Unexpected error in {self.name}: {e}")

    def __repr__(self) -> str:
        param_names = list(self.params)
        return f"<Tool {self.name!r} params={param_names}>"
