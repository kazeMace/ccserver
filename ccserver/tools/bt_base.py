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
from dataclasses import dataclass, field
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

    content:   String sent back to the LLM. Keep it focused — the LLM reads
               this to decide its next step. Truncate large outputs.
    is_error:  True signals to the LLM that the tool failed. The LLM will
               attempt to recover or report the problem rather than continuing
               as if the call succeeded.
    """

    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: str) -> "ToolResult":
        return cls(content=content, is_error=False)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)

    def to_api_dict(self, tool_use_id: str) -> dict:
        """Render as an Anthropic tool_result content block."""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


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


class BaseTool(ABC):
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
