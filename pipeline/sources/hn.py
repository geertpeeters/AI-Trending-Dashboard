"""Hacker News via de publieke Algolia API."""

from __future__ import annotations

import urllib.parse

from pipeline.util import http_get_json

API = "https://hn.algolia.com/api/v1/search"


def fetch(source_cfg: dict, category: dict) -> list[dict]:
    query = str(source_cfg.get("query") or "").strip()
    limit = int(source_cfg.get("limit") or 15)
    params: dict[str, str] = {
        "tags": "story",
        "hitsPerPage": str(min(limit * 3, 90)),
    }
    if query:
        params["query"] = query
    else:
        # lege query = "trending nu": de HN-frontpage
        params["tags"] = "front_page"
    url = f"{API}?{urllib.parse.urlencode(params)}"
    payload = http_get_json(url, headers={"Accept": "application/json"})

    items: list[dict] = []
    for hit in payload.get("hits", []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        hn_id = str(hit.get("objectID") or "")
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hn_id}"
        items.append({
            "title": title,
            "url": link,
            "source": "hn",
            "source_id": hn_id,
            "author": hit.get("author") or "",
            "hn_points": int(hit.get("points") or 0),
            "hn_comments": int(hit.get("num_comments") or 0),
            "stars": 0,
            "created_at": (hit.get("created_at") or "").replace("+00:00", "Z"),
            "discussion_url": f"https://news.ycombinator.com/item?id={hn_id}",
            "description": "",
            "query": query,
        })
        if len(items) >= limit:
            break
    return items
