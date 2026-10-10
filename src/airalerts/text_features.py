"""Text features from the Air Force Telegram channel (@kpszsu).

Pipeline
--------
1. ``build_gazetteer``: settlement name -> oblast from the official KATOTTG
   catalogue, with Ukrainian case forms ("Козин", "Українку", "Ніжина", ...).
2. ``extract``: for every post, which regions it mentions (explicit oblast names
   such as "Сумщина"/"Сумської обл." or settlements after a preposition such as
   "на Козин") and which threat types it mentions (drones, missiles, ballistics...).
3. ``text_feature_arrays``: for every forecast origin T (end of a panel hour) and
   region, counts of mentions in the windows [T - w, T). Only posts published
   strictly before T are used.
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict

import numpy as np
import pandas as pd

from . import config
from .data import HourlyPanel

KATOTTG_URL = ("https://raw.githubusercontent.com/0G3RA/ua-geo-set/main/data/raw-kattog.json")
GAZETTEER_CSV = config.ROOT / "data" / "external" / "gazetteer.csv"

OBLAST_UA = {  # KATOTTG level-1 name -> dataset region
    "Вінницька": "Vinnytska oblast", "Волинська": "Volynska oblast",
    "Дніпропетровська": "Dnipropetrovska oblast", "Донецька": "Donetska oblast",
    "Житомирська": "Zhytomyrska oblast", "Закарпатська": "Zakarpatska oblast",
    "Запорізька": "Zaporizka oblast", "Івано-Франківська": "Ivano-Frankivska oblast",
    "Київська": "Kyivska oblast", "Кіровоградська": "Kirovohradska oblast",
    "Львівська": "Lvivska oblast", "Миколаївська": "Mykolaivska oblast",
    "Одеська": "Odeska oblast", "Полтавська": "Poltavska oblast",
    "Рівненська": "Rivnenska oblast", "Сумська": "Sumska oblast",
    "Тернопільська": "Ternopilska oblast", "Харківська": "Kharkivska oblast",
    "Херсонська": "Khersonska oblast", "Хмельницька": "Khmelnytska oblast",
    "Черкаська": "Cherkaska oblast", "Чернівецька": "Chernivetska oblast",
    "Чернігівська": "Chernihivska oblast",
}

# Explicit region names: oblast adjective, colloquial "-щина", regional centre.
REGION_PATTERNS = {
    "Vinnytska oblast": r"вінниц|вінниччин",
    "Volynska oblast": r"волин|луцьк",
    "Dnipropetrovska oblast": r"дніпропетров|дніпр[оаіу]\b|дніпрі\b|кривий ріг|кривого рогу|кривому розі|криворіж|павлоград|нікопол",
    "Donetska oblast": r"донеччин|донецьк|краматорськ|слов.янськ|покровськ|дружківк|костянтинівк",
    "Zhytomyrska oblast": r"житомир|коростен|бердич",
    "Zakarpatska oblast": r"закарпат|ужгород|мукачев",
    "Zaporizka oblast": r"запоріж|запорізьк",
    "Ivano-Frankivska oblast": r"франків|прикарпат",
    "Kyivska oblast": r"київщин|київськ\w* обл|київськ\w* област",
    "Kyiv City": r"\bкиїв\b|\bкиєв[аіу]\b|столиц",
    "Kirovohradska oblast": r"кіровоград|кропивницьк",
    "Lvivska oblast": r"львів",
    "Mykolaivska oblast": r"миколаїв",
    "Odeska oblast": r"одес|одещин|ізмаїл",
    "Poltavska oblast": r"полтав|кременчу|миргород",
    "Rivnenska oblast": r"рівненськ|рівненщин|\bрівне\b|\bрівного\b",
    "Sumska oblast": r"сумщин|сумськ|\bсуми\b|\bсумах\b|шостк|конотоп|охтирк",
    "Ternopilska oblast": r"тернопіл|тернопол",
    "Kharkivska oblast": r"харків|ізюм|куп.янськ|чугу",
    "Khersonska oblast": r"херсон",
    "Khmelnytska oblast": r"хмельниц|хмельниччин|кам.янець-подільськ|старокостянтин",
    "Cherkaska oblast": r"черкас|черкащин|умань|умані",
    "Chernivetska oblast": r"чернівц|буковин",
    "Chernihivska oblast": r"чернігів|ніжин|прилук",
}
REGION_RE = {r: re.compile(p) for r, p in REGION_PATTERNS.items()}

THREATS = {
    "drone": r"бпла|шахед|мопед|🛵|дрон",
    "jet": r"реактивн|🏍",
    "missile": r"ракет|крилат|калібр|х-101|х-59|х-69|х-22|🚀|швидкісн",
    "ballistic": r"балістик|балістич|іскандер|кн-23",
    "kab": r"\bкаб|керован\w* авіабомб",
    "aviation": r"ту-95|ту-160|ту-22|міг-31|миг-31|стратегічн|тактичн\w* авіац|су-34|су-35",
    "launch": r"пуск|зліт|злет",
}
THREAT_RE = {k: re.compile(p) for k, p in THREATS.items()}

PREPOSITIONS = {"на", "в", "у", "до", "з", "із", "зі", "біля", "повз", "через", "напрямку",
                "бік", "районі", "району", "над", "від", "курсом", "околиці", "околицях"}
WORD_RE = re.compile(r"[А-ЯІЇЄҐа-яіїєґ'’ʼ-]+")


# ----------------------------------------------------------------- gazetteer
def _forms(word: str) -> set[str]:
    """Rough Ukrainian case forms (nom/acc/gen/loc) of a place-name word."""
    w = word
    f = {w}
    if w.endswith("ка"):
        f |= {w[:-1] + "у", w[:-1] + "и", w[:-2] + "ці"}
    elif w.endswith("а"):
        f |= {w[:-1] + "у", w[:-1] + "и", w[:-1] + "і", w[:-1] + "ої", w[:-1] + "ій"}
    elif w.endswith("я"):
        f |= {w[:-1] + "ю", w[:-1] + "ї", w[:-1] + "і"}
    elif w.endswith(("е", "є")):
        f |= {w[:-1] + "ого", w[:-1] + "ому", w[:-1] + "і"}
    elif w.endswith("ий"):
        f |= {w[:-2] + "ого", w[:-2] + "ому"}
    elif w.endswith("и"):
        f |= {w[:-1], w[:-1] + "ів", w[:-1] + "ах"}
    elif w.endswith("ів"):
        f |= {w[:-2] + "ова", w[:-2] + "ові", w[:-2] + "еві"}
    elif w.endswith(("іль", "іл")):
        stem = w[: -3 if w.endswith("іль") else -2]
        f |= {stem + "оля", stem + "олі"}
    elif w.endswith("ь"):
        f |= {w[:-1] + "я", w[:-1] + "і"}
    elif w[-1:].isalpha():
        f |= {w + "а", w + "у", w + "і"}
    return f


def _phrase_forms(name: str) -> set[str]:
    words = name.split()
    if len(words) == 1:
        return _forms(words[0])
    # multi-word: decline only the last word, keep (or decline) the first ones loosely
    out = set()
    for last in _forms(words[-1]):
        for first in _forms(words[0]) if len(words) == 2 else {words[0]}:
            out.add(" ".join([first, *words[1:-1], last]))
    return out


def build_gazetteer(path=GAZETTEER_CSV) -> pd.DataFrame:
    """Settlement name -> oblast, resolving ambiguous names by settlement rank:
    a city (M) beats an urban-type settlement (T/X) which beats a village (C).
    Names still shared by several oblasts at the top rank are dropped."""
    with urllib.request.urlopen(KATOTTG_URL, timeout=60) as r:
        items = json.load(r)["items"]
    obl = {i["level1"]: i["name"] for i in items if i["category"] == "O"}
    rank = {"M": 3, "T": 2, "X": 2, "C": 1}
    best: dict[str, tuple[int, set[str]]] = {}
    for i in items:
        if i["category"] not in rank or obl.get(i["level1"]) not in OBLAST_UA:
            continue
        name, rk, region = i["name"], rank[i["category"]], OBLAST_UA[obl[i["level1"]]]
        cur = best.get(name)
        if cur is None or rk > cur[0]:
            best[name] = (rk, {region})
        elif rk == cur[0]:
            cur[1].add(region)
    rows = [(n, next(iter(rs)), rk) for n, (rk, rs) in best.items() if len(rs) == 1 and len(n) >= 4]
    rows.append(("Київ", "Kyiv City", 4))
    g = pd.DataFrame(rows, columns=["name", "region", "rank"]).sort_values("name")
    path.parent.mkdir(parents=True, exist_ok=True)
    g.to_csv(path, index=False)
    return g


def load_gazetteer(path=GAZETTEER_CSV) -> dict[str, str]:
    g = pd.read_csv(path) if path.exists() else build_gazetteer(path)
    forms: dict[str, set[str]] = defaultdict(set)
    for name, region in zip(g.name, g.region):
        for f in _phrase_forms(_norm(name)):
            forms[f.lower()].add(region)
    return {f: next(iter(rs)) for f, rs in forms.items() if len(rs) == 1}


def _norm(s: str) -> str:
    return s.replace("’", "'").replace("ʼ", "'")


# ----------------------------------------------------------------- extraction
def extract(posts: pd.DataFrame, gaz: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (mentions: post_time, region, how) and (threats: post_time x threat flags)."""
    mentions, threats = [], []
    for t, text in zip(posts.published_at, posts.text.fillna("")):
        text = _norm(str(text))
        low = text.lower()
        found = {r for r, rx in REGION_RE.items() if rx.search(low)}
        for r in found:
            mentions.append((t, r, "region"))
        words = WORD_RE.findall(text)
        for k, wd in enumerate(words):
            if not wd[:1].isupper() or k == 0 or words[k - 1].lower() not in PREPOSITIONS:
                continue
            for n in (3, 2, 1):
                phrase = " ".join(words[k:k + n]).lower()
                r = gaz.get(phrase)
                if r is not None:
                    if r not in found:
                        mentions.append((t, r, "settlement"))
                        found.add(r)
                    break
        threats.append({"published_at": t, **{k: int(bool(rx.search(low)))
                                                 for k, rx in THREAT_RE.items()}})
    return (pd.DataFrame(mentions, columns=["published_at", "region", "how"]),
            pd.DataFrame(threats))


def load_posts(channel="kpszsu") -> pd.DataFrame:
    p = pd.read_csv(config.DATA_RAW / f"telegram_{channel}.csv")
    p["published_at"] = pd.to_datetime(p["published_at"], utc=True, format="ISO8601")
    return p.sort_values("published_at").reset_index(drop=True)


# ----------------------------------------------------------------- features
WINDOWS_MIN = (30, 60, 180)
THREAT_WINDOWS_MIN = (60, 360)


def _window_counts(times: np.ndarray, origins: np.ndarray, w_min: int) -> np.ndarray:
    """# of events with origin - w <= t < origin, for each origin (int64 ns arrays)."""
    hi = np.searchsorted(times, origins, side="left")
    lo = np.searchsorted(times, origins - np.int64(w_min) * 60_000_000_000, side="left")
    return (hi - lo).astype(np.float32)


def text_feature_arrays(p: HourlyPanel, posts: pd.DataFrame, mentions: pd.DataFrame,
                        threats: pd.DataFrame) -> dict[str, np.ndarray]:
    """(T, R) arrays aligned with panel rows; origin of row i is index[i] + 1h.

    Rows before the first scraped post are NaN (no text available yet)."""
    origins = (p.index + pd.Timedelta(hours=1)).asi8
    T, R = len(origins), len(p.regions)
    first_post = posts.published_at.min().value
    valid = origins >= first_post + 6 * 3600 * 10**9  # 6 h warm-up for the windows
    f: dict[str, np.ndarray] = {}

    per_region = {r: np.sort(mentions.loc[mentions.region == r, "published_at"].astype("int64")
                             .to_numpy()) for r in p.regions}
    for w in WINDOWS_MIN:
        a = np.zeros((T, R), np.float32)
        for j, r in enumerate(p.regions):
            a[:, j] = _window_counts(per_region[r], origins, w)
        f[f"tg_reg_{w}m"] = a
    since = np.zeros((T, R), np.float32)
    for j, r in enumerate(p.regions):
        times = per_region[r]
        if len(times) == 0:
            since[:, j] = 7 * 24 * 60
            continue
        k = np.searchsorted(times, origins, side="left") - 1
        last = np.where(k >= 0, times[np.clip(k, 0, None)], np.int64(-1) * 10**18)
        since[:, j] = np.minimum((origins - last) / 6e10, 7 * 24 * 60)
    f["tg_reg_since_min"] = since

    # neighbours' mentions in the last hour
    idx = {r: j for j, r in enumerate(p.regions)}
    W = np.zeros((R, R), np.float32)
    for r, ns in config.NEIGHBOURS.items():
        for n in ns:
            W[idx[r], idx[n]] = 1.0
    W /= W.sum(axis=1, keepdims=True)
    f["tg_nb_60m"] = f["tg_reg_60m"] @ W.T

    all_times = np.sort(posts.published_at.astype("int64").to_numpy())
    for w in (60, 180):
        f[f"tg_posts_{w}m"] = np.repeat(_window_counts(all_times, origins, w)[:, None], R, axis=1)
    for k in THREATS:
        times = np.sort(threats.loc[threats[k] == 1, "published_at"].astype("int64").to_numpy())
        for w in THREAT_WINDOWS_MIN:
            f[f"tg_{k}_{w}m"] = np.repeat(_window_counts(times, origins, w)[:, None], R, axis=1)

    for a in f.values():
        a[~valid] = np.nan
    return f
