"""
Browser Tool: Web search, navigation, scraping.
Search backend: DuckDuckGo HTML endpoint (no external service required).
"""

import subprocess
import re
import json
import time
import hashlib
import urllib.parse

from agent.core import mcp
from tools.standards import success, error, warning


# ---------------------------------------------------------------------------
# Config (hardcoded — no external service, no env dependency needed)
# ---------------------------------------------------------------------------

SEARCH_TIMEOUT = 10
CACHE_TTL_SECONDS = 300          # 5 min
RETRY_BACKOFF_SECONDS = 1.5      # single retry delay on empty results

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"

# In-memory cache: {query_hash: (timestamp, results)}
_search_cache = {}


# ---------------------------------------------------------------------------
# Shared fetch helpers (unchanged — still used by open/scrape)
# ---------------------------------------------------------------------------

def _curl_fetch(url: str, timeout: int = 10) -> str:
    """Fetch URL content via curl."""
    cmd = f"curl -s -L -A 'Mozilla/5.0' --max-time {timeout} '{url}' 2>/dev/null"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout + 2)
    return result.stdout if result.returncode == 0 else ""


def _strip_html(html: str) -> str:
    """Strip HTML tags and clean up whitespace."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# DuckDuckGo search backend
# ---------------------------------------------------------------------------

def _cache_key(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


def _cache_get(query: str):
    key = _cache_key(query)
    entry = _search_cache.get(key)
    if not entry:
        return None
    ts, results = entry
    if time.time() - ts > CACHE_TTL_SECONDS:
        del _search_cache[key]
        return None
    return results


def _cache_set(query: str, results: list) -> None:
    _search_cache[_cache_key(query)] = (time.time(), results)


def _dedupe(results: list) -> list:
    seen = set()
    deduped = []
    for r in results:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            deduped.append(r)
    return deduped


def _clean_ddg_redirect_url(raw_url: str) -> str:
    """DuckDuckGo HTML wraps result links in a redirect (/l/?uddg=<encoded>)."""
    if "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                return urllib.parse.unquote(qs["uddg"][0])
        except Exception:
            pass
    return raw_url


def _parse_ddg_html(html: str) -> list:
    """Extract title/url/snippet triples from DuckDuckGo's HTML result markup."""
    results = []
    blocks = re.findall(r'<div class="result__body">(.*?)</div>\s*</div>', html, flags=re.DOTALL)
    if not blocks:
        blocks = re.split(r'(?=<a[^>]+class="result__a")', html)

    for block in blocks[:10]:
        title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, flags=re.DOTALL)
        url_match = re.search(r'class="result__a"\s+href="([^"]+)"', block)
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, flags=re.DOTALL)

        if not title_match or not url_match:
            continue

        title = _strip_html(title_match.group(1))
        url = _clean_ddg_redirect_url(url_match.group(1))
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


def _query_ddg(query: str, timeout: int) -> dict:
    """
    Single call to DuckDuckGo HTML search. Returns a dict distinguishing failure modes:
        {"status": "ok", "results": [...]}
        {"status": "empty"}          -> reachable, zero results parsed
        {"status": "unreachable"}    -> connection/timeout failure
        {"status": "parse_error"}    -> got a response, couldn't parse any results out of it
    """
    q = urllib.parse.quote(query)
    url = f"{DDG_SEARCH_URL}?q={q}"
    cmd = f"curl -s -L -A 'Mozilla/5.0' --max-time {timeout} '{url}' 2>/dev/null"

    try:
        result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return {"status": "unreachable"}

    if result.returncode != 0 or not result.stdout:
        return {"status": "unreachable"}

    try:
        results = _dedupe(_parse_ddg_html(result.stdout))
    except Exception:
        return {"status": "parse_error"}

    if not results:
        return {"status": "empty"}

    return {"status": "ok", "results": results}


def _web_search(query: str) -> dict:
    """
    Search via DuckDuckGo with cache + single retry-on-empty.
    Returns dict:
        {"status": "cached"/"ok", "results": [...]}
        {"status": "empty"}
        {"status": "unreachable"}
        {"status": "parse_error"}
    """
    cached = _cache_get(query)
    if cached is not None:
        return {"status": "cached", "results": cached}

    outcome = _query_ddg(query, SEARCH_TIMEOUT)

    if outcome["status"] == "empty":
        time.sleep(RETRY_BACKOFF_SECONDS)
        outcome = _query_ddg(query, SEARCH_TIMEOUT)

    if outcome["status"] == "ok":
        _cache_set(query, outcome["results"])

    return outcome


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@mcp.tool()
def browser(action: str, query: str = None, url: str = None, selector: str = None,
            text: str = None, timeout: int = 10) -> dict:
    """
    Browser tool for web search and content retrieval.

    Actions:
        search: Search the web via DuckDuckGo, return top results with snippets
        open: Fetch a URL and return readable text content
        scrape: Extract all text from a URL

    Args:
        action: What to do (search, open, scrape)
        query: Search query (for search action)
        url: URL to fetch (for open/scrape actions)
        timeout: Seconds to wait (default 10)
    """
    try:
        if action == "search":
            if not query:
                return error("search action requires 'query' parameter")

            outcome = _web_search(query)
            status = outcome["status"]

            if status == "unreachable":
                return error("Could not reach DuckDuckGo search endpoint. Check network connectivity.")

            if status == "parse_error":
                return error("DuckDuckGo returned a response but it couldn't be parsed (markup may have changed).")

            if status == "empty":
                return error(f"No results found for: {query}")

            results = outcome["results"]
            top = results[0]

            summary = top["snippet"] if top["snippet"] else _strip_html(_curl_fetch(top["url"], timeout))[:3000]

            return success(data={
                "action": "search",
                "query": query,
                "cached": status == "cached",
                "top_result": {
                    "title": top["title"],
                    "url": top["url"],
                    "summary": summary
                },
                "other_results": results[1:]
            })

        elif action == "open":
            if not url:
                return error("open action requires 'url' parameter")

            html = _curl_fetch(url, timeout)
            if not html:
                return error(f"Could not fetch: {url}")

            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "Unknown"
            content = _strip_html(html)[:5000]

            return success(data={
                "action": "open",
                "url": url,
                "title": title,
                "content": content
            })

        elif action == "scrape":
            if not url:
                return error("scrape action requires 'url' parameter")

            html = _curl_fetch(url, timeout)
            if not html:
                return error(f"Could not scrape: {url}")

            content = _strip_html(html)[:8000]

            return success(data={
                "action": "scrape",
                "url": url,
                "content": content
            })

        else:
            return warning(data={}, message=f"Unknown action '{action}'. Supported: search, open, scrape")

    except Exception as e:
        return error(f"Browser error: {str(e)}")