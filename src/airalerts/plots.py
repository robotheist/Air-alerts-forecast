"""Static figures for the report (matplotlib, PNG)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from . import config  # noqa: E402

# categorical slots in fixed order (validated default palette)
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUES = LinearSegmentedColormap.from_list(
    "blues", ["#fcfcfb", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])

plt.rcParams.update({
    "figure.dpi": 130, "savefig.bbox": "tight", "font.size": 9,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "lines.linewidth": 2,
})

SHORT = {r: r.replace(" oblast", "") for r in config.REGIONS}


def _save(fig, name):
    config.FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.FIGURES / name, facecolor="white")
    plt.close(fig)


def alert_share_over_time(panel, name="01_alert_share_over_time.png"):
    s = pd.Series(panel.any_active.mean(axis=1), index=panel.index)
    w = s.resample("W").mean()
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(w.index, w.values, color=C[0])
    ax.set_title("Share of region-hours with an active alert, weekly")
    ax.set_ylabel("share of region-hours")
    ax.set_ylim(0, None)
    ax.annotate(f"{w.iloc[-1]:.0%} last week", (w.index[-1], w.iloc[-1]),
                xytext=(-80, 10), textcoords="offset points", color=INK2)
    _save(fig, name)


def hour_region_heatmap(panel, days=28, name="02_hour_by_region_heatmap.png"):
    idx = panel.index[-24 * days:].tz_convert(config.LOCAL_TZ)
    A = pd.DataFrame(panel.any_active[-24 * days:], index=idx, columns=panel.regions)
    m = A.groupby(A.index.hour).mean().T
    m = m.loc[m.mean(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(9, 6))
    im = ax.imshow(m.values, aspect="auto", cmap=BLUES, vmin=0, vmax=1)
    ax.set_yticks(range(len(m)), [SHORT[r] for r in m.index])
    ax.set_xticks(range(0, 24, 2), [f"{h:02d}" for h in range(0, 24, 2)])
    ax.set_xlabel("hour of day, Kyiv time")
    ax.grid(False)
    ax.set_title(f"P(alert during hour) by region and hour of day, last {days} days")
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cb.outline.set_visible(False)
    _save(fig, name)


MODEL_COLORS = {"logreg_v1": C[0], "lgb_v2": C[1], "lgb_v3_text": C[2]}


def skill_by_horizon(table: pd.DataFrame, quiet_table: pd.DataFrame,
                     models=("logreg_v1", "lgb_v2"), ref="climatology_28d",
                     name="03_skill_by_horizon.png"):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    panels = [(table, "All forecast origins"),
              (quiet_table, "Only origins with no alert running (new alerts)")]
    for ax, (tab, title) in zip(axes, panels):
        t = tab[tab.h != "all"].copy()
        t["h"] = t.h.astype(int)
        ref_b = t[t.model == ref].set_index("h").brier
        for m in models:
            if m not in set(t.model):
                continue
            g = t[t.model == m].set_index("h").sort_index()
            bss = 1 - g.brier / ref_b.loc[g.index]
            ax.plot(g.index, bss, marker="o", ms=5, color=MODEL_COLORS[m], label=m)
            ax.annotate(f"{bss.iloc[0]:+.0%}", (g.index[0], bss.iloc[0]),
                        xytext=(6, 4), textcoords="offset points", color=INK2, fontsize=8)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_title(title, fontsize=10)
        ax.set_xscale("log")
        ax.set_xticks(config.HORIZONS, [str(h) for h in config.HORIZONS])
        ax.set_xlabel("forecast horizon, hours")
    axes[0].set_ylabel("Brier skill vs. climatology")
    axes[0].legend(loc="upper right")
    fig.suptitle("Improvement over the 28-day hour-of-day climatology (test period)",
                 x=0.06, ha="left", fontweight="bold", fontsize=11, y=1.03)
    _save(fig, name)


def reliability_plot(df: pd.DataFrame, col: str, horizons=(1, 6, 24),
                     name="04_reliability.png"):
    from .evaluation import reliability
    fig, ax = plt.subplots(figsize=(4.6, 4.4))
    ax.plot([0, 1], [0, 1], color=INK2, lw=0.8, ls="--")
    for k, h in enumerate(horizons):
        g = df[df.h == h]
        r = reliability(g.y.to_numpy(), g[col].to_numpy())
        r = r[r.n >= 50]
        ax.plot(r.p_mean, r.y_mean, marker="o", ms=5, color=C[k], label=f"h = {h} h")
    ax.set_xlabel("forecast probability")
    ax.set_ylabel("observed frequency")
    ax.set_title("Calibration on the test period")
    ax.legend(loc="upper left")
    ax.set_aspect("equal")
    _save(fig, name)


def example_timeline(df: pd.DataFrame, col: str, region: str, h: int, days=7,
                     name="05_example_timeline.png"):
    g = df[(df.region == region) & (df.h == h)].sort_values("target_time")
    g = g[g.target_time >= g.target_time.max() - pd.Timedelta(days=days)]
    t = g.target_time.dt.tz_convert(config.LOCAL_TZ)
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.fill_between(t, 0, g.y, step="post", color="#cde2fb", lw=0, label="alert active")
    ax.plot(t, g[col], color=C[1], lw=1.6, label=f"forecast made {h} h earlier")
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("P(alert)")
    ax.set_title(f"{region}: forecast {h} h ahead vs. what happened (last {days} days of test)")
    ax.legend(loc="upper left", ncols=2)
    _save(fig, name)


def importance_plot(imp: pd.Series, top=15, name="06_feature_importance.png"):
    s = (imp / imp.sum()).head(top)[::-1]
    fig, ax = plt.subplots(figsize=(6, 4.6))
    ax.barh(s.index, s.values, color=C[0], height=0.6)
    ax.set_xlabel("share of total gain")
    ax.set_title("What the model relies on (LightGBM gain)")
    ax.grid(axis="y", visible=False)
    _save(fig, name)
