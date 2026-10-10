# Experiment log

Chronological notes on what was tried, what failed and why the design changed.
Numbers are Brier scores (lower is better) unless stated otherwise.
*Validation* = 28 days before the test period (15.08–12.09.2026), *test* = last
28 days (12.09–10.10.2026, never used for any choice).

## 0. Data choice
- Two sources in [Vadimkin/ukrainian-air-raid-sirens-dataset](https://github.com/Vadimkin/ukrainian-air-raid-sirens-dataset):
  *official* (raion/hromada level) and *volunteer* (eTryvoga, oblast level).
- The official file stops on 2026-09-07 and changed its unit from oblast to raion in Dec 2025
  (a structural break). The volunteer file is updated daily (last record: the morning of
  2026-10-10) and is consistent since 2022 → **volunteer data is used**.
- Luhansk oblast (permanent alert since 2022) is excluded; 24 regions remain (incl. Kyiv City).
- Alerts longer than 24 h (missing "all clear" messages) are capped at 24 h.

## 1. Problem framing
At the end of every hour `T`, for every region, predict P(an alert is active at any moment of
hour `[T+h-1, T+h)`) for `h ∈ {1, 3, 6, 12, 24}`.
- Alerts are rasterised to a minute-level on/off matrix. This is exactly the information a
  real-time observer has: at time `T` they know which sirens were on, but **not** when an
  ongoing alert will end. Features only aggregate rows `≤ T`.
- `tests/test_leakage.py` cuts the alert log at a moment `C` (drops later alerts, truncates
  running ones at `C`) and checks every feature for origins `≤ C` is bit-identical to the
  full-history version.
- A second, subtler leak: a training row with origin before the test start but **target**
  after it (e.g. `h = 24` made 5 h before the cut-off). `train_mask` requires the target hour
  to end before the cut-off.

## 2. Baselines (validation)
| model | h=1 | h=6 | h=24 |
|---|---|---|---|
| persistence (state now) | 0.190 | 0.314 | 0.313 |
| same hour yesterday | 0.319 | 0.319 | 0.323 |
| climatology: 28-day rate at that hour of day | 0.171 | 0.171 | 0.174 |

Persistence and "yesterday" are hard 0/1 forecasts → terrible Brier/log-loss.
Climatology is the baseline to beat.

## 3. First attempt — logistic regression (v1)
Own-region lags + `h` as a numeric feature, trained on everything since mid-2023.
- h=1: 0.150 (beats climatology), **h≥3: 0.172–0.176 — worse than climatology.**
- What went wrong: (a) a linear model cannot express "recent state matters for h=1 but
  not for h=24" when `h` is just another additive input; (b) the base rate drifted
  (19.8 % of region-hours over the whole war vs 33 % in the last 4 weeks), and a single
  intercept fitted on 3 years under-predicts the current regime.

## 4. Gradient boosting (v2)
LightGBM, one global model for all regions and horizons, plus neighbour-region state
(adjacency graph of oblasts) and national context (share of regions on alert, new alerts
in last 1–3 h).
- h=1: 0.120, h=6: 0.166, h=24: 0.170 — beats climatology at every horizon.
- Training window (validation, all horizons): 90 d 0.160 · 180 d 0.158 · 365 d 0.158 · all 0.157.
  More history helps slightly even though the regime drifts.
- Time-decay weights (half-life 90/180/365 d) vs none: differences ≤ 0.0003 — noise. Kept
  180 d half-life as a cheap hedge against drift.
- Per-horizon models (5 separate boosters) were **worse** (0.1605 vs 0.1597): the shared
  model pools information across horizons.
- **Extra "regime" features made it worse** (0.1597 vs 0.1576): national 7-day level,
  hours since last massive attack (≥ 60 % of regions), 7d-vs-28d trend, neighbours'
  seasonal lags. They let the model memorise past regimes that do not repeat.
  They stay in the feature table but are not used by the model.
- Region ID as a categorical: no change (the 28-day rate already encodes the region).

## 5. Test (walk-forward, weekly refit) and a sobering finding
| model | all origins h=1 | h=6 | quiet origins h=1 | h=6 |
|---|---|---|---|---|
| climatology | 0.184 | 0.185 | 0.160 | 0.177 |
| logreg v1 | 0.163 | 0.187 | 0.153 | 0.178 |
| LightGBM v2 | **0.131** | **0.179** | **0.147** | **0.172** |

Calibration of v2 is close to the diagonal at every horizon.

**What went wrong:** the impressive +29 % skill at h=1 comes mostly from alerts that are
*already running* (an alert in progress will probably still be on in an hour). Looking only
at "quiet" origins — where the real question is *will a new alert start?* — skill falls to
+8 %. The alert history alone carries little information about the next attack; beyond 3 h
everything collapses to ≈ climatology (+2–3 %).

This motivates the optional text data: Air Force / monitoring Telegram channels announce
launches and drone directions before sirens are switched on.

## 6. Telegram data (@kpszsu, Air Force of Ukraine)
- 41,241 posts, 2025-10-01 → 2026-10-10, scraped from the public web preview `t.me/s/kpszsu`
  (no API keys). Post IDs are continuous apart from 127 deleted posts.
- **What went wrong (1):** neither the cloud sandbox nor the local sandbox could reach t.me, so the
  Python scraper (`scripts/00_scrape_telegram.py`) could not run there. The same logic was re-written
  in JavaScript (`scripts/browser_scraper.js`) and run inside a browser, saving CSV parts to disk.
- **What went wrong (2):** three parallel workers downloaded their parts at the same moment and the
  browser silently kept only one file per moment. **4 of 7 parts were lost.** This was caught by checking
  post-ID gaps after merging. The missing ID ranges were re-scraped, and downloads were serialised.
  The merge script now reports ID gaps.

## 7. From text to features
- Regions are found by (a) explicit names: oblast adjectives, "-щина" forms, regional centres, Kyiv
  districts; (b) settlements from the official KATOTTG catalogue (14k unambiguous names, Ukrainian
  case forms generated by rules) that follow a preposition ("на Козин", "в напрямку Білої Церкви")
  or form a whole short post ("Боярка."). Ambiguous names are resolved as city > town > village.
- Coverage on a hand-checked week of posts: 93 % of posts → ≥ 1 region on the first try. After adding
  common misspellings ("Вінничині"), Kyiv neighbourhoods, "-ець"/"-їв" case forms and the rule that
  an exact name beats a generated case form of another name, coverage rose to **97.9 %**.
- Threat types: drones, jet drones, missiles, ballistic, guided bombs (KAB), aviation, launches,
  all-clear. Features: region mentions in the last 30/60/180 min, minutes since last mention,
  neighbours' mentions, national counts per threat type in the last 1 h / 6 h.
- **What went wrong (3):** pandas 3 parses timestamps at microsecond resolution, so
  `astype("int64")` produced microseconds while the panel used nanoseconds. Every text feature was
  silently zero or NaN. A lead-time sanity check showed "0 % of random moments have a mention", which
  exposed the bug. All conversions now go through `to_ns()`. The leakage test now uses µs data and
  asserts plausible counts, so it fails on the old code.

## 8. Does the channel speak before the siren?
`scripts/05_text_lead_time.py`, 38,789 alert onsets:

| region mentioned within … before onset | 5 min | 15 min | 30 min | 60 min |
|---|---|---|---|---|
| before an alert onset | 13 % | 23 % | 32 % | 44 % |
| at a random moment | 4 % | 10 % | 18 % | 29 % |

The signal is real: a mention in the last 30 min almost doubles the chance that an alert follows.
Among quiet origins, P(alert next hour) is 0.41 with a mention in the last hour vs 0.13 without.
But two thirds of onsets get no prior mention, and the lead time is mostly under 15 minutes.

## 9. v3 = v2 + text features
Fair comparison: v2 re-trained on the same text-covered window (`lgb_v2_short`).

Validation (15.08–12.09): h=1 Brier 0.1203 → **0.1174** (−2.4 %), quiet h=1 0.1338 → 0.1305,
ROC-AUC quiet h=1 0.804 → 0.818; no gain at h ≥ 6. Shorter windows (10 min) added nothing.

Test (12.09–10.10, weekly walk-forward), paired day-block bootstrap (`scripts/06_significance.py`):

| horizon | v2 (same window) | v3 text | change | 95 % CI of Δ Brier | P(no gain) |
|---|---|---|---|---|---|
| 1 h | 0.1310 | 0.1304 | −0.4 % | [−0.0015, +0.0005] | 0.15 |
| 3 h | 0.1777 | 0.1770 | −0.4 % | [−0.0015, +0.0000] | 0.03 |
| 6–24 h | 0.179–0.180 | 0.179–0.180 | ±0.1 % | includes 0 | > 0.3 |

**Conclusion:** the channel helps a little at 1–3 h. The gain is clear on validation but small and
borderline-significant on the test month. Why it is small:
1. The alert history already sees the same moving threat through neighbouring sirens and the national
   wave features.
2. The channel's lead time (minutes) is shorter than the hourly resolution of the target.
3. September–October doubled the post volume (6.3k posts in September vs ~2.5k/month in winter),
   which shifts the count features.

A 10–15-minute target or per-post direction parsing ("курсом на захід") are the natural next steps.
