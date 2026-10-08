"""Static server + API (ratings, referenties, topics, bronnen, discover, rebuild).

    python3 server/server.py            # poort 8184 (env PORT)
    PORT=9000 python3 server/server.py

Schrijfacties (POST/DELETE op /api/references, /api/topics, /api/sources,
/api/rebuild) zijn alleen toegestaan vanaf WRITE_NETWORKS; leeg = alles geweigerd.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline import references as ref_mod  # noqa: E402
from pipeline.config_loader import (  # noqa: E402
    add_source,
    add_topic_web,
    category_by_id,
    load_config,
    read_changelog,
    remove_source,
    remove_topic,
)
from pipeline.sources import FETCHERS  # noqa: E402
from pipeline.sources import fetch_source  # noqa: E402

SITE_DIR = os.environ.get("SITE_DIR", os.path.join(ROOT, "site"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
RATINGS_PATH = os.path.join(DATA_DIR, "ratings.json")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8184"))
RATING_SALT = os.environ.get("RATING_SALT", "ai-trending-rating")
MAX_RATINGS_PER_HOUR = int(os.environ.get("MAX_RATINGS_PER_HOUR", "60"))
MAX_WRITES_PER_HOUR = int(os.environ.get("MAX_WRITES_PER_HOUR", "60"))
MIN_RATING_INTERVAL = float(os.environ.get("MIN_RATING_INTERVAL", "1.0"))

WRITE_NETWORKS: list[str] = [
    net.strip()
    for net in re.split(r"[,;\s]+", os.environ.get("WRITE_NETWORKS") or "")
    if net.strip()
]
DISCOVER_TTL = 600
MAX_DISCOVER_RESULTS = 15

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
}

_lock = threading.Lock()
_rate_window: dict[str, list[float]] = {}
_recent_post: dict[str, float] = {}
_discover_cache: dict[str, tuple[float, dict]] = {}
_rebuild_proc: dict = {}


def _write_allowed(ip: str) -> bool:
    """Alleen vanaf thuisnetwerk (WRITE_NETWORKS); leeg = alles geweigerd."""
    if not WRITE_NETWORKS:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in WRITE_NETWORKS:
        try:
            if addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def _load_ratings() -> dict:
    try:
        with open(RATINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"slugs": {}}


def _save_ratings(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = f"{RATINGS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, RATINGS_PATH)


def _client_key(ip: str) -> str:
    return hmac.new(RATING_SALT.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def _known_slug(slug: str) -> bool:
    path = os.path.join(SITE_DIR, "items", f"{slug}.html")
    return os.path.isfile(path) and "/" not in slug and "\\" not in slug


def _throttle(ip: str, kind: str) -> tuple[bool, str]:
    now = time.time()
    if kind == "post":
        last = _recent_post.get(ip, 0)
        if now - last < MIN_RATING_INTERVAL:
            return True, "te snel, even wachten"
        _recent_post[ip] = now
    limit = MAX_WRITES_PER_HOUR if kind == "write" else (
        MAX_RATINGS_PER_HOUR if kind == "post" else 120)
    window = _rate_window.setdefault(f"{ip}:{kind}", [])
    window[:] = [t for t in window if now - t < 3600]
    if len(window) >= limit:
        return True, "limiet bereikt, probeer later opnieuw"
    window.append(now)
    return False, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-trending/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # stil, behalve fouten
        if self.path.startswith("/api/") and "4" in str(args[0:1]):
            super().log_message(fmt, *args)

    # ---------- helpers ----------
    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                # rechterlijk = door onze vertrouwde proxy (Traefik) toegevoegd;
                # de linkerkant komt van de client zelf en is te spoofen.
                return parts[-1]
        return self.client_address[0]

    def _drain_body(self) -> None:
        """Lees een eventuele request-body weg zodat keep-alive intact blijft.

        Een vroege return (403/429/404) zonder uitlezen laat de body in de
        socket staan; de volgende request op dezelfde verbinding wordt dan als
        rommel geparseerd (400 "bad request syntax" met HTML-antwoord).
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0:
            return
        if length > 1_048_576:
            self.close_connection = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _send_file(self, abs_path: str) -> None:
        if not os.path.isfile(abs_path):
            self._not_found()
            return
        ext = os.path.splitext(abs_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(abs_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if ext == ".html" else "public, max-age=300")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self) -> None:
        body = b"404 not found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    @staticmethod
    def _safe_path(url_path: str, base: str | None = None, prefix: str = "/") -> str | None:
        base = os.path.abspath(base or SITE_DIR)
        decoded = urllib.parse.unquote(url_path)
        if not decoded.startswith(prefix):
            return None
        relative = decoded[len(prefix):].lstrip("/")
        cleaned = os.path.normpath(relative)
        if cleaned.startswith("..") or ".." in cleaned.split(os.sep) or os.path.isabs(cleaned):
            return None
        full = os.path.abspath(os.path.join(base, cleaned))
        if full != base and not full.startswith(base + os.sep):
            return None
        return full

    def _require_write(self) -> tuple[bool, str, int]:
        """IP-guard + rate-limit voor schrijfacties."""
        ip = self._client_ip()
        if not _write_allowed(ip):
            allowed = ", ".join(WRITE_NETWORKS) or "geen netwerken"
            return False, (f"schrijfacties alleen vanaf thuisnetwerk — jouw IP: {ip} "
                           f"(toegestaan: {allowed})"), 403
        blocked, reason = _throttle(ip, "write")
        if blocked:
            return False, reason, 429
        return True, "", 200

    def _read_json(self, allow_empty: bool = False) -> tuple[dict | None, str]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            if allow_empty:
                return {}, ""
            return None, "lege body"
        if length > 65536:
            return None, "te grote body"
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "ongeldige JSON"
        if not isinstance(payload, dict):
            return None, "JSON-object verwacht"
        return payload, ""

    @staticmethod
    def _load_config_or_none() -> dict | None:
        try:
            return load_config()
        except Exception:  # noqa: BLE001 - door de caller als 500 gemeld
            return None

    # ---------- verbs ----------
    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        handlers = {
            "/api/rating": self._post_rating,
            "/api/references": self._post_reference,
            "/api/topics": self._post_topic,
            "/api/sources": self._post_source,
            "/api/rebuild": self._post_rebuild,
        }
        handler = handlers.get(path)
        if handler is None:
            self._drain_body()
            self._send_error_json(
                "alleen POST op /api/rating, /api/references, /api/topics, "
                "/api/sources, /api/rebuild", 404)
            return
        if path != "/api/rating":
            ok, reason, status = self._require_write()
            if not ok:
                self._drain_body()
                self._send_error_json(reason, status)
                return
        handler()

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        handlers = {
            "/api/references": self._delete_reference,
            "/api/sources": self._delete_source,
            "/api/topics": self._delete_topic,
        }
        handler = handlers.get(path)
        if handler is None:
            self._drain_body()
            self._send_error_json(
                "alleen DELETE op /api/references, /api/topics, /api/sources", 404)
            return
        ok, reason, status = self._require_write()
        if not ok:
            self._drain_body()
            self._send_error_json(reason, status)
            return
        handler()

    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/healthz":
            self._send_json({"status": "ok", "time": int(time.time())})
            return
        if path == "/api/rating":
            query = urllib.parse.parse_qs(parsed.query)
            slug = (query.get("slug") or [""])[0]
            self._get_rating(slug)
            return
        if path == "/api/manage":
            self._get_manage()
            return
        if path == "/api/references":
            self._send_json({"references": ref_mod.load_references(DATA_DIR)})
            return
        if path == "/api/discover":
            self._get_discover(urllib.parse.parse_qs(parsed.query))
            return
        if path.startswith("/api/"):
            self._send_error_json("onbekend endpoint", 404)
            return

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(SITE_DIR, "index.html"))
            return
        if path.startswith("/data/"):
            target = self._safe_path(path, base=DATA_DIR, prefix="/data/")
            if target is None:
                self._send_error_json("pad niet toegestaan", 403)
                return
            self._send_file(target)
            return
        if path.startswith("/assets/") or path.startswith("/categories/") \
                or path.startswith("/items/") or path.endswith((".html", ".css", ".js", ".json", ".ico", ".svg")):
            target = self._safe_path(path)
            if target is None:
                self._send_error_json("pad niet toegestaan", 403)
                return
            self._send_file(target)
            return
        self._not_found()

    # ---------- ratings ----------
    def _get_rating(self, slug: str) -> None:
        if not slug:
            self._send_error_json("slug ontbreekt")
            return
        with _lock:
            data = _load_ratings()
            entry = data.get("slugs", {}).get(slug) or {}
        dist = entry.get("dist") or {str(i): 0 for i in range(1, 6)}
        count = int(entry.get("count") or 0)
        total = int(entry.get("sum") or 0)
        average = round(total / count, 2) if count else None
        my = (entry.get("scores") or {}).get(_client_key(self._client_ip()))
        self._send_json({
            "slug": slug,
            "count": count,
            "average": average,
            "histogram": {str(i): int(dist.get(str(i), 0)) for i in range(1, 6)},
            "my_score": my,
        })

    def _post_rating(self) -> None:
        ip = self._client_ip()
        blocked, reason = _throttle(ip, "post")
        if blocked:
            self._drain_body()
            self._send_error_json(reason, 429)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self._send_error_json("lege of te grote body")
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json("ongeldige JSON")
            return

        slug = str(payload.get("slug") or "").strip()
        try:
            score = int(payload.get("score"))
        except (TypeError, ValueError):
            score = 0
        if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,79}", slug):
            self._send_error_json("ongeldige slug")
            return
        if score < 1 or score > 5:
            self._send_error_json("score moet 1..5 zijn")
            return
        if not _known_slug(slug):
            self._send_error_json("onbekend item", 404)
            return

        key = _client_key(ip)
        with _lock:
            data = _load_ratings()
            slugs = data.setdefault("slugs", {})
            entry = slugs.setdefault(slug, {
                "count": 0,
                "sum": 0,
                "dist": {str(i): 0 for i in range(1, 6)},
                "scores": {},
            })
            previous = entry.get("scores", {}).get(key)
            if previous is not None:
                old = int(previous)
                entry["count"] = max(0, int(entry.get("count", 0)) - 1)
                entry["sum"] = max(0, int(entry.get("sum", 0)) - old)
                entry["dist"][str(old)] = max(0, int(entry["dist"].get(str(old), 0)) - 1)
            entry.setdefault("scores", {})[key] = score
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["sum"] = int(entry.get("sum", 0)) + score
            entry["dist"][str(score)] = int(entry["dist"].get(str(score), 0)) + 1
            _save_ratings(data)

        count = int(entry["count"])
        dist = entry.get("dist") or {str(i): 0 for i in range(1, 6)}
        self._send_json({
            "slug": slug,
            "count": count,
            "average": round(int(entry["sum"]) / count, 2) if count else None,
            "histogram": {str(i): int(dist.get(str(i), 0)) for i in range(1, 6)},
            "my_score": score,
        })

    # ---------- beheer: referenties ----------
    def _post_reference(self) -> None:
        payload, error = self._read_json()
        if payload is None:
            self._send_error_json(error)
            return
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        category = str(payload.get("category") or "").strip()
        if category_by_id(config, category) is None:
            self._send_error_json("onbekende categorie")
            return
        ref, err = ref_mod.add_reference(
            DATA_DIR,
            url=payload.get("url"),
            title=str(payload.get("title") or ""),
            note=str(payload.get("note") or ""),
            category=category,
            tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None,
        )
        if err:
            self._send_error_json(err, 409 if "bestaat al" in err else 400)
            return
        self._send_json({"reference": ref, "message": "toegevoegd — geldt vanaf de volgende crawl"}, 201)

    def _delete_reference(self) -> None:
        ref_id = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id", [""])[0]
        if not ref_id:
            payload, error = self._read_json(allow_empty=True)
            if payload is None:
                self._send_error_json(error)
                return
            ref_id = str(payload.get("id") or "")
        if not ref_id:
            self._send_error_json("id ontbreekt")
            return
        if not ref_mod.remove_reference(DATA_DIR, ref_id):
            self._send_error_json("onbekende referentie", 404)
            return
        self._send_json({"removed": ref_id, "message": "verwijderd — verdwijnt bij de volgende crawl"})

    # ---------- beheer: topics + bronnen ----------
    def _post_topic(self) -> None:
        payload, error = self._read_json()
        if payload is None:
            self._send_error_json(error)
            return
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        topic, err = add_topic_web(config, payload)
        if err:
            self._send_error_json(err, 409 if "bestaat al" in err else 400)
            return
        self._send_json({"topic": topic, "message": "toegevoegd — geldt vanaf de volgende crawl"}, 201)

    def _post_source(self) -> None:
        payload, error = self._read_json()
        if payload is None:
            self._send_error_json(error)
            return
        kind = str(payload.get("type") or "").strip()
        if kind not in FETCHERS:
            self._send_error_json(f"onbekend type; toegestaan: {', '.join(sorted(FETCHERS))}")
            return
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        category = str(payload.get("category") or "").strip()
        entry, err = add_source(config, category, payload)
        if err:
            self._send_error_json(err, 409 if "bestaat al" in err else 400)
            return
        self._send_json({"source": entry, "message": "toegevoegd — geldt vanaf de volgende crawl"}, 201)

    def _delete_source(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        payload, error = self._read_json(allow_empty=True)
        if payload is None:
            self._send_error_json(error)
            return
        match = {
            "category": str(payload.get("category") or query.get("category", [""])[0] or ""),
            "index": payload.get("index", query.get("index", [None])[0]),
            "type": payload.get("type", query.get("type", [""])[0]),
            "query": payload.get("query", query.get("query", [""])[0]),
            "url": payload.get("url", query.get("url", [""])[0]),
        }
        if not match["category"]:
            self._send_error_json("category ontbreekt")
            return
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        removed, err = remove_source(config, match["category"], match)
        if err:
            self._send_error_json(err, 404 if "niet gevonden" in err else 400)
            return
        self._send_json({"removed": removed, "message": "verwijderd — geldt vanaf de volgende crawl"})

    def _delete_topic(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        payload, error = self._read_json(allow_empty=True)
        if payload is None:
            self._send_error_json(error)
            return
        category = str(payload.get("category_id") or payload.get("category")
                       or query.get("category_id", [""])[0] or "").strip()
        topic_id = str(payload.get("topic_id") or payload.get("id")
                       or query.get("topic_id", [""])[0] or "").strip()
        if not category or not topic_id:
            self._send_error_json("category_id en topic_id ontbreken")
            return
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        removed, err = remove_topic(config, category, topic_id)
        if err:
            self._send_error_json(err, 404 if "niet gevonden" in err else 400)
            return
        self._send_json({"removed": removed, "message": "verwijderd — geldt vanaf de volgende crawl"})

    # ---------- beheer: overzicht + discover + rebuild ----------
    def _get_manage(self) -> None:
        config = self._load_config_or_none()
        if config is None:
            self._send_error_json("config laden mislukt", 500)
            return
        categories = []
        for cat in config["categories"]:
            categories.append({
                "id": cat["id"],
                "title": cat.get("title", {}),
                "sources": [{**src, "index": i}
                            for i, src in enumerate(cat.get("sources") or [])],
                "topics": [{
                    "id": t.get("id"),
                    "title": t.get("title", {}),
                    "query": t.get("query", ""),
                    "ai_generated": bool(t.get("ai_generated")),
                } for t in cat.get("topics") or []],
            })
        self._send_json({
            "categories": categories,
            "references": ref_mod.load_references(DATA_DIR),
            "changelog": read_changelog(15),
            "write_networks": WRITE_NETWORKS,
        })

    def _get_discover(self, query: dict) -> None:
        ip = self._client_ip()
        blocked, reason = _throttle(ip, "read")
        if blocked:
            self._send_error_json(reason, 429)
            return
        q = (query.get("q") or [""])[0].strip()[:100]
        wanted = [s.strip().lower() for s in (query.get("sources") or ["hn,github,arxiv"])[0].split(",")]
        kinds = [k for k in dict.fromkeys(wanted) if k in ("hn", "github", "arxiv", "openrouter")][:5]
        if not kinds:
            kinds = ["hn"]
        try:
            limit = max(1, min(int((query.get("limit") or ["10"])[0]), MAX_DISCOVER_RESULTS))
        except ValueError:
            limit = 10

        cache_key = json.dumps([q, kinds, limit])
        with _lock:
            hit = _discover_cache.get(cache_key)
        if hit and time.time() - hit[0] < DISCOVER_TTL:
            self._send_json({**hit[1], "cached": True})
            return

        type_for = {"hn": "hn_search", "github": "github_search", "arxiv": "arxiv",
                    "openrouter": "openrouter"}
        results: list[dict] = []
        errors: list[str] = []
        for kind in kinds:
            if not q and kind in ("github", "arxiv"):
                continue  # zoek-only bronnen; lege query = trending-overzicht
            if kind == "openrouter" and q:
                continue
            cfg: dict = {"type": type_for[kind], "query": q, "limit": limit}
            if kind == "openrouter":
                cfg = {"type": "openrouter", "scope": "trending", "limit": limit}
            try:
                found = fetch_source(cfg, {"id": "discover", "title": {}, "description": {}})
            except Exception as exc:  # noqa: BLE001 - bron-fouten mogen de request niet breken
                errors.append(f"{kind}: {exc}")
                continue
            for it in found:
                results.append({
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "source": it.get("source", kind),
                    "snippet": (it.get("description") or "")[:300],
                    "points": int(it.get("hn_points") or 0) or int(it.get("stars") or 0),
                    "stars": int(it.get("stars") or 0),
                    "created_at": (it.get("created_at") or "")[:10],
                    "discussion_url": it.get("discussion_url") or "",
                })

        results.sort(key=lambda r: r["points"], reverse=True)
        payload = {
            "query": q,
            "sources": kinds,
            "results": results[: max(limit * 2, limit)],
            "errors": errors,
            "generated_at": int(time.time()),
        }
        with _lock:
            if len(_discover_cache) > 200:
                _discover_cache.clear()
            _discover_cache[cache_key] = (time.time(), payload)
        self._send_json({**payload, "cached": False})

    def _post_rebuild(self) -> None:
        payload, error = self._read_json(allow_empty=True)
        if payload is None:
            self._send_error_json(error)
            return
        lock_path = os.path.join(DATA_DIR, ".crawl.lock")
        try:
            if os.path.exists(lock_path) and time.time() - os.path.getmtime(lock_path) < 1800:
                self._send_error_json("een crawl draait al", 409)
                return
        except OSError:
            pass
        if _rebuild_proc.get("proc") is not None and _rebuild_proc["proc"].poll() is None:
            self._send_error_json("een crawl draait al", 409)
            return

        categories = payload.get("categories") or ""
        if isinstance(categories, list):
            categories = ",".join(str(c) for c in categories)
        cmd = [sys.executable, os.path.join(ROOT, "pipeline", "crawl.py")]
        if str(categories).strip():
            cmd += ["--categories", str(categories).strip()[:500]]
        if payload.get("ai") is False:
            cmd.append("--no-ai")

        os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
        log_path = os.path.join(DATA_DIR, "logs", f"web-trigger-{int(time.time())}.log")
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        except OSError as exc:
            self._send_error_json(f"crawl kon niet starten: {exc}", 500)
            return
        _rebuild_proc["proc"] = proc
        self._send_json({
            "status": "gestart",
            "pid": proc.pid,
            "log": os.path.relpath(log_path, ROOT),
            "message": "crawl gestart; ververs de pagina over enkele minuten",
        }, 202)



def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"server op http://{HOST}:{PORT}  site={SITE_DIR}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ngestopt", flush=True)
        server.server_close()


if __name__ == "__main__":
    main()
