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
