"""OpenRouter /models — gratis modellen en recent toegevoegde modellen.

Let op: OpenRouter geeft zonder API-key geen gebruiksranglijst ("trending"),
dus `scope: trending` sorteert op recente publicatie; `scope: free` filtert op
`:free`-suffix of nul-prijs voor prompt tokens.
"""

from __future__ import annotations

from pipeline.util import http_get_json

API = "https://openrouter.ai/api/v1/models"


def _price(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_free(model: dict) -> bool:
    model_id = str(model.get("id") or "")
    pricing = model.get("pricing") or {}
    return model_id.endswith(":free") or _price(pricing.get("prompt")) == 0.0


def fetch(source_cfg: dict, category: dict) -> list[dict]:
    scope = str(source_cfg.get("scope") or "free")
    limit = int(source_cfg.get("limit") or 20)
    payload = http_get_json(API, headers={"Accept": "application/json"})
    models = payload.get("data") or []

    if scope == "free":
        models = [m for m in models if _is_free(m)]
        models.sort(key=_created_epoch, reverse=True)
    else:
        models = [m for m in models if not _is_free(m)]
        models.sort(key=_created_epoch, reverse=True)

    items: list[dict] = []
    for model in models[: limit]:
        model_id = str(model.get("id") or "")
        pricing = model.get("pricing") or {}
        prompt_price = _price(pricing.get("prompt"))
        items.append({
            "title": str(model.get("name") or model_id),
            "url": f"https://openrouter.ai/{model_id}",
            "source": "openrouter",
            "source_id": model_id,
            "author": model_id.split("/")[0] if "/" in model_id else "",
            "points": 0,
            "comments": 0,
            "stars": 0,
            "created_at": _created_iso(model),
            "discussion_url": f"https://openrouter.ai/{model_id}",
            "description": str(model.get("description") or "").strip()[:400],
            "context_length": int(model.get("context_length") or 0),
            "prompt_price": prompt_price,
            "free": _is_free(model),
            "query": scope,
        })
    return items


def _created_epoch(model: dict) -> int:
    try:
        return int(model.get("created") or 0)
    except (TypeError, ValueError):
        return 0


def _created_iso(model: dict) -> str:
    epoch = _created_epoch(model)
    if epoch <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat()
