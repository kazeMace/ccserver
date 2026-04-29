"""
bt_ddg_websearch — Provider-agnostic web search using DuckDuckGo.

Searches via DuckDuckGo HTML interface (no API key required, no Anthropic dependency).
Returns structured search results: title, URL, and snippet for each result.

Key features:
  - Server-side domain filtering (more reliable than site: query syntax).
  - 5-minute TTL cache to avoid repeated expensive requests.
  - BeautifulSoup parsing (robust against minor HTML changes).

For cc_reverse:v2.1.81 + AnthropicAdapter, BTWebSearch takes precedence.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ccserver.model import ModelAdapter

from ..base import BuiltinTools, ToolParam, ToolResult

logger = logging.getLogger(__name__)

# ─── Limits ───────────────────────────────────────────────────────────────────

MAX_RESULTS = 10          # Max number of search results to return
MAX_SNIPPET_CHARS = 500   # Per-result snippet length cap
MAX_TOTAL_CHARS = 20_000  # Total output length cap (matches BTWebSearch)
CACHE_TTL_SECONDS = 300   # 5-minute TTL for search results

# Concurrency limit: prevent flooding DuckDuckGo.
_SEMAPHORE: asyncio.Semaphore = asyncio.Semaphore(5)

# Shared HTTP client (connection pool).
_http_client: httpx.AsyncClient | None = None

# Search result cache: key=(query_hash,) → value=(results_list, timestamp)
_cache: dict[int, tuple[list[dict[str, str]], float]] = {}

# Default model — not used for search itself, but kept for API symmetry.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _get_client() -> httpx.AsyncClient:
    """Lazily create and return the shared httpx client with connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    return _http_client


async def close_http_client() -> None:
    """关闭模块级共享的 httpx.AsyncClient，释放 TCP 连接池。"""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def _make_cache_key(query: str, allowed: list | None, blocked: list | None) -> int:
    """Stable cache key for a search query (hash of args)."""
    import hashlib
    key_str = f"{query}|{sorted(allowed or [])}|{sorted(blocked or [])}"
    return hashlib.sha256(key_str.encode()).digest().__hash__()


def _get_cached(query: str, allowed: list | None, blocked: list | None) -> list[dict[str, str]] | None:
    """
    Return cached results if still valid (within TTL), otherwise None.
    Cleans up expired entries while checking.
    """
    key = _make_cache_key(query, allowed, blocked)
    now = time.monotonic()

    if key in _cache:
        results, ts = _cache[key]
        if now - ts < CACHE_TTL_SECONDS:
            return results
        else:
            del _cache[key]

    # Prune other expired entries (lazy cleanup on every call).
    expired = [k for k, (_, t) in _cache.items() if now - t >= CACHE_TTL_SECONDS]
    for k in expired:
        del _cache[k]

    return None


def _put_cache(query: str, allowed: list | None, blocked: list | None, results: list[dict[str, str]]) -> None:
    """Store results in cache with current timestamp."""
    key = _make_cache_key(query, allowed, blocked)
    _cache[key] = (results, time.monotonic())


def _filter_by_domain(
    results: list[dict[str, str]],
    allowed: list[str] | None,
    blocked: list[str] | None,
) -> list[dict[str, str]]:
    """
    Server-side domain filtering: keep only results matching allowed domains
    and exclude results matching blocked domains.
    """
    if not allowed and not blocked:
        return results

    filtered: list[dict[str, str]] = []
    for r in results:
        url: str = r.get("url", "")

        if allowed:
            if not any(_url_matches_domain(url, d) for d in allowed):
                continue

        if blocked:
            if any(_url_matches_domain(url, d) for d in blocked):
                continue

        filtered.append(r)

    return filtered


def _url_matches_domain(url: str, domain: str) -> bool:
    """Check if URL belongs to the given domain (handles subdomains)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.netloc.endswith(domain) or parsed.netloc == domain
    except Exception:
        return False


async def _search_ddg_raw(query: str) -> list[dict[str, str]]:
    """
    Perform a raw DuckDuckGo HTML search and return structured results.
    No domain filtering here — done by _filter_by_domain() at a higher level.
    """
    client = _get_client()
    encoded_query = query.replace(" ", "+")
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

    response = await client.get(url)
    response.raise_for_status()

    return _parse_ddg_html(str(response.text))


def _parse_ddg_html(html: str) -> list[dict[str, str]]:
    """
    Parse DuckDuckGo HTML with BeautifulSoup.
    Each result block is a <div class="result"> containing:
      - <a class="result-a"> for title + URL
      - <a class="result-snippet"> for the snippet
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.warning("BTDDGWebSearch: BeautifulSoup parse failed | error=%s", exc)
        return []

    results: list[dict[str, str]] = []

    for block in soup.find_all("div", class_="result"):
        # Find the title/URL link.
        link_tag = block.find("a", class_="result-a")
        if not link_tag:
            # Fallback: any <a> with href inside the result block.
            link_tag = block.find("a", href=True)

        if not link_tag:
            continue

        href = link_tag.get("href", "")
        if not href.startswith("http"):
            continue

        title = link_tag.get_text(strip=True)

        # Find snippet.
        snippet_tag = block.find("a", class_="result-snippet")
        snippet = ""
        if snippet_tag:
            snippet = snippet_tag.get_text(strip=True)[:MAX_SNIPPET_CHARS]
        else:
            # Fallback: second <a> tag if no result-snippet class.
            all_links = block.find_all("a", href=True)
            if len(all_links) > 1:
                snippet = all_links[1].get_text(strip=True)[:MAX_SNIPPET_CHARS]

        results.append({"title": title, "url": href, "snippet": snippet})

        if len(results) >= MAX_RESULTS:
            break

    if not results and html.strip():
        logger.warning(
            "BTDDGWebSearch: parsed zero results from non-empty HTML "
            "(DuckDuckGo page structure may have changed)"
        )

    return results


# ─── Tool class ────────────────────────────────────────────────────────────────


class BTDDGWebSearch(BuiltinTools):
    """
    Provider-agnostic WebSearch using DuckDuckGo HTML.

    Registered as "WebSearch" in PromptLib.build_tools() when:
      - lib_id != "cc_reverse:v2.1.81", OR
      - Adapter is not AnthropicAdapter

    Features:
      - 5-minute result cache (per unique query + domain filters)
      - Server-side domain filtering (allowed/blocked)
      - BeautifulSoup parsing (robust)
      - Semaphore-concurrency guard (max 5 simultaneous searches)
      - 2 retries with exponential backoff on transient errors
    """

    name = "DDGSearch"
    risk = "low"
    tags = ["network", "search"]
    description = (
        "Search the web using DuckDuckGo (no API key required) and return results with titles, URLs, and summaries. "
        "Use this when WebSearch is unavailable or as a fallback for web searches. "
        "Do NOT use for information already in your training (standard library docs, well-known APIs). "
        "Returns up to 10 results, each with title, URL, and a short description. "
        "Results are cached for 5 minutes. "
        "Results are capped at 20,000 characters total."
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
                "Restrict results to only these domains (including subdomains). "
                "Example: ['docs.python.org', 'github.com'] to limit to official docs and GitHub."
            ),
            required=False,
            items={"type": "string"},
        ),
        "blocked_domains": ToolParam(
            type="array",
            description=(
                "Exclude results from these domains (including subdomains). "
                "Example: ['reddit.com', 'stackoverflow.com'] to skip community discussion sites."
            ),
            required=False,
            items={"type": "string"},
        ),
    }

    def __init__(self, adapter: ModelAdapter | None = None, model: str = DEFAULT_MODEL):
        """
        Args:
            adapter: Optional. Kept for API compatibility with BTWebSearch; not used.
            model:   Optional. Not used — DuckDuckGo provides raw results, no LLM needed.
        """
        self.adapter = adapter
        self.model = model

    async def run(
        self,
        query: str,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> ToolResult:
        async with _SEMAPHORE:
            try:
                results = await self._do_search(query, allowed_domains, blocked_domains)
            except Exception as exc:
                logger.exception("BTDDGWebSearch failed")
                return ToolResult.error(f"Web search failed: {exc}")

        if not results:
            return ToolResult.error("Web search returned no results.")

        output = _format_results(results)

        if len(output) > MAX_TOTAL_CHARS:
            output = output[:MAX_TOTAL_CHARS] + "\n\n[Results truncated]"

        return ToolResult.ok(output)

    async def _do_search(
        self,
        query: str,
        allowed_domains: list[str] | None,
        blocked_domains: list[str] | None,
    ) -> list[dict[str, str]]:
        """
        Search with cache check, retry, and server-side domain filtering.
        """
        # 1. Cache check.
        cached = _get_cached(query, allowed_domains, blocked_domains)
        if cached is not None:
            logger.debug(f"DDG cache hit for query: {query[:50]}")
            return cached

        # 2. Perform search with retry.
        raw_results: list[dict[str, str]] | None = None
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                raw_results = await _search_ddg_raw(query)
                break
            except (httpx.ConnectError, httpx.ReadTimeout,
                    httpx.PoolTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 2 ** attempt  # 指数退避：1s, 2s
                    logger.warning(
                        "BTDDGWebSearch: transient error, retrying (%d/3) after %.0fs | error=%s",
                        attempt + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except Exception as exc:
                last_exc = exc
                break

        if raw_results is None:
            raise last_exc or RuntimeError("BTDDGWebSearch: unknown error")

        # 3. Server-side domain filtering.
        filtered = _filter_by_domain(raw_results, allowed_domains, blocked_domains)

        # 4. Cache the filtered results.
        _put_cache(query, allowed_domains, blocked_domains, filtered)

        return filtered


def _format_results(results: list[dict[str, str]]) -> str:
    """
    Format search results as readable plain text for the LLM.
    Each result: title, URL, and snippet.
    """
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        if r.get("snippet"):
            lines.append(f"    {r['snippet']}")
        lines.append("")
    return "\n".join(lines).strip()
