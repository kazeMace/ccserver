from .base import BuiltinTools, ToolParam, ToolResult


class BTCompact(BuiltinTools):

    name = "Compact"

    description = (
        "Trigger manual compression of the current conversation history. "
        "Use this at a logical checkpoint when the conversation is very long "
        "and you want to summarize completed work before continuing. "
        "The agent replaces the full history with a concise summary, "
        "freeing up context window space. "
        "Compression also happens automatically when the token threshold is reached — "
        "only call this manually when you explicitly want to compact at a specific point."
    )

    params = {
        "focus": ToolParam(
            type="string",
            description=(
                "Optional hint to the summarizer about what context is most important to preserve. "
                "Example: 'the current file being edited and the bug we are fixing', "
                "'the API design decisions made in the last 10 messages'."
            ),
            required=False,
        ),
    }

    # BTCompact has no workdir dependency — it signals the agent loop,
    # it does not perform IO itself. The actual compaction is handled by Agent._loop().

    async def run(self, focus: str = "") -> ToolResult:
        # This return value is a signal to Agent._handle_tools().
        # Agent._handle_tools() checks result.content == COMPACT_SIGNAL to trigger compaction.
        return ToolResult.ok(COMPACT_SIGNAL)


# Sentinel value checked by Agent._handle_tools() to detect compact requests.
COMPACT_SIGNAL = "__COMPACT__"
