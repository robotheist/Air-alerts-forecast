"""Does the Air Force channel talk about a region *before* its siren goes off?

For every alert onset in the Telegram period we look back for the latest post
that mentions that region and compare with random moments (the base rate).
Output: reports/figures/07_text_lead_time.png, reports/metrics/text_lead_time.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from airalerts import config, data, plots
from airalerts.text_features import to_ns  # noqa: E402

WINDOWS = [5, 15, 30, 60, 120]

if __name__ == "__main__":
    mentions = pd.read_parquet(config.DATA_PROCESSED / "telegram_mentions.parquet")
    alerts = data.load_alerts()
    t0 = mentions.published_at.min() + pd.Timedelta(hours=6)
    t1 = mentions.published_at.max()
    alerts = alerts[(alerts.started_at >= t0) & (alerts.started_at <= t1)]
    rng = np.random.default_rng(0)
    rows, leads = [], []
    for r, g in alerts.groupby("region"):
        times = np.sort(to_ns(mentions.loc[mentions.region == r, "published_at"]))
        if len(times) == 0:
            continue
        def last_before(ts):
            k = np.searchsorted(times, ts, side="left") - 1
            return np.where(k >= 0, (ts - times[np.clip(k, 0, None)]) / 6e10, np.inf)
        onset = to_ns(g.started_at)
        rand = rng.integers(t0.value, t1.value, size=5 * len(onset))
        lo, lr = last_before(onset), last_before(rand)
        leads.append(lo[lo <= 180])
        rows.append({"region": r, "onsets": len(onset),
                     **{f"onset_{w}m": (lo <= w).mean() for w in WINDOWS},
                     **{f"random_{w}m": (lr <= w).mean() for w in WINDOWS}})
    tab = pd.DataFrame(rows)
    w_on = {w: np.average(tab[f"onset_{w}m"], weights=tab.onsets) for w in WINDOWS}
    w_rd = {w: np.average(tab[f"random_{w}m"], weights=tab.onsets) for w in WINDOWS}
    summary = pd.DataFrame({"window_min": WINDOWS,
                            "share_onsets_with_mention": [w_on[w] for w in WINDOWS],
                            "share_random_moments_with_mention": [w_rd[w] for w in WINDOWS]})
    summary["lift"] = summary.iloc[:, 1] / summary.iloc[:, 2]
    config.METRICS.mkdir(parents=True, exist_ok=True)
    summary.to_csv(config.METRICS / "text_lead_time.csv", index=False)
    tab.to_csv(config.METRICS / "text_lead_time_by_region.csv", index=False)
    print(f"{int(tab.onsets.sum()):,} alert onsets {t0:%Y-%m-%d} .. {t1:%Y-%m-%d}")
    print(summary.round(3).to_string(index=False))
    plots.lead_time_plot(np.concatenate(leads), summary)
