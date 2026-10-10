"""Scrape a public Telegram channel through its web preview (t.me/s/<channel>).

No API keys needed. Pages are walked backwards in time with ``?before=<id>``
until ``--since`` is reached. Output: data/raw/telegram_<channel>.csv with
columns ``id, published_at (UTC), text``. Re-running only fetches posts newer
than the ones already saved.

    python scripts/00_scrape_telegram.py kpszsu --since 2025-09-01
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airalerts import config  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"
MSG_RE = re.compile(
    r'data-post="[^/"]+/(\d+)"(.*?)(?=data-post="|\Z)', re.S)
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def fetch(channel: str, before: int | None) -> str:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse(page: str) -> list[tuple[int, datetime, str]]:
    out = []
    for m in MSG_RE.finditer(page):
        pid, body = int(m.group(1)), m.group(2)
        t = TIME_RE.search(body)
        if not t:
            continue
        ts = datetime.fromisoformat(t.group(1)).astimezone(timezone.utc)
        tx = TEXT_RE.search(body)
        text = ""
        if tx:
            text = html.unescape(TAG_RE.sub(" ", tx.group(1).replace("<br/>", "\n")))
            text = re.sub(r"[ \t]+", " ", text).strip()
        out.append((pid, ts, text))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--since", default="2025-09-01", help="UTC date to scrape back to")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
    path = config.DATA_RAW / f"telegram_{a.channel}.csv"
    have: dict[int, tuple[str, str]] = {}
    if path.exists():
        with path.open() as f:
            for row in csv.DictReader(f):
                have[int(row["id"])] = (row["published_at"], row["text"])
    newest_saved = max(have, default=0)

    before, pages, new = None, 0, 0
    while True:
        posts = parse(fetch(a.channel, before))
        pages += 1
        if not posts:
            break
        for pid, ts, text in posts:
            if pid not in have:
                have[pid] = (ts.isoformat(), text)
                new += 1
        oldest_id, oldest_ts = min((p[0], p[1]) for p in posts)
        if pages % 20 == 0:
            print(f"  {pages} pages, back to {oldest_ts:%Y-%m-%d %H:%M}, {len(have):,} posts")
        if oldest_ts < since or oldest_id <= newest_saved or oldest_id <= 1:
            break
        before = oldest_id
        time.sleep(a.sleep)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "published_at", "text"])
        for pid in sorted(have):
            w.writerow([pid, *have[pid]])
    print(f"{a.channel}: +{new} new, {len(have):,} total posts -> {path}")


if __name__ == "__main__":
    main()
