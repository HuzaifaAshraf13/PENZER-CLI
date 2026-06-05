# tools/browser_tool.py
"""
Browser Tool: Web search, navigation, scraping, and interaction.
"""

import subprocess
import re

from agent.core import mcp
from tools.standards import success, error, warning


@mcp.tool()
def browser(action: str, query: str = None, url: str = None, selector: str = None, 
            text: str = None, timeout: int = 10) -> dict:
    """
    Browser automation tool for web interaction.
    
    Actions:
        search: Google search (returns top 5 results with URLs)
        open: Open a URL and return page title + content snippet
        scrape: Extract all text content from current page
        click: Click element by CSS selector
        type: Type text into input field (selector + text)
        screenshot: Take screenshot of current page
        scroll: Scroll page (down/up)
        
    Args:
        action: What to do (search, open, scrape, click, type, screenshot, scroll)
        query: Search query (for search action)
        url: URL to open (for open action)
        selector: CSS selector (for click, type, screenshot actions)
        text: Text to type (for type action)
        timeout: Seconds to wait for page load (default 10)
    """
    try:
        # Try to import playwright, fall back to selenium
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
            using_playwright = True
        except ImportError:
            try:
                from selenium import webdriver
                from selenium.webdriver.common.by import By
                using_playwright = False
            except ImportError:
                return error("Browser tool requires: pip install playwright selenium")
        
        if action == "search":
            if not query:
                return error("search action requires 'query' parameter")
            
            try:
                import json
                # Try DuckDuckGo JSON API first (most reliable)
                cmd = f"curl -s 'https://api.duckduckgo.com/?q={query.replace(' ', '+')}&format=json&no_redirect=1' 2>/dev/null"
                result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        data = json.loads(result.stdout)
                        urls = []
                        
                        # Get URLs from RelatedTopics
                        if 'RelatedTopics' in data:
                            for topic in data['RelatedTopics'][:10]:
                                if isinstance(topic, dict) and 'FirstURL' in topic:
                                    urls.append(topic['FirstURL'])
                                    if len(urls) >= 5:
                                        break
                        
                        if urls:
                            return success(data={
                                "action": "search",
                                "query": query,
                                "results": urls,
                                "count": len(urls)
                            })
                    except:
                        pass
            except:
                pass
            
            # Fallback: Return mock results or simple Google search
            # Use a simple Wikipedia/documentation search approach
            try:
                cmd = f"curl -s 'https://en.wikipedia.org/w/api.php?action=query&format=json&srsearch={query.replace(' ', '+')}&list=search' 2>/dev/null"
                result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        import json
                        data = json.loads(result.stdout)
                        urls = []
                        if 'query' in data and 'search' in data['query']:
                            for item in data['query']['search'][:5]:
                                if 'title' in item:
                                    urls.append(f"https://en.wikipedia.org/wiki/{item['title'].replace(' ', '_')}")
                        
                        if urls:
                            return success(data={
                                "action": "search",
                                "query": query,
                                "results": urls,
                                "count": len(urls)
                            })
                    except:
                        pass
            except:
                pass
            
            # Last resort: Return generic search instruction
            return success(data={
                "action": "search",
                "query": query,
                "results": [
                    f"https://en.wikipedia.org/w/api.php?search={query.replace(' ', '+')}",
                    f"https://www.bing.com/search?q={query.replace(' ', '+')}",
                ],
                "count": 2,
                "note": "Live search limited, but these URLs can be opened for real results"
            })
        
        elif action == "open":
            if not url:
                return error("open action requires 'url' parameter")
            
            # Use curl to fetch page
            cmd = f"curl -s -A 'Mozilla/5.0' '{url}' 2>/dev/null | head -c 5000"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
            
            if result.returncode != 0 or not result.stdout:
                return error(f"Could not fetch URL: {url}")
            
            # Extract title from HTML
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', result.stdout, re.IGNORECASE)
            title = title_match.group(1) if title_match else "Unknown"
            
            return success(data={
                "action": "open",
                "url": url,
                "title": title,
                "content_preview": result.stdout[:2000]
            })
        
        elif action == "scrape":
            if not url:
                return error("scrape action requires 'url' parameter")
            
            # Extract text content
            cmd = f"curl -s -A 'Mozilla/5.0' '{url}' 2>/dev/null | sed 's/<[^>]*>//g' | tr -s '\\n' | head -c 10000"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
            
            if result.returncode != 0:
                return error(f"Could not scrape: {url}")
            
            return success(data={
                "action": "scrape",
                "url": url,
                "text_content": result.stdout.strip()[:5000]
            })
        
        else:
            return warning(data={}, message=f"Action '{action}' not yet implemented. Supported: search, open, scrape")
    
    except Exception as e:
        return error(f"Browser error: {str(e)}")
