"""
Browser Tool: Web search, navigation, scraping.
"""

import subprocess
import re
import json

from agent.core import mcp
from tools.standards import success, error, warning


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


def _wikipedia_search(query: str) -> list:
    """Search Wikipedia and return results with summaries."""
    cmd = f"curl -s 'https://en.wikipedia.org/w/api.php?action=query&format=json&list=search&srsearch={query.replace(' ', '+')}' 2>/dev/null"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
    results = []
    if result.returncode == 0 and result.stdout:
        try:
            data = json.loads(result.stdout)
            for item in data.get('query', {}).get('search', [])[:3]:
                title = item.get('title', '')
                snippet = re.sub(r'<[^>]+>', '', item.get('snippet', ''))
                url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                results.append({"title": title, "url": url, "snippet": snippet})
        except:
            pass
    return results


def _wikipedia_summary(title: str) -> str:
    """Get Wikipedia article summary."""
    cmd = f"curl -s 'https://en.wikipedia.org/w/api.php?action=query&format=json&prop=extracts&exintro=true&explaintext=true&titles={title.replace(' ', '+')}' 2>/dev/null"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
    if result.returncode == 0 and result.stdout:
        try:
            data = json.loads(result.stdout)
            pages = data.get('query', {}).get('pages', {})
            for page in pages.values():
                extract = page.get('extract', '')
                if extract:
                    return extract[:3000]
        except:
            pass
    return ""


@mcp.tool()
def browser(action: str, query: str = None, url: str = None, selector: str = None,
            text: str = None, timeout: int = 10) -> dict:
    """
    Browser tool for web search and content retrieval.

    Actions:
        search: Search the web and return results WITH content summaries
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

            results = _wikipedia_search(query)

            if not results:
                return error(f"No results found for: {query}")

            # Auto-fetch summary for top result
            top = results[0]
            summary = _wikipedia_summary(top["title"])

            # Also try to get content from top result URL if no summary
            if not summary:
                html = _curl_fetch(top["url"], timeout)
                summary = _strip_html(html)[:3000] if html else "Could not fetch content."

            return success(data={
                "action": "search",
                "query": query,
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