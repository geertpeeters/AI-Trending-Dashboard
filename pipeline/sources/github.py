"""GitHub Repository Search (10 req/min zonder token, 30 met token)."""

from __future__ import annotations

import os
import urllib.parse

from pipeline.util import http_get_json

API = "https://api.github.com/search/repositories"

_RATE_LIMITED = False


def reset() -> None:
    """Herstelt de circuit-breaker (per run)."""
    global _RATE_LIMITED
    _RATE_LIMITED = False


def _headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch(source_cfg: dict, category: dict) -> list[dict]:
    global _RATE_LIMITED
    if _RATE_LIMITED:
        return []
    query = str(source_cfg.get("query") or "").strip()
    if not query:
        return []
    limit = int(source_cfg.get("limit") or 15)
    url = API + "?" + urllib.parse.urlencode({"q": query, "per_page": min(limit, 30)})
    try:
        payload = http_get_json(url, headers=_headers())
    except RuntimeError as exc:
        if "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
            _RATE_LIMITED = True
            return []
        raise

    items: list[dict] = []
    for repo in payload.get("items", []):
        name = repo.get("full_name") or repo.get("name") or ""
        if not name:
            continue
        items.append({
            "title": name,
            "url": repo.get("html_url") or f"https://github.com/{name}",
            "source": "github",
            "source_id": str(repo.get("id") or name),
            "author": (repo.get("owner") or {}).get("login") or "",
            "points": 0,
            "comments": int(repo.get("open_issues_count") or 0),
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
            "language": repo.get("language") or "",
            "created_at": repo.get("created_at") or "",
            "pushed_at": repo.get("pushed_at") or "",
            "discussion_url": repo.get("html_url") or "",
            "description": (repo.get("description") or "").strip(),
            "query": query,
        })
        if len(items) >= limit:
            break
    return items
