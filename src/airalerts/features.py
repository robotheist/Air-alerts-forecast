"""Leakage-safe feature construction.

Forecast origin for panel row ``i`` is the moment ``T = index[i] + 1h`` (end of
hour ``i``). Every feature for that origin is computed only from panel rows
``<= i``. The target for horizon ``h`` is whether *any* alert is active in
region ``r`` during hour row ``i + h`` (i.e. the window ``[T + h - 1, T + h)``).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from . import config
from .data import HourlyPanel


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    """Row-shift so that out[i] = a[i - k] (NaN where unavailable). k may be < 0."""
    out = np.full(a.shape, np.nan, dtype=np.float32)
    if k >= 0:
        out[k:] = a[: len(a) - k] if k else a
    else:
        out[:k] = a[-k:]
    return out


def _roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    """Trailing mean over rows i-w+1..i (inclusive, so known at origin i)."""
    return pd.DataFrame(a).rolling(w, min_periods=1).mean().to_numpy(np.float32)


def _neigh_mean(a: np.ndarray, regions: list[str]) -> np.ndarray:
    idx = {r: j for j, r in enumerate(regions)}
    W = np.zeros((len(regions), len(regions)), dtype=np.float32)
    for r, ns in config.NEIGHBOURS.items():
        for n in ns:
            W[idx[r], idx[n]] = 1.0
    W /= W.sum(axis=1, keepdims=True)
    return np.nan_to_num(a) @ W.T


def origin_features(p: HourlyPanel) -> dict[str, np.ndarray]:
    """Features that depend only on the origin (not on the horizon). Shape (T, R)."""
    A = p.any_active.astype(np.float32)
    f: dict[str, np.ndarray] = {
        "active_now": p.active_end.astype(np.float32),
        "run_min": p.run_minutes_end,
        "since_end_min": p.since_end_minutes,
        "occ_1h": p.occ,
        "occ_3h": _roll_mean(p.occ, 3),
        "occ_6h": _roll_mean(p.occ, 6),
        "occ_24h": _roll_mean(p.occ, 24),
        "any_7d": _roll_mean(A, 24 * 7),
        "any_28d": _roll_mean(A, 24 * 28),
        "onsets_24h": _roll_mean(p.onsets.astype(np.float32), 24) * 24,
    }
    f["nb_active_now"] = _neigh_mean(f["active_now"], p.regions)
    f["nb_occ_3h"] = _neigh_mean(f["occ_3h"], p.regions)
    f["nb_any_24h"] = _neigh_mean(_roll_mean(A, 24), p.regions)
    R = A.shape[1]
    nat_now = f["active_now"].mean(axis=1, keepdims=True)
    nat_onsets_1h = p.onsets.sum(axis=1, keepdims=True).astype(np.float32)
    f["nat_active_now"] = np.repeat(nat_now, R, axis=1)
    f["nat_any_1h"] = np.repeat(A.mean(axis=1, keepdims=True), R, axis=1)
    f["nat_onsets_1h"] = np.repeat(nat_onsets_1h, R, axis=1)
    f["nat_onsets_3h"] = np.repeat(_roll_mean(nat_onsets_1h, 3) * 3, R, axis=1)
    f["nat_change_1h"] = f["nat_active_now"] - np.repeat(
        _shift(nat_now, 1), R, axis=1)
    # v2 additions: medium-term national regime and "massive attack" memory
    nat_any = A.mean(axis=1, keepdims=True)
    f["nat_any_24h"] = np.repeat(_roll_mean(nat_any, 24), R, axis=1)
    f["nat_any_7d"] = np.repeat(_roll_mean(nat_any, 24 * 7), R, axis=1)
    massive = (nat_any >= 0.6).ravel()
    t = np.arange(len(massive))
    last = np.maximum.accumulate(np.where(massive, t, -10**6))
    f["hours_since_massive"] = np.repeat(
        np.minimum(t - last, 24 * 60)[:, None].astype(np.float32), R, axis=1)
    f["trend_7d_28d"] = f["any_7d"] - f["any_28d"]
    return f


def horizon_features(p: HourlyPanel, h: int) -> dict[str, np.ndarray]:
    """Features that refer to the target hour (row i + h) but use only rows <= i."""
    assert 1 <= h <= 24, "seasonal lags below assume h <= 24"
    A = p.any_active.astype(np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices at the very start
        hod7 = np.nanmean([_shift(A, 24 * k - h) for k in range(1, 8)], axis=0)
        hod28 = np.nanmean([_shift(A, 24 * k - h) for k in range(1, 29)], axis=0)
    f = {
        # same target hour yesterday / a week ago: row i + h - 24 (- 144) <= i
        "same_hour_1d": _shift(A, 24 - h),
        "same_hour_7d": _shift(A, 168 - h),
        # recent climatology of the target hour-of-day: mean over k = 1..K days
        "hod_rate_7d": hod7,
        "hod_rate_28d": hod28,
        "nat_same_hour_1d": np.repeat(
            _shift(A.mean(axis=1, keepdims=True), 24 - h), A.shape[1], axis=1),
    }
    f["nb_same_hour_1d"] = _neigh_mean(f["same_hour_1d"], p.regions)
    f["nb_hod_rate_7d"] = _neigh_mean(f["hod_rate_7d"], p.regions)
    return f


def build_dataset(p: HourlyPanel, horizons=config.HORIZONS,
                  origin_start: pd.Timestamp | None = None,
                  extra: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
    """Long table: one row per (origin hour, region, horizon) with target ``y``.

    Rows whose target hour lies beyond the panel are kept with ``y = NaN`` so that
    the latest origins can still be used for live forecasting.
    """
    T, R = p.occ.shape
    of = origin_features(p)
    if extra:  # e.g. Telegram text features, already aligned with panel rows
        of = {**of, **extra}
    A = p.any_active.astype(np.float32)
    i0 = 0 if origin_start is None else int(p.index.searchsorted(origin_start))
    rows = np.arange(i0, T)
    origin_time = p.index[rows] + pd.Timedelta(hours=1)
    frames = []
    for h in horizons:
        hf = horizon_features(p, h)
        y = _shift(A, -h)  # y[i] = A[i + h]
        target_time = p.index[rows] + pd.Timedelta(hours=h)  # start of target hour
        local = target_time.tz_convert(config.LOCAL_TZ)
        cols = {name: arr[rows].ravel() for name, arr in {**of, **hf}.items()}
        df = pd.DataFrame(cols)
        df["origin_time"] = np.repeat(origin_time, R)
        df["region"] = pd.Categorical(np.tile(p.regions, len(rows)), categories=p.regions)
        df["h"] = h
        df["target_hour_local"] = np.repeat(local.hour, R)
        df["target_dow_local"] = np.repeat(local.dayofweek, R)
        df["origin_hour_local"] = np.repeat(
            origin_time.tz_convert(config.LOCAL_TZ).hour, R)
        df["y"] = y[rows].ravel()
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["target_time"] = out["origin_time"] + pd.to_timedelta(out["h"] - 1, unit="h")
    return out


META_COLS = ["origin_time", "target_time", "region", "y"]


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META_COLS]
