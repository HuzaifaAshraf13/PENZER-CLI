"""
Browser Tool: Web search, navigation, scraping, and content extraction.
Single search provider (DuckDuckGo). Production-minimal design.
"""
import re
import logging
from urllib.parse import urljoin
from typing import Optional, Dict, Any
import requests
from bs4 import BeautifulSoup
from readability import Document  # pip install readability-lxml
from agent.core import mcp
from tools.standards import success, error, warning
import warnings
warnings.filterwarnings("ignore", module="requests")

logger = logging.getLogger(__name__)

# Default headers to mimic a real browser
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Session with connection pooling and retries
_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=3)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


class SessionManager:
    """Track lightweight browser navigation state for one agent session."""

    _sessions: dict[str, dict] = {}

    @classmethod
    def get(cls, session_id: str) -> dict:
        return cls._sessions.setdefault(session_id, {"id": session_id, "current_url": "", "history": []})

    @classmethod
    def record(cls, session_id: str, url: str) -> None:
        state = cls.get(session_id)
        state["current_url"] = url
        state["history"].append(url)


def _fetch_html(url: str, timeout: int = 15) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch HTML content from a URL.
    Returns (html_content, error_message) or (None, error_message) on failure.
    """
    try:
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        encoding = resp.encoding if resp.encoding else "utf-8"
        html = resp.content.decode(encoding, errors="ignore")
        return html, None
    except requests.exceptions.RequestException as e:
        return None, f"Request error: {str(e)}"


def _extract_text(html: str) -> str:
    """Extract clean, readable text from HTML using readability-lxml."""
    try:
        doc = Document(html)
        return doc.summary()  # returns HTML of the main content
    except Exception:
        # Fallback: strip all tags and clean up
        soup = BeautifulSoup(html, "html.parser")
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text


def _extract_title(html: str) -> str:
    """Extract page title."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return "No title"


def _search_duckduckgo(query: str, max_results: int = 5) -> list[Dict[str, str]]:
    """
    Perform a web search using DuckDuckGo (HTML version) and return results.
    Single search provider, no fallbacks.
    """
    search_url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Accept": "text/html",
    }
    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        # DuckDuckGo HTML results are inside <a class="result__a"> with parent <div class="result">
        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href")
            if not href:
                continue
            # DDG uses relative URLs with query parameters; convert to absolute
            if href.startswith("/"):
                href = urljoin("https://duckduckgo.com", href)
            # Extract snippet
            snippet_elem = result.select_one(".result__snippet")
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")
        return []


@mcp.tool()
def browser(action: str,
            query: str = None,
            url: str = None,
            timeout: int = 15,
            max_results: int = 5,
            session_id: str | None = None) -> dict:
    """
    A minimal browser tool for web search, navigation, and content extraction.
    Single search provider (DuckDuckGo). No optional dependencies.
    
    Actions:
        search   : Search the web (DuckDuckGo) and return top results with snippets.
        open     : Fetch a URL, extract title and main content (cleaned, readable text).
        scrape   : Fetch a URL and return raw HTML text (for custom parsing).
        summary  : Fetch a URL and extract a short summary using readability.
    
    Args:
        action      : The action to perform.
        query       : Search query (for search action).
        url         : The URL to fetch (for open/scrape/summary actions).
        timeout     : Request timeout in seconds (default 15).
        max_results : Max number of search results to return (default 5).
    
    Returns:
        Standardised success/error dict with relevant data.
    """
    try:
        if action == "search":
            if not query:
                return error("search action requires 'query' parameter")
            results = _search_duckduckgo(query, max_results=max_results)
            if not results:
                return error(f"No results found for: {query}")
            if session_id:
                SessionManager.record(session_id, results[0]["url"])
            
            return success(data={
                "action": "search",
                "query": query,
                "results": results,
                "top_result": results[0],
            })
        
        elif action == "open":
            if not url:
                return error("open action requires 'url' parameter")
            html, err = _fetch_html(url, timeout=timeout)
            if err:
                return error(f"Failed to fetch {url}: {err}")
            title = _extract_title(html)
            # Extract main content as clean text
            content = _extract_text(html)
            # Truncate to reasonable length
            if len(content) > 5000:
                content = content[:5000] + "... [truncated]"
            if session_id:
                SessionManager.record(session_id, url)
            return success(data={
                "action": "open",
                "url": url,
                "title": title,
                "content": content,
                "chars": len(content)
            })
        
        elif action == "scrape":
            if not url:
                return error("scrape action requires 'url' parameter")
            html, err = _fetch_html(url, timeout=timeout)
            if err:
                return error(f"Failed to scrape {url}: {err}")
            # Return raw HTML (truncated for safety)
            if len(html) > 8000:
                html = html[:8000] + "... [truncated]"
            return success(data={
                "action": "scrape",
                "url": url,
                "html": html,
                "chars": len(html)
            })
        
        elif action == "summary":
            if not url:
                return error("summary action requires 'url' parameter")
            html, err = _fetch_html(url, timeout=timeout)
            if err:
                return error(f"Failed to fetch {url}: {err}")
            doc = Document(html)
            title = doc.title() or _extract_title(html)
            # Get main article text
            summary_html = doc.summary()
            soup = BeautifulSoup(summary_html, "html.parser")
            text = soup.get_text(" ", strip=True)
            # Limit to 2000 characters
            if len(text) > 2000:
                text = text[:2000] + "..."
            return success(data={
                "action": "summary",
                "url": url,
                "title": title,
                "summary": text
            })
        
        else:
            return warning(data={}, message=f"Unknown action '{action}'. Supported: search, open, scrape, summary")
    
    except Exception as e:
        logger.exception("Browser tool error")
        return error(f"Browser tool error: {str(e)}")


@mcp.tool()
def browser_info(session_id: str) -> dict:
    """Return the current URL and navigation history for a browser session."""
    state = SessionManager.get(session_id)
    return success(data={
        "id": state["id"],
        "current_url": state["current_url"],
        "history": list(state["history"]),
    })