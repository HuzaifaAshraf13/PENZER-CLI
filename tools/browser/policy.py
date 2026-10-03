"""URL policy shared by browser navigation and web search."""

from urllib.parse import urlparse


def is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_government_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (
        host.endswith(".gov")
        or ".gov." in host
        or host.endswith(".mil")
        or ".mil." in host
    )