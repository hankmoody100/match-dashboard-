"""Länderspiele für das Tendenz-Board.

Ergebnisse: martj42/international_results (GitHub, alle Länderspiele seit 1872).
Kommende Spiele: The Odds API, Spielplan-Abfrage (zählt nicht zum Gratis-Kontingent).
Modell: Elo nach dem Verfahren von eloratings.net (Turniergewicht, Tordifferenz, Heimvorteil),
daraus erwartete Tore per Poisson-Regression, Remis-Korrektur wie im Liga-Modell.
"""
import datetime as dt
import difflib
import io
import json
import math
import os
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests

RESULTS = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
ODDS_API = "https://api.the-odds-api.com/v4"
HOME_ADV = 100
INTL_WORDS = ("nations league", "qualif", "friendl", "international", "africa cup", "copa america", "asian cup",
              "gold cup", "world cup", "euro 20", "european championship")
CLUB_WORDS = ("champions league", "europa league", "conference league", "club", "libertadores", "sudamericana", "women", "u21", "u-21")
ALIAS = {"usa": "United States", "united states of america": "United States", "czechia": "Czech Republic",
         "turkiye": "Turkey", "korea republic": "South Korea", "republic of korea": "South Korea", "korea dpr": "North Korea",
         "ireland": "Republic of Ireland", "ir iran": "Iran", "cote d ivoire": "Ivory Coast", "cape verde islands": "Cape Verde",
         "bosnia herzegovina": "Bosnia and Herzegovina", "north macedonia": "North Macedonia", "china pr": "China PR"}


def log(*a):
    print("[Länderspiele]", *a, flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s.replace("-", " ")).strip()


def k_factor(t):
    t = t.lower()
    if t == "fifa world cup":
        return 60
    if any(x in t for x in ("euro", "copa am", "african cup of nations", "afc asian cup", "gold cup", "confederations")) and "qualif" not in t:
        return 50
    if "qualif" in t or "nations league" in t:
        return 40
    if t == "friendly":
        return 20
    return 30


def gd_mult(gd):
    gd = abs(gd)
    return 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


def team_match(name, teams, low):
    n = norm(name)
    if n in ALIAS:
        return ALIAS[n]
    if n in low:
        return low[n]
    m = difflib.get_close_matches(n, list(low), n=1, cutoff=0.82)
    return low[m[0]] if m else None


def run(data_dir, now, hours_since):
    data_dir = Path(data_dir)
    cache = data_dir / "intl_results.csv"
    if not cache.exists() or (now.timestamp() - cache.stat().st_mtime) / 3600 >= 12:
        try:
            r = requests.get(RESULTS, timeout=60)
            if r.ok and r.text.startswith("date,"):
                cache.write_text(r.text, encoding="utf-8")
        except Exception as e:
            log(f"Ergebnisse nicht erreichbar: {e}")
    if not cache.exists():
        log("Keine Ergebnisdaten.")
        return
    df = pd.read_csv(cache)
    df = df[df.home_score.notna() & df.away_score.notna()].sort_values("date")

    # --- Elo über die gesamte Historie ---
    elo, rows = {}, []
    for r in df.itertuples(index=False):
        h, a = r.home_team, r.away_team
        eh, ea = elo.get(h, 1500.0), elo.get(a, 1500.0)
        adv = 0 if bool(r.neutral) else HOME_ADV
        dr = eh + adv - ea
        we = 1 / (1 + 10 ** (-dr / 400))
        hs, as_ = int(r.home_score), int(r.away_score)
        w = 1.0 if hs > as_ else 0.5 if hs == as_ else 0.0
        k = k_factor(str(r.tournament)) * gd_mult(hs - as_)
        elo[h] = eh + k * (w - we)
        elo[a] = ea - k * (w - we)
        if r.date >= "2010-01-01":
            rows.append((dr, hs, as_))
    # --- Tore aus Elo-Differenz: log(Tore) = c + d * Differenz/100 (Poisson, Newton) ---
    X = np.array([[1, d / 100] for d, _, _ in rows] + [[1, -d / 100] for d, _, _ in rows])
    y = np.array([g for _, g, _ in rows] + [g for _, _, g in rows], float)
    b = np.array([0.2, 0.2])
    for _ in range(25):
        mu = np.exp(X @ b)
        g = X.T @ (y - mu)
        H = -(X * mu[:, None]).T @ X
        b = b - np.linalg.solve(H, g)
    log(f"Elo für {len(elo)} Teams, Tor-Modell c={b[0]:.3f} d={b[1]:.3f} aus {len(rows)} Spielen")

    # --- Kommende Spiele ---
    teams = set(elo)
    low = {norm(t): t for t in teams}
    fixtures, unmatched = [], set()
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key:
        try:
            sports = requests.get(f"{ODDS_API}/sports/", params={"apiKey": key}, timeout=30).json()
            sports = [s for s in sports if s.get("group") == "Soccer" and s.get("active")
                      and any(w in (s.get("title", "") + " " + s.get("description", "")).lower() for w in INTL_WORDS)
                      and not any(w in (s.get("title", "") + " " + s.get("description", "")).lower() for w in CLUB_WORDS)]
            log(f"Wettbewerbe: {[s['title'] for s in sports]}")
            for s in sports:
                evs = requests.get(f"{ODDS_API}/sports/{s['key']}/events", params={"apiKey": key, "dateFormat": "iso"}, timeout=30).json()
                for ev in evs:
                    h, a = team_match(ev.get("home_team", ""), teams, low), team_match(ev.get("away_team", ""), teams, low)
                    if not h or not a:
                        unmatched.update(x for x, y in ((ev.get("home_team"), h), (ev.get("away_team"), a)) if not y)
                        continue
                    if ev.get("commence_time", "") < now.isoformat()[:16]:
                        continue
                    fixtures.append({"start": ev["commence_time"], "comp": s.get("title", ""), "h": h, "a": a})
        except Exception as e:
            log(f"Spielplan nicht erreichbar: {e}")
    else:
        log("Kein ODDS_API_KEY, Länderspiele nur aus Sofascore/ESPN.")
    # Alle Länderspiele aus dem Gesamt-Spielplan (Sofascore, ESPN), Nachwuchs und Frauen ausgenommen
    try:
        sched = json.loads((data_dir / "sf_schedule.json").read_text(encoding="utf-8"))
    except Exception:
        sched = {}
    for e in sched.get("events", []):
        if not e.get("nat") or e.get("youth") or e.get("start", "") < now.isoformat()[:16]:
            continue
        h_, a_ = team_match(e["h"], teams, low), team_match(e["a"], teams, low)
        if not h_ or not a_:
            unmatched.update(x for x, y in ((e["h"], h_), (e["a"], a_)) if not y)
            continue
        fixtures.append({"start": e["start"], "comp": e.get("comp", ""), "h": h_, "a": a_})

    def form(t, n=6):
        sub = df[(df.home_team == t) | (df.away_team == t)].tail(n)
        return [[r.date, r.away_team if r.home_team == t else r.home_team, int(r.home_score if r.home_team == t else r.away_score),
                 int(r.away_score if r.home_team == t else r.home_score), str(r.tournament), "H" if r.home_team == t and not r.neutral else "N" if r.neutral else "A"]
                for r in sub.itertuples(index=False)][::-1]

    out_fx = []
    seen = set()
    for f in sorted(fixtures, key=lambda x: x["start"]):
        k_ = (f["start"][:10], frozenset((f["h"], f["a"])))
        if k_ in seen:
            continue
        seen.add(k_)
        eh, ea = elo.get(f["h"], 1500), elo.get(f["a"], 1500)
        dr = eh + HOME_ADV - ea                      # Heimvorteil angenommen (Nations League ohne neutrale Plätze)
        lh, la = math.exp(b[0] + b[1] * dr / 100), math.exp(b[0] - b[1] * dr / 100)
        h2h = df[((df.home_team == f["h"]) & (df.away_team == f["a"])) | ((df.home_team == f["a"]) & (df.away_team == f["h"]))].tail(5)
        out_fx.append({**f, "eh": round(eh), "ea": round(ea), "lh": round(lh, 3), "la": round(la, 3),
                       "fh": form(f["h"]), "fa": form(f["a"]),
                       "h2h": [[r.date, r.home_team, r.away_team, int(r.home_score), int(r.away_score), str(r.tournament)] for r in h2h.itertuples(index=False)][::-1]})
    ranking = sorted(((t, round(e)) for t, e in elo.items() if (df.home_team == t).any() and df[(df.home_team == t) | (df.away_team == t)].date.max() >= "2024-01-01"),
                     key=lambda x: -x[1])[:60]
    active = set(df[df.date >= "2018-01-01"].home_team) | set(df[df.date >= "2018-01-01"].away_team)
    out = {"updated": now.isoformat(timespec="minutes"), "results_until": df.date.max(), "fixtures": out_fx,
           "elo_all": {t: round(e) for t, e in elo.items() if t in active},
           "ranking": ranking, "unmatched": sorted(unmatched)[:30], "model": {"c": round(float(b[0]), 4), "d": round(float(b[1]), 4)}}
    (data_dir / "international.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{len(out_fx)} kommende Länderspiele, Ergebnisse bis {out['results_until']}" + (f", nicht zugeordnet: {sorted(unmatched)[:10]}" if unmatched else ""))
