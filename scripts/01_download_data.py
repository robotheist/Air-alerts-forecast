"""Download the latest volunteer (eTryvoga) alert log."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airalerts import config, data  # noqa: E402

if __name__ == "__main__":
    data.download()
    df = data.load_alerts()
    print(f"saved {config.RAW_CSV.relative_to(config.ROOT)}: {len(df):,} alerts, "
          f"{df.started_at.min():%Y-%m-%d} .. {df.started_at.max():%Y-%m-%d %H:%M} UTC")
