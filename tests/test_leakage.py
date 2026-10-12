"""Leakage tests: features for an origin must not change if we erase the future.

We simulate what was actually known at a cut-off moment ``C``: alerts that start
after ``C`` are removed and alerts still running at ``C`` are cut at ``C``
(their real end was unknown). Every feature for origins ``<= C`` must be
identical to the one computed from the full history.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airalerts import config, data, features  # noqa: E402


def _synthetic_alerts(seed=0, days=60):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-01", tz="UTC")
    rows = []
    for r in config.REGIONS:
        t = start
        while t < start + pd.Timedelta(days=days):
            t += pd.Timedelta(minutes=int(rng.exponential(300)))
            d = pd.Timedelta(minutes=int(rng.exponential(60)) + 1)
            rows.append((r, t, t + d))
            t += d
    return pd.DataFrame(rows, columns=["region", "started_at", "finished_at"])


@pytest.fixture(scope="module")
def alerts():
    if config.RAW_CSV.exists():
        a = data.load_alerts()
        return a[a.started_at >= a.started_at.max() - pd.Timedelta(days=60)]
    return _synthetic_alerts()


@pytest.mark.parametrize("cut_offset_h", [200, 555, 1001])
def test_features_do_not_see_the_future(alerts, cut_offset_h):
    start = alerts.started_at.min().floor("h")
    end = alerts.finished_at.max().floor("h") - pd.Timedelta(hours=1)
    cut = start + pd.Timedelta(hours=cut_offset_h)

    known = alerts[alerts.started_at < cut].copy()
    known["finished_at"] = known["finished_at"].clip(upper=cut)

    p_full = data.build_panel(alerts, end=end)
    p_known = data.build_panel(known, end=end)
    p_known.index = p_full.index  # same grid
    assert p_known.index[0] == start

    d_full = features.build_dataset(p_full)
    d_known = features.build_dataset(p_known)
    mask = d_full.origin_time <= cut
    cols = features.feature_columns(d_full)
    a = d_full.loc[mask, cols].to_numpy(float)
    b = d_known.loc[mask, cols].to_numpy(float)
    np.testing.assert_allclose(a, b, equal_nan=True, rtol=1e-6, atol=1e-5)
    # and the target really is in the future: it must differ somewhere
    assert not np.array_equal(d_full.loc[mask, "y"].fillna(-1), d_known.loc[mask, "y"].fillna(-1))


def test_target_time_after_origin(alerts):
    p = data.build_panel(alerts)
    d = features.build_dataset(p)
    assert (d.target_time >= d.origin_time).all()
    assert ((d.target_time - d.origin_time) == pd.to_timedelta(d.h - 1, unit="h")).all()


def test_text_features_do_not_see_the_future():
    """Posts published at or after the origin must not affect its text features."""
    from airalerts import text_features as tf

    start = pd.Timestamp("2026-03-01", tz="UTC")
    alerts = _synthetic_alerts(days=10)
    alerts["started_at"] += start - pd.Timestamp("2026-01-01", tz="UTC")
    alerts["finished_at"] += start - pd.Timestamp("2026-01-01", tz="UTC")
    p = data.build_panel(alerts)
    rng = np.random.default_rng(1)
    texts = ["🛵 Групи БпЛА на півночі Сумщини", "На Київ", "Ракети на Харків",
             "🏍 Реактивний БпЛА на Теплик", "Загроза балістики з півдня"]
    times = start + pd.to_timedelta(np.sort(rng.uniform(0, 9 * 24 * 60, 3000)), unit="min")
    posts = pd.DataFrame({"published_at": times.as_unit("us"),  # real data is parsed as us
                          "text": rng.choice(texts, len(times))})
    gaz = {"теплик": "Vinnytska oblast"}

    def feats(ps, known_until=None):
        m, th = tf.extract(ps, gaz)
        return tf.text_feature_arrays(p, ps, m, th, known_until=known_until)

    full = feats(posts)
    # sanity: counts must be plausible (~3000 posts over 9 days -> ~14 per hour)
    assert 5 < np.nanmean(full["tg_posts_60m"]) < 30
    cut_row = 100
    cut = p.index[cut_row] + pd.Timedelta(hours=1)          # origin of row 100
    known = feats(posts[posts.published_at < cut], known_until=cut)  # scraped at moment `cut`
    for k in full:
        np.testing.assert_array_equal(full[k][: cut_row + 1], known[k][: cut_row + 1], err_msg=k)
    # a post exactly at the origin time is NOT visible to that origin
    edge = pd.concat([posts[posts.published_at < cut],
                      pd.DataFrame({"published_at": [cut], "text": ["Ракети на Харків"]})])
    e = feats(edge, known_until=cut)
    np.testing.assert_array_equal(e["tg_missile_60m"][cut_row], known["tg_missile_60m"][cut_row])


def test_target_matches_raw_intervals(alerts):
    """y must equal 'some raw alert interval overlaps the target hour' (independent check)."""
    p = data.build_panel(alerts)
    d = features.build_dataset(p, horizons=(1, 6, 24))
    d = d[d.y.notna()].sample(3000, random_state=0)
    for row in d.itertuples():
        a = row.target_time
        b = a + pd.Timedelta(hours=1)
        g = alerts[alerts.region == row.region]
        hit = ((g.started_at < b) & (g.finished_at > a)).any()
        assert int(hit) == int(row.y), (row.region, a)


def test_text_features_are_nan_outside_scraped_range():
    """Origins after the last scraped post must not get fake zero counts."""
    from airalerts import text_features as tf

    alerts = _synthetic_alerts(days=6)
    p = data.build_panel(alerts)
    t0 = p.index[48]
    times = t0 + pd.to_timedelta(np.arange(0, 48 * 60, 7), unit="min")
    posts = pd.DataFrame({"published_at": times, "text": "На Київ"})
    m, th = tf.extract(posts, {})
    f = tf.text_feature_arrays(p, posts, m, th)
    origins = p.index + pd.Timedelta(hours=1)
    after = origins > posts.published_at.max()
    before = origins < posts.published_at.min() + pd.Timedelta(hours=6)
    inside = ~after & ~before
    assert np.isnan(f["tg_posts_60m"][after]).all()
    assert np.isnan(f["tg_posts_60m"][before]).all()
    assert (f["tg_posts_60m"][inside] > 0).all()


def test_gazetteer_regressions():
    """Known false matches found in review must stay fixed (oblast names are not villages)."""
    from airalerts import text_features as tf

    if not tf.GAZETTEER_CSV.exists():
        pytest.skip("gazetteer not built")
    gaz = tf.load_gazetteer()
    cases = {
        "БпЛА на Харківщині курсом на н.п.Чугуїв": {"Kharkivska oblast"},
        "Ракети з Чорного моря на Одесу": {"Odeska oblast"},
        "Реактивний БпЛА в районі Кривого Рогу": {"Dnipropetrovska oblast"},
        "БпЛА на півночі Харківщини, курсом на Золочів": {"Kharkivska oblast"},
        "На Козин": {"Kyivska oblast"},
        "Кам'янець-Подільський: БпЛА в напрямку міста": {"Khmelnytska oblast"},
        "На Нивки.": {"Kyiv City"},
    }
    posts = pd.DataFrame({"published_at": pd.date_range("2026-01-01", periods=len(cases),
                                                        freq="min", tz="UTC"),
                          "text": list(cases)})
    m, _ = tf.extract(posts, gaz)
    for t, expected in zip(posts.published_at, cases.values()):
        assert set(m.loc[m.published_at == t, "region"]) == expected
