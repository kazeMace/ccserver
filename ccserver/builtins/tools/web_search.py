"""
bt_websearch — Web search tool backed by Anthropic's built-in web search capability.

Uses the Anthropic Beta API (web_search_20250305). This tool makes a secondary
LLM call that has web search enabled, then extracts and returns the search results as plain text.

Requires betas=["web-search-2025-03-05"] and a model that supports web search.
Currently only AnthropicAdapter is supported.
"""

import json

from ccserver.model import ModelAdapter, AnthropicAdapter

from .base import BuiltinTools, ToolParam, ToolResult

# Maximum characters to include in the returned result.
MAX_RESULT_CHARS = 20_000

# default model for WebSearch
DEFAULT_WEBSEARCH_MODEL = "claude-haiku-4-5-20251001"


class BTWebSearch(BuiltinTools):

    name = "WebSearch"
    description = (
        "Search the web and return a summary of the results. "
        "Use this when you need up-to-date information, recent events, package versions, "
        "or data that may not be in your training knowledge. "
        "Do NOT use for information already in your training (standard library docs, well-known APIs). "
        "Returns a model-generated summary plus source titles and URLs. "
        "Results are capped at 20,000 characters."
    )

    params = {
        "query": ToolParam(
            type="string",
            description=(
                "The search query. Be specific for better results. "
                "Examples: 'Python asyncio timeout best practices 2025', "
                "'FastAPI WebSocket authentication example'."
            ),
        ),
        "allowed_domains": ToolParam(
            type="array",
            description=(
                "Restrict results to only these domains. "
                "Example: ['docs.python.org', 'github.com'] to limit to official docs and GitHub."
            ),
            required=False,
            items={"type": "string"},
        ),
        "blocked_domains": ToolParam(
            type="array",
            description=(
                "Exclude results from these domains. "
                "Example: ['reddit.com', 'stackoverflow.com'] to skip community discussion sites."
            ),
            required=False,
            items={"type": "string"},
        ),
    }

    def __init__(self, adapter: ModelAdapter, model: str = DEFAULT_WEBSEARCH_MODEL):
        """
        Args:
            adapter: AnthropicAdapter 实例（当前仅支持 Anthropic Beta API）。
            model:   搜索时使用的模型名称，用户可在 PromptLib 中自定义。
        """
        self.adapter = adapter
        self.model = model

    async def run(
        self,
        query: str,
        allowed_domains: list = None,
        blocked_domains: list = None,
    ) -> ToolResult:
        if not isinstance(self.adapter, AnthropicAdapter):
            return ToolResult.error("WebSearch requires Anthropic provider.")

        try:
            # Build the web_search tool schema for the beta API.
            web_search_tool: dict = {"type": "web_search_20250305", "name": "web_search"}
            if allowed_domains:
                web_search_tool["allowed_domains"] = allowed_domains
            if blocked_domains:
                web_search_tool["blocked_domains"] = blocked_domains

            # Call with web search enabled.
            response = await self.adapter._client.beta.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": f"Search the web for the following query and summarize the results:\n\n{query}",
                    }
                ],
                tools=[web_search_tool],
                betas=["web-search-2025-03-05"],
            )

            result_text = _extract_text_from_response(response)
            if not result_text:
                return ToolResult.error("Web search returned no results.")

            # Truncate to avoid overwhelming the main LLM context.
            if len(result_text) > MAX_RESULT_CHARS:
                result_text = result_text[:MAX_RESULT_CHARS] + "\n\n[Results truncated]"

            return ToolResult.ok(result_text)

        except Exception as e:
            return ToolResult.error(f"Web search failed: {e}")


def _extract_text_from_response(response) -> str:
    """
    Extract plain text from the Anthropic beta response.
    The response may contain text blocks, tool_use blocks, and web_search_tool_result blocks.
    We collect all text blocks and search result snippets.
    """
    parts = []

    for block in response.content:
        block_type = getattr(block, "type", None)

        if block_type == "text":
            text = getattr(block, "text", "").strip()
            if text:
                parts.append(text)

        elif block_type == "web_search_tool_result":
            # Each result item has title, url, and encrypted_content (not directly readable).
            # The model's text blocks above already summarize these for us.
            content = getattr(block, "content", [])
            if isinstance(content, list):
                for item in content:
                    title = getattr(item, "title", "")
                    url = getattr(item, "url", "")
                    if title or url:
                        parts.append(f"- {title} ({url})")

    return "\n\n".join(parts)
