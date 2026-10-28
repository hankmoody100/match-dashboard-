"""xG-Daten für das Tendenz-Board.

Echte xG-Werte kommen von understat.com (Bundesliga, Premier League, LaLiga, Serie A, Ligue 1).
Für alle anderen Ligen mit Schussdaten wird ein Schuss-xG geschätzt: Tore ~ a * Schüsse aufs Tor
+ b * Schüsse daneben, je Liga per kleinster Quadrate gelernt.
"""
import codecs
import datetime as dt
import json
import re
import time
from pathlib import Path

import numpy as np
import requests

SLUG = {"D1": "Bundesliga", "E0": "EPL", "SP1": "La_liga", "I1": "Serie_A", "F1": "Ligue_1"}
UA = {"User-Agent": "Mozilla/5.0 (tendenz-board, privates Dashboard)"}
REFRESH_H = 6
# Spalten, die an jedes Spiel angehängt werden
XCOLS = ["gxh", "gxa", "nxh", "nxa", "pph", "ppa", "dph", "dpa", "xs"]


def log(*a):
    print(*a, flush=True)


def _decode_js(s):
    return json.loads(codecs.decode(s, "unicode_escape"))


def fetch_league(slug, year):
    """Liefert die Team-Historien einer Saison oder None."""
    url = f"https://understat.com/league/{slug}/{year}"
    try:
        r = requests.get(f"https://understat.com/getLeagueData/{slug}/{year}", timeout=30,
                         headers={**UA, "X-Requested-With": "XMLHttpRequest", "Referer": url})
        if r.ok and r.text.strip().startswith("{"):
            j = r.json()
            if j.get("teams"):
                return j["teams"]
    except Exception as e:
        log(f"  Understat API {slug} {year}: {e}")
    try:
        r = requests.get(url, timeout=30, headers=UA)
        m = re.search(r"teamsData\s*=\s*JSON\.parse\('(.*?)'\)", r.text)
        if m:
            return _decode_js(m.group(1))
    except Exception as e:
        log(f"  Understat Seite {slug} {year}: {e}")
    return None


def _ppda(v):
    if isinstance(v, dict) and v.get("def"):
        return round(float(v["att"]) / float(v["def"]), 2)
    return None


def update_cache(data_dir, years, now, hours_since, match_name):
    """Lädt Understat-Daten in data/xg_understat.json (abgeschlossene Saisonen nur einmal)."""
    path = Path(data_dir) / "xg_understat.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    for code, slug in SLUG.items():
        for y in years:
            k = f"{code}|{y}"
            e = cache.get(k)
            current = y == years[-1]
            if e and (not current or hours_since(e.get("t")) < REFRESH_H):
                continue
            teams = fetch_league(slug, y)
            time.sleep(1.5)
            if not teams:
                log(f"  Understat {slug} {y}: keine Daten")
                continue
            rows = []
            for t in teams.values():
                title = t.get("title")
                for g in t.get("history", []):
                    rows.append([title, str(g.get("date", ""))[:10], g.get("h_a"),
                                 round(float(g.get("xG", 0)), 3), round(float(g.get("xGA", 0)), 3),
                                 round(float(g.get("npxG", 0)), 3), round(float(g.get("npxGA", 0)), 3),
                                 _ppda(g.get("ppda")), int(g.get("deep", 0) or 0), int(g.get("deep_allowed", 0) or 0)])
            cache[k] = {"t": now.isoformat(timespec="minutes"), "rows": rows}
            log(f"  Understat {slug} {y}: {len(rows)} Team-Spiele")
    path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    return cache


def attach(matches, cols, cache, match_name):
    """Hängt xG-Spalten an jede Spielzeile. Rückgabe: neue Zeilen, neue Spalten, offene Teamnamen."""
    ci = {c: i for i, c in enumerate(cols)}
    # Understat-Zeilen je Liga: (Team, Datum, h/a) -> Werte
    by_league, titles = {}, {}
    for k, e in cache.items():
        code = k.split("|")[0]
        d = by_league.setdefault(code, {})
        for r in e.get("rows", []):
            d[(r[0], r[1], r[2])] = r
            titles.setdefault(code, set()).add(r[0])
    name_map, unmatched = {}, set()
    out = []
    # Schuss-xG je Liga lernen
    coef = {}
    for code in {m[ci["l"]] for m in matches}:
        X, y = [], []
        for m in matches:
            if m[ci["l"]] != code:
                continue
            for s, sot, g in ((m[ci["hs"]], m[ci["hst"]], m[ci["hg"]]), (m[ci["as"]], m[ci["ast"]], m[ci["ag"]])):
                if None not in (s, sot, g) and s >= sot:
                    X.append([sot, s - sot]); y.append(g)
        if len(y) > 200:
            a, b = np.linalg.lstsq(np.array(X, float), np.array(y, float), rcond=None)[0]
            coef[code] = (max(a, 0.05), max(b, 0.0))
    for m in matches:
        code = m[ci["l"]]
        vals = [None] * len(XCOLS)
        if code in by_league:
            for side, team in (("h", m[ci["h"]]), ("a", m[ci["a"]])):
                key = (code, team)
                if key not in name_map:
                    name_map[key] = match_name(team, code, sorted(titles.get(code, [])), "us")
                    if not name_map[key]:
                        unmatched.add(f"{code}|{team}")
            th, ta = name_map.get((code, m[ci["h"]])), name_map.get((code, m[ci["a"]]))
            r = None
            if th:
                d0 = dt.date.fromisoformat(m[ci["d"]])
                for off in (0, -1, 1):
                    r = by_league[code].get((th, (d0 + dt.timedelta(days=off)).isoformat(), "h"))
                    if r:
                        break
            if r:
                # Heimteam-Sicht: xG = Heim-xG, xGA = Gast-xG
                ra = None
                if ta:
                    ra = by_league[code].get((ta, r[1], "a"))
                vals = [r[3], r[4], r[5], r[6], r[7], ra[7] if ra else None, r[8], r[9], 1]
        if vals[0] is None and code in coef:
            a, b = coef[code]
            hs, hst, as_, ast = m[ci["hs"]], m[ci["hst"]], m[ci["as"]], m[ci["ast"]]
            if None not in (hs, hst, as_, ast):
                vals = [round(a * hst + b * max(hs - hst, 0), 2), round(a * ast + b * max(as_ - ast, 0), 2),
                        None, None, None, None, None, None, 0]
        out.append(list(m) + vals)
    return out, cols + XCOLS, sorted(unmatched), coef
