"""
bt_webfetch — Fetch web page content and process it with a prompt.

Flow:
  1. Fetch the URL with httpx (60s timeout, 10MB limit).
  2. Convert HTML to Markdown with html2text.
  3. Truncate to MAX_MARKDOWN_CHARS to avoid overloading the LLM.
  4. Call LLM with the user's prompt to extract relevant information.
  5. Return the model's answer.

Dependencies: httpx, html2text (install with: uv pip install httpx html2text)
"""

import httpx

from ccserver.model import ModelAdapter

from .base import BuiltinTools, ToolParam, ToolResult

# Limits to avoid overloading context or memory.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_MARKDOWN_CHARS = 50_000              # ~12k tokens
FETCH_TIMEOUT_SECONDS = 60

# default model for WebFetch
DEFAULT_WEBFETCH_MODEL = "claude-haiku-4-5-20251001"


class BTWebFetch(BuiltinTools):

    name = "WebFetch"
    description = (
        "Fetch a web page and extract specific information from it using a prompt. "
        "Use this when you have a specific URL and need targeted information from it. "
        "Use WebSearch instead when you don't have a URL and need to find information first. "
        "The tool downloads the page, converts HTML to Markdown, then uses a model "
        "to answer your prompt based on the page content. "
        "Returns the model's answer — not raw HTML. "
        "Fails on authenticated pages, login walls, or URLs that block automated access."
    )

    params = {
        "url": ToolParam(
            type="string",
            description=(
                "The URL to fetch. Must start with http:// or https://. "
                "Example: 'https://docs.python.org/3/library/asyncio.html'."
            ),
        ),
        "prompt": ToolParam(
            type="string",
            description=(
                "What to extract or answer from the fetched page content. "
                "Be specific for better results. "
                "Examples: 'Summarize the main points', "
                "'List all function signatures with their parameters', "
                "'Extract the installation instructions'."
            ),
        ),
    }

    def __init__(self, adapter: ModelAdapter, model: str = DEFAULT_WEBFETCH_MODEL):
        """
        Args:
            adapter: 任意 ModelAdapter 实例（Anthropic、OpenAI、Volcano 等）。
            model:   处理网页内容时使用的模型名称，用户可在 PromptLib 中自定义。
        """
        self.adapter = adapter
        self.model = model

    async def run(self, url: str, prompt: str) -> ToolResult:
        try:
            # Basic URL validation — must start with http:// or https://.
            if not (url.startswith("http://") or url.startswith("https://")):
                return ToolResult.error("Invalid URL: must start with http:// or https://")

            # Step 1: Fetch the page.
            markdown_content = await _fetch_as_markdown(url)

            # Step 2: Truncate to avoid token overload.
            if len(markdown_content) > MAX_MARKDOWN_CHARS:
                markdown_content = markdown_content[:MAX_MARKDOWN_CHARS] + "\n\n[Content truncated]"

            # Step 3: Use LLM to process the content with the user's prompt.
            result = await _apply_prompt(self.adapter, self.model, url, markdown_content, prompt)
            return ToolResult.ok(result)

        except Exception as e:
            return ToolResult.error(f"WebFetch failed: {e}")


async def _fetch_as_markdown(url: str) -> str:
    """
    Download the URL and convert HTML to Markdown.
    Raises on HTTP errors or timeouts.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CCServer/1.0)",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
    }

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT_SECONDS,
    ) as http_client:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()

        # Check content size before decoding.
        raw_bytes = response.content
        if len(raw_bytes) > MAX_RESPONSE_BYTES:
            raw_bytes = raw_bytes[:MAX_RESPONSE_BYTES]

        content_type = response.headers.get("content-type", "")
        raw_text = raw_bytes.decode("utf-8", errors="replace")

        if "text/html" in content_type:
            return _html_to_markdown(raw_text)
        else:
            # For plain text, JSON, Markdown, etc. — return as-is.
            return raw_text


def _html_to_markdown(html: str) -> str:
    """Convert HTML string to Markdown using html2text."""
    try:
        import html2text
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.body_width = 0       # No line wrapping.
        return converter.handle(html)
    except ImportError:
        # Fallback: strip all HTML tags with a simple approach.
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


async def _apply_prompt(adapter: ModelAdapter, model: str, url: str, content: str, prompt: str) -> str:
    """
    Call LLM to answer the prompt based on the fetched page content.
    Returns the model's text response.
    """
    user_message = (
        f"The following is the content of the web page at: {url}\n\n"
        f"---\n{content}\n---\n\n"
        f"Based on the content above, please: {prompt}"
    )

    response = await adapter.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract text from the response.
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text

    return "No response from model."
