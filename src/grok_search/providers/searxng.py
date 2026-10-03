"""SearXNG JSON search; uses the existing httpx dependency."""
import httpx

from ..config import config

_PLATFORM_DOMAINS = {
    "github": "github.com",
    "reddit": "reddit.com",
    "twitter": "x.com",
    "x": "x.com",
    "hugging face": "huggingface.co",
    "huggingface": "huggingface.co",
}


async def search(query: str, platform: str = "", max_results: int = 8) -> tuple[list[dict], list]:
    base_url = httpx.URL(config.searxng_base_url)
    if base_url.scheme not in ("http", "https") or not base_url.host:
        raise ValueError("SEARXNG_BASE_URL must be an HTTP(S) URL")
    domains = list(dict.fromkeys(
        _PLATFORM_DOMAINS[p.strip().lower()]
        for p in platform.split(",") if p.strip().lower() in _PLATFORM_DOMAINS
    ))
    if domains:
        query += " (" + " OR ".join(f"site:{domain}" for domain in domains) + ")"
    params = {"q": query, "format": "json"}
    if config.searxng_engines:
        params["engines"] = config.searxng_engines

    async with httpx.AsyncClient(timeout=config.searxng_timeout) as client:
        response = await client.get(f"{str(base_url).rstrip('/')}/search", params=params)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("SearXNG returned an invalid JSON response; enable search.formats: [html, json]")

    sources = []
    seen = set()
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        url = url.strip()
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            continue
        if parsed.scheme not in ("http", "https") or not parsed.host or url in seen:
            continue
        seen.add(url)
        title = item.get("title")
        content = item.get("content")
        sources.append({
            "url": url,
            "title": title.strip() if isinstance(title, str) else "",
            "description": content.strip()[:3000] if isinstance(content, str) else "",
            "provider": "searxng",
        })
        if len(sources) >= max_results:
            break
    return sources, data.get("unresponsive_engines", [])


def format_results(sources: list[dict]) -> str:
    return "\n\n".join(
        f"{i}. {s['title'] or s['url']}\n{s['url']}\n{s['description']}".strip()
        for i, s in enumerate(sources, 1)
    )
