"""Alle verfügbaren Fußballspiele weltweit für das Tendenz-Board.

Spielplan: Sofascore (alle Länder, Ligen, Pokale, Länderspiele), Ausweichquelle ESPN für Länderspiele.
Bewertung je nach Datenlage:
  3 = tiefes Liga-Modell (wird im Reiter "Kommende Spiele" gezeigt)
  2 = Elo-Wertung: Länder-Elo (international.py) oder ClubElo (europäische Vereine)
  1 = eigene Elo aus gesammelten Ergebnissen (wächst täglich)
  0 = keine Daten, nur Heimvorteil
Endergebnisse werden täglich gesammelt (data/sf_results.json), damit auch Ligen ohne freie Wertung
mit der Zeit eine eigene Elo bekommen.
"""
import datetime as dt
import difflib
import json
import math
import re
import unicodedata
from pathlib import Path

import requests

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json", "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}
ESPN_SLUGS = ["fifa.friendly", "uefa.nations", "fifa.worldq.uefa", "fifa.worldq.conmebol", "fifa.worldq.concacaf",
              "fifa.worldq.afc", "fifa.worldq.caf", "uefa.euroq", "concacaf.nations.league", "caf.nations_qual", "afc.asian.cup.qual"]
COUNTRY3 = {"England": "ENG", "Germany": "GER", "Spain": "ESP", "Italy": "ITA", "France": "FRA", "Netherlands": "NED",
            "Portugal": "POR", "Belgium": "BEL", "Turkey": "TUR", "Türkiye": "TUR", "Scotland": "SCO", "Austria": "AUT",
            "Switzerland": "SUI", "Denmark": "DEN", "Sweden": "SWE", "Norway": "NOR", "Poland": "POL", "Czech Republic": "CZE",
            "Czechia": "CZE", "Greece": "GRE", "Croatia": "CRO", "Serbia": "SRB", "Romania": "ROM", "Ukraine": "UKR",
            "Russia": "RUS", "Hungary": "HUN", "Slovakia": "SVK", "Slovenia": "SVN", "Bulgaria": "BUL", "Cyprus": "CYP",
            "Israel": "ISR", "Finland": "FIN", "Ireland": "IRL", "Wales": "WAL", "Northern Ireland": "NIR", "Iceland": "ISL",
            "Bosnia & Herzegovina": "BIH", "Albania": "ALB", "Kazakhstan": "KAZ", "Belarus": "BLR", "Azerbaijan": "AZE",
            "Georgia": "GEO", "Armenia": "ARM", "Lithuania": "LTU", "Latvia": "LVA", "Estonia": "EST", "Moldova": "MDA",
            "North Macedonia": "MKD", "Montenegro": "MNE", "Kosovo": "KOS", "Luxembourg": "LUX", "Malta": "MLT", "Faroe Islands": "FRO"}
YOUTH_W = re.compile(r"\b(U-?\d{2}|U\d{2}|Women|Woman|Frauen|W)\b|\(W\)", re.I)
HFA_CLUB, HFA_NAT = 65, 100
LOG_BASE, SLOPE = math.log(1.27), 0.17     # erwartete Tore aus Elo-Differenz (Vereine)


def log(*a):
    print("[Alle Spiele]", *a, flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s.replace("-", " ")).split()


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


# ---------- Abruf ----------
def _sofascore_day(d):
    r = requests.get(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{d}", headers=BROWSER, timeout=40)
    if r.status_code != 200:
        log(f"Sofascore {d}: Status {r.status_code}")
        return None
    return r.json().get("events", [])


def _ev(ev, src="sofascore"):
    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
    tour = ev.get("tournament") or {}
    ut = tour.get("uniqueTournament") or {}
    cat = (tour.get("category") or {}).get("name", "")
    national = bool(ht.get("national") or at.get("national"))
    comp = ut.get("name") or tour.get("name", "")
    youth = bool(YOUTH_W.search(ht.get("name", "") + " " + at.get("name", "") + " " + comp)) or ht.get("gender") == "F"
    return {"id": ev.get("id"), "slug": ev.get("slug"), "cid": ev.get("customId"), "start": dt.datetime.fromtimestamp(ev.get("startTimestamp", 0), dt.timezone.utc).isoformat(),
            "country": cat, "comp": comp, "h": ht.get("name", ""), "a": at.get("name", ""), "hid": ht.get("id"), "aid": at.get("id"),
            "nat": national, "youth": youth, "status": (ev.get("status") or {}).get("type"),
            "hs": (ev.get("homeScore") or {}).get("current"), "as": (ev.get("awayScore") or {}).get("current"), "src": src}


def _espn(now):
    out = []
    span = f"{now:%Y%m%d}-{(now + dt.timedelta(days=7)):%Y%m%d}"
    for slug in ESPN_SLUGS:
        try:
            r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard", params={"dates": span}, timeout=30)
            if r.status_code != 200:
                continue
            j = r.json()
            name = ((j.get("leagues") or [{}])[0]).get("name", slug)
            for ev in j.get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                if "home" not in teams or "away" not in teams:
                    continue
                st = (ev.get("status") or {}).get("type") or {}
                out.append({"id": f"espn{ev.get('id')}", "start": ev.get("date", "").replace("Z", ":00+00:00") if len(ev.get("date", "")) == 17 else ev.get("date", ""),
                            "country": "International", "comp": name, "h": teams["home"]["team"].get("displayName", ""),
                            "a": teams["away"]["team"].get("displayName", ""), "hid": None, "aid": None, "nat": True, "youth": False,
                            "status": "notstarted" if st.get("state") == "pre" else "finished" if st.get("completed") else "other",
                            "hs": None, "as": None, "src": "espn"})
        except Exception as e:
            log(f"ESPN {slug}: {e}")
    return out


def fetch_schedule(data_dir, now, hours_since):
    """Spielplan (heute + 3 Tage) alle 3 Stunden, gesammelte Endergebnisse (gestern + heute)."""
    data_dir = Path(data_dir)
    sched = load(data_dir / "sf_schedule.json", {})
    if hours_since(sched.get("t")) < 3:
        return sched
    events, results = [], load(data_dir / "sf_results.json", {"rows": []})
    seen_res = {r[0] for r in results["rows"]}
    ok = False
    for i in range(-1, 4):
        d = (now.date() + dt.timedelta(days=i)).isoformat()
        try:
            raw = _sofascore_day(d)
        except Exception as e:
            log(f"Sofascore {d}: {e}")
            raw = None
        if raw is None:
            continue
        ok = True
        for ev in raw:
            e = _ev(ev)
            if e["status"] == "notstarted" and e["start"] >= now.isoformat():
                events.append(e)
            elif e["status"] == "finished" and e["hs"] is not None and e["id"] not in seen_res:
                results["rows"].append([e["id"], e["start"][:10], e["hid"], e["aid"], e["h"], e["a"], e["hs"], e["as"],
                                        e["country"], e["comp"], int(e["nat"]), int(e["youth"])])
                seen_res.add(e["id"])
    espn = _espn(now)
    events += [e for e in espn if e["status"] == "notstarted"]
    cutoff = (now.date() - dt.timedelta(days=730)).isoformat()
    results["rows"] = [r for r in results["rows"] if r[1] >= cutoff]
    save(data_dir / "sf_results.json", results)
    sched = {"t": now.isoformat(timespec="minutes"), "sofascore_ok": ok, "events": events}
    save(data_dir / "sf_schedule.json", sched)
    log(f"Spielplan: {len(events)} kommende Spiele ({sum(1 for e in events if e['nat'])} Länderspiele), "
        f"{len(results['rows'])} gesammelte Ergebnisse, Sofascore {'ok' if ok else 'nicht erreichbar'}, ESPN {len(espn)}")
    return sched


# ---------- Spieldetails (Form, Serien, Ausfälle, Aufstellung) ----------
DETAIL_BUDGET = 120


def _get(path):
    r = requests.get(f"https://api.sofascore.com/api/v1/{path}", headers=BROWSER, timeout=25)
    return r.json() if r.status_code == 200 else None


def fetch_details(data_dir, now):
    """Für Spiele der nächsten 36 Stunden: Form/Tabellenplatz, Serien, Ausfälle; ab 90 Minuten vorher die Aufstellung."""
    import time
    data_dir = Path(data_dir)
    sched = load(data_dir / "sf_schedule.json", {"events": []})
    det = load(data_dir / "sf_details.json", {})
    soon = (now + dt.timedelta(hours=36)).isoformat()
    todo = [e for e in sched.get("events", []) if e.get("src") == "sofascore" and e.get("id") and now.isoformat() <= e["start"] <= soon]
    todo.sort(key=lambda e: e["start"])
    used, blocked = 0, False
    for e in todo:
        if used >= DETAIL_BUDGET or blocked:
            break
        k = str(e["id"])
        d = det.get(k, {})
        mins = (dt.datetime.fromisoformat(e["start"]) - now).total_seconds() / 60
        need_base = "form" not in d
        need_lineup = mins <= 90 and not d.get("confirmed") and (now.timestamp() - d.get("lt", 0)) > 1200
        need_missing = "missing" not in d or (mins <= 24 * 60 and (now.timestamp() - d.get("mt", 0)) > 6 * 3600)
        if not (need_base or need_lineup or need_missing):
            continue
        try:
            if need_base:
                pf = _get(f"event/{k}/pregame-form"); used += 1
                if pf is None and used == 1:
                    blocked = True
                    log("Spieldetails: Sofascore blockt, übersprungen")
                    break
                ts = _get(f"event/{k}/team-streaks"); used += 1
                d["form"] = {side: {"pos": (pf or {}).get(side + "Team", {}).get("position"), "form": (pf or {}).get(side + "Team", {}).get("form", []),
                                    "rating": (pf or {}).get(side + "Team", {}).get("avgRating")} for side in ("home", "away")}
                d["streaks"] = [[s.get("name"), s.get("value"), s.get("team")] for s in (ts or {}).get("general", [])][:12]
                d["h2h_streaks"] = [[s.get("name"), s.get("value"), s.get("team")] for s in (ts or {}).get("head2head", [])][:6]
            if need_missing or need_lineup:
                lu = _get(f"event/{k}/lineups"); used += 1
                if lu:
                    d["confirmed"] = bool(lu.get("confirmed"))
                    d["missing"] = {side: [[(m.get("player") or {}).get("name"), m.get("type"), m.get("reason")] for m in (lu.get(side) or {}).get("missingPlayers", [])]
                                    for side in ("home", "away")}
                    if d["confirmed"]:
                        d["xi"] = {side: [(p.get("player") or {}).get("name") for p in (lu.get(side) or {}).get("players", []) if not p.get("substitute")]
                                   for side in ("home", "away")}
                else:
                    d.setdefault("missing", {})
                d["mt"] = d["lt"] = now.timestamp()
            d["t"] = now.isoformat(timespec="minutes")
            det[k] = d
            time.sleep(0.4)
        except Exception as e_:
            log(f"Spieldetails {k}: {e_}")
    keep = {str(e["id"]) for e in sched.get("events", [])}
    det = {k: v for k, v in det.items() if k in keep}
    save(data_dir / "sf_details.json", det)
    log(f"Spieldetails: {used} Abrufe, {len(det)} Spiele mit Details")


# ---------- Bewertung ----------
def _own_elo(results):
    elo, n = {}, {}
    for r in sorted(results, key=lambda x: x[1]):
        h, a, hs, as_ = r[2] or r[4], r[3] or r[5], r[6], r[7]
        eh, ea = elo.get(h, 1500.0), elo.get(a, 1500.0)
        we = 1 / (1 + 10 ** (-(eh + HFA_CLUB - ea) / 400))
        w = 1.0 if hs > as_ else 0.5 if hs == as_ else 0.0
        k = 20 * (1 if abs(hs - as_) <= 1 else 1.5 if abs(hs - as_) == 2 else (11 + abs(hs - as_)) / 8)
        elo[h], elo[a] = eh + k * (w - we), ea - k * (w - we)
        n[h], n[a] = n.get(h, 0) + 1, n.get(a, 0) + 1
    return elo, n


def _match(name, cands_norm, cutoff=0.8):
    nn = " ".join(norm(name))
    if nn in cands_norm:
        return cands_norm[nn]
    best, score = None, 0
    for k, v in cands_norm.items():
        s = difflib.SequenceMatcher(None, nn, k).ratio()
        if len(k) >= 4 and (k in nn or nn in k):
            s = max(s, 0.9)
        if s > score:
            best, score = v, s
    return best if score >= cutoff else None


def build(data_dir, now, deep_teams):
    """deep_teams: Menge normalisierter Teamnamen der 12 Ligen mit tiefem Modell."""
    data_dir = Path(data_dir)
    sched = load(data_dir / "sf_schedule.json", {"events": []})
    intl = load(data_dir / "international.json", {})
    nat_elo = intl.get("elo_all", {})
    eloj = load(data_dir / "elo.json", {})
    club = eloj.get("all", {})                    # ClubElo: Verein -> [Elo, Land]
    by_cty = {}
    for c, (e, cty) in club.items():
        by_cty.setdefault(cty, {})[" ".join(norm(c))] = c
    all_club = {" ".join(norm(c)): c for c in club}
    nat_norm = {" ".join(norm(t)): t for t in nat_elo}
    own, own_n = _own_elo(load(data_dir / "sf_results.json", {"rows": []})["rows"])
    out, seen = [], set()
    for e in sorted(sched.get("events", []), key=lambda x: x["start"]):
        if e["start"] < now.isoformat():
            continue
        k_ = (e["start"][:10], " ".join(norm(e["h"])), " ".join(norm(e["a"])))
        if k_ in seen:
            continue
        seen.add(k_)
        deep = " ".join(norm(e["h"])) in deep_teams and " ".join(norm(e["a"])) in deep_teams and not e["nat"]
        eh = ea = None
        q, basis = 0, "keine Daten"
        if e["nat"] and not e["youth"]:
            th, ta = _match(e["h"], nat_norm, 0.85), _match(e["a"], nat_norm, 0.85)
            if th and ta:
                eh, ea, q, basis = nat_elo[th], nat_elo[ta], 2, "Länder-Elo"
        elif not e["nat"] and not e["youth"]:
            pool = by_cty.get(COUNTRY3.get(e["country"], ""), all_club if e["country"] in ("Europe", "World", "International") else {})
            th, ta = _match(e["h"], pool), _match(e["a"], pool)
            if th and ta:
                eh, ea, q, basis = club[th][0], club[ta][0], 2, "ClubElo"
        if q < 2:
            kh, ka = e.get("hid") or e["h"], e.get("aid") or e["a"]
            if own_n.get(kh, 0) >= 8 and own_n.get(ka, 0) >= 8:
                eh, ea, q, basis = own[kh], own[ka], 1, f"eigene Elo ({min(own_n[kh], own_n[ka])}+ Spiele)"
            else:
                eh = ea = 1500.0
        hfa = HFA_NAT if e["nat"] else HFA_CLUB
        dr = (eh - ea) + hfa
        lh, la = math.exp(LOG_BASE + SLOPE * dr / 100), math.exp(LOG_BASE - SLOPE * dr / 100)
        out.append([e["start"], e["country"], e["comp"], e["h"], e["a"], int(e["nat"]), int(e["youth"]), 3 if deep else q,
                    round(lh, 3), round(la, 3), round(eh), round(ea), basis, e["src"], e.get("id"),
                    f"https://www.sofascore.com/{e['slug']}/{e['cid']}" if e.get("slug") and e.get("cid") else None])
    save(data_dir / "allgames.json", {"updated": now.isoformat(timespec="minutes"), "sofascore_ok": sched.get("sofascore_ok"),
                                      "cols": ["start", "country", "comp", "h", "a", "nat", "youth", "q", "lh", "la", "eh", "ea", "basis", "src", "id", "url"],
                                      "games": out})
    qs = [r[7] for r in out]
    log(f"{len(out)} Spiele bewertet: ●●● {qs.count(3)}, ●●○ {qs.count(2)}, ●○○ {qs.count(1)}, ○○○ {qs.count(0)}")
    return out
