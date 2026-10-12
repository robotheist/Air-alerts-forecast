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

# Kyiv city districts and neighbourhoods often appear alone ("Оболонь!", "На Теремки")
KYIV_DISTRICTS = (r"оболон|деміїв|теремк|святошин|сирц|сирець|дарниц|солом.янк|шулявк|академмістеч|"
                  r"жулян|біличі|русанівськ|березняк|берковец|борщагів|троєщин|позняк|осокорк|"
                  r"виноградар|лук.янівк|печерськ|\bподіл\b|\bподолі\b|відрадн|нивк|бортнич|голосіїв|голосієв|лісов\w* масив|"
                  r"харківськ\w* масив|лівобереж|правобереж|вітряні гори|кур.янівк|караваєві дачі|"
                  r"нові будинки|китаїв|чоколів|звіринец|сінн\w* площ|майдан незалежн|биківн|теличк|\bсовк(?:и|ах)\b|"
                  r"пирогів|пирогов|мишоловк|віта-литовськ|липки|липках|феофаній|бессарабк|хрещатик")

# Explicit region names: oblast adjective, colloquial "-щина", regional centre.
REGION_PATTERNS = {
    "Vinnytska oblast": r"вінниц|вінниччин|вінничин",
    "Volynska oblast": r"волин|луцьк",
    "Dnipropetrovska oblast": r"дніпропетр|дніпр[оаіу]\b|дніпрі\b|кривий ріг|кривого рогу|кривому розі|криворіж|павлоград|нікопол",
    "Donetska oblast": r"донеччин|донецьк|краматорськ|слов.янськ|покровськ|дружківк|костянтинівк",
    "Zhytomyrska oblast": r"житомир|коростен|бердич",
    "Zakarpatska oblast": r"закарпат|ужгород|мукачев",
    "Zaporizka oblast": r"запоріж|запорізьк",
    "Ivano-Frankivska oblast": r"франків|прикарпат",
    "Kyivska oblast": r"київщин|київськ",
    "Kyiv City": r"\bкиїв\b|\bкиєв[аіу]\b|\bкиєвом\b|столиц|" + KYIV_DISTRICTS,
    "Kirovohradska oblast": r"кіровогра|кропивницьк",
    "Lvivska oblast": r"львів",
    "Mykolaivska oblast": r"миколаїв|миколаєв",
    "Odeska oblast": r"одес|одещин|ізмаїл|\bзаток",
    "Poltavska oblast": r"полтав|кременчу|миргород",
    "Rivnenska oblast": r"рівненськ|рівненщин|\bрівне\b|\bрівного\b",
    "Sumska oblast": r"сумщин|сумськ|\bсуми\b|\bсумах\b|шостк|конотоп|охтирк",
    "Ternopilska oblast": r"тернопіл|тернопол",
    "Kharkivska oblast": r"харків|ізюм|куп.янськ|чугу",
    "Khersonska oblast": r"херсон",
    "Khmelnytska oblast": r"хмельниц|хмельниччин|кам.янець.подільськ|старокостянтин",
    "Cherkaska oblast": r"черкас|черкащин|умань|умані",
    "Chernivetska oblast": r"чернівц|буковин",
    "Chernihivska oblast": r"черніг|ніжин|прилук",
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
    "allclear": r"відбій",
}
THREAT_RE = {k: re.compile(p) for k, p in THREATS.items()}

PREPOSITIONS = {"на", "в", "у", "до", "з", "із", "зі", "біля", "повз", "через", "напрямку",
                "бік", "районі", "району", "над", "від", "курсом", "курс", "околиці", "околицях",
                "південніше", "північніше", "західніше", "східніше", "поблизу",
                "п"}  # "н.п.Знам'янка" -> [..., "н", "п", "Знам'янка"]
WORD_RE = re.compile(r"[А-ЯІЇЄҐа-яіїєґ'’ʼ-]+")
# words that look like settlement names but in this channel mean seas, not villages
STOP_FORMS = {"чорного", "чорному", "чорне", "чорним", "азовського", "азовському", "азовське"}


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
    elif w.endswith("їв"):
        f |= {w[:-2] + "єва", w[:-2] + "єві", w[:-2] + "євом"}
    elif w.endswith("ів"):
        f |= {w[:-2] + "ова", w[:-2] + "ові", w[:-2] + "еві", w[:-2] + "овом"}
    elif w.endswith(("іль", "іл")):
        stem = w[: -3 if w.endswith("іль") else -2]
        f |= {stem + "оля", stem + "олі"}
    elif w.endswith("ець"):
        f |= {w[:-3] + "ця", w[:-3] + "цю", w[:-3] + "ці", w[:-3] + "цем"}
    elif w.endswith("ь"):
        f |= {w[:-1] + "я", w[:-1] + "і", w[:-1] + "ем"}
    elif w[-1:].isalpha():
        f |= {w + "а", w + "у", w + "і", w + "ом"}
    # vowel alternation in the last closed syllable: Ріг -> Рогу, Кривий Ріг -> Кривого Рогу
    m = re.search(r"і([^аеєиіїоуюяь']+)$", w)
    if m and not w.endswith(("ів", "іль", "іл")):
        alt = w[: m.start()] + "о" + m.group(1)
        f |= {alt + "а", alt + "у", alt + "ом"}
    if w.endswith("а"):
        f.add(w[:-1] + "ою")
    elif w.endswith("я"):
        f |= {w[:-1] + "ею", w[:-1] + "єю"}
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
    """All settlements of the 24 regions from KATOTTG: name, region, rank
    (city 3 > urban-type settlement 2 > village 1). Ambiguity is resolved later."""
    with urllib.request.urlopen(KATOTTG_URL, timeout=60) as r:
        items = json.load(r)["items"]
    obl = {i["level1"]: i["name"] for i in items if i["category"] == "O"}
    rank = {"M": 3, "T": 2, "X": 2, "C": 1}
    rows = {(i["name"], OBLAST_UA[obl[i["level1"]]], rank[i["category"]])
            for i in items
            if i["category"] in rank and obl.get(i["level1"]) in OBLAST_UA and len(i["name"]) >= 4}
    rows.add(("Київ", "Kyiv City", 4))
    g = pd.DataFrame(sorted(rows), columns=["name", "region", "rank"])
    path.parent.mkdir(parents=True, exist_ok=True)
    g.to_csv(path, index=False)
    return g


class Gazetteer:
    """Case form -> candidate regions with priorities.

    Priority = 10 * (form is the exact nominative name) + settlement rank, so the
    exact name beats a generated case form of another name, and a city beats a
    village. ``lookup`` returns the region for a form, or ``None`` if it is
    ambiguous; a candidate whose oblast is already named in the same post wins
    ("на Харківщині ... Золочів" -> the Kharkiv Золочів, not the Lviv one).
    """

    def __init__(self, forms: dict[str, dict[str, int]]):
        self.forms = forms

    @classmethod
    def from_mapping(cls, mapping: dict[str, str]) -> "Gazetteer":
        return cls({f: {r: 1} for f, r in mapping.items()})

    def lookup(self, form: str, context: set[str]) -> tuple[str | None, bool]:
        """(region, is_from_context)."""
        cands = self.forms.get(form)
        if not cands:
            return None, False
        in_ctx = [r for r in cands if r in context]
        if in_ctx:
            return in_ctx[0], True
        top = max(cands.values())
        best = [r for r, pr in cands.items() if pr == top]
        return (best[0], False) if len(best) == 1 else (None, False)

    def __len__(self):
        return len(self.forms)


def load_gazetteer(path=GAZETTEER_CSV) -> Gazetteer:
    g = pd.read_csv(path) if path.exists() else build_gazetteer(path)
    forms: dict[str, dict[str, int]] = defaultdict(dict)
    for name, region, rank in zip(g.name, g.region, g["rank"]):
        nom = _norm(name).lower()
        for f in _phrase_forms(_norm(name)):
            f = f.lower()
            pr = 10 * (f == nom) + int(rank)
            if pr > forms[f].get(region, -1):
                forms[f][region] = pr
    return Gazetteer(dict(forms))


def _norm(s: str) -> str:
    return s.replace("’", "'").replace("ʼ", "'")


# ----------------------------------------------------------------- extraction
def extract(posts: pd.DataFrame, gaz) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (mentions: post_time, region, how) and (threats: post_time x threat flags)."""
    if not isinstance(gaz, Gazetteer):
        gaz = Gazetteer.from_mapping(gaz)
    mentions, threats = [], []
    for t, text in zip(posts.published_at, posts.text.fillna("")):
        text = _norm(str(text))
        low = text.lower()
        spans = []                      # character spans already explained by region names
        found = set()
        for r, rx in REGION_RE.items():
            for m in rx.finditer(low):
                spans.append((m.start(), m.end()))
                found.add(r)
        for r in found:
            mentions.append((t, r, "region"))
        words = list(WORD_RE.finditer(text))
        for k, wm in enumerate(words):
            wd = wm.group()
            # a settlement name follows a preposition, or *is* the whole short post ("Боярка.")
            standalone = k == 0 and len(words) <= 3
            prev = words[k - 1].group().lower() if k > 0 else ""
            if not wd[:1].isupper() or not (standalone or prev in PREPOSITIONS):
                continue
            if wd.lower() in STOP_FORMS:
                continue
            for n in (3, 2, 1):
                chunk = words[k:k + n]
                if len(chunk) < n:
                    continue
                a, b = chunk[0].start(), chunk[-1].end()
                if any(a < e and s_ < b for s_, e in spans):
                    continue            # overlaps "Харківщині", "Кривого Рогу", ... -> not a village
                r, from_ctx = gaz.lookup(" ".join(c.group() for c in chunk).lower(), found)
                if r is not None:
                    if not from_ctx and r not in found:
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
WINDOWS_MIN = (10, 30, 60, 180)
THREAT_WINDOWS_MIN = (60, 360)


def to_ns(t) -> np.ndarray:
    """Timestamps -> int64 nanoseconds, whatever the stored resolution (pandas 3
    parses to microseconds by default, so a bare ``astype("int64")`` is unit-dependent)."""
    if isinstance(t, pd.Series):
        return t.dt.as_unit("ns").astype("int64").to_numpy()
    return pd.DatetimeIndex(t).as_unit("ns").asi8


def _window_counts(times: np.ndarray, origins: np.ndarray, w_min: int) -> np.ndarray:
    """# of events with origin - w <= t < origin, for each origin (int64 ns arrays)."""
    hi = np.searchsorted(times, origins, side="left")
    lo = np.searchsorted(times, origins - np.int64(w_min) * 60_000_000_000, side="left")
    return (hi - lo).astype(np.float32)


def text_feature_arrays(p: HourlyPanel, posts: pd.DataFrame, mentions: pd.DataFrame,
                        threats: pd.DataFrame,
                        known_until: pd.Timestamp | None = None) -> dict[str, np.ndarray]:
    """(T, R) arrays aligned with panel rows; origin of row i is index[i] + 1h.

    Origins before the first scraped post (+6 h warm-up) or after the last one are NaN."""
    origins = to_ns(p.index + pd.Timedelta(hours=1))
    T, R = len(origins), len(p.regions)
    first_post = posts.published_at.min().value
    # the channel is fully scraped up to ``known_until`` (default: the last post we have)
    last_post = (posts.published_at.max() if known_until is None else known_until).value
    # text is "known" only between a 6 h warm-up after the first scraped post and the
    # last scraped post; outside that range the counts would be fake zeros -> NaN
    valid = (origins >= first_post + 6 * 3600 * 10**9) & (origins <= last_post)
    f: dict[str, np.ndarray] = {}

    per_region = {r: np.sort(to_ns(mentions.loc[mentions.region == r, "published_at"]))
                  for r in p.regions}
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
    f["tg_nb_30m"] = f["tg_reg_30m"] @ W.T
    f["tg_nb_60m"] = f["tg_reg_60m"] @ W.T

    all_times = np.sort(to_ns(posts.published_at))
    for w in (60, 180):
        f[f"tg_posts_{w}m"] = np.repeat(_window_counts(all_times, origins, w)[:, None], R, axis=1)
    for k in THREATS:
        times = np.sort(to_ns(threats.loc[threats[k] == 1, "published_at"]))
        for w in THREAT_WINDOWS_MIN:
            f[f"tg_{k}_{w}m"] = np.repeat(_window_counts(times, origins, w)[:, None], R, axis=1)

    for a in f.values():
        a[~valid] = np.nan
    return f
