#!/usr/bin/env python3
"""Holt alle Daten für das Tendenz-Board und schreibt sie nach DATA_DIR.

Quellen: football-data.co.uk (Ergebnisse, Spielplan, Quoten), ClubElo (zweite Prognose),
Google-News-RSS (Schlagzeilen je Team), Open-Meteo (Koordinaten fürs Wetter).
Jede Quelle hat ihren eigenen Takt, damit der Abruf alle 20 Minuten laufen kann,
ohne die Anbieter zu belasten. Fällt eine Quelle aus, laufen die anderen weiter.
"""
import datetime as dt
import difflib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
ALIASES_FILE = ROOT / "scripts" / "aliases.json"
BASE = "https://www.football-data.co.uk"
SEASONS_BACK = 5
UA = {"User-Agent": "tendenz-board/2.0 (privates Dashboard)"}

# Takt je Quelle (Stunden)
EVERY = {"results": 3, "elo": 2, "news_team": 3}
NEWS_BUDGET = 40      # max. Teams pro Lauf
GEO_BUDGET = 25       # max. Ortssuchen pro Lauf
NEWS_DAYS_AHEAD = 4   # News nur für Spiele der nächsten Tage

LEAGUES = {
    "D1": "Bundesliga", "D2": "2. Bundesliga", "E0": "Premier League", "SP1": "LaLiga",
    "I1": "Serie A", "F1": "Ligue 1", "N1": "Eredivisie", "P1": "Liga Portugal",
    "B1": "Pro League (BEL)", "T1": "Süper Lig", "SC0": "Scottish Premiership",
}
EXTRA = {"AUT": ("Austria", "Bundesliga (AUT)")}
COUNTRY = {"D1": "GER", "D2": "GER", "E0": "ENG", "SP1": "ESP", "I1": "ITA", "F1": "FRA", "N1": "NED",
           "P1": "POR", "B1": "BEL", "T1": "TUR", "SC0": "SCO", "AUT": "AUT"}
ISO2 = {"GER": "DE", "ENG": "GB", "SCO": "GB", "ESP": "ES", "ITA": "IT", "FRA": "FR", "NED": "NL",
        "POR": "PT", "BEL": "BE", "TUR": "TR", "AUT": "AT"}
GERMAN = {"AUT", "D1", "D2"}

# oh..ou = Schlussquote (Pinnacle, sonst Schnitt), ph..pu = Pinnacle vor dem Spiel, xh..xu = beste Quote vor dem Spiel
COLS = ["l", "s", "d", "h", "a", "hg", "ag", "hhg", "hag", "hs", "as", "hst", "ast",
        "hc", "ac", "hy", "ay", "hr", "ar", "oh", "od", "oa", "oo", "ou",
        "ph", "pd", "pa", "po", "pu", "xh", "xd", "xa", "xo", "xu", "hf", "af"]
# Spielplan: oh..ou = Pinnacle (sonst Schnitt) aktuell, xh..xu = beste aktuelle Quote
FCOLS = ["l", "d", "t", "h", "a", "oh", "od", "oa", "oo", "ou", "xh", "xd", "xa", "xo", "xu"]
TEXT = {"l", "s", "d", "h", "a", "t"}
import understat
ODDS = {"oh", "od", "oa", "oo", "ou", "ph", "pd", "pa", "po", "pu", "xh", "xd", "xa", "xo", "xu"}
ODDS_H = ["PSCH", "AvgCH", "PSH", "AvgH", "B365H"]
ODDS_D = ["PSCD", "AvgCD", "PSD", "AvgD", "B365D"]
ODDS_A = ["PSCA", "AvgCA", "PSA", "AvgA", "B365A"]
ODDS_O = ["PC>2.5", "AvgC>2.5", "P>2.5", "Avg>2.5", "B365>2.5"]
ODDS_U = ["PC<2.5", "AvgC<2.5", "P<2.5", "Avg<2.5", "B365<2.5"]
PRE = {"ph": ["PSH", "AvgH"], "pd": ["PSD", "AvgD"], "pa": ["PSA", "AvgA"], "po": ["P>2.5", "Avg>2.5"], "pu": ["P<2.5", "Avg<2.5"]}
MAX = {"xh": ["MaxH", "BbMxH"], "xd": ["MaxD", "BbMxD"], "xa": ["MaxA", "BbMxA"], "xo": ["Max>2.5", "BbMx>2.5"], "xu": ["Max<2.5", "BbMx<2.5"]}
MAXC = {"xh": ["MaxCH"], "xd": ["MaxCD"], "xa": ["MaxCA"]}

NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()


def log(*a):
    print(*a, flush=True)


# ---------- Hilfen ----------
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def hours_since(iso):
    if not iso:
        return 1e9
    try:
        return (NOW - dt.datetime.fromisoformat(iso)).total_seconds() / 3600
    except Exception:
        return 1e9


def http_get(url, timeout=30, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=timeout, headers=UA)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except Exception as e:
            log(f"  Fehler bei {url[:90]}: {e}")
            time.sleep(2 * (attempt + 1))
    return None


def get_csv(url):
    r = http_get(url)
    if r is None:
        return None
    try:
        text = r.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = r.content.decode("latin-1")
    try:
        return pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    except Exception as e:
        log(f"  CSV unlesbar {url}: {e}")
        return None


def nan_series(df):
    return pd.Series([float("nan")] * len(df), index=df.index)


def num(df, col):
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else nan_series(df)


def first(df, cols):
    s = nan_series(df)
    for c in cols:
        if c in df.columns:
            s = s.fillna(pd.to_numeric(df[c], errors="coerce"))
    return s


def dates(series):
    return pd.to_datetime(series, dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")


def clean(v, digits=None):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return int(v) if digits is None else round(float(v), digits)


def to_rows(frame, cols):
    rows = []
    for tup in frame[cols].itertuples(index=False, name=None):
        rec = dict(zip(cols, tup))
        if not isinstance(rec["d"], str):
            continue
        rows.append([(rec[c] if isinstance(rec[c], str) else "") if c in TEXT
                     else clean(rec[c], 2) if c in ODDS else clean(rec[c]) for c in cols])
    return rows


def label(y):
    return f"{y}/{(y + 1) % 100:02d}"


def season_years():
    start = TODAY.year if TODAY.month >= 7 else TODAY.year - 1
    return list(range(start - SEASONS_BACK + 1, start + 1))


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


ALIASES = load_json(ALIASES_FILE, {})


def alias(team, field):
    return (ALIASES.get(team) or {}).get(field)


# ---------- football-data.co.uk ----------
def main_league_rows(code, y):
    sc = f"{y % 100:02d}{(y + 1) % 100:02d}"
    df = get_csv(f"{BASE}/mmz4281/{sc}/{code}.csv")
    if df is None or "HomeTeam" not in df.columns:
        return []
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    if df.empty:
        return []
    f = pd.DataFrame({
        "l": code, "s": label(y), "d": dates(df["Date"]),
        "h": df["HomeTeam"].astype(str).str.strip(), "a": df["AwayTeam"].astype(str).str.strip(),
        "hg": num(df, "FTHG"), "ag": num(df, "FTAG"), "hhg": num(df, "HTHG"), "hag": num(df, "HTAG"),
        "hs": num(df, "HS"), "as": num(df, "AS"), "hst": num(df, "HST"), "ast": num(df, "AST"),
        "hc": num(df, "HC"), "ac": num(df, "AC"), "hy": num(df, "HY"), "ay": num(df, "AY"),
        "hr": num(df, "HR"), "ar": num(df, "AR"),
        "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A),
        "oo": first(df, ODDS_O), "ou": first(df, ODDS_U),
        **{k: first(df, v) for k, v in PRE.items()}, **{k: first(df, v) for k, v in MAX.items()},
        "hf": num(df, "HF"), "af": num(df, "AF"),
    })
    return to_rows(f, COLS)


def extra_league_rows(code, years):
    df = get_csv(f"{BASE}/new/{code}.csv")
    if df is None or "Home" not in df.columns:
        return []
    df = df.dropna(subset=["Home", "Away", "HG", "AG"]).copy()
    df["_y"] = pd.to_numeric(df["Season"].astype(str).str[:4], errors="coerce")
    df = df[df["_y"].isin(years)]
    if df.empty:
        return []
    nan = nan_series(df)
    f = pd.DataFrame({
        "l": code, "s": df["_y"].astype(int).map(label), "d": dates(df["Date"]),
        "h": df["Home"].astype(str).str.strip(), "a": df["Away"].astype(str).str.strip(),
        "hg": num(df, "HG"), "ag": num(df, "AG"), "hhg": nan, "hag": nan,
        "hs": nan, "as": nan, "hst": nan, "ast": nan, "hc": nan, "ac": nan,
        "hy": nan, "ay": nan, "hr": nan, "ar": nan,
        "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A), "oo": nan, "ou": nan,
        "ph": nan, "pd": nan, "pa": nan, "po": nan, "pu": nan,
        "xh": first(df, MAXC["xh"]), "xd": first(df, MAXC["xd"]), "xa": first(df, MAXC["xa"]), "xo": nan, "xu": nan,
        "hf": nan, "af": nan,
    })
    return to_rows(f, COLS)


def fetch_results(old_rows):
    """Laufende Saison immer neu, abgeschlossene Saisonen nur, wenn sie noch fehlen."""
    years = season_years()
    have = {}
    for r in old_rows or []:
        if len(r) == len(COLS):
            have.setdefault((r[0], r[1]), []).append(r)
    rows = []
    for code in LEAGUES:
        for y in years:
            if y != years[-1] and have.get((code, label(y))):
                rows += have[(code, label(y))]
                continue
            log(f"Ergebnisse {code} {label(y)}")
            rows += main_league_rows(code, y)
    for code in EXTRA:
        log(f"Ergebnisse {code}")
        rows += extra_league_rows(code, years)
    return rows


def fetch_fixtures():
    frames = []
    df = get_csv(f"{BASE}/fixtures.csv")
    if df is None:
        log("football-data Spielplan: Datei nicht abrufbar")
    elif "Div" not in df.columns:
        log(f"football-data Spielplan: unerwartetes Format, Spalten {list(df.columns)[:6]}")
    else:
        d_ = dates(df["Date"]) if "Date" in df.columns else pd.Series([], dtype=str)
        log(f"football-data Spielplan: {len(df)} Zeilen, Ligen {sorted(df['Div'].dropna().unique().tolist())[:20]}, "
            f"Daten {d_.min() if len(d_) else '-'} bis {d_.max() if len(d_) else '-'}")
    if df is not None and "Div" in df.columns:
        df = df[df["Div"].isin(LEAGUES.keys())].dropna(subset=["HomeTeam", "AwayTeam"])
        if not df.empty:
            frames.append(pd.DataFrame({
                "l": df["Div"], "d": dates(df["Date"]), "t": df["Time"] if "Time" in df.columns else "",
                "h": df["HomeTeam"].astype(str).str.strip(), "a": df["AwayTeam"].astype(str).str.strip(),
                "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A),
                "oo": first(df, ODDS_O), "ou": first(df, ODDS_U),
                **{k: first(df, v) for k, v in MAX.items()}}))
    df = get_csv(f"{BASE}/new_league_fixtures.csv")
    if df is not None and "Country" in df.columns:
        for code, (country, _) in EXTRA.items():
            sub = df[df["Country"] == country].dropna(subset=["Home", "Away"])
            if sub.empty:
                continue
            nan = nan_series(sub)
            frames.append(pd.DataFrame({
                "l": code, "d": dates(sub["Date"]), "t": sub["Time"] if "Time" in sub.columns else "",
                "h": sub["Home"].astype(str).str.strip(), "a": sub["Away"].astype(str).str.strip(),
                "oh": first(sub, ODDS_H), "od": first(sub, ODDS_D), "oa": first(sub, ODDS_A),
                "oo": nan, "ou": nan, "xh": first(sub, ["MaxH", "MaxCH"]), "xd": first(sub, ["MaxD", "MaxCD"]),
                "xa": first(sub, ["MaxA", "MaxCA"]), "xo": nan, "xu": nan}))
    if not frames:
        return None
    rows = []
    for f in frames:
        rows += to_rows(f, FCOLS)
    iso = TODAY.isoformat()
    rows = [r for r in rows if r[1] >= iso]
    rows.sort(key=lambda r: (r[1], r[2]))
    return rows


# ---------- Ersatzquellen für den Spielplan ----------
ODDS_API = "https://api.the-odds-api.com/v4"
SPORT_KEYS = {"D1": "soccer_germany_bundesliga", "D2": "soccer_germany_bundesliga2", "E0": "soccer_epl",
              "SP1": "soccer_spain_la_liga", "I1": "soccer_italy_serie_a", "F1": "soccer_france_ligue_one",
              "N1": "soccer_netherlands_eredivisie", "P1": "soccer_portugal_primeira_liga",
              "B1": "soccer_belgium_first_div", "T1": "soccer_turkey_super_league", "SC0": "soccer_spl",
              "AUT": "soccer_austria_bundesliga"}


def to_fd_name(name, league, fd_teams):
    """Ordnet einen fremden Teamnamen dem football-data-Namen zu (über Aliase und Ähnlichkeit)."""
    nn = norm(name)
    best, score = None, 0.0
    for t in fd_teams:
        for cand in [t] + [x for x in ((ALIASES.get(t) or {}).get(k) for k in ("search", "elo", "us")) if x]:
            nc = norm(cand)
            if not nc:
                continue
            s = difflib.SequenceMatcher(None, nn, nc).ratio()
            if len(nc) >= 4 and (nc in nn or nn in nc):
                s = max(s, 0.9)
            if nn == nc:
                s = 1.0
            if s > score:
                best, score = t, s
    return best if score >= 0.72 else None


def uk_time(iso):
    """UTC-Zeit -> britische Ortszeit (so liefert auch football-data die Anstoßzeiten)."""
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/London"))
        return t.date().isoformat(), t.strftime("%H:%M")
    except Exception:
        return iso[:10], ""


def fixtures_fallback(fixtures, matches):
    """Ergänzt fehlende Spiele aus The Odds API (Spielplan kostet keine Abrufe) und ClubElo."""
    years = season_years()
    cur = label(years[-1])
    teams = {}
    for m in matches:
        if m[1] == cur:
            teams.setdefault(m[0], set()).update([m[3], m[4]])
    have = {(r[0], r[1], r[3], r[4]) for r in fixtures}
    limit = (TODAY + dt.timedelta(days=10)).isoformat()
    added = {"odds_api": 0, "clubelo": 0}
    unmatched = set()

    def add(l, d, t, h, a, src):
        if d < TODAY.isoformat() or d > limit:
            return
        fh, fa = to_fd_name(h, l, teams.get(l, [])), to_fd_name(a, l, teams.get(l, []))
        if not fh or not fa or fh == fa:
            unmatched.update(x for x, y in ((h, fh), (a, fa)) if not y)
            return
        if any((l, dd, fh, fa) in have for dd in (d, (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat(),
                                                   (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat())):
            return
        fixtures.append([l, d, t, fh, fa] + [None] * (len(FCOLS) - 5))
        have.add((l, d, fh, fa))
        added[src] += 1

    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key:
        for l, sk in SPORT_KEYS.items():
            try:
                r = requests.get(f"{ODDS_API}/sports/{sk}/events", params={"apiKey": key, "dateFormat": "iso"}, timeout=30)
                if r.status_code != 200:
                    log(f"  Odds API Spielplan {sk}: Status {r.status_code}")
                    continue
                for ev in r.json():
                    d, t = uk_time(ev.get("commence_time", ""))
                    add(l, d, t, ev.get("home_team", ""), ev.get("away_team", ""), "odds_api")
            except Exception as e:
                log(f"  Odds API Spielplan {sk}: {e}")
    fx = get_csv("http://api.clubelo.com/Fixtures")
    if fx is not None and {"Home", "Away", "Date", "Country"} <= set(fx.columns):
        by_country = {}
        for l, c in COUNTRY.items():
            by_country.setdefault(c, []).append(l)
        for e in fx.to_dict("records"):
            d = str(e["Date"])[:10]
            for l in by_country.get(e["Country"], []):
                h_, a_ = to_fd_name(e["Home"], l, teams.get(l, [])), to_fd_name(e["Away"], l, teams.get(l, []))
                if h_ and a_:
                    add(l, d, "", e["Home"], e["Away"], "clubelo")
                    break
    fixtures.sort(key=lambda r: (r[1], r[2] or ""))
    log(f"Spielplan ergänzt: {added['odds_api']} aus The Odds API, {added['clubelo']} aus ClubElo"
        + (f", nicht zugeordnet: {sorted(unmatched)[:15]}" if unmatched else ""))
    return fixtures


def fkey(r):
    return f"{r[0]}|{r[1]}|{r[3]}|{r[4]}"


# ---------- Quotenverlauf ----------
def update_odds_history(fixtures):
    path = DATA / "odds_history.json"
    hist = load_json(path, {})
    stamp = NOW.isoformat(timespec="minutes")
    for r in fixtures:
        snap = r[5:10]
        if all(v is None for v in snap):
            continue
        lst = hist.setdefault(fkey(r), [])
        if not lst or lst[-1][1:] != snap:
            lst.append([stamp] + snap)
        if len(lst) > 60:
            hist[fkey(r)] = [lst[0]] + lst[-59:]
    cutoff = (TODAY - dt.timedelta(days=3)).isoformat()
    hist = {k: v for k, v in hist.items() if k.split("|")[1] >= cutoff}
    save_json(path, hist)
    log(f"Quotenverlauf: {len(hist)} Spiele")


# ---------- ClubElo ----------
def match_name(team, league, candidates, field="elo"):
    """Ordnet einen football-data-Namen einem Namen der anderen Quelle zu (field: elo oder us)."""
    forced = alias(team, field)
    if forced and forced in candidates:
        return forced
    names = [team] + [x for x in [alias(team, "search"), forced] if x]
    best, score = None, 0.0
    for c in candidates:
        nc = norm(c)
        for n in names:
            nn = norm(n)
            if not nn or not nc:
                continue
            s = difflib.SequenceMatcher(None, nn, nc).ratio()
            if len(nc) >= 4 and (nc in nn or nn in nc):
                s = max(s, 0.9)
            if s > score:
                best, score = c, s
    return best if score >= 0.72 else None


def update_elo(fixtures, team_keys):
    path = DATA / "elo.json"
    old = load_json(path, {})
    ratings = get_csv(f"http://api.clubelo.com/{TODAY.isoformat()}")
    fx = get_csv("http://api.clubelo.com/Fixtures")
    if ratings is None or "Club" not in ratings.columns:
        log("ClubElo nicht erreichbar, behalte alten Stand.")
        return
    by_country = {}
    elo_of = {}
    for r in ratings.itertuples(index=False):
        rec = r._asdict()
        by_country.setdefault(rec.get("Country"), []).append(rec["Club"])
        elo_of[rec["Club"]] = round(float(rec["Elo"]))
    mapping, unmatched = {}, []
    for l, team in sorted(team_keys):
        m = match_name(team, l, by_country.get(COUNTRY.get(l), []))
        if m:
            mapping[f"{l}|{team}"] = m
        else:
            unmatched.append(f"{l}|{team}")
    out = {"updated": NOW.isoformat(timespec="minutes"), "ratings": {}, "fixtures": {}, "unmatched": unmatched}
    for k, club in mapping.items():
        out["ratings"][k] = elo_of.get(club)
    if fx is not None and {"Home", "Away", "Date"} <= set(fx.columns):
        gd = [c for c in fx.columns if c.startswith("GD")]
        res = [c for c in fx.columns if c.startswith("R:")]
        idx = {}
        for r in fx.to_dict("records"):
            idx.setdefault((r["Home"], r["Away"]), []).append(r)
        for r in fixtures:
            h, a = mapping.get(f"{r[0]}|{r[3]}"), mapping.get(f"{r[0]}|{r[4]}")
            if not h or not a:
                continue
            for e in idx.get((h, a), []):
                try:
                    if abs((dt.date.fromisoformat(str(e["Date"])[:10]) - dt.date.fromisoformat(r[1])).days) > 1:
                        continue
                except ValueError:
                    continue
                p1 = sum(float(e[c] or 0) for c in gd if c.startswith("GD>") or (c.startswith("GD=") and not c.startswith("GD=-") and c != "GD=0"))
                px = float(e.get("GD=0") or 0)
                p2 = sum(float(e[c] or 0) for c in gd if c.startswith("GD<") or c.startswith("GD=-"))
                tot = p1 + px + p2
                if tot <= 0:
                    continue
                top = sorted(((float(e[c] or 0), c[2:]) for c in res), reverse=True)[:3]
                out["fixtures"][fkey(r)] = {"p1": round(p1 / tot * 100, 1), "px": round(px / tot * 100, 1),
                                            "p2": round(p2 / tot * 100, 1),
                                            "top": [[s, round(p * 100, 1)] for p, s in top]}
                break
    save_json(path, out)
    log(f"ClubElo: {len(mapping)} Teams zugeordnet, {len(unmatched)} offen, {len(out['fixtures'])} Prognosen")


# ---------- News ----------
KW_DE = '(verletzt OR Verletzung OR Ausfall OR fällt OR gesperrt OR Sperre OR fraglich OR Aufstellung OR Startelf OR Trainer)'
KW_EN = '(injury OR injured OR ruled OR suspended OR suspension OR doubt OR lineup OR "team news" OR manager)'


def fetch_news(league, team):
    name = alias(team, "search") or team
    if league in GERMAN:
        q, params = f'"{name}" {KW_DE} when:3d', "hl=de&gl=AT&ceid=AT:de"
    else:
        q, params = f'"{name}" {KW_EN} when:3d', "hl=en-GB&gl=GB&ceid=GB:en"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&{params}"
    r = http_get(url, timeout=20, tries=2)
    if r is None:
        return None
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return None
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        pub = it.findtext("pubDate") or ""
        try:
            pub_iso = dt.datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(
                tzinfo=dt.timezone.utc).isoformat(timespec="minutes")
        except ValueError:
            pub_iso = ""
        items.append({"title": title, "link": it.findtext("link") or "", "source": src, "date": pub_iso})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:6]


def update_news(fixtures):
    path = DATA / "news.json"
    news = load_json(path, {"teams": {}})
    limit = (TODAY + dt.timedelta(days=NEWS_DAYS_AHEAD)).isoformat()
    order = []
    for r in fixtures:
        if r[1] <= limit:
            for team in (r[3], r[4]):
                k = f"{r[0]}|{team}"
                if k not in order:
                    order.append(k)
    budget = NEWS_BUDGET
    for k in order:
        e = news["teams"].get(k)
        if e and hours_since(e.get("t")) < EVERY["news_team"]:
            continue
        if budget <= 0:
            break
        l, team = k.split("|", 1)
        items = fetch_news(l, team)
        budget -= 1
        if items is None:
            continue
        news["teams"][k] = {"t": NOW.isoformat(timespec="minutes"), "items": items}
        time.sleep(1.2)
    keep = set(order)
    news["teams"] = {k: v for k, v in news["teams"].items() if k in keep or hours_since(v.get("t")) < 96}
    news["updated"] = NOW.isoformat(timespec="minutes")
    save_json(path, news)
    log(f"News: {NEWS_BUDGET - budget} Teams abgefragt, {len(news['teams'])} im Bestand")


# ---------- Orte fürs Wetter ----------
STOP = {"fc", "sc", "ac", "cf", "sv", "vfb", "vfl", "tsg", "rb", "red", "bull", "united", "city", "real", "athletic",
        "sporting", "club", "de", "cd", "ud", "rcd", "ss", "as", "afc", "borussia", "eintracht", "bayer", "fortuna",
        "hertha", "union", "werder", "olympique", "stade", "ogc", "racing", "sk", "fk", "ksc", "kv", "krc", "rsc",
        "sp", "ath", "ein", "man", "the", "and", "of", "1", "spor", "wsg", "lask", "rapid", "austria", "sturm",
        "psv", "az", "fcsb", "st", "us", "calcio"}


def place_candidates(team):
    cands = []
    for src in [alias(team, "city"), alias(team, "search"), team]:
        if src and src not in cands:
            cands.append(src)
    for src in [alias(team, "search"), team]:
        if not src:
            continue
        toks = [t for t in re.split(r"[\s'\.\-]+", src) if t and t.lower() not in STOP and len(t) > 2]
        for t in sorted(toks, key=len, reverse=True):
            if t not in cands:
                cands.append(t)
    return cands


def update_geo(fixtures):
    path = DATA / "geo.json"
    geo = load_json(path, {})
    budget = GEO_BUDGET
    for r in fixtures:
        k = f"{r[0]}|{r[3]}"
        if k in geo and (geo[k] or {}).get("lat") is not None:
            continue
        if k in geo and hours_since((geo[k] or {}).get("miss")) < 24:
            continue
        if budget <= 0:
            break
        cc = ISO2.get(COUNTRY.get(r[0], ""), "")
        found = None
        for q in place_candidates(r[3]):
            budget -= 1
            url = ("https://geocoding-api.open-meteo.com/v1/search?count=1&language=de&format=json"
                   f"&name={urllib.parse.quote(q)}" + (f"&countryCode={cc}" if cc else ""))
            resp = http_get(url, timeout=15, tries=1)
            res = (resp.json().get("results") if resp is not None else None) or []
            if res:
                found = {"lat": res[0]["latitude"], "lon": res[0]["longitude"], "name": res[0].get("name", q)}
                break
            if budget <= 0:
                break
        geo[k] = found or {"miss": NOW.isoformat(timespec="minutes")}
    save_json(path, geo)


# ---------- Ablauf ----------
def main():
    DATA.mkdir(parents=True, exist_ok=True)
    meta = load_json(DATA / "meta.json", {})
    football = load_json(DATA / "football.json", None)

    # 1) Ergebnisse (alle 3 Stunden)
    matches = football["matches"] if football else []
    fcols_old = (football or {}).get("cols") or []
    if fcols_old[:len(COLS)] == COLS:
        matches = [r[:len(COLS)] for r in matches]               # angehängte xG-Spalten wieder abnehmen
    outdated = bool(matches) and (fcols_old[:len(COLS)] != COLS)  # altes Datenformat
    if not football or outdated or hours_since(meta.get("results")) >= EVERY["results"]:
        fresh = fetch_results(matches)
        if fresh:
            matches = fresh
            meta["results"] = NOW.isoformat(timespec="minutes")
    if not matches:
        log("Keine Ergebnisse verfügbar, Abbruch ohne Änderung.")
        sys.exit(1)

    # 2) Spielplan und Quoten (jeder Lauf)
    fixtures = fetch_fixtures()
    if fixtures is None:
        fixtures = [r for r in (football or {}).get("fixtures", []) if r[1] >= TODAY.isoformat()]
        log("Spielplan nicht erreichbar, nutze letzten Stand.")
    else:
        meta["fixtures"] = NOW.isoformat(timespec="minutes")
    # Fehlt der Spielplan bei football-data ganz oder für einzelne Ligen, aus Ersatzquellen ergänzen
    try:
        soon = (TODAY + dt.timedelta(days=10)).isoformat()
        covered = {r[0] for r in fixtures if r[1] <= soon}
        if len(covered) < len(LEAGUES) + len(EXTRA):
            fixtures = fixtures_fallback(fixtures, matches)
    except Exception as e:
        log(f"Ersatz-Spielplan übersprungen: {e}")

    # xG: Understat für die großen fünf Ligen, Schuss-xG für den Rest
    cols_out, rows_out, xg_info = COLS, matches, {}
    try:
        cache = understat.update_cache(DATA, season_years(), NOW, hours_since, match_name)
        rows_out, cols_out, xg_unmatched, coef = understat.attach(matches, COLS, cache, match_name)
        xg_info = {"unmatched": xg_unmatched, "shot_model": {k: [round(a, 3), round(b, 3)] for k, (a, b) in coef.items()}}
        log(f"xG: {sum(1 for r in rows_out if r[-1] == 1)} Spiele mit Understat-xG, {sum(1 for r in rows_out if r[-1] == 0)} mit Schuss-xG")
    except Exception as e:
        log(f"xG übersprungen: {e}")

    names = dict(LEAGUES)
    names.update({k: v[1] for k, v in EXTRA.items()})
    save_json(DATA / "football.json", {
        "updated": meta.get("fixtures") or meta.get("results"), "results_updated": meta.get("results"),
        "source": "football-data.co.uk, understat.com", "leagues": names, "xg": xg_info,
        "cols": cols_out, "matches": rows_out, "fcols": FCOLS, "fixtures": fixtures})
    log(f"{len(matches)} Spiele, {len(fixtures)} kommende Partien")

    # 3) Rating-Modell (Python, siehe model.py)
    try:
        import model
        model.run(rows_out, cols_out, fixtures, FCOLS, DATA, meta, NOW)
    except Exception as e:
        log(f"Modell übersprungen: {e}")

    # 4) Zusatzquellen, jede für sich abgesichert
    steps = [("Quotenverlauf", lambda: update_odds_history(fixtures))]
    if hours_since(meta.get("elo")) >= EVERY["elo"]:
        team_keys = {(m[0], m[3]) for m in matches} | {(m[0], m[4]) for m in matches}
        steps.append(("ClubElo", lambda: update_elo(fixtures, team_keys) or meta.__setitem__("elo", NOW.isoformat(timespec="minutes"))))
    steps.append(("News", lambda: update_news(fixtures)))
    steps.append(("Orte", lambda: update_geo(fixtures)))
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            log(f"{name} übersprungen: {e}")

    # 5) Tennis (eigene Quellen und eigenes Modell, siehe tennis.py)
    try:
        import tennis
        tennis.run(DATA, NOW, meta, hours_since)
    except Exception as e:
        log(f"Tennis übersprungen: {e}")

    meta["run"] = NOW.isoformat(timespec="minutes")
    save_json(DATA / "meta.json", meta)


if __name__ == "__main__":
    main()
