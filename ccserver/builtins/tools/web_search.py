"""
bt_websearch — Web search tool backed by Anthropic's built-in web search capability.

Uses the Anthropic Beta API (web_search_20250305). This tool makes a secondary
LLM call that has web search enabled, then extracts and returns the search results as plain text.

Requires betas=["web-search-2025-03-05"] and a model that supports web search.
Currently only AnthropicAdapter is supported.

For non-Anthropic adapters, use BTDDGWebSearch (duckduckgo_search.py) instead.
"""

import asyncio
import logging

from ccserver.model import ModelAdapter, AnthropicAdapter

from .base import BuiltinTools, ToolParam, ToolResult

logger = logging.getLogger(__name__)

# Maximum characters to include in the returned result.
MAX_RESULT_CHARS = 20_000

# Default model for WebSearch.
DEFAULT_WEBSEARCH_MODEL = "claude-haiku-4-5-20251001"

# Concurrency limit: prevent flooding the Anthropic API.
_semaphore: asyncio.Semaphore = asyncio.Semaphore(3)


class BTWebSearch(BuiltinTools):
    """
    WebSearch backed by Anthropic's web_search_20250305 beta tool.

    Usage condition:
      - Adapter must be AnthropicAdapter.
      - Registered in PromptLib.build_tools() only when this condition holds.
      - For non-Anthropic adapters, BTDDGWebSearch is used instead.
    """

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

    def __init__(
        self,
        adapter: ModelAdapter,
        model: str = DEFAULT_WEBSEARCH_MODEL,
    ):
        """
        Args:
            adapter: AnthropicAdapter instance (required for beta web search API).
            model:   Model used for the search call. Defaults to haiku for speed/cost.
        """
        self.adapter = adapter
        self.model = model

    async def run(
        self,
        query: str,
        allowed_domains: list = None,
        blocked_domains: list = None,
    ) -> ToolResult:
        # Guard: ensure we have an Anthropic adapter.
        if not isinstance(self.adapter, AnthropicAdapter):
            return ToolResult.error("WebSearch requires Anthropic provider.")

        # Concurrency guard.
        async with _semaphore:
            try:
                result_text = await self._do_search(
                    query, allowed_domains, blocked_domains,
                )
            except Exception as exc:
                logger.exception("WebSearch unexpected error")
                return ToolResult.error(f"Web search failed: {exc}")

        if not result_text:
            return ToolResult.error("Web search returned no results.")

        # Truncate to avoid overwhelming the main LLM context.
        if len(result_text) > MAX_RESULT_CHARS:
            result_text = result_text[:MAX_RESULT_CHARS] + "\n\n[Results truncated]"

        return ToolResult.ok(result_text)

    async def _do_search(
        self,
        query: str,
        allowed_domains: list | None,
        blocked_domains: list | None,
    ) -> str:
        """
        Perform the actual search with a retry loop.
        Retries once on transient errors (429 rate limit, 503, etc.).
        """
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                response = await self.adapter._client.beta.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Search the web for the following query and summarize the results:\n\n{query}",
                        }
                    ],
                    tools=[self._build_tool_schema(allowed_domains, blocked_domains)],
                    betas=["web-search-2025-03-05"],
                )
                return _extract_text_from_response(response)

            except Exception as exc:
                last_exc = exc
                # Retry once on rate limit (429) or service unavailable (503).
                status = getattr(exc, "status_code", None)
                if status in (429, 503) and attempt == 0:
                    wait = 2 ** attempt   # 1s back-off
                    logger.warning(f"WebSearch retrying after {status}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                # All other errors: break immediately.
                break

        # All attempts exhausted.
        raise last_exc or RuntimeError("WebSearch: unknown error")

    def _build_tool_schema(
        self,
        allowed_domains: list | None,
        blocked_domains: list | None,
    ) -> dict:
        """Build the web_search_20250305 tool schema with optional domain filters."""
        tool: dict = {"type": "web_search_20250305", "name": "web_search"}
        if allowed_domains:
            tool["allowed_domains"] = allowed_domains
        if blocked_domains:
            tool["blocked_domains"] = blocked_domains
        return tool


def _extract_text_from_response(response) -> str:
    """
    Extract plain text from the Anthropic beta web-search response.

    Response blocks:
      - "text"               → model's own summary (primary source)
      - "web_search_tool_result" → structured result items (supplemental)

    Strategy: collect all text blocks first. If they are empty or too short,
    fall back to parsing the structured result items (title + url).
    This avoids duplicate entries when the model already summarized in text blocks.
    """
    text_parts: list[str] = []
    result_items: list[tuple[str, str]] = []

    for block in response.content:
        block_type = getattr(block, "type", None)

        if block_type == "text":
            text = getattr(block, "text", "").strip()
            if text:
                text_parts.append(text)

        elif block_type == "web_search_tool_result":
            content = getattr(block, "content", [])
            if isinstance(content, list):
                for item in content:
                    title = getattr(item, "title", "")
                    url = getattr(item, "url", "")
                    if title or url:
                        result_items.append((title, url))

    # Prefer the model's own summary if available.
    if text_parts:
        return "\n\n".join(text_parts)

    # Fallback: format result items as a simple list.
    if result_items:
        lines = [f"- {title} ({url})" for title, url in result_items]
        return "\n".join(lines)

    return ""
