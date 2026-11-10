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


import re, unicodedata


def nkey(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _std(id_, start, country, comp, h, a, status, hs=None, as_=None, src=""):
    return {"id": id_, "start": start, "country": country, "comp": comp, "h": h, "a": a, "hid": None, "aid": None,
            "status": status, "hs": hs, "as": as_, "hn": None, "an": None, "slug": None, "cid": None, "src": src}


def nba_cdn():
    """Offizielle NBA-Datendatei: ganze Saison mit Spielplan und Ergebnissen."""
    j = requests.get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json", headers=BROWSER, timeout=40).json()
    out = []
    for d in (j.get("leagueSchedule") or {}).get("gameDates", []):
        for g in d.get("games", []):
            ht, at = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            if not ht.get("teamName") or not at.get("teamName"):
                continue
            st = g.get("gameStatus")
            start = (g.get("gameDateTimeUTC") or "").replace("Z", "+00:00")
            out.append(_std(f"nba{g.get('gameId')}", start, "USA", "NBA", f"{ht.get('teamCity')} {ht.get('teamName')}", f"{at.get('teamCity')} {at.get('teamName')}",
                            "finished" if st == 3 else "notstarted" if st == 1 else "other", ht.get("score") if st == 3 else None, at.get("score") if st == 3 else None, "nba.com"))
    return out


def nhl_week(day):
    """Offizielle NHL-Schnittstelle: eine Spielwoche ab Tag 'day'."""
    j = requests.get(f"https://api-web.nhle.com/v1/schedule/{day}", headers=BROWSER, timeout=40).json()
    out = []
    for w in j.get("gameWeek", []):
        for g in w.get("games", []):
            ht, at = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            name = lambda t: f"{(t.get('placeName') or {}).get('default', '')} {(t.get('commonName') or {}).get('default', '')}".strip()
            st = g.get("gameState")
            fin = st in ("OFF", "FINAL")
            out.append(_std(f"nhl{g.get('id')}", (g.get("startTimeUTC") or "").replace("Z", "+00:00"), "USA", "NHL", name(ht), name(at),
                            "finished" if fin else "notstarted" if st in ("FUT", "PRE") else "other", ht.get("score") if fin else None, at.get("score") if fin else None, "nhl.com"))
    return out


ESPN_LG = {"basketball": ("basketball", "nba", "NBA"), "ice-hockey": ("hockey", "nhl", "NHL")}


def espn_day(sport, d):
    """ESPN-Spielplan eines Tages (NBA, NHL): zuverlässig von Servern aus, ohne Key."""
    sp, lg, name = ESPN_LG[sport]
    j = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{sp}/{lg}/scoreboard", params={"dates": d.replace("-", "")},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()
    out = []
    for ev in j.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        tm = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in tm or "away" not in tm:
            continue
        st = ((ev.get("status") or {}).get("type") or {})
        fin = bool(st.get("completed"))
        sc = lambda c: int(float(c.get("score"))) if fin and c.get("score") not in (None, "") else None
        start = ev.get("date", "").replace("Z", "+00:00")
        if len(start) == 22:          # 2026-11-10T00:30+00:00 -> Sekunden ergänzen
            start = start[:16] + ":00" + start[16:]
        out.append(_std(f"espn{ev.get('id')}", start, "USA", name, tm["home"]["team"].get("displayName", ""), tm["away"]["team"].get("displayName", ""),
                        "finished" if fin else "notstarted" if st.get("state") == "pre" else "other", sc(tm["home"]), sc(tm["away"]), "espn"))
    return out


TSDB_SPORT = {"basketball": "Basketball", "ice-hockey": "Ice_Hockey", "handball": "Handball"}


def tsdb_day(sport, d):
    """TheSportsDB (freier Schlüssel): alle Spiele eines Tages je Sportart."""
    j = requests.get(f"https://www.thesportsdb.com/api/v1/json/123/eventsday.php", params={"d": d, "s": TSDB_SPORT[sport]}, headers=BROWSER, timeout=30).json()
    out = []
    for e in j.get("events") or []:
        hs, as_ = e.get("intHomeScore"), e.get("intAwayScore")
        fin = hs not in (None, "") and as_ not in (None, "") and (e.get("strStatus") or "").lower() in ("match finished", "ft", "aot", "aet", "finished", "")
        ts = e.get("strTimestamp") or f"{e.get('dateEvent')}T{e.get('strTime') or '12:00:00'}"
        start = ts if "+" in ts or ts.endswith("Z") else ts + "+00:00"
        out.append(_std(f"tsdb{e.get('idEvent')}", start.replace("Z", "+00:00"), e.get("strCountry") or "", e.get("strLeague") or "",
                        e.get("strHomeTeam") or "", e.get("strAwayTeam") or "", "finished" if fin else "notstarted",
                        int(hs) if fin else None, int(as_) if fin else None, "thesportsdb"))
    return out


def collect(data_dir, now, hours_since):
    """Spielplan (−1 bis +3 Tage) alle 3 Stunden, Ergebnisse sammeln, 90 Tage rückwirkend nachladen."""
    data_dir = Path(data_dir)
    st = load(data_dir / "ms_state.json", {})
    for sport in SPORTS:
        s = st.setdefault(sport, {})
        if hours_since(s.get("t")) < 3:
            continue
        res = load(data_dir / f"ms_results_{sport}.json", {"rows": []})
        seen = {r[0] for r in res["rows"]} | {f"{r[1]}|{nkey(r[4])}|{nkey(r[5])}" for r in res["rows"]}
        sched, sched_keys, src_count = [], set(), {}

        def take(e, src):
            nk = f"{e['start'][:10]}|{nkey(e['h'])}|{nkey(e['a'])}"
            if e["status"] == "notstarted" and e["start"] >= now.isoformat():
                if nk in sched_keys:
                    return
                sched_keys.add(nk); sched.append(e); src_count[src] = src_count.get(src, 0) + 1
            elif e["status"] == "finished" and e["hs"] is not None and e["id"] not in seen and nk not in seen:
                res["rows"].append([e["id"], e["start"][:10], None, None, e["h"], e["a"], e["hs"], e["as"], e.get("hn"), e.get("an"), e["country"], e["comp"]])
                seen.add(e["id"]); seen.add(nk)

        days = [now.date() + dt.timedelta(days=i) for i in range(-1, 4)]
        back = s.get("back")
        back = dt.date.fromisoformat(back) if back else now.date() - dt.timedelta(days=1)
        limit = now.date() - dt.timedelta(days=BACKFILL_DAYS)
        extra = []
        while back > limit and len(extra) < BACKFILL_STEP:
            back -= dt.timedelta(days=1)
            extra.append(back)
        any_ok = False
        # 1) Sofascore
        sofa_ok = False
        for d in days + extra:
            try:
                evs = _day(sport, d.isoformat())
            except Exception as ex:
                log(f"{sport} Sofascore {d}: {ex}")
                evs = None
            if evs is None:
                if d == days[0]:
                    break                           # gesperrt: keine weiteren Sofascore-Abrufe in diesem Lauf
                continue
            sofa_ok = True
            for ev in evs:
                x = _row(ev); x["src"] = "sofascore"; take(x, "sofascore")
            time.sleep(0.3)
        any_ok |= sofa_ok
        # 2) Offizielle Quellen
        if sport in ESPN_LG:
            try:
                eb = s.get("espn_back")
                eb = dt.date.fromisoformat(eb) if eb else now.date() - dt.timedelta(days=1)
                edays = list(days)
                for _ in range(10):
                    if eb <= limit:
                        break
                    eb -= dt.timedelta(days=1)
                    edays.append(eb)
                for d in edays:
                    for x in espn_day(sport, d.isoformat()):
                        take(x, "espn")
                    time.sleep(0.2)
                s["espn_back"] = eb.isoformat()
                any_ok = True
            except Exception as ex:
                log(f"{sport} ESPN: {ex}")
        try:
            if sport == "basketball":
                for x in nba_cdn():
                    if x["start"][:10] >= (now.date() - dt.timedelta(days=BACKFILL_DAYS)).isoformat():
                        take(x, "nba.com")
                any_ok = True
            if sport == "ice-hockey":
                for x in nhl_week(now.date().isoformat()):
                    take(x, "nhl.com")
                nb = s.get("nhl_back")
                nb = dt.date.fromisoformat(nb) if nb else now.date()
                for _ in range(3):
                    if nb <= limit:
                        break
                    nb -= dt.timedelta(days=7)
                    for x in nhl_week(nb.isoformat()):
                        take(x, "nhl.com")
                s["nhl_back"] = nb.isoformat()
                any_ok = True
        except Exception as ex:
            log(f"{sport} offizielle Quelle (nba.com/nhl.com): {str(ex)[:120]}")
        # 3) TheSportsDB für alle Ligen
        tsdb_ok = False
        try:
            tb = s.get("tsdb_back")
            tb = dt.date.fromisoformat(tb) if tb else now.date() - dt.timedelta(days=1)
            tdays = list(days)
            for _ in range(5):
                if tb <= limit:
                    break
                tb -= dt.timedelta(days=1)
                tdays.append(tb)
            for d in tdays:
                for x in tsdb_day(sport, d.isoformat()):
                    take(x, "thesportsdb")
                tsdb_ok = True
                time.sleep(0.6)
            s["tsdb_back"] = tb.isoformat()
        except Exception as ex:
            log(f"{sport} TheSportsDB: {ex}")
        any_ok |= tsdb_ok
        if any_ok:
            res["rows"] = [r for r in res["rows"] if r[1] >= (now.date() - dt.timedelta(days=400)).isoformat()]
            save(data_dir / f"ms_results_{sport}.json", res)
            save(data_dir / f"ms_schedule_{sport}.json", {"t": now.isoformat(timespec="minutes"), "events": sched})
            s.update({"t": now.isoformat(timespec="minutes"), "back": back.isoformat() if sofa_ok else s.get("back")})
        log(f"{SPORTS[sport]['name']}: {len(sched)} kommende Spiele ({', '.join(f'{k} {v}' for k, v in src_count.items()) or 'keine'}), "
            f"{len(res['rows'])} gesammelte Ergebnisse, Sofascore {'ok' if sofa_ok else 'nicht erreichbar'}, TheSportsDB {'ok' if tsdb_ok else 'nicht erreichbar'}")
    save(data_dir / "ms_state.json", st)


def _details(sport_events, data_dir, now):
    """Serien, Ausfälle und (ab 90 Minuten vorher) Aufstellung für Top-Ligen der nächsten 36 Stunden."""
    det = load(Path(data_dir) / "ms_details.json", {})
    soon = (now + dt.timedelta(hours=36)).isoformat()
    todo = sorted([e for e in sport_events if e.get("src", "sofascore") == "sofascore" and isinstance(e.get("id"), int)
                   and now.isoformat() <= e["start"] <= soon and any(w in e["comp"].lower() for w in TOP)], key=lambda e: e["start"])
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
        back = []                                     # Rückrechnung: Datum, Liga, Heim, Gast, Siegchance Heim vor dem Spiel, Ergebnis
        last_name, last_comp = {}, {}
        for r in res:
            h, a, hs, as_ = nkey(r[4]), nkey(r[5]), r[6], r[7]
            if hs is None or as_ is None:
                continue
            eh, ea = elo.get(h, 1500.0), elo.get(a, 1500.0)
            dr = eh + cfg["hfa"] - ea
            pairs.append((dr, hs - as_, hs + as_, r[11]))
            we = 1 / (1 + 10 ** (-dr / 400))
            if n.get(h, 0) >= 10 and n.get(a, 0) >= 10:
                back.append([r[1], r[11], r[4], r[5], round(we * 100, 1), 1 if hs > as_ else 0 if hs < as_ else 0.5])
            last_name[h], last_name[a] = r[4], r[5]
            last_comp[h] = last_comp[a] = [r[10], r[11], r[1]]
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
            h, a = nkey(e["h"]), nkey(e["a"])
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
            h2h = [x for x in hist.get(h, []) if nkey(x[2]) == a][-5:][::-1]
            games.append({"id": e["id"], "start": e["start"], "country": e["country"], "comp": e["comp"], "h": e["h"], "a": e["a"],
                          "q": q, "nh": nh, "na": na, "eh": round(eh), "ea": round(ea), "p": round(p_home * 100, 1),
                          "m": round(margin, 2), "sd": round(sd, 2), "eth": round(exp_h, 2), "eta": round(exp_a, 2),
                          "fh": form(h), "fa": form(a), "fhh": "".join("S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in Hh[-5:]),
                          "faa": "".join("S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in Aa[-5:]),
                          "hfor": hf, "hag": hag, "afor": af, "aag": aag, "lh": last(h), "la": last(a), "h2h": h2h,
                          "top": any(w in e["comp"].lower() for w in TOP),
                          "url": f"https://www.sofascore.com/{e['slug']}/{e['cid']}" if e.get("slug") and e.get("cid") else None})
            all_events.append(e)
        # Teamübersicht: alle Teams mit Spielen in den letzten 120 Tagen
        cutoff = (now.date() - dt.timedelta(days=120)).isoformat()
        teams = []
        for tid, hs_ in hist.items():
            if not hs_ or hs_[-1][0] < cutoff or len(hs_) < 3:
                continue
            last20 = hs_[-20:]
            H_, A_ = [x for x in last20 if x[1] == "H"], [x for x in last20 if x[1] == "A"]
            av = lambda arr, i: round(sum(x[i] for x in arr) / len(arr), 1) if arr else None
            wl = lambda arr: f"{sum(1 for x in arr if x[3] > x[4])}-{sum(1 for x in arr if x[3] < x[4])}"
            res = ["S" if x[3] > x[4] else "N" if x[3] < x[4] else "U" for x in hs_]
            streak = 0
            for c in reversed(res):
                if c == res[-1]:
                    streak += 1
                else:
                    break
            cc = last_comp.get(tid, ["", "", ""])
            teams.append([last_name.get(tid, str(tid)), cc[0], cc[1], len(hs_), round(sum(1 for c in res if c == "S") / len(res) * 100, 1),
                          wl([x for x in hs_ if x[1] == "H"]), wl([x for x in hs_ if x[1] == "A"]), "".join(res[-5:]), f"{res[-1]}{streak}",
                          av(last20, 3), av(last20, 4), av(H_, 3), av(H_, 4), av(A_, 3), av(A_, 4), round(elo.get(tid, 1500))])
        out[sport] = {"name": cfg["name"], "slope": slope, "sd": sd, "games": games, "back": back[-8000:],
                      "tcols": ["team", "country", "comp", "n", "win", "home", "away", "form", "streak", "pf", "pa", "pfh", "pah", "pfa", "paa", "elo"],
                      "teams": teams}
        qs = [g["q"] for g in games]
        log(f"{cfg['name']}: {len(games)} Spiele bewertet (●●○ {qs.count(2)}, ●○○ {qs.count(1)}, ○○○ {qs.count(0)}), Streuung {sd:.1f}")
    _details(all_events, data_dir, now)
    save(data_dir / "sports.json", {"updated": now.isoformat(timespec="minutes"), "sports": out})
