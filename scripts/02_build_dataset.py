"""Raw alert log (+ optional Telegram posts) -> hourly panel -> feature table (parquet)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from airalerts import config, data, features, text_features as tf  # noqa: E402

# Older history is used only through the rolling features of later origins.
ORIGIN_START = pd.Timestamp("2023-06-01", tz="UTC")
CHANNEL = "kpszsu"

if __name__ == "__main__":
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    panel = data.build_panel(data.load_alerts())
    extra = None
    if (config.DATA_RAW / f"telegram_{CHANNEL}.csv").exists():
        posts = tf.load_posts(CHANNEL)
        mentions, threats = tf.extract(posts, tf.load_gazetteer())
        extra = tf.text_feature_arrays(panel, posts, mentions, threats)
        share = mentions.groupby("how").size() / len(posts)
        print(f"telegram @{CHANNEL}: {len(posts):,} posts "
              f"{posts.published_at.min():%Y-%m-%d} .. {posts.published_at.max():%Y-%m-%d %H:%M}; "
              f"region mentions per post: {share.round(2).to_dict()}")
        mentions.to_parquet(config.DATA_PROCESSED / "telegram_mentions.parquet")
    df = features.build_dataset(panel, origin_start=ORIGIN_START, extra=extra)
    df.to_parquet(config.DATA_PROCESSED / "dataset.parquet")
    print(f"panel {panel.occ.shape[0]:,} hours x {len(panel.regions)} regions, "
          f"{panel.index[0]:%Y-%m-%d} .. {panel.index[-1]:%Y-%m-%d %H:%M} UTC")
    print(f"dataset {len(df):,} rows x {len(features.feature_columns(df))} features")
