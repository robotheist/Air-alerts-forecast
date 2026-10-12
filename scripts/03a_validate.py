"""Validation run: the same weekly walk-forward as the test, on the VAL_DAYS before it.

All modelling choices (features, training window, text features) were made by
looking at these numbers, never at the test month.
Outputs: reports/metrics/validation_scores.csv, validation_scores_quiet_origins.csv
"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd  # noqa: E402

from airalerts import config, evaluation as ev, models  # noqa: E402

if __name__ == "__main__":
    df = pd.read_parquet(config.DATA_PROCESSED / "dataset.parquet")
    split = ev.make_split(df, train_days=None)
    end = split.test_start - pd.Timedelta(hours=1)
    print(f"validation origins: {split.val_start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC")
    learned = ev.learned_models(df)
    val, _ = ev.walk_forward(df, split.val_start, end, learned)
    names = [*models.BASELINES, *learned]
    config.METRICS.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 140)
    for suffix, sub in [("", val), ("_quiet_origins", val[val.active_now == 0])]:
        t = ev.score_table(sub, names)
        t.to_csv(config.METRICS / f"validation_scores{suffix}.csv", index=False)
        print(f"\n===== VALIDATION{suffix.upper()} =====")
        for k in ["brier", "roc_auc"]:
            print(f"\n{k}\n", t.pivot(index="model", columns="h", values=k)
                  .loc[names].round(4).to_string())
