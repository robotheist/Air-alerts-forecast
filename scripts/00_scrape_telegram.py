"""Scrape a public Telegram channel through its web preview (t.me/s/<channel>).

No API keys and no third-party packages: only the Python standard library
(with a fallback to the system ``curl`` if Python's SSL certificates are missing).
Pages are walked backwards in time with ``?before=<id>`` until ``--since``.

Output: data/raw/telegram_<channel>.csv with columns ``id, published_at, text``
(published_at in UTC). The script is resumable: it saves every 50 pages, and a
re-run first fetches posts newer than the saved ones, then continues backwards
from the oldest saved post if ``--since`` has not been reached yet.

    python3 scripts/00_scrape_telegram.py kpszsu --since 2025-10-01
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"
MSG_RE = re.compile(r'data-post="[^/"]+/(\d+)"(.*?)(?=data-post="|\Z)', re.S)
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def fetch(channel: str, before: int | None, retries: int = 4) -> str:
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # SSL cert problems on some macOS Pythons -> curl
            if "CERTIFICATE" in str(e).upper():
                out = subprocess.run(["curl", "-sS", "-A", UA, url],
                                     capture_output=True, check=True)
                return out.stdout.decode("utf-8", "replace")
            wait = 5 * (attempt + 1)
            print(f"  ! {e} — retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"failed to fetch {url}")


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
            raw = re.sub(r"<br\s*/?>", "\n", tx.group(1))
            text = html.unescape(TAG_RE.sub("", raw))
            text = re.sub(r"[ \t]+", " ", text).strip()
        out.append((pid, ts, text))
    return out


def load(path: Path) -> dict[int, tuple[str, str]]:
    have = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                have[int(row["id"])] = (row["published_at"], row["text"])
    return have


def save(path: Path, have: dict[int, tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "published_at", "text"])
        for pid in sorted(have):
            w.writerow([pid, *have[pid]])
    tmp.replace(path)


def walk(channel, have, path, start_before, stop, sleep):
    """Walk backwards from ``start_before`` until ``stop(oldest_id, oldest_ts)``."""
    before, pages, new = start_before, 0, 0
    while True:
        posts = parse(fetch(channel, before))
        pages += 1
        if not posts:
            break
        for pid, ts, text in posts:
            if pid not in have:
                have[pid] = (ts.isoformat(), text)
                new += 1
        oldest_id, oldest_ts = min((p[0], p[1]) for p in posts)
        if pages % 50 == 0:
            save(path, have)
            print(f"  {pages} pages, reached {oldest_ts:%Y-%m-%d %H:%M} UTC, "
                  f"{len(have):,} posts saved", flush=True)
        if stop(oldest_id, oldest_ts) or oldest_id <= 1:
            break
        before = oldest_id
        time.sleep(sleep)
    save(path, have)
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--since", default="2025-10-01", help="UTC date to scrape back to")
    ap.add_argument("--sleep", type=float, default=0.7, help="pause between pages, s")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
    path = RAW / f"telegram_{a.channel}.csv"
    have = load(path)
    print(f"{a.channel}: {len(have):,} posts already saved -> {path}")

    new = 0
    if have:  # 1) catch up with posts newer than what we have
        newest = max(have)
        new += walk(a.channel, have, path, None, lambda i, t: i <= newest, a.sleep)
    oldest_ts = min((datetime.fromisoformat(v[0]) for v in have.values()), default=None)
    if oldest_ts is None or oldest_ts > since:  # 2) go further back in time
        start = min(have) if have else None
        new += walk(a.channel, have, path, start, lambda i, t: t < since, a.sleep)

    ts = sorted(datetime.fromisoformat(v[0]) for v in have.values())
    print(f"done: +{new:,} new, {len(have):,} posts total, "
          f"{ts[0]:%Y-%m-%d} .. {ts[-1]:%Y-%m-%d %H:%M} UTC -> {path}")


if __name__ == "__main__":
    sys.exit(main())
