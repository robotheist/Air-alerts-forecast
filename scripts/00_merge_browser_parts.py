"""Merge CSV parts downloaded by scripts/browser_scraper.js into data/raw/telegram_<channel>.csv.

    python scripts/00_merge_browser_parts.py ~/Downloads --channel kpszsu
"""
import argparse
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--channel", default="kpszsu")
    a = ap.parse_args()
    parts = sorted(a.folder.glob(f"tg_{a.channel}_*part_*.csv"))
    out = RAW / f"telegram_{a.channel}.csv"
    frames = [pd.read_csv(p, dtype={"text": str}) for p in parts]
    if out.exists():
        frames.append(pd.read_csv(out, dtype={"text": str}))
    df = (pd.concat(frames).drop_duplicates("id", keep="first").sort_values("id"))
    df["published_at"] = pd.to_datetime(df.published_at, utc=True, format="ISO8601")
    df["published_at"] = df.published_at.map(lambda t: t.isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"{len(parts)} parts -> {out}: {len(df):,} posts, "
          f"{df.published_at.min()[:10]} .. {df.published_at.max()[:16]}, id gaps: "
          f"{(df.id.max() - df.id.min() + 1) - len(df):,}")
