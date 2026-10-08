"""Gedeelde helpers: HTTP, JSON, slugify, atomaire bestandschrijving."""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "ai-trending-dashboard/1.0 (+static site generator)"
DEFAULT_TIMEOUT = 25

_CTX = ssl.create_default_context()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def http_request(url: str, *, headers: dict | None = None, data: bytes | None = None,
                 method: str | None = None, timeout: int = DEFAULT_TIMEOUT,
                 retries: int = 2) -> tuple[int, bytes, dict]:
    """Doet een HTTP-verzoek met simpele retry. Geeft (status, body, headers)."""
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:  # server antwoordde
            body = exc.read() if hasattr(exc, "read") else b""
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            return exc.code, body, dict(exc.headers or {})
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_err if last_err else RuntimeError("http_request faalde")


def http_get_json(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    status, body, _ = http_request(url, headers=headers, timeout=timeout)
    if status >= 400:
        raise RuntimeError(f"HTTP {status} voor {url}: {body[:200]!r}")
    return json.loads(body.decode("utf-8", "replace"))


def http_get_text(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    status, body, _ = http_request(url, headers=headers, timeout=timeout)
    if status >= 400:
        raise RuntimeError(f"HTTP {status} voor {url}")
    return body.decode("utf-8", "replace")


def slugify(text: str, max_len: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "item"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def atomic_write_json(path: str, payload) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def atomic_write_text(path: str, text: str) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def age_days(created: str | None, reference: datetime | None = None) -> float:
    dt = parse_iso(created)
    if dt is None:
        return 30.0
    ref = reference or datetime.now(timezone.utc)
    return max(0.0, (ref - dt).total_seconds() / 86400.0)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)
