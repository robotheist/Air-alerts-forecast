"""Build the "plan around alerts" page: app/planner.html (one self-contained file).

It embeds a snapshot of
  * the model forecast for the next 24 hours from the latest data hour
    (v3 with Telegram for +1 h, v2 for +2..+24 h, as recommended in the README),
  * the "rhythm": for every region, start hour and duration (1-6 h), the share of the
    last 28 days on which an alert touched that window,
  * a backtest of those rhythm probabilities on the latest 28 days,
  * the alert state at the snapshot moment and the last 24 h of @kpszsu posts
    with the regions our text pipeline recognised,
  * a simplified map of the oblasts (data/external/ua_oblasts.geojson, OpenStreetMap).

Run after scripts/02_build_dataset.py:  python scripts/07_build_planner.py
"""
import json
import math
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from airalerts import config, data, evaluation as ev, features, models  # noqa: E402
from airalerts import text_features as tf  # noqa: E402

APP = config.ROOT / "app"
GEOJSON = config.ROOT / "data" / "external" / "ua_oblasts.geojson"
RHYTHM_DAYS = 28
MAX_DUR = 6

ISO = {"UA-05": "Vinnytska oblast", "UA-07": "Volynska oblast", "UA-12": "Dnipropetrovska oblast",
       "UA-14": "Donetska oblast", "UA-18": "Zhytomyrska oblast", "UA-21": "Zakarpatska oblast",
       "UA-23": "Zaporizka oblast", "UA-26": "Ivano-Frankivska oblast", "UA-32": "Kyivska oblast",
       "UA-35": "Kirovohradska oblast", "UA-46": "Lvivska oblast", "UA-48": "Mykolaivska oblast",
       "UA-51": "Odeska oblast", "UA-53": "Poltavska oblast", "UA-56": "Rivnenska oblast",
       "UA-59": "Sumska oblast", "UA-61": "Ternopilska oblast", "UA-63": "Kharkivska oblast",
       "UA-65": "Khersonska oblast", "UA-68": "Khmelnytska oblast", "UA-71": "Cherkaska oblast",
       "UA-74": "Chernihivska oblast", "UA-77": "Chernivetska oblast"}
NAME_UK = {"Vinnytska oblast": "Вінницька", "Volynska oblast": "Волинська",
           "Dnipropetrovska oblast": "Дніпропетровська", "Donetska oblast": "Донецька",
           "Zhytomyrska oblast": "Житомирська", "Zakarpatska oblast": "Закарпатська",
           "Zaporizka oblast": "Запорізька", "Ivano-Frankivska oblast": "Івано-Франківська",
           "Kyivska oblast": "Київська", "Kirovohradska oblast": "Кіровоградська",
           "Lvivska oblast": "Львівська", "Mykolaivska oblast": "Миколаївська",
           "Odeska oblast": "Одеська", "Poltavska oblast": "Полтавська",
           "Rivnenska oblast": "Рівненська", "Sumska oblast": "Сумська",
           "Ternopilska oblast": "Тернопільська", "Kharkivska oblast": "Харківська",
           "Khersonska oblast": "Херсонська", "Khmelnytska oblast": "Хмельницька",
           "Cherkaska oblast": "Черкаська", "Chernivetska oblast": "Чернівецька",
           "Chernihivska oblast": "Чернігівська", "Kyiv City": "м. Київ"}


# ------------------------------------------------------------------ map geometry
def _rdp(pts, eps):
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0]), np.array(pts[-1])
    d = np.array(pts[1:-1]) - a
    ab = b - a
    n = np.hypot(*ab) or 1e-12
    dist = np.abs(ab[0] * d[:, 1] - ab[1] * d[:, 0]) / n
    i = int(np.argmax(dist))
    if dist[i] > eps:
        return _rdp(pts[: i + 2], eps)[:-1] + _rdp(pts[i + 1:], eps)
    return [pts[0], pts[-1]]


def build_map():
    gj = json.loads(GEOJSON.read_text())
    lat0 = math.radians(48.5)
    proj = lambda lon, lat: (lon * math.cos(lat0), -lat)  # noqa: E731 equirectangular
    shapes = []
    for f in gj["features"]:
        rings = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "MultiPolygon":
            rings = [r for poly in rings for r in poly]
        # some features list several outer rings; keep the largest (shoelace area)
        area = lambda r: abs(sum(x0 * y1 - x1 * y0 for (x0, y0, *_), (x1, y1, *_) in zip(r, r[1:])))  # noqa: E731
        ring = max(rings, key=area)
        pts = [proj(*p[:2]) for p in ring]
        shapes.append((f["properties"].get("iso3166-2"), f["properties"].get("name"), pts))
    xs = [x for _, _, p in shapes for x, _ in p]
    ys = [y for _, _, p in shapes for _, y in p]
    x0, y0, W = min(xs), min(ys), 1000.0
    s = W / (max(xs) - x0)
    H = (max(ys) - y0) * s
    out, grey = {}, []
    for iso, name, pts in shapes:
        q = [((x - x0) * s, (y - y0) * s) for x, y in pts]
        # closed ring: first == last point, so simplify the two halves separately
        mid = len(q) // 2
        q = _rdp(q[: mid + 1], 0.8)[:-1] + _rdp(q[mid:], 0.8)
        d = "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in q) + "Z"
        if iso in ISO:
            out[ISO[iso]] = d
        else:
            grey.append({"name": name, "d": d})
    kx, ky = proj(30.52, 50.45)
    kyiv = ((kx - x0) * s, (ky - y0) * s)
    return out, grey, kyiv, (W, H)


# ------------------------------------------------------------------ forecasts
def forecast_next_24h(panel, df, extra):
    origin_start = panel.index[-1]
    rows = features.build_dataset(panel, horizons=range(1, 25), origin_start=origin_start,
                                  extra=extra)
    rows = rows[rows.origin_time == rows.origin_time.max()].copy()
    last = df.origin_time.max()
    print("training v2 on all labelled rows ...", flush=True)
    v2 = models.LGBModel().fit(df[ev.train_mask(df, df.origin_time.min(), last)])
    rows["p"] = v2.predict(rows)
    if extra is not None:
        text_start = df.loc[df.tg_reg_60m.notna(), "origin_time"].min()
        print("training v3 (text) ...", flush=True)
        v3 = models.LGBModel(models.V3_FEATURES).fit(df[ev.train_mask(df, text_start, last)])
        h1 = rows.h == 1
        rows.loc[h1, "p"] = v3.predict(rows[h1])
    return rows


def rhythm(panel):
    """P(alert touches [start, start+d h)) over the last RHYTHM_DAYS local days."""
    A = panel.any_active
    local = panel.index.tz_convert(config.LOCAL_TZ)
    T = len(A)
    out = np.zeros((A.shape[1], 24, MAX_DUR), np.float32)
    starts_all = np.arange(T)
    for s_h in range(24):
        st = starts_all[(local.hour == s_h)]
        for d in range(1, MAX_DUR + 1):
            ok = st[st + d <= T][-RHYTHM_DAYS:]
            win = np.stack([A[ok + k] for k in range(d)]).max(axis=0)  # (days, R)
            out[:, s_h, d - 1] = win.mean(axis=0)
    return out, RHYTHM_DAYS


def backtest_rhythm(panel, days=28):
    """For each of the last `days` days: rhythm from the 28 days before it vs what happened."""
    A = panel.any_active
    local = panel.index.tz_convert(config.LOCAL_TZ)
    T = len(A)
    idx_by_hour = {h: np.where(local.hour == h)[0] for h in range(24)}
    ps, ys = [], []
    for s_h in range(24):
        st = idx_by_hour[s_h]
        for d in (1, 2, 3):
            st_ok = st[st + d <= T]
            win = np.stack([A[st_ok + k] for k in range(d)]).max(axis=0)  # (n_days, R)
            for j in range(len(st_ok) - days, len(st_ok)):
                if j < RHYTHM_DAYS:
                    continue
                ps.append(win[j - RHYTHM_DAYS:j].mean(axis=0))
                ys.append(win[j])
    p, y = np.concatenate(ps), np.concatenate(ys)
    bins = np.linspace(0, 1, 11)
    b = np.clip(np.digitize(p, bins) - 1, 0, 9)
    rel = [{"p": float(p[b == k].mean()), "y": float(y[b == k].mean()), "n": int((b == k).sum())}
           for k in range(10) if (b == k).sum() >= 30]
    return {"brier": float(((p - y) ** 2).mean()),
            "brier_base": float(((y.mean() - y) ** 2).mean()), "n": int(len(y)),
            "reliability": rel}


def main():
    alerts = data.load_alerts()
    panel = data.build_panel(alerts)
    df = pd.read_parquet(config.DATA_PROCESSED / "dataset.parquet")
    extra, posts = None, None
    if (config.DATA_RAW / "telegram_kpszsu.csv").exists():
        posts = tf.load_posts()
        gaz = tf.load_gazetteer()
        m, th = tf.extract(posts, gaz)
        extra = tf.text_feature_arrays(panel, posts, m, th)

    rows = forecast_next_24h(panel, df, extra)
    rh, n_days = rhythm(panel)
    bt = backtest_rhythm(panel)
    print(f"rhythm backtest: Brier {bt['brier']:.3f} vs base rate {bt['brier_base']:.3f}")
    paths, grey, kyiv, (W, H) = build_map()

    origin = rows.origin_time.max()
    to_local = lambda t: t.tz_convert(config.LOCAL_TZ).strftime("%Y-%m-%dT%H:%M")  # noqa: E731
    regions = []
    last = len(panel.index) - 1
    for j, r in enumerate(panel.regions):
        g = rows[rows.region == r].sort_values("h")
        regions.append({
            "id": r, "name": NAME_UK[r], "d": paths.get(r),
            "active": int(panel.active_end[last, j]),
            "since_min": float(panel.since_end_minutes[last, j]),
            "run_min": float(panel.run_minutes_end[last, j]),
            "forecast": [round(float(x), 3) for x in g.p],
            "rhythm": np.round(rh[j], 3).tolist(),
            "neighbours": config.NEIGHBOURS[r],
        })
    hours = [to_local(origin + pd.Timedelta(hours=h - 1)) for h in range(1, 25)]

    feed = []
    if posts is not None:
        recent = posts[posts.published_at >= posts.published_at.max() - pd.Timedelta(hours=24)]
        mm, _ = tf.extract(recent, tf.load_gazetteer())
        regs = mm.groupby("published_at").region.apply(lambda s: sorted(set(s))).to_dict()
        for t, text in zip(recent.published_at, recent.text.fillna("")):
            if text.strip():
                feed.append({"t": to_local(t), "text": text, "regions": regs.get(t, [])})
        feed.reverse()

    payload = {
        "snapshot": {"origin": to_local(origin),
                     "alerts_until": to_local(panel.index[-1] + pd.Timedelta(hours=1)),
                     "telegram_until": to_local(posts.published_at.max()) if posts is not None else None,
                     "rhythm_days": n_days},
        "hours": hours, "regions": regions, "grey": grey,
        "kyiv": [round(kyiv[0], 1), round(kyiv[1], 1)], "viewbox": [round(W), round(H)],
        "backtest": bt, "feed": feed,
    }
    template = (APP / "planner_template.html").read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    (APP / "planner.html").write_text(html, encoding="utf-8")
    print(f"app/planner.html: {len(html) / 1e6:.2f} MB, origin {payload['snapshot']['origin']} Kyiv, "
          f"{len(feed)} posts in the feed")


if __name__ == "__main__":
    main()
