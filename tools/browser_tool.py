"""
Browser Tool: Web search, navigation, scraping, and content extraction.
"""

import re
import json
import logging
from urllib.parse import urljoin, quote_plus
from typing import Optional, Dict, Any

import requests
from bs4 import BeautifulSoup
from readability import Document  # pip install readability-lxml

from agent.core import mcp
from tools.standards import success, error, warning
import warnings
warnings.filterwarnings("ignore", module="requests")
# Optional Selenium/Playwright support for JavaScript rendering
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

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


def _fetch_html(url: str, timeout: int = 15, use_selenium: bool = False) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch HTML content from a URL.
    Returns (html_content, error_message) or (None, error_message) on failure.
    """
    # Try headless browser if requested and available
    if use_selenium and SELENIUM_AVAILABLE:
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=options
            )
            driver.set_page_load_timeout(timeout)
            driver.get(url)
            html = driver.page_source
            driver.quit()
            return html, None
        except Exception as e:
            logger.warning(f"Selenium fetch failed, falling back to requests: {e}")
            # Fall through to requests

    # Standard requests fetch
    try:
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        # Detect encoding from headers or fallback to UTF-8
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

        # If no results, fallback to Wikipedia
        if not results:
            return _wikipedia_search(query)
        return results
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}, falling back to Wikipedia")
        return _wikipedia_search(query)


def _wikipedia_search(query: str) -> list[Dict[str, str]]:
    """Search Wikipedia and return results."""
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": 3,
    }
    try:
        resp = requests.get(api_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            title = item["title"]
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
            results.append({"title": title, "url": url, "snippet": snippet})
        return results
    except Exception:
        return []


def _get_wikipedia_summary(title: str) -> str:
    """Get plain text summary of a Wikipedia article."""
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "exintro": True,
        "explaintext": True,
        "titles": title,
    }
    try:
        resp = requests.get(api_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            extract = page.get("extract", "")
            if extract:
                return extract[:3000]
    except Exception:
        pass
    return ""


@mcp.tool()
def browser(action: str,
            query: str = None,
            url: str = None,
            timeout: int = 15,
            render_js: bool = False,
            max_results: int = 5) -> dict:
    """
    A modern browser tool for web search, navigation, and content extraction.

    Actions:
        search   : Search the web (DuckDuckGo + Wikipedia fallback) and return top results with snippets.
        open     : Fetch a URL, extract title and main content (cleaned, readable text).
        scrape   : Fetch a URL and return raw HTML text (for custom parsing).
        summary  : Fetch a URL and extract a short summary using readability.
        wikipedia: Directly fetch a Wikipedia article summary (given a title in `query`).

    Args:
        action      : The action to perform.
        query       : Search query (for search/wikipedia) or Wikipedia title.
        url         : The URL to fetch (for open/scrape/summary).
        timeout     : Request timeout in seconds (default 15).
        render_js   : If True and selenium is installed, use a headless browser for JS-heavy pages.
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

            # For the top result, try to fetch a summary (if it's a Wikipedia page or any page)
            top = results[0]
            summary = ""
            # If it's a Wikipedia URL, get the summary via API
            if "wikipedia.org" in top["url"]:
                title = top["title"]
                summary = _get_wikipedia_summary(title)
            else:
                # Otherwise, try to open and extract a short summary
                html, err = _fetch_html(top["url"], timeout=timeout, use_selenium=render_js)
                if html:
                    # Use readability to get main content, then truncate
                    try:
                        doc = Document(html)
                        content = doc.summary()
                        soup = BeautifulSoup(content, "html.parser")
                        summary = soup.get_text(" ", strip=True)[:1000] + "..."
                    except Exception:
                        summary = top.get("snippet", "")

            return success(data={
                "action": "search",
                "query": query,
                "top_result": {
                    "title": top["title"],
                    "url": top["url"],
                    "summary": summary if summary else "No summary available.",
                },
                "other_results": results[1:]
            })

        elif action == "open":
            if not url:
                return error("open action requires 'url' parameter")
            html, err = _fetch_html(url, timeout=timeout, use_selenium=render_js)
            if err:
                return error(f"Failed to fetch {url}: {err}")
            title = _extract_title(html)
            # Extract main content as clean text
            content = _extract_text(html)
            # Truncate to reasonable length
            if len(content) > 5000:
                content = content[:5000] + "... [truncated]"
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
            html, err = _fetch_html(url, timeout=timeout, use_selenium=render_js)
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
            html, err = _fetch_html(url, timeout=timeout, use_selenium=render_js)
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

        elif action == "wikipedia":
            if not query:
                return error("wikipedia action requires 'query' (title) parameter")
            summary = _get_wikipedia_summary(query)
            if not summary:
                return error(f"No Wikipedia article found for '{query}'")
            url = f"https://en.wikipedia.org/wiki/{query.replace(' ', '_')}"
            return success(data={
                "action": "wikipedia",
                "title": query,
                "url": url,
                "summary": summary
            })

        else:
            return warning(data={}, message=f"Unknown action '{action}'. Supported: search, open, scrape, summary, wikipedia")

    except Exception as e:
        logger.exception("Browser tool error")
        return error(f"Browser tool error: {str(e)}")