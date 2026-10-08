"""LLM-client (OpenAI-compatible) voor relevance, summaries en config-voorstellen.

Configuratie (in volgorde van voorkeur):
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL          # expliciete override
    OMNIROUTE_URL / OMNIROUTE_API_KEY / OMNIROUTE_MODEL   # eigen omniroute-gateway
    https://api.openai.com/v1 + LLM_API_KEY          # laatste redmiddel

Als geen model is opgegeven en er staat een eigen gateway in de env, wordt het
eerste bruikbare model uit `GET {base}/models` gekozen en gelogd.
Faalt de AI-run, dan logt de pipeline de fout en valt hij terug op heuristieken
zodat de cron nooit breekt.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from pipeline.util import clamp, log

DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
# voorkeursvolgorde bij auto-keuze: goedkope, snelle modellen
# auto/* = de eigen router van omniroute (stabiel); ddgw/* = DuckDuckGo-gateway
# (rate-limited, VQD-token) en beeld-/zoekproviders overslaan bij automatische keuze.
_MODEL_PREFERENCE = (
    "auto/fast", "auto/cheap", "auto/best-fast", "auto/chat", "auto/best-chat",
    "gpt-4o-mini", "haiku", "flash", "mini", "small", "8b", "7b",
)
_SKIP_MODEL_PREFIXES = ("ddgw/", "aihorde/", "veo", "felo/", "pepper/")


def _pick_model(ids: list[str]) -> str | None:
    lowered = [i.lower() for i in ids]
    for needle in _MODEL_PREFERENCE:
        for original, low in zip(ids, lowered):
            if needle in low and not low.startswith(_SKIP_MODEL_PREFIXES):
                return original
    for original, low in zip(ids, lowered):  # laatste redmiddel: eerste niet-overslagen id
        if not low.startswith(_SKIP_MODEL_PREFIXES):
            return original
    return ids[0] if ids else None


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: int = 180, max_retries: int = 2):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL")
                         or os.environ.get("OMNIROUTE_URL") or DEFAULT_BASE).rstrip("/")
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OMNIROUTE_API_KEY") or ""
        self.model = (model or os.environ.get("LLM_MODEL")
                      or os.environ.get("OMNIROUTE_MODEL") or "").strip()
        self.timeout = timeout
        self.max_retries = max_retries
        if not self.model and self.base_url != DEFAULT_BASE:
            self.model = self._auto_pick_model()

    def _auto_pick_model(self) -> str:
        """Kiest een model uit {base}/models (eigen gateway, OpenAI-compatibel)."""
        url = f"{self.base_url}/models"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            ids = [str(m.get("id")) for m in (payload.get("data") or [])
                   if isinstance(m, dict) and m.get("id")]
            chosen = _pick_model(ids)
        except Exception as exc:  # noqa: BLE001 - auto-keuze is optioneel
            log(f"model-keuze faalde ({exc}), val terug op {DEFAULT_MODEL}")
            return DEFAULT_MODEL
        if not chosen:
            return DEFAULT_MODEL
        log(f"model automatisch gekozen: {chosen}")
        return chosen

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def chat(self, messages: list[dict], *, temperature: float = 0.2,
             max_tokens: int = 2200) -> str:
        if not self.configured:
            raise LLMError("LLM_API_KEY/OMNIROUTE_API_KEY ontbreekt (zie .env.example)")
        url = f"{self.base_url}/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        last_err: Exception | None = None
        rate_limited = False
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("leeg antwoord van de LLM")
                return content
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:400]
                last_err = LLMError(f"HTTP {exc.code}: {body}")
                rate_limited = exc.code == 429
                if exc.code in (400, 401, 403, 404, 422):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError,
                    json.JSONDecodeError) as exc:
                last_err = exc
                rate_limited = False
            if attempt < self.max_retries:
                # rate-limits (bv. DDG-gateway) hebben rust nodig, geen snelle herhaling
                delay = 15.0 * (attempt + 1) if rate_limited else 2.0 * (attempt + 1)
                time.sleep(delay)
        raise LLMError(f"LLM-aanroep mislukt: {last_err}")

    def json_call(self, messages: list[dict], *, temperature: float = 0.2,
                  max_tokens: int = 2200):
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return extract_json(text)


def extract_json(text: str):
    """Haalt het eerste geldige JSON-object/-array uit een model-antwoord."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
    for candidate in (cleaned, text):
        decoder = json.JSONDecoder()
        for index, char in enumerate(candidate):
            if char in "[{":
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                    if isinstance(value, (list, dict)):
                        return value
                except json.JSONDecodeError:
                    continue
    raise LLMError(f"geen JSON in model-antwoord: {text[:200]!r}")


SYSTEM_REVIEW = (
    "You are the editorial engine of an AI industry trending dashboard. "
    "Be strict and practical. Always answer with a single JSON array, no prose."
)

_KINDS = ("tool", "model", "paper", "library", "framework", "dataset", "article", "other")

PROMPT_ITEM_REVIEW = """Rate each item for a daily AI-industry trending dashboard.

Category: {category_id} ({category_title})
Topic keywords for this category: {keywords}

For every item return an object:
{{"slug": "...", "relevance": 0-10, "verdict": "keep"|"drop",
  "tags": ["max 4 short lowercase tags"],
  "kind": "tool|model|paper|library|framework|dataset|article|other",
  "purpose_en": "max 160 chars, one line: what is this FOR",
  "purpose_nl": "max 160 chars, one line (Dutch): waar is dit VOOR",
  "summary_en": "600-800 chars, 3-6 sentences",
  "summary_nl": "600-800 chars, 3-6 sentences"}}

Rules:
- relevance 0-10: 10 = must-read trend, 5 = relevant, <5 = noise for this category.
- verdict "drop" when relevance < {min_relevance} or off-topic.
- summaries: 3-6 sentences with a fixed shape: (1) what the thing is, (2) what it is
  for and who uses it, (3) why it matters now. Concrete facts (numbers, license,
  maturity, model/provider) when known from the data. Factual, no marketing,
  no invented details.
- purpose_* is a single standalone line, not a repeat of the summary's first sentence:
  name the job it does or the problem it solves (e.g. "Turns any API into a
  tool-calling agent", "Free inference API with a 1M-token context window").
- kind: pick from the list based on source and title; "article" for blogs/posts,
  "paper" for arXiv, "model" for a model release/weight page, "tool"/"library"/
  "framework" for software you install or call.
- purpose_nl may be a translation of purpose_en. Keep slugs exactly as given.

Items:
{items}
"""

SYSTEM_TOPIC = (
    "You review the topic structure of an AI trending dashboard. "
    "Answer with a single JSON array only."
)

PROMPT_TOPIC_REVIEW = """Score every hosted topic (0-10) for audience relevance this week.

Return: [{{"category_id":"...","topic_id":"...","score":0-10,
           "action":"keep"|"expand"|"drop","note":"max 160 chars"}}]

Topics:
{topics}
"""

SYSTEM_TRENDS = (
    "You detect emerging AI-industry trends from raw item data. "
    "Answer with a single JSON object only."
)

PROMPT_TRENDS = """From the items below, detect at most {max_new} NEW recurring trends that are
not yet covered by an existing topic. Only propose trends that appear in several
items or clearly matter to builders.

Existing topic ids (do not duplicate): {existing}

Return:
{{"proposals": [{{"category_id":"...","title_en":"...","title_nl":"...",
                 "query":"short search query","keywords":["..."],
                 "reason":"max 200 chars"}}]}}

Items:
{items}
"""


def review_items(llm: LLMClient, items: list[dict], *, category: dict,
                 min_relevance: int, keywords: list[str]) -> dict:
    """AI-review A: relevance 0-10 + keep/drop + tags + kind + purpose + summaries."""
    if not items:
        return {}
    payload = [{
        "slug": it["slug"],
        "title": it.get("title", "")[:160],
        "description": (it.get("description") or "")[:300],
        "source": it.get("source"),
        "stars": it.get("stars", 0),
        "hn_points": it.get("hn_points", 0),
        "url": it.get("url", ""),
    } for it in items]

    prompt = PROMPT_ITEM_REVIEW.format(
        category_id=category["id"],
        category_title=json.dumps(category.get("title", {}), ensure_ascii=False),
        keywords=", ".join(keywords[:24]) or "-",
        min_relevance=min_relevance,
        items=json.dumps(payload, ensure_ascii=False),
    )
    raw = llm.json_call([
        {"role": "system", "content": SYSTEM_REVIEW},
        {"role": "user", "content": prompt},
    ], max_tokens=7000)

    results: dict[str, dict] = {}
    entries = raw if isinstance(raw, list) else raw.get("items") or raw.get("results") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "")
        if not slug:
            continue
        try:
            relevance = int(clamp(float(entry.get("relevance", 0)), 0, 10))
        except (TypeError, ValueError):
            relevance = 0
        verdict = str(entry.get("verdict") or ("keep" if relevance >= min_relevance else "drop"))
        tags = entry.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        kind = str(entry.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            kind = ""
        purpose_en = str(entry.get("purpose_en") or "").strip()[:160]
        purpose_nl = str(entry.get("purpose_nl") or purpose_en).strip()[:160]
        results[slug] = {
            "relevance": relevance,
            "verdict": "keep" if verdict == "keep" else "drop",
            "tags": [str(t).lower()[:24] for t in tags][:4],
            "kind": kind,
            "purpose": {"en": purpose_en, "nl": purpose_nl},
            "summary": {
                "en": str(entry.get("summary_en") or "").strip()[:900],
                "nl": str(entry.get("summary_nl") or entry.get("summary_en") or "").strip()[:900],
            },
        }
    return results


def review_topics(llm: LLMClient, config: dict) -> list[dict]:
    """AI-review A (topics): keep/expand/drop per gehost topic."""
    topics = []
    for cat in config["categories"]:
        for topic in cat.get("topics", []):
            topics.append({
                "category_id": cat["id"],
                "topic_id": topic["id"],
                "title": topic.get("title", {}),
                "keywords": topic.get("keywords", [])[:8],
            })
    if not topics:
        return []
    raw = llm.json_call([
        {"role": "system", "content": SYSTEM_TOPIC},
        {"role": "user", "content": PROMPT_TOPIC_REVIEW.format(
            topics=json.dumps(topics, ensure_ascii=False))},
    ], max_tokens=1800)
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict) and entry.get("topic_id"):
            entry["score"] = int(clamp(float(entry.get("score", 0)), 0, 10))
            entry["action"] = str(entry.get("action") or "keep")
            out.append(entry)
    return out


def detect_trends(llm: LLMClient, config: dict, items: list[dict], *,
                  max_new: int = 5) -> list[dict]:
    """AI-review B: nieuwe trends -> voorstellen voor categories.yaml."""
    if not items:
        return []
    existing = sorted({
        topic["id"]
        for cat in config["categories"]
        for topic in cat.get("topics", [])
    })
    payload = [{
        "category_id": it.get("category"),
        "title": it.get("title", "")[:140],
        "summary": (it.get("summary") or {}).get("en", "")[:400],
        "purpose": (it.get("purpose") or {}).get("en", ""),
        "tags": it.get("tags", []),
        "trend": it.get("trend"),
        "source": it.get("source"),
    } for it in items]

    raw = llm.json_call([
        {"role": "system", "content": SYSTEM_TRENDS},
        {"role": "user", "content": PROMPT_TRENDS.format(
            max_new=max_new,
            existing=", ".join(existing),
            items=json.dumps(payload, ensure_ascii=False))},
    ], temperature=0.3, max_tokens=1600)

    proposals = raw.get("proposals") if isinstance(raw, dict) else raw
    if not isinstance(proposals, list):
        return []
    valid_ids = {cat["id"] for cat in config["categories"]}
    cleaned = []
    for proposal in proposals[:max_new]:
        if not isinstance(proposal, dict):
            continue
        if str(proposal.get("category_id") or "") not in valid_ids:
            continue
        if not proposal.get("title_en"):
            continue
        cleaned.append(proposal)
    return cleaned


def run_full_review(llm: LLMClient, config: dict, category_items: dict[str, list[dict]],
                    keywords_by_category: dict[str, list[str]], *,
                    on_batch=None) -> dict:
    """Voert review A (items + topics) en review B (nieuwe trends) uit.

    Elke stap faalt onafhankelijk: een fout in een stap crasht de run niet.
    `on_batch(reviews)` wordt na elke geslaagde item-batch aangeroepen (tussenopslag),
    zodat een crash halverwege geen AI-werk verliest.
    """
    result = {
        "item_reviews": {},
        "topic_review": [],
        "trend_proposals": [],
        "errors": [],
    }

    batch_size = int(config.get("ai_review", {}).get("batch_size", 8))
    pending = [(cid, category_items.get(cid) or []) for cid in
               (c["id"] for c in config["categories"])]
    pending = [(cid, items) for cid, items in pending if items]
    total_batches = sum((len(items) + batch_size - 1) // batch_size for _, items in pending)
    done = 0
    consecutive_failures = 0  # adaptieve backoff bij gateway-pieken

    for category in config["categories"]:
        cid = category["id"]
        items = category_items.get(cid) or []
        if not items:
            continue
        min_rel = int(category.get("ai_review", {}).get("min_relevance", 5))
        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            done += 1
            try:
                reviews = review_items(llm, batch, category=category,
                                       min_relevance=min_rel,
                                       keywords=keywords_by_category.get(cid, []))
                result["item_reviews"].update(reviews)
                consecutive_failures = 0
                log(f"  AI-review batch {done}/{total_batches} ({cid}) ok, "
                    f"{len(result['item_reviews'])} items")
                if on_batch:
                    try:
                        on_batch(reviews)
                    except Exception as exc:  # noqa: BLE001 - tussenopslag is optioneel
                        log(f"  tussenopslag faalde: {exc}")
                        result["errors"].append(f"persist:{exc}")
            except Exception as exc:  # noqa: BLE001 - cron mag niet breken
                consecutive_failures += 1
                log(f"AI item-review faalde voor {cid} (batch {done}/{total_batches}): {exc}")
                result["errors"].append(f"items:{cid}:{exc}")
                if consecutive_failures >= 2:
                    pause = min(30 * consecutive_failures, 120)
                    log(f"  gateway-piek: {consecutive_failures} mislukte batches -> "
                        f"{pause}s pauze")
                    time.sleep(pause)
            time.sleep(1.0)  # rustpauze: gateway niet vollopen

    try:
        result["topic_review"] = review_topics(llm, config)
    except Exception as exc:  # noqa: BLE001
        log(f"AI topic-review faalde: {exc}")
        result["errors"].append(f"topics:{exc}")

    try:
        candidates = sorted(
            (it for items in category_items.values() for it in items),
            key=lambda i: i.get("trend", 0), reverse=True)[:35]
        result["trend_proposals"] = detect_trends(
            llm, config, candidates,
            max_new=int(config.get("ai_review", {}).get("max_new_topics_per_run", 5)))
    except Exception as exc:  # noqa: BLE001
        log(f"AI trend-detectie faalde: {exc}")
        result["errors"].append(f"trends:{exc}")

    return result
