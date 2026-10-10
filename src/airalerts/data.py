"""Download, clean and convert the raw alert log into an hourly region panel.

The raw data is a list of alert intervals ``(region, started_at, finished_at)``.
We rasterise it to a minute-level on/off matrix (a real-time siren status that
is fully observable at every moment) and aggregate it to hours.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config

# Alerts longer than this are almost always missing "all clear" messages.
MAX_ALERT_MINUTES = 24 * 60


def download(url: str = config.DATASET_URL, dest=config.RAW_CSV) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def load_alerts(path=config.RAW_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["started_at"] = pd.to_datetime(df["started_at"], utc=True)
    df["finished_at"] = pd.to_datetime(df["finished_at"], utc=True)
    df = df[df["region"].isin(config.REGIONS)].copy()
    dur = (df["finished_at"] - df["started_at"]).dt.total_seconds() / 60
    df = df[dur >= 0]
    cap = df["started_at"] + pd.Timedelta(minutes=MAX_ALERT_MINUTES)
    df["finished_at"] = df["finished_at"].where(df["finished_at"] <= cap, cap)
    return df.sort_values("started_at").reset_index(drop=True)


@dataclass
class HourlyPanel:
    """Hour x region arrays. Row i describes the hour [index[i], index[i] + 1h)."""

    index: pd.DatetimeIndex          # hour starts, UTC
    regions: list[str]
    occ: np.ndarray                  # fraction of minutes under alert, float32
    onsets: np.ndarray               # number of alerts that started in the hour
    active_end: np.ndarray           # alert active in the last minute of the hour
    run_minutes_end: np.ndarray      # length of current alert at the end of the hour (0 if none)
    since_end_minutes: np.ndarray    # minutes since last alert minute at end of hour

    @property
    def any_active(self) -> np.ndarray:
        return (self.occ > 0).astype(np.int8)


def build_panel(alerts: pd.DataFrame, end: pd.Timestamp | None = None) -> HourlyPanel:
    start = alerts["started_at"].min().floor("h")
    if end is None:
        # Alerts are published once they end; keep a 1h safety margin at the edge.
        end = alerts["finished_at"].max().floor("h") - pd.Timedelta(hours=1)
    n_min = int((end - start).total_seconds() // 60)
    n_hours = n_min // 60
    n_min = n_hours * 60
    regions = config.REGIONS
    minute = np.zeros((n_min + 1, len(regions)), dtype=np.int16)  # difference array
    onsets = np.zeros((n_hours, len(regions)), dtype=np.int16)

    for j, r in enumerate(regions):
        sub = alerts[alerts["region"] == r]
        s = ((sub["started_at"] - start).dt.total_seconds() // 60).astype(int).to_numpy()
        e = np.ceil((sub["finished_at"] - start).dt.total_seconds() / 60).astype(int).to_numpy()
        keep = (s < n_min) & (e > 0)
        s, e = np.clip(s[keep], 0, n_min), np.clip(e[keep], 0, n_min)
        np.add.at(minute[:, j], s, 1)
        np.add.at(minute[:, j], e, -1)
        np.add.at(onsets[:, j], s[s < n_min] // 60, 1)

    on = (np.cumsum(minute[:-1], axis=0) > 0)  # (n_min, R) bool
    occ = on.reshape(n_hours, 60, -1).mean(axis=1).astype(np.float32)
    active_end = on[59::60].astype(np.int8)

    # run length of the current alert and time since the last alert minute,
    # both measured at the last minute of each hour (known in real time).
    t = np.arange(n_min)[:, None]
    last_off = np.maximum.accumulate(np.where(~on, t, -1), axis=0)
    last_on = np.maximum.accumulate(np.where(on, t, -10**7), axis=0)
    run = np.where(on, t - last_off, 0)[59::60]
    since = np.minimum(t - last_on, 7 * 24 * 60)[59::60]

    index = pd.date_range(start, periods=n_hours, freq="h")
    return HourlyPanel(index, regions, occ, onsets, active_end,
                       run.astype(np.float32), since.astype(np.float32))
