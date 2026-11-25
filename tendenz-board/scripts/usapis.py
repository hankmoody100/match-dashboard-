"""Offizielle bzw. öffentliche US-Sportdaten (ohne Key) als zweite Quelle neben Sofascore.

NBA: ESPN Verletztenbericht und Tabelle (Bilanz gesamt, heim, auswärts, letzte 10, Serie, Punkte).
NHL: offizielle NHL-Schnittstelle (api-web.nhle.com) für die Tabelle, ESPN für den Verletztenbericht.
Jede Abfrage ist einzeln abgesichert: fällt eine aus, bleiben die anderen und der letzte Stand erhalten.
"""
import json
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (tendenz-board, privates Dashboard)"}
ESPN = "https://site.api.espn.com/apis"


def log(*a):
    print("[US-Daten]", *a, flush=True)


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def espn_injuries(sport, league):
    j = _get(f"{ESPN}/site/v2/sports/{sport}/{league}/injuries")
    out = {}
    for t in j.get("injuries", []):
        team = t.get("displayName") or (t.get("team") or {}).get("displayName")
        rows = []
        for i in t.get("injuries", []):
            ath = i.get("athlete") or {}
            rows.append([ath.get("displayName"), ((ath.get("position") or {}).get("abbreviation")), i.get("status"),
                         i.get("shortComment") or (i.get("details") or {}).get("type") or ""])
        if team:
            out[team] = rows
    return out


def espn_standings(sport, league):
    j = _get(f"{ESPN}/v2/sports/{sport}/{league}/standings")
    out = {}
    groups = j.get("children") or [j]
    for g in groups:
        for e in ((g.get("standings") or {}).get("entries") or []):
            team = (e.get("team") or {}).get("displayName")
            s = {}
            for st in e.get("stats", []):
                key = (st.get("type") or st.get("name") or "").lower()
                s[key] = st.get("displayValue") or st.get("summary") or st.get("value")
            if team:
                out[team] = {"rec": f"{s.get('wins', '?')}-{s.get('losses', '?')}", "home": s.get("home"), "road": s.get("road") or s.get("away"),
                             "l10": s.get("lasttengames") or s.get("last ten games"), "streak": s.get("streak"),
                             "pf": s.get("avgpointsfor"), "pa": s.get("avgpointsagainst"), "diff": s.get("differential") or s.get("pointdifferential")}
    return out


def nhl_standings():
    j = _get("https://api-web.nhle.com/v1/standings/now")
    out = {}
    for t in j.get("standings", []):
        name = (t.get("teamName") or {}).get("default")
        gp = t.get("gamesPlayed") or 0
        out[name] = {"rec": f"{t.get('wins')}-{t.get('losses')}-{t.get('otLosses')}", "pts": t.get("points"), "rank": t.get("leagueSequence"),
                     "home": f"{t.get('homeWins')}-{t.get('homeLosses')}-{t.get('homeOtLosses')}",
                     "road": f"{t.get('roadWins')}-{t.get('roadLosses')}-{t.get('roadOtLosses')}",
                     "l10": f"{t.get('l10Wins')}-{t.get('l10Losses')}-{t.get('l10OtLosses')}",
                     "streak": f"{t.get('streakCode', '')}{t.get('streakCount', '')}",
                     "pf": round(t.get("goalFor", 0) / gp, 2) if gp else None, "pa": round(t.get("goalAgainst", 0) / gp, 2) if gp else None}
    return out


def run(data_dir, now):
    path = Path(data_dir) / "us_official.json"
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        out = {}
    jobs = {("nba", "injuries"): lambda: espn_injuries("basketball", "nba"),
            ("nba", "standings"): lambda: espn_standings("basketball", "nba"),
            ("nhl", "injuries"): lambda: espn_injuries("hockey", "nhl"),
            ("nhl", "standings"): nhl_standings}
    status = {}
    for (lg, part), fn in jobs.items():
        try:
            data = fn()
            if data:
                out.setdefault(lg, {})[part] = data
                out[lg][part + "_t"] = now.isoformat(timespec="minutes")
            status[f"{lg} {part}"] = len(data)
        except Exception as e:
            status[f"{lg} {part}"] = f"Fehler: {str(e)[:80]}"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(status)
    return status
