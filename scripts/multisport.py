"""Basketball, Eishockey und Handball für das Tendenz-Board (alle Ligen weltweit).

Spielplan und Ergebnisse: Sofascore (Tagesplan je Sportart). Ergebnisse werden gesammelt und einmalig
90 Tage rückwirkend nachgeladen (10 Tage pro Lauf), damit sofort eine Wertung entsteht.
Modell je Team (Sofascore-Team-ID): Elo mit Punkt-/Tordifferenz und Heimvorteil, dazu Heim- und
Auswärtsschnitte der letzten 10 Spiele. Basketball/Handball: Vorsprung und Gesamtpunkte als Normalverteilung,
Streuung aus den eigenen Daten gelernt. Eishockey: Tore als Poisson (3-Weg nach 60 Minuten), Elo für Sieg inkl. Verlängerung.
"""
import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np
import requests

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json", "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}
SPORTS = {
    "basketball": {"name": "Basketball", "hfa": 70, "k": 20, "pts_per_elo": 1 / 28, "sd": 12.0, "avg_total": 165.0},
    "ice-hockey": {"name": "Eishockey", "hfa": 35, "k": 12, "pts_per_elo": 1 / 160, "sd": 2.2, "avg_total": 5.8},
    "handball": {"name": "Handball", "hfa": 80, "k": 20, "pts_per_elo": 1 / 60, "sd": 5.5, "avg_total": 56.0},
}
TOP = ("nba", "euroleague", "eurocup", "acb", "bbl", "lega a", "basketball champions", "nhl", "ice hockey league", "del", "shl",
       "liiga", "khl", "national league", "extraliga", "champions hockey", "handball-bundesliga", "hbl", "ehf", "liqui moly", "starligue")
BACKFILL_DAYS, BACKFILL_STEP = 90, 10
DETAIL_BUDGET = 60


def log(*a):
    print("[Mehr Sport]", *a, flush=True)


def load(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def save(p, o):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _day(sport, d):
    r = requests.get(f"https://api.sofascore.com/api/v1/sport/{sport}/scheduled-events/{d}", headers=BROWSER, timeout=40)
    if r.status_code != 200:
        log(f"{sport} {d}: Status {r.status_code}")
        return None
    return r.json().get("events", [])


def _row(ev):
    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
    t = ev.get("tournament") or {}
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    return {"id": ev.get("id"), "start": dt.datetime.fromtimestamp(ev.get("startTimestamp", 0), dt.timezone.utc).isoformat(),
            "country": (t.get("category") or {}).get("name", ""), "comp": (t.get("uniqueTournament") or {}).get("name") or t.get("name", ""),
            "h": ht.get("name", ""), "a": at.get("name", ""), "hid": ht.get("id"), "aid": at.get("id"),
            "status": (ev.get("status") or {}).get("type"), "hs": hs.get("current"), "as": as_.get("current"),
            "hn": hs.get("normaltime"), "an": as_.get("normaltime"), "slug": ev.get("slug"), "cid": ev.get("customId")}


def collect(data_dir, now, hours_since):
    """Spielplan (−1 bis +3 Tage) alle 3 Stunden, Ergebnisse sammeln, 90 Tage rückwirkend nachladen."""
    data_dir = Path(data_dir)
    st = load(data_dir / "ms_state.json", {})
    for sport in SPORTS:
        s = st.setdefault(sport, {})
        if hours_since(s.get("t")) < 3:
            continue
        res = load(data_dir / f"ms_results_{sport}.json", {"rows": []})
        seen = {r[0] for r in res["rows"]}
        sched, ok = [], False
        days = [now.date() + dt.timedelta(days=i) for i in range(-1, 4)]
        back = s.get("back")
        back = dt.date.fromisoformat(back) if back else now.date() - dt.timedelta(days=1)
        limit = now.date() - dt.timedelta(days=BACKFILL_DAYS)
        extra = []
        while back > limit and len(extra) < BACKFILL_STEP:
            back -= dt.timedelta(days=1)
            extra.append(back)
        for d in days + extra:
            try:
                evs = _day(sport, d.isoformat())
            except Exception as e:
                log(f"{sport} {d}: {e}")
                evs = None
            if evs is None:
                continue
            ok = True
            for ev in evs:
                e = _row(ev)
                if e["status"] == "notstarted" and e["start"] >= now.isoformat() and d >= now.date() - dt.timedelta(days=1):
                    sched.append(e)
                elif e["status"] == "finished" and e["hs"] is not None and e["id"] not in seen:
                    res["rows"].append([e["id"], e["start"][:10], e["hid"], e["aid"], e["h"], e["a"], e["hs"], e["as"],
                                        e["hn"], e["an"], e["country"], e["comp"]])
                    seen.add(e["id"])
            time.sleep(0.3)
        if ok:
            res["rows"] = [r for r in res["rows"] if r[1] >= (now.date() - dt.timedelta(days=400)).isoformat()]
            save(data_dir / f"ms_results_{sport}.json", res)
            save(data_dir / f"ms_schedule_{sport}.json", {"t": now.isoformat(timespec="minutes"), "events": sched})
            s.update({"t": now.isoformat(timespec="minutes"), "back": back.isoformat()})
        log(f"{SPORTS[sport]['name']}: {len(sched)} kommende Spiele, {len(res['rows'])} gesammelte Ergebnisse, nachgeladen bis {back}, Sofascore {'ok' if ok else 'nicht erreichbar'}")
    save(data_dir / "ms_state.json", st)


def _details(sport_events, data_dir, now):
    """Serien, Ausfälle und (ab 90 Minuten vorher) Aufstellung für Top-Ligen der nächsten 36 Stunden."""
    det = load(Path(data_dir) / "ms_details.json", {})
    soon = (now + dt.timedelta(hours=36)).isoformat()
    todo = sorted([e for e in sport_events if now.isoformat() <= e["start"] <= soon and any(w in e["comp"].lower() for w in TOP)], key=lambda e: e["start"])
    used = 0
    for e in todo:
        if used >= DETAIL_BUDGET:
            break
        k, d = str(e["id"]), det.get(str(e["id"]), {})
        mins = (dt.datetime.fromisoformat(e["start"]) - now).total_seconds() / 60
        need_base = "streaks" not in d
        need_lu = (mins <= 90 and not d.get("confirmed") and now.timestamp() - d.get("lt", 0) > 1200) or "missing" not in d
        if not (need_base or need_lu):
            continue
        try:
            if need_base:
                r = requests.get(f"https://api.sofascore.com/api/v1/event/{k}/team-streaks", headers=BROWSER, timeout=25); used += 1
                if r.status_code == 403:
                    log("Details: Sofascore blockt"); break
                j = r.json() if r.status_code == 200 else {}
                d["streaks"] = [[x.get("name"), x.get("value"), x.get("team")] for x in j.get("general", [])][:12]
            if need_lu:
                r = requests.get(f"https://api.sofascore.com/api/v1/event/{k}/lineups", headers=BROWSER, timeout=25); used += 1
                j = r.json() if r.status_code == 200 else {}
                d["confirmed"] = bool(j.get("confirmed"))
                d["missing"] = {sd: [[(m.get("player") or {}).get("name"), m.get("type"), m.get("reason")] for m in (j.get(sd) or {}).get("missingPlayers", [])] for sd in ("home", "away")}
                d["lt"] = now.timestamp()
            d["t"] = now.isoformat(timespec="minutes")
            det[k] = d
            time.sleep(0.3)
        except Exception as ex:
            log(f"Details {k}: {ex}")
    keep = {str(e["id"]) for e in sport_events}
    save(Path(data_dir) / "ms_details.json", {k: v for k, v in det.items() if k in keep})
    log(f"Details: {used} Abrufe")


def build(data_dir, now):
    data_dir = Path(data_dir)
    out, all_events = {}, []
    for sport, cfg in SPORTS.items():
        res = sorted(load(data_dir / f"ms_results_{sport}.json", {"rows": []})["rows"], key=lambda r: r[1])
        sched = load(data_dir / f"ms_schedule_{sport}.json", {"events": []})["events"]
        elo, n, hist = {}, {}, {}
        pairs = []                                    # (Elo-Differenz inkl. Heimvorteil, Punktdifferenz, Gesamt)
        for r in res:
            h, a, hs, as_ = r[2] or r[4], r[3] or r[5], r[6], r[7]
            if hs is None or as_ is None:
                continue
            eh, ea = elo.get(h, 1500.0), elo.get(a, 1500.0)
            dr = eh + cfg["hfa"] - ea
            pairs.append((dr, hs - as_, hs + as_, r[11]))
            we = 1 / (1 + 10 ** (-dr / 400))
            w = 1.0 if hs > as_ else 0.0 if hs < as_ else 0.5
            mov = abs(hs - as_) * (1 if sport != "basketball" else 0.25)
            k = cfg["k"] * math.log(mov + 1 + 1e-9 if mov > 0 else 1.5) * (2.2 / (abs(dr) * 0.001 + 2.2))
            elo[h], elo[a] = eh + k * (w - we), ea - k * (w - we)
            n[h], n[a] = n.get(h, 0) + 1, n.get(a, 0) + 1
            for tid, gf, ga, venue, opp in ((h, hs, as_, "H", r[5]), (a, as_, hs, "A", r[4])):
                hist.setdefault(tid, []).append([r[1], venue, opp, gf, ga, r[11]])
        # Punkte je Elo-Punkt und Streuung aus den Daten lernen
        slope, sd = cfg["pts_per_elo"], cfg["sd"]
        if len(pairs) > 300:
            x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs], float)
            slope = float((x * y).sum() / (x * x).sum())
            sd = float(np.std(y - slope * x))
        league_tot = {}
        for p in pairs[-5000:]:
            league_tot.setdefault(p[3], []).append(p[2])
        games = []
        for e in sorted(sched, key=lambda x: x["start"]):
            if e["start"] < now.isoformat():
                continue
            h, a = e["hid"] or e["h"], e["aid"] or e["a"]
            nh, na = n.get(h, 0), n.get(a, 0)
            q = 2 if min(nh, na) >= 10 else 1 if min(nh, na) >= 4 else 0
            eh, ea = elo.get(h, 1500.0), elo.get(a, 1500.0)
            dr = eh + cfg["hfa"] - ea
            p_home = 1 / (1 + 10 ** (-dr / 400))
            Hh = [x for x in hist.get(h, []) if x[1] == "H"][-10:]
            Aa = [x for x in hist.get(a, []) if x[1] == "A"][-10:]
            def avg(arr, i):
                return sum(x[i] for x in arr) / len(arr) if arr else None
            lt = league_tot.get(e["comp"])
            base_tot = sum(lt[-200:]) / len(lt[-200:]) if lt and len(lt) >= 10 else cfg["avg_total"]
            hf, hag, af, aag = avg(Hh, 3), avg(Hh, 4), avg(Aa, 3), avg(Aa, 4)
            if None not in (hf, hag, af, aag) and len(Hh) >= 3 and len(Aa) >= 3:
                exp_h, exp_a = (hf + aag) / 2, (af + hag) / 2
            else:
                exp_h = exp_a = base_tot / 2
            margin = slope * dr
            form = lambda tid, k=5: "".join("S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in hist.get(tid, [])[-k:])
            last = lambda tid, k=6: hist.get(tid, [])[-k:][::-1]
            h2h = [x for x in hist.get(h, []) if x[2] == e["a"]][-5:][::-1]
            games.append({"id": e["id"], "start": e["start"], "country": e["country"], "comp": e["comp"], "h": e["h"], "a": e["a"],
                          "q": q, "nh": nh, "na": na, "eh": round(eh), "ea": round(ea), "p": round(p_home * 100, 1),
                          "m": round(margin, 2), "sd": round(sd, 2), "eth": round(exp_h, 2), "eta": round(exp_a, 2),
                          "fh": form(h), "fa": form(a), "fhh": "".join("S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in Hh[-5:]),
                          "faa": "".join("S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in Aa[-5:]),
                          "hfor": hf, "hag": hag, "afor": af, "aag": aag, "lh": last(h), "la": last(a), "h2h": h2h,
                          "top": any(w in e["comp"].lower() for w in TOP),
                          "url": f"https://www.sofascore.com/{e['slug']}/{e['cid']}" if e.get("slug") and e.get("cid") else None})
            all_events.append(e)
        out[sport] = {"name": cfg["name"], "slope": slope, "sd": sd, "games": games}
        qs = [g["q"] for g in games]
        log(f"{cfg['name']}: {len(games)} Spiele bewertet (●●○ {qs.count(2)}, ●○○ {qs.count(1)}, ○○○ {qs.count(0)}), Streuung {sd:.1f}")
    _details(all_events, data_dir, now)
    save(data_dir / "sports.json", {"updated": now.isoformat(timespec="minutes"), "sports": out})
