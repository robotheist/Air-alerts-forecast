"""Is v3 (with Telegram) really better than v2 trained on the same window?

Paired day-block bootstrap of the Brier-score difference on the test period
(resampling whole days keeps the strong within-day correlation of alerts).
Output: reports/metrics/significance_v3_vs_v2.csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from airalerts import config  # noqa: E402

A, B, N_BOOT = "lgb_v3_text", "lgb_v2_short", 2000

if __name__ == "__main__":
    t = pd.read_parquet(config.METRICS / "test_predictions.parquet")
    t["day"] = t.origin_time.dt.floor("D")
    t["d"] = (t[A] - t.y) ** 2 - (t[B] - t.y) ** 2   # < 0 means A is better
    rng = np.random.default_rng(0)
    rows = []
    for subset, g0 in [("all", t), ("quiet", t[t.active_now == 0])]:
        for h, g in g0.groupby("h"):
            daily = g.groupby("day").d.agg(["sum", "size"])
            days = len(daily)
            idx = rng.integers(0, days, size=(N_BOOT, days))
            boot = daily["sum"].to_numpy()[idx].sum(1) / daily["size"].to_numpy()[idx].sum(1)
            base = ((g[B] - g.y) ** 2).mean()
            rows.append({"subset": subset, "h": h, "brier_diff": g.d.mean(),
                         "rel_change_%": 100 * g.d.mean() / base,
                         "ci95_low": np.quantile(boot, 0.025), "ci95_high": np.quantile(boot, 0.975),
                         "p_A_not_better": (boot >= 0).mean()})
    res = pd.DataFrame(rows)
    res.to_csv(config.METRICS / "significance_v3_vs_v2.csv", index=False)
    print(res.round(5).to_string(index=False))
