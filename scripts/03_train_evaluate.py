"""Walk-forward evaluation on the latest TEST_DAYS days.

The test period is split into weekly blocks. Before each block every learned
model is re-trained on rows whose *target hour* ended before the block starts,
exactly as it would be retrained weekly in production. Baselines need no
training. Outputs: reports/metrics/*.csv, reports/figures/*.png.
"""
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd  # noqa: E402

from airalerts import config, data, evaluation as ev, models, plots  # noqa: E402

REFIT_EVERY = pd.Timedelta(days=7)

if __name__ == "__main__":
    df = pd.read_parquet(config.DATA_PROCESSED / "dataset.parquet")
    split = ev.make_split(df, train_days=None)
    print(f"test origins: {split.test_start:%Y-%m-%d %H:%M} .. {split.test_end:%Y-%m-%d %H:%M} UTC")

    blocks, preds = [], []
    t0 = split.test_start
    while t0 <= split.test_end:
        blocks.append((t0, min(t0 + REFIT_EVERY, split.test_end + pd.Timedelta(hours=1))))
        t0 += REFIT_EVERY

    learned = {"logreg_v1": models.LogRegModel, "lgb_v2": models.LGBModel}
    importances = []
    for start, end in blocks:
        test = df[ev.period_mask(df, start, end)].copy()
        train = df[ev.train_mask(df, df.origin_time.min(), start)]
        for name in models.BASELINES:
            test[name] = models.predict_baseline(test, name)
        for name, cls in learned.items():
            tic = time.time()
            m = cls().fit(train)
            test[name] = m.predict(test)
            print(f"  block {start:%m-%d}: {name} trained on {len(train):,} rows "
                  f"in {time.time() - tic:.0f}s")
            if name == "lgb_v2":
                importances.append(m.importance())
        preds.append(test)

    test = pd.concat(preds, ignore_index=True)
    names = [*models.BASELINES, *learned]
    table = ev.score_table(test, names)
    config.METRICS.mkdir(parents=True, exist_ok=True)
    table.to_csv(config.METRICS / "test_scores.csv", index=False)
    # Harder sub-problem: origins with no alert running -> predicting a NEW alert.
    quiet = test[test.active_now == 0]
    quiet_table = ev.score_table(quiet, names)
    quiet_table.to_csv(config.METRICS / "test_scores_quiet_origins.csv", index=False)
    by_region = pd.DataFrame([
        {"region": r, **ev.scores(g.y, g.lgb_v2), "brier_climatology": ev.scores(
            g.y, g.climatology_28d)["brier"]}
        for r, g in test[test.h == 1].groupby("region", observed=True)])
    by_region.to_csv(config.METRICS / "test_scores_by_region_h1.csv", index=False)
    test[["origin_time", "target_time", "region", "h", "y", "active_now", *names]].to_parquet(
        config.METRICS / "test_predictions.parquet")

    pd.set_option("display.width", 140)
    for title, t in [("ALL ORIGINS", table), ("QUIET ORIGINS (no alert now)", quiet_table)]:
        print(f"\n===== {title} =====")
        for k in ["brier", "log_loss", "roc_auc", "pr_auc"]:
            print(f"\n{k}\n", t.pivot(index="model", columns="h", values=k)
                  .loc[names].round(4).to_string())

    panel = data.build_panel(data.load_alerts())
    imp = pd.concat(importances, axis=1).mean(axis=1).sort_values(ascending=False)
    plots.alert_share_over_time(panel)
    plots.hour_region_heatmap(panel)
    plots.skill_by_horizon(table, quiet_table)
    plots.reliability_plot(test, "lgb_v2")
    plots.example_timeline(test, "lgb_v2", "Kyiv City", h=1)
    plots.importance_plot(imp)
    print(f"\nfigures -> {config.FIGURES.relative_to(config.ROOT)}")
