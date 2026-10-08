"""Handmatige referenties (data/references.json): zelf toegevoegde links.

De server schrijft dit bestand bij; de pipeline injecteert de referenties bij
elke run als items met `source: manual` (nooit door AI gedropt).
"""

from __future__ import annotations

import os
import urllib.parse

from pipeline.util import atomic_write_json, now_iso, read_json, slugify

MAX_URL = 2000
MAX_TITLE = 200
MAX_NOTE = 500


def references_path(data_dir: str) -> str:
    return os.path.join(data_dir, "references.json")


def load_references(data_dir: str) -> list[dict]:
    payload = read_json(references_path(data_dir), default={}) or {}
    refs = payload.get("references") if isinstance(payload, dict) else None
    if not isinstance(refs, list):
        return []
    return [r for r in refs if isinstance(r, dict) and r.get("url")]


def save_references(data_dir: str, refs: list[dict]) -> None:
    atomic_write_json(references_path(data_dir), {
        "updated_at": now_iso(),
        "references": refs,
    })


def normalize_url(raw: str) -> tuple[str, str]:
    """Geeft (geschonde_url, foutmelding)."""
    url = str(raw or "").strip()
    if not url:
        return "", "url ontbreekt"
    if any(ch.isspace() for ch in url):
        return "", "url mag geen spaties bevatten"
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "", "ongeldige url"
    if "." not in parsed.netloc and parsed.netloc != "localhost":
        return "", "ongeldig domein (verwacht bijv. https://voorbeeld.nl/...)"
    if len(url) > MAX_URL:
        return "", f"url te lang (max {MAX_URL})"
    return url, ""


def _make_id(title: str, url: str, existing: set[str]) -> str:
    base = slugify(title or urllib.parse.urlparse(url).netloc + urllib.parse.urlparse(url).path)
    ref_id = base
    counter = 2
    while ref_id in existing:
        ref_id = f"{base}-{counter}"
        counter += 1
    return ref_id


def add_reference(data_dir: str, *, url: str, title: str = "", note: str = "",
                  category: str = "", tags: list[str] | None = None) -> tuple[dict | None, str]:
    """Voegt een referentie toe. Geeft (ref, "") of (None, fout)."""
    url, error = normalize_url(url)
    if error:
        return None, error
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    note = " ".join(str(note or "").split())[:MAX_NOTE]

    refs = load_references(data_dir)
    for existing_ref in refs:
        if existing_ref.get("url") == url:
            return None, f"bestaat al als '{existing_ref.get('id')}'"

    ref = {
        "id": _make_id(title, url, {str(r.get("id")) for r in refs}),
        "url": url,
        "title": title,
        "category": str(category or "").strip(),
        "note": note,
        "tags": [str(t).strip().lower()[:24] for t in (tags or []) if str(t).strip()][:8],
        "added_at": now_iso(),
    }
    refs.append(ref)
    save_references(data_dir, refs)
    return ref, ""


def remove_reference(data_dir: str, ref_id: str) -> bool:
    refs = load_references(data_dir)
    remaining = [r for r in refs if r.get("id") != ref_id]
    if len(remaining) == len(refs):
        return False
    save_references(data_dir, remaining)
    return True
