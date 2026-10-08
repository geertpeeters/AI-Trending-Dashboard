"""Generieke RSS 2.0 / Atom feeds."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pipeline.util import http_get_text

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(el, path: str, default: str = "") -> str:
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else default


def fetch(source_cfg: dict, category: dict) -> list[dict]:
    url = str(source_cfg.get("url") or source_cfg.get("query") or "").strip()
    if not url:
        return []
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    limit = int(source_cfg.get("limit") or 10)
    xml_text = http_get_text(url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml"})
    root = ET.fromstring(xml_text)

    items: list[dict] = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            return []
        for el in channel.findall("item")[: limit]:
            title = _text(el, "title")
            link = _text(el, "link")
            if not title and not link:
                continue
            items.append(_item(title or link, link, "rss", _text(el, "pubDate"),
                               _text(el, "description"), _text(el, "guid") or link, url))
    else:
        for el in root.findall(f"{ATOM_NS}entry")[: limit]:
            title = _text(el, f"{ATOM_NS}title")
            link = ""
            for link_el in el.findall(f"{ATOM_NS}link"):
                if link_el.get("rel") in (None, "alternate"):
                    link = link_el.get("href") or ""
                    break
            if not title and not link:
                continue
            items.append(_item(title or link, link, "rss", _text(el, f"{ATOM_NS}published") or _text(el, f"{ATOM_NS}updated"),
                               _text(el, f"{ATOM_NS}summary"), _text(el, f"{ATOM_NS}id") or link, url))
    return items


def _item(title: str, url: str, source: str, created: str, description: str, entry_id: str, feed_url: str) -> dict:
    return {
        "title": " ".join(title.split())[:200],
        "url": url,
        "source": source,
        "source_id": entry_id or url,
        "author": "",
        "points": 0,
        "comments": 0,
        "stars": 0,
        "created_at": created or "",
        "discussion_url": url,
        "description": " ".join(description.split())[:400],
        "query": feed_url,
    }
