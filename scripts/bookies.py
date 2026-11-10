"""Echte Buchmacher-Quoten (bet365, Interwetten) über Odds-API.io, sobald ein Key hinterlegt ist.

Aktivierung: GitHub-Secret ODDS_API_IO_KEY (Odds-API.io, z. B. Tarif Solo mit bet365 + Interwetten).
Ohne Key passiert nichts, das Board nutzt dann die kostenlosen bet365-Quoten der Top-Ligen (football-data).
Ablauf je Sportart: Ligen -> kommende Spiele bei bet365 -> Quoten in Paketen. Alle 30 Minuten.
"""
import json
import os
import time
from pathlib import Path

import requests

API = "https://api.odds-api.io/v3"
SPORTS = ["football", "basketball", "ice-hockey", "tennis", "handball"]
BOOKS = ["Bet365", "Interwetten"]
EVERY_MIN = 30
MAX_LEAGUES = 400


def log(*a):
    print("[Buchmacher]", *a, flush=True)


def run(data_dir, now, hours_since):
    key = os.environ.get("ODDS_API_IO_KEY", "").strip()
    path = Path(data_dir) / "bookie_odds.json"
    if not key:
        log("kein ODDS_API_IO_KEY hinterlegt, nur kostenlose bet365-Quoten der Top-Ligen")
        return
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        old = {}
    if hours_since(old.get("t")) * 60 < EVERY_MIN:
        return
    s = requests.Session()
    get = lambda ep, **p: s.get(f"{API}/{ep}", params={"apiKey": key, **p}, timeout=30)
    games, used = [], 0
    for sport in SPORTS:
        try:
            r = get("leagues", sport=sport); used += 1
            if r.status_code != 200:
                log(f"{sport}: Ligen Status {r.status_code}")
                continue
            leagues = [l for l in r.json() if (l.get("eventsCount") or 0) > 0][:MAX_LEAGUES]
        except Exception as e:
            log(f"{sport}: {e}")
            continue
        ids, meta = [], {}
        for lg in leagues:
            try:
                r = get("events", sport=sport, league=lg["slug"], status="pending", bookmaker="Bet365"); used += 1
                if r.status_code != 200:
                    continue
                for ev in r.json():
                    ids.append(ev["id"])
                    meta[ev["id"]] = {"sport": sport, "league": lg.get("name"), "home": ev.get("home"), "away": ev.get("away"), "date": ev.get("date")}
            except Exception:
                continue
        for i in range(0, len(ids), 10):
            chunk = ids[i:i + 10]
            try:
                r = get("odds/multi", eventIds=",".join(map(str, chunk)), bookmakers=",".join(BOOKS)); used += 1
                if r.status_code != 200:
                    continue
                data = r.json()
                for ev in (data if isinstance(data, list) else [data]):
                    m = meta.get(ev.get("id"), {})
                    bm = {}
                    for book, markets in (ev.get("bookmakers") or {}).items():
                        mk = {}
                        for x in markets:
                            name, odds = x.get("name"), x.get("odds") or []
                            if name in ("ML", "Totals", "Both Teams To Score", "Double Chance", "Spread"):
                                mk[name] = odds
                        bm[book] = mk
                    if bm:
                        games.append({**m, "id": ev.get("id"), "bm": bm, "urls": ev.get("urls") or {}})
            except Exception as e:
                log(f"Quoten-Paket: {e}")
            time.sleep(0.1)
        log(f"{sport}: {sum(1 for g in games if g.get('sport') == sport)} Spiele mit Quoten")
    path.write_text(json.dumps({"t": now.isoformat(timespec="minutes"), "games": games}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{len(games)} Spiele mit bet365/Interwetten-Quoten, {used} Abfragen")
