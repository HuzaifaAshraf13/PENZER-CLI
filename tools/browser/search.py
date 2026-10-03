"""Browser-independent web search and result ranking."""

import asyncio
import re
from typing import Any, Dict
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .policy import is_government_domain, is_http_url
from .results import error, success

_QUERY_STOP_WORDS = {
    "the", "and", "for", "official", "latest", "current", "news", "update",
    "mission", "information", "about", "from", "with",
}
_ROMAN_NUMERALS = {"ii", "iii", "iv", "v", "vi"}


def _query_terms(query: str) -> list[str]:
    return [
        token for token in dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower()))
        if (len(token) > 2 or token in _ROMAN_NUMERALS) and token not in _QUERY_STOP_WORDS
    ]


def _minimum_relevance(query: str) -> int:
    terms = _query_terms(query)
    if not terms:
        return 1
    return min(2, len(terms))


def _has_relevant_results(query: str, ranked: list[Dict[str, Any]]) -> bool:
    minimum = _minimum_relevance(query)
    roman_terms = set(_query_terms(query)) & _ROMAN_NUMERALS
    return any(
        item.get("relevance", 0) >= minimum
        and roman_terms.issubset(set(item.get("query_matches", [])))
        for item in ranked
    )


def _parse_bing_rss(payload: bytes) -> list[Dict[str, Any]]:
    rss = ElementTree.fromstring(payload)
    results = []
    for result in rss.iter():
        if result.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        fields = {
            child.tag.rsplit("}", 1)[-1].lower(): (child.text or "").strip()
            for child in result
        }
        if fields.get("title") and fields.get("link"):
            results.append({
                "title": fields["title"],
                "url": fields["link"],
                "snippet": fields.get("description", ""),
                "date": fields.get("pubdate", ""),
            })
    return results


async def _search_bing(query: str) -> tuple[str, list[Dict[str, Any]]]:
    url = f"https://www.bing.com/search?{urlencode({'format': 'rss', 'q': query})}"
    response = await asyncio.to_thread(requests.get, url, timeout=15)
    response.raise_for_status()
    return url, _parse_bing_rss(response.content)


def _unwrap_duckduckgo_url(href: str) -> str:
    url = urljoin("https://duckduckgo.com", href)
    parsed = urlparse(url)
    if (parsed.hostname or "").endswith("duckduckgo.com") and parsed.path == "/l/":
        url = parse_qs(parsed.query).get("uddg", [url])[0]
    return url


async def _search_duckduckgo(query: str) -> list[Dict[str, Any]]:
    response = await asyncio.to_thread(
        requests.get,
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; PenzerCLI/1.0)"},
        timeout=15,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        if link is None:
            continue
        url = _unwrap_duckduckgo_url(str(link.get("href") or ""))
        if not is_http_url(url):
            continue
        snippet = result.select_one(".result__snippet")
        results.append({
            "title": link.get_text(" ", strip=True),
            "url": url,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            "date": "",
        })
    return results


def _rank_results(query: str, results: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    query_terms = _query_terms(query)
    for item in results:
        item["government_domain"] = is_government_domain(item["url"])
        candidate_text = " ".join(str(item.get(key, "")) for key in ("title", "url", "snippet")).lower()
        item["query_matches"] = sorted(
            term for term in query_terms
            if re.search(rf"\b{re.escape(term)}\b", candidate_text)
            or any(term in token for token in re.findall(r"[a-z0-9]+", candidate_text) if len(token) > len(term))
        )
        item["relevance"] = len(item["query_matches"])
    return sorted(
        results,
        key=lambda item: (int(item.get("relevance", 0)), bool(item.get("government_domain"))),
        reverse=True,
    )


def _search_success(query: str, provider: str, url: str, ranked: list[Dict[str, Any]]) -> Dict[str, Any]:
    minimum = _minimum_relevance(query)
    roman_terms = set(_query_terms(query)) & _ROMAN_NUMERALS
    results = [
        item for item in ranked
        if item.get("relevance", 0) >= minimum
        and roman_terms.issubset(set(item.get("query_matches", [])))
    ][:10]
    government = [item for item in results if item.get("government_domain")]
    title = "Bing search results" if provider == "bing" else "DuckDuckGo search results"
    return success({
        "query": query,
        "url": url,
        "provider": provider,
        "title": title,
        "government_domain_candidates": government,
        "relevant_government_results": [item for item in government if item.get("relevance", 0) >= 2],
        "results": results,
        "content": "\n".join(item["snippet"] for item in results if item["snippet"]),
    }, f"Searched the web for {query}")


async def search_web(query: str) -> Dict[str, Any]:
    query = query.strip()
    if not query:
        return error("Search query required")

    provider_errors = []
    candidates = []
    try:
        bing_url, candidates = await _search_bing(query)
        ranked = _rank_results(query, candidates)
        if _has_relevant_results(query, ranked):
            return _search_success(query, "bing", bing_url, ranked)
    except (requests.RequestException, ElementTree.ParseError) as exc:
        provider_errors.append(f"Bing: {exc}")

    try:
        candidates = await _search_duckduckgo(query)
        ranked = _rank_results(query, candidates)
        if _has_relevant_results(query, ranked):
            return _search_success(query, "duckduckgo", "https://html.duckduckgo.com/html/", ranked)
    except requests.RequestException as exc:
        provider_errors.append(f"DuckDuckGo: {exc}")

    ranked = _rank_results(query, candidates)
    return error(
        "No relevant search results found. Try a different query or source; do not repeat the same search.",
        {
            "query": query,
            "no_relevant_results": True,
            "provider_errors": provider_errors,
            "results": [
                {key: item.get(key, "") for key in ("title", "url", "relevance")}
                for item in ranked[:3]
            ],
        },
    )