"""Time-based splits and probabilistic forecast metrics."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from . import config


@dataclass
class Split:
    train_start: pd.Timestamp
    val_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_split(df: pd.DataFrame, train_days: int | None) -> Split:
    """Last TEST_DAYS of forecast origins with a known target = test,
    the VAL_DAYS before = validation, and ``train_days`` before that = train."""
    known = df[df.y.notna()]
    test_end = known.origin_time.max()
    test_start = test_end - pd.Timedelta(days=config.TEST_DAYS) + pd.Timedelta(hours=1)
    val_start = test_start - pd.Timedelta(days=config.VAL_DAYS)
    train_start = (df.origin_time.min() if train_days is None
                   else val_start - pd.Timedelta(days=train_days))
    return Split(train_start, val_start, test_start, test_end)


def train_mask(df: pd.DataFrame, start: pd.Timestamp, cutoff: pd.Timestamp) -> pd.Series:
    """Rows usable for training a model that is evaluated from ``cutoff`` on.

    The *target* hour (not only the origin) must end before ``cutoff`` — otherwise
    labels from the evaluation period would leak into training through long horizons.
    """
    return (df.origin_time >= start) & (df.target_time + pd.Timedelta(hours=1) <= cutoff) \
        & df.y.notna()


def period_mask(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return (df.origin_time >= start) & (df.origin_time < end) & df.y.notna()


def scores(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, int)
    return {
        "brier": brier_score_loss(y, p),
        "log_loss": log_loss(y, p, labels=[0, 1]),
        "roc_auc": roc_auc_score(y, p) if 0 < y.mean() < 1 else np.nan,
        "pr_auc": average_precision_score(y, p),
        "base_rate": y.mean(),
        "n": len(y),
    }


def score_table(df: pd.DataFrame, pred_cols: list[str]) -> pd.DataFrame:
    """Metrics per model x horizon (+ an 'all' row)."""
    rows = []
    for m in pred_cols:
        for h, g in [*df.groupby("h"), ("all", df)]:
            rows.append({"model": m, "h": h, **scores(g.y, g[m])})
    return pd.DataFrame(rows)


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    b = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    d = pd.DataFrame({"b": b, "y": y, "p": p})
    return d.groupby("b").agg(p_mean=("p", "mean"), y_mean=("y", "mean"), n=("y", "size"))


def learned_models(df: pd.DataFrame) -> dict:
    """name -> (model factory, first origin allowed in training). Shared by test and validation."""
    from . import models

    learned = {"logreg_v1": (models.LogRegModel, df.origin_time.min()),
               "lgb_v2": (models.LGBModel, df.origin_time.min())}
    if "tg_reg_60m" in df.columns:
        text_start = df.loc[df.tg_reg_60m.notna(), "origin_time"].min()
        # fair comparison: v2 and v3 trained on exactly the same (shorter) window
        learned["lgb_v2_short"] = (models.LGBModel, text_start)
        learned["lgb_v3_text"] = (lambda: models.LGBModel(models.V3_FEATURES), text_start)
    return learned


def walk_forward(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, learned: dict,
                 refit_every: pd.Timedelta = pd.Timedelta(days=7), verbose: bool = True):
    """Evaluate origins in [start, end] in blocks of ``refit_every``.

    Before each block every learned model is re-trained on rows whose *target hour*
    ended before the block starts (as weekly retraining would work in production).
    Returns (predictions frame, {model name: [feature importance per block]}).
    """
    import time

    from . import models

    blocks, t0 = [], start
    while t0 <= end:
        blocks.append((t0, min(t0 + refit_every, end + pd.Timedelta(hours=1))))
        t0 += refit_every
    preds, importances = [], {}
    for b0, b1 in blocks:
        part = df[period_mask(df, b0, b1)].copy()
        for name in models.BASELINES:
            part[name] = models.predict_baseline(part, name)
        for name, (make, train_from) in learned.items():
            train = df[train_mask(df, train_from, b0)]
            tic = time.time()
            m = make().fit(train)
            part[name] = m.predict(part)
            if verbose:
                print(f"  block {b0:%m-%d}: {name} trained on {len(train):,} rows "
                      f"in {time.time() - tic:.0f}s", flush=True)
            if hasattr(m, "importance"):
                importances.setdefault(name, []).append(m.importance())
        preds.append(part)
    return pd.concat(preds, ignore_index=True), importances
