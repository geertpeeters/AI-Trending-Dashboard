"""Laden, valideren en AI-veilig bijwerken van config/categories.yaml."""

from __future__ import annotations

import copy
import json
import os
import shutil

import yaml

from pipeline.util import now_iso, read_json, slugify

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(ROOT, "config", "categories.yaml")
CONFIG_BACKUP_PATH = os.path.join(ROOT, "config", "categories.prev.yaml")
CHANGELOG_PATH = os.path.join(ROOT, "data", "config-changelog.jsonl")

DEFAULTS = {
    "refresh": "daily",
    "max_items_per_category": 20,
}


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict) or not raw.get("categories"):
        raise ValueError(f"{path} bevat geen categorieen")

    defaults = dict(DEFAULTS)
    defaults.update(raw.get("defaults") or {})
    raw["defaults"] = defaults
    raw.setdefault("version", 1)
    raw.setdefault("ai_review", {"enabled": True, "min_relevance": 5})

    seen: set[str] = set()
    for cat in raw["categories"]:
        if not isinstance(cat, dict) or not cat.get("id"):
            raise ValueError("elke categorie heeft een 'id' nodig")
        if cat["id"] in seen:
            raise ValueError(f"dubbele categorie-id: {cat['id']}")
        seen.add(cat["id"])
        cat.setdefault("sources", [])
        cat.setdefault("topics", [])
        cat.setdefault("description", {"en": "", "nl": ""})
        cat["ai_review"] = {**(raw["ai_review"]), **(cat.get("ai_review") or {})}
        for src in cat["sources"]:
            src.setdefault("type", "hn_search")
            src.setdefault("query", "")
            src.setdefault("limit", 15)
    return raw


def localized(value, lang: str, fallback_lang: str = "en") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get(lang) or value.get(fallback_lang) or next(iter(value.values()), ""))
    return str(value or "")


def category_by_id(config: dict, category_id: str) -> dict | None:
    for cat in config["categories"]:
        if cat["id"] == category_id:
            return cat
    return None


def all_topic_keywords(config: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cat in config["categories"]:
        for topic in cat.get("topics", []):
            out.setdefault(cat["id"], [])
            out[cat["id"]].extend([str(k).lower() for k in topic.get("keywords", [])])
    return out


def _append_changelog(entry: dict) -> None:
    os.makedirs(os.path.dirname(CHANGELOG_PATH), exist_ok=True)
    with open(CHANGELOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _commit(draft: dict, config_path: str, actor: str, action: str, details: dict) -> None:
    """Schrijft de config atomair weg met backup + changelog-regel."""
    backup_path = config_path + ".prev"
    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)

    payload = {
        "version": draft.get("version", 1),
        "defaults": draft.get("defaults"),
        "ai_review": draft.get("ai_review"),
        "categories": draft["categories"],
    }
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False, width=100)

    _append_changelog({
        "ts": now_iso(),
        "actor": actor,
        "action": action,
        "backup": os.path.relpath(backup_path, ROOT),
        "config": os.path.relpath(config_path, ROOT),
        **details,
    })


def propose_topics(config: dict, proposals: list[dict], config_path: str = CONFIG_PATH,
                   actor: str = "ai") -> list[dict]:
    """Voegt AI-voorgestelde (of handmatige) topics toe aan categories.yaml.

    Veilig: bestaande ids blijven, dubbele ids worden overgeslagen, er wordt
    altijd een backup gemaakt en elke wijziging komt in de changelog.
    """
    applied: list[dict] = []
    if not proposals:
        return applied

    max_new = int(config.get("ai_review", {}).get("max_new_topics_per_run", 5))

    draft = copy.deepcopy(config)
    draft_by_id = {cat["id"]: cat for cat in draft["categories"]}

    for proposal in proposals:
        if len(applied) >= max_new:
            break
        if not isinstance(proposal, dict):
            continue
        target = str(proposal.get("category_id") or proposal.get("category") or "").strip()
        title_en = str(proposal.get("title_en") or proposal.get("title") or "").strip()
        title_nl = str(proposal.get("title_nl") or title_en).strip()
        if target not in draft_by_id or not title_en:
            continue
        topic_id = slugify(str(proposal.get("id") or title_en))
        existing = {t.get("id") for t in draft_by_id[target].get("topics", [])}
        if topic_id in existing:
            continue
        keywords = proposal.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        keywords = [str(k).lower() for k in keywords][:12]
        keywords.extend([w for w in slugify(title_en).split("-") if len(w) > 3 and w not in keywords])
        draft_by_id[target]["topics"].append({
            "id": topic_id,
            "title": {"en": title_en, "nl": title_nl},
            "query": str(proposal.get("query") or title_en),
            "keywords": keywords[:12],
            "ai_generated": True,
        })
        applied.append({
            "category_id": target,
            "topic_id": topic_id,
            "title": {"en": title_en, "nl": title_nl},
            "reason": str(proposal.get("reason") or "")[:400],
            "keywords": keywords[:12],
        })

    if not applied:
        return applied

    _commit(draft, config_path, actor, "add_topics", {"topics": applied})
    return applied


def add_topic_web(config: dict, payload: dict, config_path: str = CONFIG_PATH) -> tuple[dict | None, str]:
    """Handmatig topic toevoegen via de website (changelog-actor: web)."""
    category_id = str(payload.get("category_id") or payload.get("category") or "").strip()
    title_en = str(payload.get("title_en") or "").strip()
    title_nl = str(payload.get("title_nl") or title_en).strip()
    if not category_id or category_id not in {c["id"] for c in config["categories"]}:
        return None, "onbekende categorie"
    if not title_en:
        return None, "titel (en) ontbreekt"
    topic_id = slugify(str(payload.get("id") or title_en))
    existing = category_by_id(config, category_id) or {}
    if topic_id in {t.get("id") for t in existing.get("topics", [])}:
        return None, f"topic '{topic_id}' bestaat al"
    proposal = {
        "category_id": category_id,
        "title_en": title_en[:80],
        "title_nl": title_nl[:80],
        "query": str(payload.get("query") or title_en)[:120],
        "keywords": payload.get("keywords") or [],
        "reason": str(payload.get("reason") or "")[:400] or "handmatig toegevoegd via de website",
    }
    applied = propose_topics(config, [proposal], config_path, actor="web")
    if not applied:
        return None, "topic kon niet worden toegevoegd (controleer de velden)"
    return applied[0], ""


def remove_topic(config: dict, category_id: str, topic_id: str,
                 config_path: str = CONFIG_PATH) -> tuple[dict | None, str]:
    """Verwijdert een topic uit een categorie (backup + changelog, actor web)."""
    cat = category_by_id(config, category_id)
    if not cat:
        return None, "onbekende categorie"
    topics = cat.get("topics", [])
    removed = next((t for t in topics if t.get("id") == topic_id), None)
    if removed is None:
        return None, "topic niet gevonden"

    draft = copy.deepcopy(config)
    draft_cat = category_by_id(draft, category_id) or {}
    draft_cat["topics"] = [t for t in draft_cat.get("topics", []) if t.get("id") != topic_id]
    _commit(draft, config_path, "web", "remove_topic",
            {"category_id": category_id, "topic": removed})
    return removed, ""


def _clean_limit(value) -> int:
    try:
        limit = int(value or 15)
    except (TypeError, ValueError):
        limit = 15
    return max(1, min(limit, 50))


def add_source(config: dict, category_id: str, source: dict,
               config_path: str = CONFIG_PATH) -> tuple[dict | None, str]:
    """Voegt een bron toe aan een categorie (backup + changelog, actor web)."""
    cat = category_by_id(config, category_id)
    if not cat:
        return None, "onbekende categorie"
    kind = str(source.get("type") or "").strip()
    query = str(source.get("query") or source.get("url") or "").strip()
    if not kind:
        return None, "type ontbreekt"
    if not query:
        return None, "query of url ontbreekt"
    if len(query) > 300:
        return None, "query/url te lang"
    entry = {"type": kind, "limit": _clean_limit(source.get("limit"))}
    if kind == "rss":
        entry["url"] = query
    else:
        entry["query"] = query
    for existing_src in cat.get("sources", []):
        same_query = str(existing_src.get("query") or existing_src.get("url") or "")
        if str(existing_src.get("type")) == kind and same_query == query:
            return None, "bron bestaat al in deze categorie"

    draft = copy.deepcopy(config)
    (category_by_id(draft, category_id) or {}).setdefault("sources", []).append(entry)
    _commit(draft, config_path, "web", "add_source", {"category_id": category_id, "source": entry})
    return entry, ""


def remove_source(config: dict, category_id: str, match: dict,
                  config_path: str = CONFIG_PATH) -> tuple[dict | None, str]:
    """Verwijdert een bron op index of op (type, query/url)."""
    cat = category_by_id(config, category_id)
    if not cat:
        return None, "onbekende categorie"
    sources = cat.get("sources", [])
    index: int | None = None
    if match.get("index") is not None and str(match.get("index")).strip() != "":
        try:
            index = int(match.get("index"))
        except (TypeError, ValueError):
            return None, "ongeldige index"
        if not 0 <= index < len(sources):
            return None, "index buiten bereik"
    else:
        kind = str(match.get("type") or "").strip()
        query = str(match.get("query") or match.get("url") or "").strip()
        for i, candidate in enumerate(sources):
            same_query = str(candidate.get("query") or candidate.get("url") or "")
            if str(candidate.get("type")) == kind and same_query == query:
                index = i
                break
        if index is None:
            return None, "bron niet gevonden"
    removed = sources[index]

    draft = copy.deepcopy(config)
    draft_cat = category_by_id(draft, category_id) or {}
    draft_cat.setdefault("sources", []).pop(index)
    _commit(draft, config_path, "web", "remove_source", {"category_id": category_id, "source": removed})
    return removed, ""


def read_changelog(limit: int = 50) -> list[dict]:
    entries = read_json(CHANGELOG_PATH, default=None)
    if isinstance(entries, list):  # defensief; het bestand is JSONL
        return entries[-limit:]
    out: list[dict] = []
    try:
        with open(CHANGELOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        pass
    return out[-limit:]
