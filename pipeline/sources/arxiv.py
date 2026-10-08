"""arXiv Atom-feed (publiek, geen key nodig)."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from pipeline.util import http_get_text

API = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch(source_cfg: dict, category: dict) -> list[dict]:
    query = str(source_cfg.get("query") or "").strip()
    if not query:
        return []
    limit = int(source_cfg.get("limit") or 10)
    terms = [w for w in re.split(r"[^\w.+-]+", query) if w][:6] or [query]
    search_query = " AND ".join(f"all:{term}" for term in terms)
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(min(limit, 30)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    xml_text = http_get_text(url, headers={"Accept": "application/atom+xml"})
    root = ET.fromstring(xml_text)

    items: list[dict] = []
    for entry in root.findall("atom:entry", NS):
        title = " ".join((entry.findtext("atom:title", "", NS) or "").split())
        if not title:
            continue
        entry_id = (entry.findtext("atom:id", "", NS) or "").strip()
        authors = [a.findtext("atom:name", "", NS) for a in entry.findall("atom:author", NS)]
        items.append({
            "title": title,
            "url": entry_id,
            "source": "arxiv",
            "source_id": entry_id.rsplit("/", 1)[-1],
            "author": ", ".join(a for a in authors if a)[:120],
            "points": 0,
            "comments": 0,
            "stars": 0,
            "created_at": (entry.findtext("atom:published", "", NS) or "").replace("Z", "+00:00"),
            "discussion_url": entry_id,
            "description": " ".join((entry.findtext("atom:summary", "", NS) or "").split())[:500],
            "query": query,
        })
        if len(items) >= limit:
            break
    return items
