"""Trend-score, sterrendelta en dagelijkse history-snapshots."""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

from pipeline.util import age_days, atomic_write_json, clamp, now_iso, read_json

WEIGHTS = {
    "hn": 0.35,        # HN-punten (log-schaal)
    "github": 0.25,    # sterrendelta per dag
    "recency": 0.20,   # exp-decay over leeftijd
    "ai": 0.20,        # AI-relevance 0..10
}
HISTORY_DAYS = 14
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def history_dir(data_dir: str) -> str:
    path = os.path.join(data_dir, "history")
    os.makedirs(path, exist_ok=True)
    return path


def _history_path(data_dir: str, day: datetime) -> str:
    return os.path.join(history_dir(data_dir), f"{day.strftime('%Y-%m-%d')}.json")


def load_history(data_dir: str, days: int = HISTORY_DAYS) -> list[tuple[str, dict]]:
    """Laadt de laatste `days` snapshots, oud -> nieuw."""
    out: list[tuple[str, dict]] = []
    today = datetime.now(timezone.utc)
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        payload = read_json(_history_path(data_dir, day), default=None)
        if isinstance(payload, dict):
            out.append((day.strftime("%Y-%m-%d"), payload.get("items", payload)))
    return out


def write_snapshot(data_dir: str, items: dict) -> str:
    day = datetime.now(timezone.utc)
    payload = {
        "date": day.strftime("%Y-%m-%d"),
        "generated_at": now_iso(),
        "items": {
            slug: {
                "trend": round(float(item.get("trend", 0)), 1),
                "stars": int(item.get("stars", 0)),
                "hn_points": int(item.get("hn_points", 0)),
                "relevance": int(item.get("relevance", 0)),
            }
            for slug, item in items.items()
        },
    }
    path = _history_path(data_dir, day)
    atomic_write_json(path, payload)
    return path


def previous_day_value(history: list[tuple[str, dict]], slug: str, field: str, skip_today: bool = True):
    for index in range(len(history) - 1, -1, -1):
        if skip_today and index == len(history) - 1:
            continue
        value = history[index][1].get(slug)
        if isinstance(value, dict) and field in value:
            return value[field]
    return None


def star_delta_per_day(stars: int, created_at: str, previous_stars=None, created_days_ago: float | None = None) -> float:
    if previous_stars is not None:
        delta = stars - int(previous_stars)
        return float(delta)
    age = created_days_ago if created_days_ago is not None else age_days(created_at)
    if age <= 0.5:
        return float(stars)
    return stars / age


def hn_component(points: int) -> float:
    if points <= 0:
        return 0.0
    return clamp(math.log10(1 + points) / 3.0, 0.0, 1.0)


def github_component(delta_per_day: float) -> float:
    return clamp(delta_per_day / 50.0, 0.0, 1.0)


def recency_component(created_at: str) -> float:
    return clamp(math.exp(-age_days(created_at) / 7.0), 0.0, 1.0)


def ai_component(relevance: float) -> float:
    return clamp(float(relevance) / 10.0, 0.0, 1.0)


def trend_score(*, hn_points: int, star_delta: float, created_at: str, relevance: float) -> float:
    score = (
        WEIGHTS["hn"] * hn_component(hn_points)
        + WEIGHTS["github"] * github_component(star_delta)
        + WEIGHTS["recency"] * recency_component(created_at)
        + WEIGHTS["ai"] * ai_component(relevance)
    )
    return round(100.0 * score, 1)


def score_items(items: dict, history: list[tuple[str, dict]]) -> dict:
    """Berekent star_delta, relevance-default en trend voor elk item."""
    for slug, item in items.items():
        previous_stars = previous_day_value(history, slug, "stars")
        previous_relevance = previous_day_value(history, slug, "relevance")
        item["star_delta"] = round(
            star_delta_per_day(
                int(item.get("stars", 0)),
                item.get("created_at", ""),
                previous_stars=previous_stars,
            ),
            1,
        )
        if item.get("relevance") is None or item.get("relevance") == 0:
            item["relevance"] = int(previous_relevance or heuristic_relevance(item))
        item["trend"] = trend_score(
            hn_points=int(item.get("hn_points", 0)),
            star_delta=float(item.get("star_delta", 0.0)),
            created_at=item.get("created_at", ""),
            relevance=float(item.get("relevance", 0)),
        )
        item["scored_at"] = now_iso()
    return items


def heuristic_relevance(item: dict) -> int:
    """Fallback wanneer de AI-run faalt of is uitgeschakeld."""
    value = 3.0
    value += hn_component(int(item.get("hn_points", 0))) * 4.0
    value += github_component(float(item.get("star_delta", 0.0))) * 3.0
    value += recency_component(item.get("created_at", "")) * 1.0
    if item.get("free"):
        value += 1.0
    return int(round(clamp(value, 1, 10)))


def sparkline(values: list[float], width: int | None = None) -> str:
    """ASCII/unicode sparkline voor 14-dagen history."""
    values = [float(v) for v in values if v is not None]
    if not values:
        return ""
    if width and len(values) > width:
        step = len(values) / width
        values = [values[min(len(values) - 1, int(i * step))] for i in range(width)]
    high = max(values)
    low = min(values)
    span = (high - low) or 1.0
    out = []
    for value in values:
        ratio = (value - low) / span
        out.append(SPARK_CHARS[int(round(ratio * (len(SPARK_CHARS) - 1)))])
    return "".join(out)


def trend_series(history: list[tuple[str, dict]], slug: str, days: int = HISTORY_DAYS) -> list[float]:
    series: list[float] = []
    for _, snapshot in history[-days:]:
        entry = snapshot.get(slug)
        series.append(float(entry.get("trend", 0.0)) if isinstance(entry, dict) else 0.0)
    return series


def build_sparkline(history: list[tuple[str, dict]], slug: str) -> str:
    return sparkline(trend_series(history, slug))
