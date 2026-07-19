from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query

from scrape_telegram import ALL_CHANNELS, dedup_posts, scrape_channel

app = FastAPI(
    title="Israel Digest News API",
    description="Collects recent posts from configured public Telegram channels for Make.com.",
    version="1.1.0",
)


def parse_since(value: str | None, hours: int) -> datetime:
    if not value:
        return datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Parameter 'since' must be an ISO timestamp, for example "
                "2026-07-19T07:00:00+03:00"
            ),
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def check_api_key(authorization: str | None) -> None:
    expected = os.getenv("DIGEST_API_KEY", "").strip()
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "Israel Digest News API",
        "version": "1.1.0",
        "health": "/health",
        "news": "/news?hours=12",
    }


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/news")
def news(
    hours: int = Query(default=12, ge=1, le=48),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    dedup_threshold: float = Query(default=0.55, ge=0.20, le=0.95),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    check_api_key(authorization)
    since_dt = parse_since(since, hours)

    all_posts: list[dict[str, Any]] = []
    channel_stats: dict[str, int] = {}
    errors: list[str] = []

    # Fetch channels in parallel. This is much faster than waiting for each
    # Telegram page one after another and helps avoid hosting timeouts.
    with ThreadPoolExecutor(max_workers=min(8, len(ALL_CHANNELS))) as pool:
        futures = {
            pool.submit(scrape_channel, channel, since_dt): channel
            for channel in ALL_CHANNELS
        }
        for future in as_completed(futures):
            channel = futures[future]
            try:
                posts = future.result()
                channel_stats[channel] = len(posts)
                all_posts.extend(posts)
            except Exception as exc:  # one failed channel must not fail the API
                channel_stats[channel] = 0
                errors.append(f"{channel}: {exc}")

    all_posts.sort(key=lambda post: post.get("time_utc", ""))
    deduped = dedup_posts(all_posts, threshold=dedup_threshold)
    deduped.sort(key=lambda post: post.get("time_utc", ""))

    if len(deduped) > limit:
        deduped = deduped[-limit:]

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_dt.isoformat(),
        "raw_count": len(all_posts),
        "count": len(deduped),
        "channels": channel_stats,
        "errors": errors,
        "posts": deduped,
    }
