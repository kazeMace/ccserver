"""
bt_webfetch — Fetch web page content and process it with a prompt.

Flow:
  1. Reuse a shared httpx.AsyncClient with connection pooling (high performance).
  2. Fetch the URL with httpx (60s timeout, 10MB limit).
  3. Convert HTML to Markdown with a cached html2text converter.
  4. Truncate to MAX_MARKDOWN_CHARS to avoid overloading the LLM.
  5. Call LLM with the user's prompt to extract relevant information.
  6. Return the model's answer.

Dependencies: httpx, html2text (install with: uv pip install httpx html2text)
"""

import asyncio
import re
from typing import Optional

import httpx
import html2text

from ccserver.model import ModelAdapter

from .base import BuiltinTools, ToolParam, ToolResult

# ─── Limits ───────────────────────────────────────────────────────────────────

MAX_RESPONSE_BYTES = 10 * 1024 * 1024   # 10 MB — body size cap
MAX_MARKDOWN_CHARS = 50_000              # ~12k tokens — LLM context cap
FETCH_TIMEOUT_SECONDS = 60               # overall request timeout

# ─── HTTP Client (shared connection pool) ─────────────────────────────────────

# Global shared client. Closed lazily when is_closed is True.
_http_client: Optional[httpx.AsyncClient] = None

# Concurrency limit: max simultaneous fetches across all BTWebFetch instances.
# Prevents overwhelming target servers and exhausting file descriptors.
_semaphore: asyncio.Semaphore = asyncio.Semaphore(10)

# Tracks URLs currently being fetched: prevents concurrent duplicate fetches
# of the same URL within the same session.
_fetching_urls: set[str] = set()


def _get_http_client() -> httpx.AsyncClient:
    """
    Return the module-level shared AsyncClient.
    Lazily creates it on first call; recreates if the previous one was closed.
    Using a shared client enables HTTP/2 connection multiplexing.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_SECONDS,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
            ),
        )
    return _http_client


# ─── HTML → Markdown converter (cached, reused) ───────────────────────────────

_html2text_converter: Optional[html2text.HTML2Text] = None


def _get_html_converter() -> html2text.HTML2Text:
    """
    Return the module-level cached HTML2Text converter.
    Avoids re-creating and re-configuring the converter on every call.
    """
    global _html2text_converter
    if _html2text_converter is None:
        _html2text_converter = html2text.HTML2Text()
        _html2text_converter.ignore_links = False   # preserve links for LLM context
        _html2text_converter.ignore_images = True  # images are not useful in text
        _html2text_converter.body_width = 0        # no line wrapping
    return _html2text_converter


# ─── Retry helpers ────────────────────────────────────────────────────────────

async def _fetch_url_with_retry(url: str) -> httpx.Response:
    """
    Fetch a URL with simple exponential-backoff retry.
    Retries on connection errors and 5xx responses (up to 3 attempts).
    """
    client = _get_http_client()
    last_err: Exception | None = None

    for attempt in range(3):
        try:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CCServer/1.0)",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            })
            # Retry on server-side errors; client errors (4xx) are permanent.
            if 500 <= response.status_code < 600:
                raise httpx.HTTPStatusError(
                    f"Server error {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            return response
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                httpx.PoolTimeout, httpx.RemoteProtocolError,
                httpx.HTTPStatusError) as exc:
            last_err = exc
            if attempt < 2:
                wait = 2 ** attempt   # 1s, 2s
                await asyncio.sleep(wait)
            # else: last attempt failed, let it propagate

    # Should not reach here, but defensively raise the last error.
    raise last_err or RuntimeError(f"Failed to fetch {url} after 3 attempts")


# ─── Constants ────────────────────────────────────────────────────────────────

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
        # ── Step 0: URL validation ──────────────────────────────────────────────
        if not (url.startswith("http://") or url.startswith("https://")):
            return ToolResult.error("Invalid URL: must start with http:// or https://")

        # ── Step 1: Deduplication — prevent concurrent duplicate fetches ───────
        if url in _fetching_urls:
            return ToolResult.error(
                f"URL {url} is already being fetched. "
                "Please wait for the in-progress request to complete before retrying."
            )
        _fetching_urls.add(url)

        try:
            # ── Step 2: Concurrency guard ─────────────────────────────────────
            async with _semaphore:
                try:
                    # Step 3: Fetch the page (with retry).
                    response = await _fetch_url_with_retry(url)

                    # Step 4: Check body size before decoding.
                    raw_bytes = response.content
                    if len(raw_bytes) > MAX_RESPONSE_BYTES:
                        raw_bytes = raw_bytes[:MAX_RESPONSE_BYTES]

                    # Step 5: Decode (replace unknown bytes, don't crash).
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type:
                        raw_text = raw_bytes.decode("utf-8", errors="replace")
                        markdown_content = _html_to_markdown(raw_text)
                    else:
                        # Plain text, JSON, Markdown — return as-is.
                        markdown_content = raw_bytes.decode("utf-8", errors="replace")

                except httpx.InvalidURL:
                    return ToolResult.error(f"Invalid URL: {url}")
                except httpx.HTTPStatusError as exc:
                    return ToolResult.error(f"HTTP {exc.response.status_code} for {url}")
                except (httpx.ConnectError, httpx.ReadTimeout,
                        httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                    return ToolResult.error(f"Connection failed for {url}: {exc}")
                except Exception as exc:
                    return ToolResult.error(f"WebFetch failed: {exc}")

            # ── Step 6: Truncate to avoid token overload ─────────────────────────
            if len(markdown_content) > MAX_MARKDOWN_CHARS:
                markdown_content = markdown_content[:MAX_MARKDOWN_CHARS] + "\n\n[Content truncated]"

            # ── Step 7: Use LLM to answer the prompt ─────────────────────────────
            result = await _apply_prompt(
                self.adapter, self.model, url, markdown_content, prompt,
            )
            return ToolResult.ok(result)

        finally:
            # Always remove from fetching set, even on error.
            _fetching_urls.discard(url)


def _html_to_markdown(html: str) -> str:
    """
    Convert HTML string to Markdown using the cached html2text converter.
    If html2text is unavailable, falls back to a simple regex-based stripper.
    """
    try:
        return _get_html_converter().handle(html)
    except Exception:
        # Fallback: strip all HTML tags with regex.
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)        # strip HTML entities
        text = re.sub(r"\s+", " ", text)
        return text.strip()


async def _apply_prompt(
    adapter: ModelAdapter,
    model: str,
    url: str,
    content: str,
    prompt: str,
) -> str:
    """
    Call LLM to answer the prompt based on the fetched page content.
    max_tokens is dynamically sized based on content length.
    Returns the model's text response or an error string.
    """
    # Dynamic token budget: simple heuristic. Complex pages get more tokens.
    content_len = len(content)
    max_tokens = min(max(512, content_len // 4), 8192)

    user_message = (
        f"The following is the content of the web page at: {url}\n\n"
        f"---\n{content}\n---\n\n"
        f"Based on the content above, please: {prompt}"
    )

    try:
        response = await adapter.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return f"LLM call failed: {exc}"

    # Extract text from the response.
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text

    return "No response from model."
