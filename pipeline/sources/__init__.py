"""Bron-adapters. Elke adapter levert een lijst genormaliseerde items op."""

from __future__ import annotations

from pipeline.sources import arxiv, github, hn, openrouter, rss

FETCHERS = {
    "hn_search": hn.fetch,
    "github_search": github.fetch,
    "openrouter": openrouter.fetch,
    "arxiv": arxiv.fetch,
    "rss": rss.fetch,
}


def fetch_source(source_cfg: dict, category: dict) -> list[dict]:
    kind = str(source_cfg.get("type", "")).strip()
    fetcher = FETCHERS.get(kind)
    if fetcher is None:
        raise ValueError(f"Onbekende bron-type: {kind!r}")
    items = fetcher(source_cfg, category)
    for item in items:
        item.setdefault("category_id", category["id"])
        item.setdefault("topics", [])
    return items
