"""Cron-entrypoint: crawl -> score -> AI-review -> render.

Gebruik:
    python3 pipeline/crawl.py            # volledige run (AI als LLM_API_KEY staat)
    python3 pipeline/crawl.py --no-ai    # heuristic fallback
    python3 pipeline/crawl.py --categories models,top-20-free-models
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from pipeline import ai as ai_mod
from pipeline import scoring
from pipeline.config_loader import all_topic_keywords, load_config, propose_topics
from pipeline.references import load_references
from pipeline.render import render_site
from pipeline.sources import fetch_source
from pipeline.sources import github as github_source
from pipeline.util import atomic_write_json, ensure_dir, log, now_iso, read_json, slugify

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_SITE_DIR = os.path.join(ROOT, "site")
STALE_AFTER_DAYS = 30
AI_MAX_ITEMS_PER_RUN = 40


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI trending dashboard pipeline")
    parser.add_argument("--no-ai", action="store_true", help="sla de LLM-stap over")
    parser.add_argument("--resummarize", action="store_true",
                        help="beoordeel alle items opnieuw (summary/purpose/kind), éénmalige backfill")
    parser.add_argument("--config", default=os.path.join(ROOT, "config", "categories.yaml"))
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--site-dir", default=DEFAULT_SITE_DIR)
    parser.add_argument("--categories", default="", help="komma-gescheiden selectie")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def load_items(data_dir: str) -> dict:
    payload = read_json(os.path.join(data_dir, "items.json"), default={}) or {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        items = {}
    return items


def item_slug(raw: dict, url_to_slug: dict) -> str:
    url = (raw.get("url") or "").strip()
    if url and url in url_to_slug:
        return url_to_slug[url]
    base = slugify(raw.get("title") or url or "item")
    slug = base
    counter = 2
    taken = set(url_to_slug.values())
    while slug in taken and url_to_slug.get(url) != slug:
        slug = f"{base}-{counter}"
        counter += 1
    if url:
        url_to_slug[url] = slug
    return slug


def merge_item(old: dict | None, new: dict, today: str) -> dict:
    out = dict(old or {})
    for key, value in new.items():
        if key in ("stars", "hn_points", "hn_comments", "forks", "comments", "points"):
            try:
                incoming = int(value or 0)
            except (TypeError, ValueError):
                incoming = 0
            if incoming > int(out.get(key, 0) or 0):
                out[key] = incoming
            elif key not in out:
                out[key] = incoming
        elif value not in (None, "", [], {}):
            out[key] = value
        else:
            out.setdefault(key, value)

    out.setdefault("stars", 0)
    out.setdefault("hn_points", 0)
    out.setdefault("hn_comments", 0)
    out.setdefault("relevance", 0)
    out.setdefault("tags", [])
    out.setdefault("summary", {"en": "", "nl": ""})
    if not isinstance(out.get("summary"), dict):
        out["summary"] = {"en": str(out["summary"]), "nl": str(out["summary"])}
    out["summary"].setdefault("en", "")
    out["summary"].setdefault("nl", "")
    out.setdefault("purpose", {"en": "", "nl": ""})
    if not isinstance(out.get("purpose"), dict):
        out["purpose"] = {"en": str(out["purpose"]), "nl": str(out["purpose"])}
    out["purpose"].setdefault("en", "")
    out["purpose"].setdefault("nl", "")
    out.setdefault("kind", "")

    categories = list(dict.fromkeys((out.get("categories") or []) + [new.get("category_id")]))
    out["categories"] = [c for c in categories if c]
    out["category"] = out["categories"][0] if out["categories"] else ""
    out["first_seen"] = out.get("first_seen") or today
    out["last_seen"] = today
    out.setdefault("dropped", False)
    return out


def assign_topics(item: dict, keywords_by_category: dict[str, list[str]]) -> list[str]:
    haystack = " ".join([
        item.get("title", ""),
        item.get("description", ""),
        " ".join(item.get("tags") or []),
        " ".join(item.get("summary", {}).values()) if isinstance(item.get("summary"), dict) else "",
    ]).lower()
    matched: list[str] = []
    for cid in item.get("categories") or []:
        for keyword in keywords_by_category.get(cid, []):
            if keyword in haystack and keyword not in matched:
                matched.append(keyword)
    return matched[:8]


def needs_review(item: dict, now: datetime) -> bool:
    if item.get("dropped"):
        return True
    # zelfherstel: geen purpose = ooit beoordeeld maar AI faalde/oude prompt ->
    # elk volgend run opnieuw proberen (ook als het review-venster nog niet verstreken is)
    if not (item.get("purpose") or {}).get("en"):
        return True
    reviewed_at = item.get("ai_reviewed_at")
    if not reviewed_at:
        return True
    try:
        reviewed = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - reviewed > timedelta(days=7)


def crawl(config: dict, data_dir: str, selected: list[str] | None) -> tuple[dict, dict[str, list[dict]], list[str]]:
    """Haalt items op per categorie. Geeft (items, per_category, fouten)."""
    errors: list[str] = []
    per_category: dict[str, list[dict]] = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    merged: dict[str, dict] = load_items(data_dir)
    url_to_slug: dict[str, str] = {}
    for slug, item in merged.items():
        if item.get("url"):
            url_to_slug[item["url"]] = slug

    for category in config["categories"]:
        if selected and category["id"] not in selected:
            continue
        collected: list[dict] = []
        for source_cfg in category.get("sources", []):
            try:
                found = fetch_source(source_cfg, category)
                log(f"  {category['id']}/{source_cfg.get('type')}: {len(found)} items")
                collected.extend(found)
            except Exception as exc:  # noqa: BLE001 - bron-fouten mogen de run niet breken
                message = f"{category['id']}/{source_cfg.get('type')}: {exc}"
                log(f"  BRON-FOUT {message}")
                errors.append(message)

        seen_urls: set[str] = set()
        category_items: list[dict] = []
        for raw in collected:
            url = (raw.get("url") or "").strip()
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            slug = item_slug(raw, url_to_slug)
            record = dict(raw)
            record["category_id"] = category["id"]
            record["slug"] = slug
            merged[slug] = merge_item(merged.get(slug), record, today)
            category_items.append(merged[slug])
        per_category[category["id"]] = category_items

    manual = inject_references(merged, per_category, config, data_dir, today, url_to_slug)
    if manual:
        log(f"  {manual} handmatige referenties ingevoegd")

    return merged, per_category, errors


def inject_references(merged: dict, per_category: dict[str, list[dict]], config: dict,
                      data_dir: str, today: str, url_to_slug: dict[str, str]) -> int:
    """Voegt data/references.json bij elke run toe als `source: manual` items.

    Handmatig toegevoegde links zijn van de gebruiker: die worden nooit door
    de AI gedropt en zijn niet onderhevig aan pruning (last_seen elke run bijgewerkt).
    """
    known = {cat["id"] for cat in config["categories"]}
    added = 0
    refs = load_references(data_dir)
    ref_urls = {str(r.get("url") or "") for r in refs}

    # referenties die verwijderd zijn: manual-items verdwijnen, gecrawelde
    # items houden hun eigen bron (vervallen dan vanzelf via prune_stale)
    for slug in [s for s, it in merged.items() if it.get("manual") and str(it.get("url") or "") not in ref_urls]:
        item = merged.pop(slug)
        if item.get("source") != "manual":
            item["manual"] = False
            item.pop("manual_note", None)
            merged[slug] = item

    for ref in refs:
        category_id = str(ref.get("category") or "").strip()
        if category_id not in known:
            log(f"  REFERENCE-FOUT {ref.get('id')}: onbekende categorie {category_id!r}")
            continue
        note = str(ref.get("note") or "")
        record = {
            "url": ref.get("url") or "",
            "title": str(ref.get("title") or ref.get("url") or ""),
            "source": "manual",
            "manual": True,
            "category_id": category_id,
            "manual_note": note,
        }
        slug = item_slug(record, url_to_slug)
        record["slug"] = slug
        existing = merged.get(slug)
        if existing and existing.get("title") and record["title"] != existing["title"]:
            record.pop("title")  # bewaar de gecrawelde titel bij een dubbele URL
        if not (existing and existing.get("description")) and note:
            record["description"] = note
        merged[slug] = merge_item(existing, record, today)
        merged[slug]["dropped"] = False  # gebruiker voegde dit expliciet toe
        per_category.setdefault(category_id, []).append(merged[slug])
        added += 1
    return added


def apply_ai_review(reviews: dict, items: dict, today: str) -> tuple[int, int]:
    kept = dropped = 0
    for slug, review in reviews.items():
        item = items.get(slug)
        if not item:
            continue
        item["relevance"] = int(review.get("relevance") or item.get("relevance") or 0)
        if review.get("tags"):
            item["tags"] = review["tags"]
        summary = review.get("summary") or {}
        if summary.get("en"):
            item["summary"]["en"] = summary["en"]
        if summary.get("nl"):
            item["summary"]["nl"] = summary["nl"]
        purpose = review.get("purpose") or {}
        if purpose.get("en"):
            item.setdefault("purpose", {"en": "", "nl": ""})
            item["purpose"]["en"] = purpose["en"]
            item["purpose"]["nl"] = purpose.get("nl") or purpose["en"]
        if review.get("kind"):
            item["kind"] = review["kind"]
        item["ai_verdict"] = review.get("verdict", "keep")
        item["ai_reviewed_at"] = now_iso()
        if item.get("manual"):
            item["ai_verdict"] = "keep"  # handmatige referenties nooit droppen
            item["dropped"] = False
            kept += 1
        elif item["ai_verdict"] == "drop":
            item["dropped"] = True
            dropped += 1
        else:
            item["dropped"] = False
            kept += 1
    return kept, dropped


def prune_stale(items: dict) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS)).strftime("%Y-%m-%d")
    stale = [slug for slug, item in items.items() if (item.get("last_seen") or "1970-01-01") < cutoff]
    for slug in stale:
        items.pop(slug, None)
    return len(stale)


def _pid_alive(pid: int) -> bool:
    """True als pid bestaat én geen zombie is (zombies blokkeren de lock niet)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
            return fh.read().rsplit(") ", 1)[-1].split(" ", 1)[0] != "Z"
    except (OSError, IndexError):  # geen /proc (niet-linux): aanname levend
        return True


def acquire_lock(data_dir: str) -> str | None:
    """Exclusieve run-lock (cron + web-trigger vechten niet om dezelfde data)."""
    path = os.path.join(data_dir, ".crawl.lock")
    ensure_dir(data_dir)
    for attempt in (0, 1):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "started": now_iso()}, fh)
            return path
        except FileExistsError:
            holder: dict = {}
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    holder = json.load(fh) or {}
            except (OSError, ValueError):
                pass
            pid = holder.get("pid")
            dead = (not isinstance(pid, int)) or pid <= 0 or not _pid_alive(pid)
            try:
                stale = dead or time.time() - os.path.getmtime(path) > 1800
            except OSError:
                stale = False
            if stale and attempt == 0:
                try:
                    os.remove(path)
                except OSError:
                    return None
                continue
            return None
    return None


def release_lock(path: str | None) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _sigterm(_signum, _frame):
    """Breng SystemExit teweeg zodat main()'s finally de lock vrijgeeft."""
    raise SystemExit(143)


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_dir(args.data_dir)
    ensure_dir(os.path.join(args.data_dir, "logs"))

    # SIGTERM (docker stop / kill) netjes afhandelen zodat de run-lock wordt vrijgegeven
    signal.signal(signal.SIGTERM, _sigterm)

    lock = acquire_lock(args.data_dir)
    if lock is None:
        log("crawl draait al (data/.crawl.lock) — deze run wordt overgeslagen")
        return 0
    try:
        return _run(args)
    finally:
        release_lock(lock)


def _run(args) -> int:
    started = now_iso()
    started_at = datetime.now(timezone.utc)

    config = load_config(args.config)
    selected = [c.strip() for c in args.categories.split(",") if c.strip()]
    keywords = all_topic_keywords(config)

    log("1/5 crawl")
    github_source.reset()
    items, per_category, errors = crawl(config, args.data_dir, selected or None)
    for cid, cat_items in per_category.items():
        for item in cat_items:
            item["topics"] = assign_topics(item, keywords)
    stale = prune_stale(items)
    if stale:
        log(f"  {stale} verouderde items verwijderd")

    log("2/5 score")
    history = scoring.load_history(args.data_dir)
    scoring.score_items(items, history)

    active = {s: i for s, i in items.items() if not i.get("dropped")}
    log(f"  {len(items)} items totaal, {len(active)} actief")

    ai_report = {"enabled": False, "errors": [], "topic_review": [], "topics_added": []}
    llm = ai_mod.LLMClient()
    use_ai = not args.no_ai and llm.configured and config.get("ai_review", {}).get("enabled", True)
    if args.resummarize and not use_ai:
        log("--resummarize overgeslagen: AI niet beschikbaar")

    if use_ai:
        log("3/5 AI-review")
        now = datetime.now(timezone.utc)
        sorted_active = sorted(active.values(), key=lambda x: x.get("trend", 0), reverse=True)
        if args.resummarize:
            reviewable = sorted_active
            log(f"  --resummarize: alle {len(reviewable)} items opnieuw beoordelen")
        else:
            reviewable = [i for i in sorted_active if needs_review(i, now)][:AI_MAX_ITEMS_PER_RUN]
        buckets: dict[str, list[dict]] = {}
        for item in reviewable:
            buckets.setdefault(item["category"], []).append(item)

        def _persist_batch(reviews: dict) -> None:
            """Tussenopslag: na elke batch toepassen + wegschrijven (crash-vast)."""
            apply_ai_review(reviews, items, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            atomic_write_json(os.path.join(args.data_dir, "items.json"), {
                "generated_at": now_iso(),
                "count": len(items),
                "items": items,
            })

        review = ai_mod.run_full_review(llm, config, buckets, keywords,
                                        on_batch=_persist_batch)
        kept, dropped = apply_ai_review(review["item_reviews"], items,
                                        datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        log(f"  {len(review['item_reviews'])} items beoordeeld ({kept} keep / {dropped} drop)")

        topics_added = propose_topics(config, review.get("trend_proposals") or [], args.config)
        if topics_added:
            config = load_config(args.config)  # herlaad na wijziging
            keywords = all_topic_keywords(config)
            log(f"  {len(topics_added)} topics toegevoegd aan categories.yaml")

        ai_report = {
            "enabled": True,
            "errors": review.get("errors", []),
            "topic_review": review.get("topic_review", []),
            "topics_added": topics_added,
            "reviewed_slugs": [i["slug"] for i in reviewable],
        }
        if ai_report["errors"]:
            for message in ai_report["errors"]:
                log(f"  AI-FOUT {message}")
    else:
        reason = "--no-ai" if args.no_ai else ("LLM_API_KEY ontbreekt" if not llm.configured else "ai_review.enabled=false")
        log(f"3/5 AI-review overgeslagen ({reason}) -> heuristic fallback")
        for item in items.values():
            if item.get("relevance", 0) in (0, None):
                item["relevance"] = scoring.heuristic_relevance(item)
        scoring.score_items(items, history)

    active = {s: i for s, i in items.items() if not i.get("dropped")}

    log("4/5 history-snapshot + data")
    snapshot_path = scoring.write_snapshot(args.data_dir, active)
    log(f"  {os.path.relpath(snapshot_path, ROOT)}")
    atomic_write_json(os.path.join(args.data_dir, "items.json"), {
        "generated_at": now_iso(),
        "count": len(items),
        "items": items,
    })
    atomic_write_json(os.path.join(args.data_dir, "ai-review.json"), ai_report)

    written = {}
    if not args.skip_render:
        log("5/5 render")
        written = render_site(config, active, scoring.load_history(args.data_dir),
                              args.site_dir, args.data_dir)
        log(f"  {len(written)} pagina's geschreven")

    duration = round((datetime.now(timezone.utc) - started_at).total_seconds(), 1)
    summary = {
        "started_at": started,
        "finished_at": now_iso(),
        "duration_s": duration,
        "ai": ai_report.get("enabled"),
        "errors": errors + list(ai_report.get("errors", [])),
        "items_total": len(items),
        "items_active": len(active),
        "pages": len(written),
        "stale_pruned": stale,
    }
    atomic_write_json(os.path.join(args.data_dir, "logs", "last-run.json"), summary)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    atomic_write_json(os.path.join(args.data_dir, "logs", f"run-{stamp}.json"), summary)

    if summary["errors"]:
        log(f"run klaar met {len(summary['errors'])} fout(en) in {duration}s")
        return 0  # cron breekt nooit
    log(f"run klaar: {len(active)} items, {len(written)} pagina's in {duration}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
