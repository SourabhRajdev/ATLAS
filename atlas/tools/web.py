"""Web tools — search, fetch pages, extract content."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx

from atlas.core.models import Tier
from atlas.tools.registry import ToolRegistry

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def register(registry: ToolRegistry, config=None) -> None:
    _serper_key = getattr(config, "serper_api_key", "") if config else ""

    @registry.register(
        name="web_search",
        description=(
            "Search the web. Returns top results with titles, snippets, and URLs. "
            "Uses Google (via Serper) when available, falls back to DuckDuckGo. "
            "Use for current events, facts, documentation, prices, anything online."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (default 8)", "default": 8},
            },
            "required": ["query"],
        },
        tier=Tier.AUTO,
    )
    async def web_search(query: str, max_results: int = 8) -> str:
        if _serper_key:
            return await _serper_search(query, max_results, _serper_key)
        return await _ddg_search(query, max_results)


async def _serper_search(query: str, max_results: int, api_key: str) -> str:
    """Google search via Serper API."""
    payload = {"q": query, "num": min(max_results, 10)}
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    # Knowledge graph answer (best single result)
    kg = data.get("knowledgeGraph", {})
    if kg.get("description"):
        results.append(f"**{kg.get('title', '')}**\n{kg['description']}\n")

    # Answer box
    ab = data.get("answerBox", {})
    if ab.get("answer"):
        results.append(f"**Answer:** {ab['answer']}\n")
    elif ab.get("snippet"):
        results.append(f"**{ab.get('title', 'Answer')}**\n{ab['snippet']}\n")

    # Organic results
    for r in data.get("organic", [])[:max_results]:
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        if title:
            results.append(f"**{title}**\n{snippet}\n{link}\n")

    if not results:
        return f"No results for '{query}'."
    return "\n".join(results[:max_results])


async def _ddg_search(query: str, max_results: int) -> str:
    """Fallback: DuckDuckGo HTML scrape."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()

    results = []
    blocks = re.findall(
        r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>.*?'
        r'<a class="result__snippet".*?>(.*?)</a>',
        resp.text, re.DOTALL,
    )
    for href, title, snippet in blocks[:max_results]:
        title = _strip_html(title).strip()
        snippet = _strip_html(snippet).strip()
        if title:
            results.append(f"**{title}**\n{snippet}\n{href}\n")

    if not results:
        return f"No structured results found for '{query}'. Try a different query."
    return "\n".join(results)

    @registry.register(
        name="fetch_url",
        description="Fetch the text content of a web page. Returns cleaned text, not raw HTML.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "description": "Max characters to return", "default": 8000},
            },
            "required": ["url"],
        },
        tier=Tier.AUTO,
    )
    async def fetch_url(url: str, max_chars: int = 8000) -> str:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            text = _html_to_text(resp.text)
        else:
            text = resp.text

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... (truncated at {max_chars} chars)"
        return text


def _strip_html(html: str) -> str:
    """Remove HTML tags."""
    return re.sub(r"<[^>]+>", "", html)


def _html_to_text(html: str) -> str:
    """Crude but effective HTML to text conversion."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.I)
    # Convert <br>, <p>, <div>, <li> to newlines
    text = re.sub(r"<(br|p|div|li|h[1-6])[^>]*>", "\n", text, flags=re.I)
    # Strip remaining tags
    text = _strip_html(text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
