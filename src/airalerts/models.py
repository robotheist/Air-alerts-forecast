"""Baselines and learned models. Every model maps a feature frame to P(alert)."""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------- baselines
# Baselines need no training: they read a single, already leakage-safe column.

BASELINES = {
    "persistence": "active_now",        # "whatever is happening now continues"
    "seasonal_1d": "same_hour_1d",      # "same as this hour yesterday"
    "climatology_28d": "hod_rate_28d",  # recent frequency of alerts at this hour of day
}


def predict_baseline(df: pd.DataFrame, name: str) -> np.ndarray:
    col = BASELINES[name]
    return df[col].fillna(df["any_28d"]).to_numpy(float)


# ---------------------------------------------------------------- v1: logistic regression
# First attempt: per-region own history only, one linear model for all horizons.

V1_FEATURES = ["active_now", "occ_1h", "occ_3h", "occ_24h", "same_hour_1d",
               "hod_rate_7d", "any_7d", "h"]


class LogRegModel:
    features = V1_FEATURES

    def fit(self, df: pd.DataFrame):
        self.m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))
        self.m.fit(df[self.features].fillna(0), df.y.astype(int))
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.m.predict_proba(df[self.features].fillna(0))[:, 1]


# ---------------------------------------------------------------- v2: gradient boosting
LGB_PARAMS = dict(
    objective="binary",
    learning_rate=0.05,
    num_leaves=63,
    min_child_samples=200,
    feature_fraction=0.8,
    bagging_fraction=0.8,
    bagging_freq=1,
    lambda_l2=1.0,
    verbose=-1,
    seed=42,
)


# Chosen on the validation month (see docs/experiment_log.md). Adding "regime"
# features (national 7-day level, hours since a massive attack, trends) made the
# validation scores *worse*, so they are built but not used.
V2_FEATURES = [
    "active_now", "run_min", "since_end_min",
    "occ_1h", "occ_3h", "occ_6h", "occ_24h", "any_7d", "any_28d", "onsets_24h",
    "nb_active_now", "nb_occ_3h", "nb_any_24h",
    "nat_active_now", "nat_any_1h", "nat_onsets_1h", "nat_onsets_3h", "nat_change_1h",
    "same_hour_1d", "same_hour_7d", "hod_rate_7d", "hod_rate_28d", "nat_same_hour_1d",
    "h", "target_hour_local", "target_dow_local", "origin_hour_local",
]


TEXT_FEATURES = [
    "tg_reg_30m", "tg_reg_60m", "tg_reg_180m", "tg_reg_since_min", "tg_nb_60m",
    "tg_posts_60m", "tg_posts_180m",
    *[f"tg_{k}_{w}m" for k in ("drone", "jet", "missile", "ballistic", "kab", "aviation", "launch")
      for w in (60, 360)],
]
V3_FEATURES = V2_FEATURES + TEXT_FEATURES


class LGBModel:
    """One global LightGBM model for all regions and horizons.

    ``half_life_days`` down-weights old samples (the war's alert regime drifts).
    """

    def __init__(self, features: list[str] = V2_FEATURES, n_rounds: int = 250,
                 half_life_days: float | None = 180, params: dict | None = None):
        self.features = features
        self.n_rounds = n_rounds
        self.half_life_days = half_life_days
        self.params = {**LGB_PARAMS, **(params or {})}

    def fit(self, df: pd.DataFrame):
        w = None
        if self.half_life_days:
            age = (df.origin_time.max() - df.origin_time).dt.total_seconds() / 86400
            w = 0.5 ** (age.to_numpy() / self.half_life_days)
        ds = lgb.Dataset(df[self.features], df.y.astype(int), weight=w,
                         categorical_feature=["region"] if "region" in self.features else "auto")
        self.booster = lgb.train(self.params, ds, num_boost_round=self.n_rounds)
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(df[self.features])

    def importance(self) -> pd.Series:
        return pd.Series(self.booster.feature_importance("gain"), index=self.features) \
            .sort_values(ascending=False)
