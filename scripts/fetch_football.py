#!/usr/bin/env python3
"""Lädt Fußballdaten von football-data.co.uk und schreibt data/football.json.

Läuft automatisch über GitHub Actions. Lokal: pip install pandas requests
"""
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://www.football-data.co.uk"
SEASONS_BACK = 3  # aktuelle Saison + 2 Vorsaisonen

# Hauptligen: volle Statistik (Halbzeit, Schüsse, Ecken, Karten, Quoten)
LEAGUES = {
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "E0": "Premier League",
    "SP1": "LaLiga",
    "I1": "Serie A",
    "F1": "Ligue 1",
    "N1": "Eredivisie",
    "P1": "Liga Portugal",
    "B1": "Pro League (BEL)",
    "T1": "Süper Lig",
    "SC0": "Scottish Premiership",
}
# Zusatzligen: nur Ergebnisse und 1X2-Quoten
EXTRA = {"AUT": ("Austria", "Bundesliga (AUT)")}

OUT = Path(__file__).resolve().parent.parent / "data" / "football.json"
COLS = ["l", "s", "d", "h", "a", "hg", "ag", "hhg", "hag", "hs", "as", "hst", "ast",
        "hc", "ac", "hy", "ay", "hr", "ar", "oh", "od", "oa", "oo", "ou"]
FCOLS = ["l", "d", "t", "h", "a", "oh", "od", "oa", "oo", "ou"]
TEXT = {"l", "s", "d", "h", "a"}
ODDS = {"oh", "od", "oa", "oo", "ou"}

# Schlusskurse bevorzugt (Pinnacle, dann Marktschnitt), sonst Eröffnungskurse
ODDS_H = ["PSCH", "AvgCH", "PSH", "AvgH", "B365H"]
ODDS_D = ["PSCD", "AvgCD", "PSD", "AvgD", "B365D"]
ODDS_A = ["PSCA", "AvgCA", "PSA", "AvgA", "B365A"]
ODDS_O = ["PC>2.5", "AvgC>2.5", "P>2.5", "Avg>2.5", "B365>2.5"]
ODDS_U = ["PC<2.5", "AvgC<2.5", "P<2.5", "Avg<2.5", "B365<2.5"]


def season_years(today):
    start = today.year if today.month >= 7 else today.year - 1
    return list(range(start - SEASONS_BACK + 1, start + 1))


def label(y):
    return f"{y}/{(y + 1) % 100:02d}"


def get_csv(url):
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "tendenz-board/1.0"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            try:
                text = r.content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = r.content.decode("latin-1")
            return pd.read_csv(io.StringIO(text), on_bad_lines="skip")
        except Exception as e:
            print(f"  Fehler bei {url}: {e}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
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
        row = []
        for c in cols:
            v = rec[c]
            if c in TEXT or c == "t":
                row.append(v if isinstance(v, str) else "")
            elif c in ODDS:
                row.append(clean(v, 2))
            else:
                row.append(clean(v))
        rows.append(row)
    return rows


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
        "hg": num(df, "FTHG"), "ag": num(df, "FTAG"),
        "hhg": num(df, "HTHG"), "hag": num(df, "HTAG"),
        "hs": num(df, "HS"), "as": num(df, "AS"), "hst": num(df, "HST"), "ast": num(df, "AST"),
        "hc": num(df, "HC"), "ac": num(df, "AC"), "hy": num(df, "HY"), "ay": num(df, "AY"),
        "hr": num(df, "HR"), "ar": num(df, "AR"),
        "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A),
        "oo": first(df, ODDS_O), "ou": first(df, ODDS_U),
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
        "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A),
        "oo": nan, "ou": nan,
    })
    return to_rows(f, COLS)


def fixture_rows(today):
    frames = []
    df = get_csv(f"{BASE}/fixtures.csv")
    if df is not None and "Div" in df.columns:
        df = df[df["Div"].isin(LEAGUES.keys())].dropna(subset=["HomeTeam", "AwayTeam"])
        if not df.empty:
            frames.append(pd.DataFrame({
                "l": df["Div"], "d": dates(df["Date"]),
                "t": df["Time"] if "Time" in df.columns else "",
                "h": df["HomeTeam"].astype(str).str.strip(), "a": df["AwayTeam"].astype(str).str.strip(),
                "oh": first(df, ODDS_H), "od": first(df, ODDS_D), "oa": first(df, ODDS_A),
                "oo": first(df, ODDS_O), "ou": first(df, ODDS_U),
            }))
    df = get_csv(f"{BASE}/new_league_fixtures.csv")
    if df is not None and "Country" in df.columns:
        for code, (country, _) in EXTRA.items():
            sub = df[df["Country"] == country].dropna(subset=["Home", "Away"])
            if sub.empty:
                continue
            nan = nan_series(sub)
            frames.append(pd.DataFrame({
                "l": code, "d": dates(sub["Date"]),
                "t": sub["Time"] if "Time" in sub.columns else "",
                "h": sub["Home"].astype(str).str.strip(), "a": sub["Away"].astype(str).str.strip(),
                "oh": first(sub, ODDS_H), "od": first(sub, ODDS_D), "oa": first(sub, ODDS_A),
                "oo": nan, "ou": nan,
            }))
    rows = []
    for f in frames:
        rows += to_rows(f, FCOLS)
    iso = today.isoformat()
    rows = [r for r in rows if r[1] >= iso]
    rows.sort(key=lambda r: (r[1], r[2]))
    return rows


def main():
    today = dt.date.today()
    years = season_years(today)
    matches = []
    for code in LEAGUES:
        for y in years:
            print(f"Lade {code} {label(y)}")
            matches += main_league_rows(code, y)
    for code in EXTRA:
        print(f"Lade {code}")
        matches += extra_league_rows(code, years)
    fixtures = fixture_rows(today)

    if not matches:
        print("Keine Spiele geladen, bestehende Datei bleibt unverändert.", file=sys.stderr)
        sys.exit(1)

    names = dict(LEAGUES)
    names.update({k: v[1] for k, v in EXTRA.items()})
    data = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "source": "football-data.co.uk",
        "leagues": names,
        "cols": COLS, "matches": matches,
        "fcols": FCOLS, "fixtures": fixtures,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(matches)} Spiele, {len(fixtures)} kommende Partien gespeichert.")


if __name__ == "__main__":
    main()
