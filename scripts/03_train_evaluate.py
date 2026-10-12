"""Walk-forward evaluation on the latest TEST_DAYS days.

The test period is split into weekly blocks. Before each block every learned
model is re-trained on rows whose *target hour* ended before the block starts,
exactly as it would be retrained weekly in production. Baselines need no
training. Outputs: reports/metrics/*.csv, reports/figures/*.png.
"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd  # noqa: E402

from airalerts import config, data, evaluation as ev, models, plots  # noqa: E402

if __name__ == "__main__":
    df = pd.read_parquet(config.DATA_PROCESSED / "dataset.parquet")
    split = ev.make_split(df, train_days=None)
    print(f"test origins: {split.test_start:%Y-%m-%d %H:%M} .. {split.test_end:%Y-%m-%d %H:%M} UTC")

    learned = ev.learned_models(df)
    has_text = "lgb_v3_text" in learned
    test, importances = ev.walk_forward(df, split.test_start, split.test_end, learned)
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
    best = "lgb_v3_text" if has_text else "lgb_v2"
    imp = pd.concat(importances[best], axis=1).mean(axis=1).sort_values(ascending=False)
    imp.to_csv(config.METRICS / f"feature_importance_{best}.csv", header=["gain"])
    plots.alert_share_over_time(panel)
    plots.hour_region_heatmap(panel)
    shown = ["logreg_v1", "lgb_v2"] + (["lgb_v3_text"] if has_text else [])
    plots.skill_by_horizon(table, quiet_table, models=shown)
    plots.reliability_plot(test, best)
    plots.example_timeline(test, shown[-2:], "Kyiv City", h=1)
    plots.importance_plot(imp)
    print(f"\nfigures -> {config.FIGURES.relative_to(config.ROOT)}")
