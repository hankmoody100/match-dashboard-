"""Tennis-Daten und -Modell für das Tendenz-Board.

Quellen (alle kostenlos, jede für sich abgesichert):
- Sackmann-Archiv auf GitHub: ATP/WTA-Matches mit Aufschlagstatistik (Stand: bis Mai 2026)
- TML-Database auf GitHub: ATP-Matches mit Aufschlagstatistik (sofern aktuell)
- tennis-data.co.uk: Ergebnisse und Quoten ATP/WTA (falls erreichbar)
- The Odds API (Gratis-Key als GitHub-Secret ODDS_API_KEY): kommende Matches mit Quoten

Modell: Belag-Elo + Aufschlag-Return-Modell (Barnett-Clarke), per logistischer
Regression kombiniert, Gewichte aus Matches vor TRACK_START gelernt.
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

TRACK_START = "2026-10-26"
YEARS_BACK = 3
ARCHIVE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main/{t}/{t}_matches_{y}.csv"
TML = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master/{f}"
TD = {"atp": "http://www.tennis-data.co.uk/{y}/{y}.xlsx", "wta": "http://www.tennis-data.co.uk/{y}w/{y}.xlsx"}
ODDS_API = "https://api.the-odds-api.com/v4"
UA = {"User-Agent": "tendenz-board/2.0 (privates Dashboard)"}
STATS = ["ace", "df", "svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced"]
ROUND_ORDER = {"Q1": 0, "Q2": 1, "Q3": 2, "R128": 3, "R64": 4, "R32": 5, "R16": 6, "QF": 7, "SF": 8, "F": 9,
               "RR": 6, "BR": 8, "ER": 3, "1st Round": 4, "2nd Round": 5, "3rd Round": 6, "4th Round": 6,
               "Quarterfinals": 7, "Semifinals": 8, "The Final": 9, "Round Robin": 6}
SURF = {"hard": "H", "clay": "C", "grass": "G", "carpet": "H"}
ODDS_EVERY_H = 6      # 500 Gratis-Abrufe im Monat reichen so für Quoten und Ergebnisse


def log(*a):
    print(f"[Tennis]", *a, flush=True)


def get(url, timeout=40):
    try:
        r = requests.get(url, timeout=timeout, headers=UA)
        if r.status_code == 200:
            return r
    except Exception as e:
        log(f"Fehler {url[:80]}: {e}")
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s.replace("-", " ")).split()


# ---------- Laden ----------
def load_sackmann_format(text, tour, src):
    df = pd.read_csv(io.StringIO(text), low_memory=False)
    out = []
    for r in df.to_dict("records"):
        if r.get("tourney_level") in ("D",) or not isinstance(r.get("winner_name"), str):
            continue   # Davis Cup / BJK Cup ohne Statistik weglassen
        sf = SURF.get(str(r.get("surface", "")).lower())
        if not sf:
            continue
        d = str(r.get("tourney_date", ""))[:8]
        try:
            date = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).isoformat()
        except ValueError:
            continue
        def st(p):
            v = [r.get(f"{p}_{k}") for k in STATS]
            return None if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in v) else [int(x) for x in v]
        def num(x):
            try:
                x = float(x); return None if math.isnan(x) else int(x)
            except (TypeError, ValueError):
                return None
        out.append({"tour": tour, "date": date, "tn": str(r.get("tourney_name", "")), "sf": sf,
                    "lv": str(r.get("tourney_level", "")), "rd": str(r.get("round", "")), "bo": num(r.get("best_of")) or 3,
                    "w": r["winner_name"].strip(), "l": str(r.get("loser_name", "")).strip(),
                    "wr": num(r.get("winner_rank")), "lr": num(r.get("loser_rank")),
                    "score": str(r.get("score", "")), "min": num(r.get("minutes")),
                    "ws": st("w"), "ls": st("l"), "src": src})
    return out


def load_archive(years):
    rows = []
    for t in ("atp", "wta"):
        for y in years:
            r = get(ARCHIVE.format(t=t, y=y))
            if r is not None:
                rows += load_sackmann_format(r.text, t, "archiv")
    log(f"Archiv: {len(rows)} Matches")
    return rows


def load_tml(years):
    rows = []
    for f in [f"{y}.csv" for y in years] + ["ongoing_tourneys.csv"]:
        r = get(TML.format(f=f))
        if r is not None:
            rows += load_sackmann_format(r.text, "atp", "tml")
    log(f"TML: {len(rows)} Matches")
    return rows


def load_tennis_data(years):
    rows = []
    for t, pattern in TD.items():
        for y in years:
            r = get(pattern.format(y=y), timeout=60)
            if r is None or not r.content[:2] == b"PK":
                continue
            try:
                df = pd.read_excel(io.BytesIO(r.content))
            except Exception as e:
                log(f"tennis-data {t} {y} unlesbar: {e}")
                continue
            for x in df.to_dict("records"):
                if str(x.get("Comment", "Completed")) not in ("Completed", "nan"):
                    continue
                sf = SURF.get(str(x.get("Surface", "")).lower())
                try:
                    date = pd.Timestamp(x["Date"]).date().isoformat()
                except Exception:
                    continue
                def f(k):
                    try:
                        v = float(x.get(k)); return None if math.isnan(v) or v <= 1 else round(v, 2)
                    except (TypeError, ValueError):
                        return None
                w, l = str(x.get("Winner", "")).strip(), str(x.get("Loser", "")).strip()
                if not sf or not w or not l:
                    continue
                rows.append({"tour": t, "date": date, "tn": str(x.get("Tournament", "")), "sf": sf,
                             "lv": str(x.get("Series", x.get("Tier", ""))), "rd": str(x.get("Round", "")),
                             "bo": int(x.get("Best of", 3) or 3), "w": w, "l": l, "short": True,
                             "wr": None, "lr": None, "score": "", "min": None, "ws": None, "ls": None, "src": "td",
                             "ow": f("PSW") or f("AvgW"), "ol": f("PSL") or f("AvgL"), "xw": f("MaxW"), "xl": f("MaxL")})
    log(f"tennis-data: {len(rows)} Matches mit Quoten")
    return rows


class Names:
    """Ordnet Kurznamen ('Sinner J.') und Schreibvarianten vollen Namen zu."""

    def __init__(self, full_names):
        self.full = {}
        self.short = {}
        for n in full_names:
            t = norm(n)
            if not t:
                continue
            self.full[" ".join(t)] = n
            for k in (1, 2, 3):
                if len(t) > k:
                    self.short.setdefault((" ".join(t[-k:]), t[0][0]), n)

    def resolve(self, name, short=False):
        t = norm(name)
        if not t:
            return None
        if not short and " ".join(t) in self.full:
            return self.full[" ".join(t)]
        if short and len(t) >= 2 and len(t[-1]) <= 2:
            return self.short.get((" ".join(t[:-1]), t[-1][0]))
        if not short:
            m = difflib.get_close_matches(" ".join(t), list(self.full), n=1, cutoff=0.88)
            if m:
                return self.full[m[0]]
            return self.short.get((t[-1], t[0][0]))
        return None


def merge(base, extra, names, attach_odds=False):
    """Fügt Matches aus extra hinzu; bei Treffern werden nur Quoten übernommen."""
    idx = {}
    for i, m in enumerate(base):
        idx.setdefault((m["tour"], frozenset((m["w"], m["l"]))), []).append(i)
    added = 0
    for e in extra:
        w = names.resolve(e["w"], e.get("short")) or (None if e.get("short") else e["w"])
        l = names.resolve(e["l"], e.get("short")) or (None if e.get("short") else e["l"])
        if not w or not l:
            continue
        e = {**e, "w": w, "l": l}
        hit = None
        for i in idx.get((e["tour"], frozenset((w, l))), []):
            if abs((dt.date.fromisoformat(base[i]["date"]) - dt.date.fromisoformat(e["date"])).days) <= 16:
                hit = i
                break
        if hit is not None:
            if attach_odds:
                for k in ("ow", "ol", "xw", "xl"):
                    if e.get(k) and not base[hit].get(k):
                        base[hit][k] = e[k]
                if base[hit]["w"] != w:   # Sicherheitsnetz: Sieger stimmt nicht überein
                    base[hit]["ow"], base[hit]["ol"], base[hit]["xw"], base[hit]["xl"] = e.get("ol"), e.get("ow"), e.get("xl"), e.get("xw")
            continue
        base.append(e)
        idx.setdefault((e["tour"], frozenset((w, l))), []).append(len(base) - 1)
        added += 1
    return added


# ---------- Wahrscheinlichkeiten (Punkt -> Spiel -> Satz -> Match) ----------
def p_game(p):
    q = 1 - p
    deuce = p * p / (p * p + q * q)
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * p ** 3 * q ** 3 * deuce


def p_tiebreak(pa, pb):
    """pa: A gewinnt Punkt bei eigenem Aufschlag, pb: B gewinnt Punkt bei eigenem Aufschlag."""
    from functools import lru_cache

    @lru_cache(None)
    def f(a, b):
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a == b and a >= 6:
            pw = pa * (1 - pb)
            pl = (1 - pa) * pb
            return pw / (pw + pl) if pw + pl > 0 else 0.5
        n = a + b
        server_a = (n % 4 == 0) or (n % 4 == 3)
        p = pa if server_a else 1 - pb
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)
    return f(0, 0)


def p_set(pa, pb):
    ha, hb = p_game(pa), p_game(pb)
    tb = p_tiebreak(pa, pb)
    from functools import lru_cache

    @lru_cache(None)
    def f(a, b):
        if a >= 6 and a - b >= 2:
            return 1.0
        if b >= 6 and b - a >= 2:
            return 0.0
        if a == 6 and b == 6:
            return tb
        serve_a = (a + b) % 2 == 0
        p = ha if serve_a else 1 - hb
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)
    return f(0, 0)


def p_match(pa, pb, bo=3):
    s = p_set(pa, pb)
    need = (bo + 1) // 2
    return sum(math.comb(need - 1 + k, k) * s ** need * (1 - s) ** k for k in range(need))


# ---------- Modell ----------
def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def run(data_dir, now, meta, hours_since):
    data_dir = Path(data_dir)
    out_path = data_dir / "tennis.json"
    years = list(range(now.year - YEARS_BACK + 1, now.year + 1))
    cache_path = data_dir / "tennis_raw.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    if not cache or hours_since(cache.get("t")) >= 12:
        base = load_archive(years)
        tml = load_tml(years)
        td = load_tennis_data(years)
        if base or tml or td:
            cache = {"t": now.isoformat(timespec="minutes"), "base": base, "tml": tml, "td": td}
            cache_path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    base, tml, td = cache.get("base", []), cache.get("tml", []), cache.get("td", [])
    if not (base or tml):
        log("Keine Tennisdaten verfügbar.")
        return
    names = Names({m["w"] for m in base + tml} | {m["l"] for m in base + tml})
    matches = [dict(m) for m in base]
    a1 = merge(matches, tml, names)
    a2 = merge(matches, td, names, attach_odds=True)
    live_path = data_dir / "tennis_live_results.json"
    try:
        live = json.loads(live_path.read_text(encoding="utf-8"))
    except Exception:
        live = {"t": None, "rows": []}
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key and hours_since(live.get("t")) >= 12:
        new = fetch_scores(key)
        if new is not None:
            seen = {(r["date"], r["w"], r["l"]) for r in live["rows"]}
            live["rows"] += [r for r in new if (r["date"], r["w"], r["l"]) not in seen]
            live["t"] = now.isoformat(timespec="minutes")
            live_path.write_text(json.dumps(live, separators=(",", ":")), encoding="utf-8")
    tsurf = {}
    for m in matches:
        tsurf[" ".join(norm(m["tn"]))] = m["sf"]
    lrows = []
    for r in live["rows"]:
        title = " ".join(norm(re.sub(r"^(ATP|WTA)\s+", "", r["tn"])))
        m_ = difflib.get_close_matches(title, list(tsurf), n=1, cutoff=0.6)
        lrows.append({**r, "sf": tsurf[m_[0]] if m_ else "H", "lv": "", "rd": "", "bo": 3, "wr": None, "lr": None,
                      "score": "", "min": None, "ws": None, "ls": None, "src": "live"})
    a3 = merge(matches, lrows, names)
    log(f"Zusammengeführt: {len(matches)} Matches (+{a1} TML, +{a2} tennis-data, +{a3} gesammelte Ergebnisse)")
    matches.sort(key=lambda m: (m["date"], ROUND_ORDER.get(m["rd"], 5)))
    stats_until = max((m["date"] for m in matches if m.get("ws")), default=None)
    results_until = max((m["date"] for m in matches), default=None)

    # --- Elo (gesamt und je Belag) und Aufschlagwerte, Match für Match ---
    elo, elo_s, cnt, cnt_s = {}, {}, {}, {}
    hist = {}            # Spieler -> Liste (Tag, Belag, Aufschlagpunkte, gewonnen, Returnpunkte, gewonnen)
    tour_avg = {}        # (Tour, Belag) -> [Aufschlagpunkte, gewonnen]
    rows = []
    feat = []
    def k_factor(n, lv):
        return 250 / ((n + 5) ** 0.4) * (1.1 if lv == "G" else 1.0)
    def serve_rates(p, sf, day, tour):
        tot = tour_avg.get((tour, sf), [1, 0.63])
        avg_s = tot[1] / tot[0] if tot[0] > 100 else 0.63
        s_pts = s_won = r_pts = r_won = 0.0
        for (d, s, sp, sw, rp, rw) in hist.get(p, []):
            if day - d > 365:
                continue
            wgt = 1.0 if s == sf else 0.25          # anderer Belag zählt nur zu einem Viertel
            s_pts += wgt * sp; s_won += wgt * sw; r_pts += wgt * rp; r_won += wgt * rw
        K = 300.0                                   # Ausgleich Richtung Tourschnitt
        spw = (s_won + K * avg_s) / (s_pts + K)
        rpw = (r_won + K * (1 - avg_s)) / (r_pts + K)
        return spw, rpw, avg_s, s_pts
    day0 = dt.date(2000, 1, 1)
    for m in matches:
        day = (dt.date.fromisoformat(m["date"]) - day0).days
        w, l, sf, tour = m["w"], m["l"], m["sf"], m["tour"]
        ew, el = elo.get(w, 1500.0), elo.get(l, 1500.0)
        sw_, sl_ = elo_s.get((w, sf), 1500.0), elo_s.get((l, sf), 1500.0)
        spw_w, rpw_w, avg_s, n_w = serve_rates(w, sf, day, tour)
        spw_l, rpw_l, _, n_l = serve_rates(l, sf, day, tour)
        pw_srv = min(max(avg_s + (spw_w - avg_s) - (rpw_l - (1 - avg_s)), 0.4), 0.9)
        pl_srv = min(max(avg_s + (spw_l - avg_s) - (rpw_w - (1 - avg_s)), 0.4), 0.9)
        p_serve = p_match(pw_srv, pl_srv, m["bo"])
        p_e_all = 1 / (1 + 10 ** ((el - ew) / 400))
        p_e_srf = 1 / (1 + 10 ** ((sl_ - sw_) / 400))
        known = cnt.get(w, 0) >= 5 and cnt.get(l, 0) >= 5
        feat.append((m["date"], logit(p_e_all), logit(p_e_srf), logit(p_serve), known, m))
        # Elo aktualisieren
        kw, kl = k_factor(cnt.get(w, 0), m["lv"]), k_factor(cnt.get(l, 0), m["lv"])
        elo[w] = ew + kw * (1 - p_e_all); elo[l] = el - kl * (1 - p_e_all)
        kws, kls = k_factor(cnt_s.get((w, sf), 0), m["lv"]), k_factor(cnt_s.get((l, sf), 0), m["lv"])
        elo_s[(w, sf)] = sw_ + kws * (1 - p_e_srf); elo_s[(l, sf)] = sl_ - kls * (1 - p_e_srf)
        cnt[w] = cnt.get(w, 0) + 1; cnt[l] = cnt.get(l, 0) + 1
        cnt_s[(w, sf)] = cnt_s.get((w, sf), 0) + 1; cnt_s[(l, sf)] = cnt_s.get((l, sf), 0) + 1
        # Aufschlagwerte aktualisieren
        if m.get("ws") and m.get("ls") and m["ws"][2] > 0 and m["ls"][2] > 0:
            a, b = m["ws"], m["ls"]
            wsw, lsw = a[4] + a[5], b[4] + b[5]
            hist.setdefault(w, []).append((day, sf, a[2], wsw, b[2], b[2] - lsw))
            hist.setdefault(l, []).append((day, sf, b[2], lsw, a[2], a[2] - wsw))
            t = tour_avg.setdefault((tour, sf), [0, 0])
            t[0] += a[2] + b[2]; t[1] += wsw + lsw
            if len(hist[w]) > 160: hist[w] = hist[w][-120:]
            if len(hist[l]) > 160: hist[l] = hist[l][-120:]

    # --- Kombination lernen: logistische Regression auf Matches vor TRACK_START ---
    X, Y = [], []
    rng = np.random.default_rng(1)
    for (d, le, ls_, lsv, known, m) in feat:
        if d >= TRACK_START or not known:
            continue
        flip = rng.random() < 0.5      # Sieger zufällig auf Seite A oder B
        x = np.array([le, ls_, lsv])
        X.append(-x if flip else x); Y.append(0 if flip else 1)
    w_ = np.array([0.5, 0.5, 0.5])
    if len(Y) > 500:
        X, Y = np.array(X), np.array(Y, float)
        for _ in range(50):               # Newton-Verfahren
            p = 1 / (1 + np.exp(-X @ w_))
            g = X.T @ (Y - p)
            H = -(X * (p * (1 - p))[:, None]).T @ X - 1e-3 * np.eye(3)
            w_ = w_ - np.linalg.solve(H, g)
    log(f"Gewichte Elo gesamt / Elo Belag / Aufschlag: {np.round(w_, 3).tolist()}")

    def combine(le, ls_, lsv):
        return 1 / (1 + math.exp(-(w_[0] * le + w_[1] * ls_ + w_[2] * lsv)))

    # --- Vergleich mit dem Markt (zwei Jahre vor TRACK_START, Matches mit Quoten) ---
    summ = {"n": 0}
    ll = {"model": [], "elo": [], "serve": [], "market": []}
    track_start = dt.date.fromisoformat(TRACK_START)
    for (d, le, ls_, lsv, known, m) in feat:
        dd = dt.date.fromisoformat(d)
        if not known or dd >= track_start or (track_start - dd).days > 730:
            continue
        pm = combine(le, ls_, lsv)
        ll["model"].append(-math.log(max(pm, 1e-6)))
        ll["elo"].append(-math.log(max(1 / (1 + math.exp(-ls_)), 1e-6)))
        ll["serve"].append(-math.log(max(1 / (1 + math.exp(-lsv)), 1e-6)))
        if m.get("ow") and m.get("ol"):
            pmk = (1 / m["ow"]) / (1 / m["ow"] + 1 / m["ol"])
            ll["market"].append((-math.log(max(pm, 1e-6)), -math.log(max(pmk, 1e-6))))
    if ll["model"]:
        summ = {"n": len(ll["model"]), "model": float(np.mean(ll["model"])), "elo": float(np.mean(ll["elo"])),
                "serve": float(np.mean(ll["serve"])), "acc": float(np.mean([x < math.log(2) for x in ll["model"]]))}
        if len(ll["market"]) > 100:
            a = np.array(ll["market"])
            summ.update({"n_market": len(a), "model_on_market": float(a[:, 0].mean()), "market": float(a[:, 1].mean())})

    # Rückrechnungen für Modell-Check und Erfolgsquote (Sieger zuerst, p = Siegchance des Siegers laut Modell)
    for (d, le, ls_, lsv, known, m) in feat:
        if not known:
            continue
        rows.append([m["tour"], d, m["sf"], m["w"], m["l"], round(combine(le, ls_, lsv) * 100, 1),
                     m.get("ow"), m.get("ol"), m.get("xw"), m.get("xl")])

    # --- Kommende Matches (The Odds API) ---
    fx_path = data_dir / "tennis_fixtures.json"
    try:
        fx_cache = json.loads(fx_path.read_text(encoding="utf-8"))
    except Exception:
        fx_cache = {}
    key = os.environ.get("ODDS_API_KEY", "").strip()
    fx_note = ""
    if not key:
        fx_note = "kein_key"
    elif hours_since(fx_cache.get("t")) >= ODDS_EVERY_H:
        fx_cache = fetch_odds_api(key, now) or fx_cache
        fx_path.write_text(json.dumps(fx_cache, separators=(",", ":")), encoding="utf-8")
    tourneys = {}
    for m in matches:
        if m["date"] >= f"{now.year - 2}":
            tourneys[" ".join(norm(m["tn"]))] = m["sf"]
    fixtures = []
    for e in fx_cache.get("events", []):
        if e["start"] < now.isoformat():
            continue
        a = names.resolve(e["a"]) or e["a"]
        b = names.resolve(e["b"]) or e["b"]
        title = " ".join(norm(re.sub(r"^(ATP|WTA)\s+", "", e["title"])))
        sf = e.get("sf") or next((s for t, s in tourneys.items() if t and (t in title or title in t)), None)
        if not sf:
            m_ = difflib.get_close_matches(title, list(tourneys), n=1, cutoff=0.6)
            sf = tourneys[m_[0]] if m_ else "H"
        slam = any(x in title for x in ("australian open", "french open", "roland garros", "wimbledon", "us open"))
        bo = 5 if (e["tour"] == "atp" and slam) else 3
        day = (dt.date.fromisoformat(e["start"][:10]) - day0).days
        spw_a, rpw_a, avg_s, n_a = serve_rates(a, sf, day, e["tour"])
        spw_b, rpw_b, _, n_b = serve_rates(b, sf, day, e["tour"])
        pa = min(max(avg_s + (spw_a - avg_s) - (rpw_b - (1 - avg_s)), 0.4), 0.9)
        pb = min(max(avg_s + (spw_b - avg_s) - (rpw_a - (1 - avg_s)), 0.4), 0.9)
        le = logit(1 / (1 + 10 ** ((elo.get(b, 1500) - elo.get(a, 1500)) / 400)))
        ls_ = logit(1 / (1 + 10 ** ((elo_s.get((b, sf), 1500) - elo_s.get((a, sf), 1500)) / 400)))
        lsv = logit(p_match(pa, pb, bo))
        pm = combine(le, ls_, lsv)
        # Aufschlagwerte so verschieben, dass die Simulation die kombinierte Siegchance trifft
        lo, hi = -0.15, 0.15
        for _ in range(30):
            mid = (lo + hi) / 2
            if p_match(min(max(pa + mid, 0.3), 0.95), min(max(pb - mid, 0.3), 0.95), bo) < pm:
                lo = mid
            else:
                hi = mid
        sh = (lo + hi) / 2
        fixtures.append([e["tour"], e["start"], e["title"], sf, bo, a, b, e.get("oa"), e.get("ob"), e.get("xa"), e.get("xb"),
                         round(min(max(pa + sh, 0.3), 0.95), 4), round(min(max(pb - sh, 0.3), 0.95), 4),
                         round(1 / (1 + math.exp(-ls_)) * 100, 1), round(pm * 100, 1),
                         int(n_a > 0), int(n_b > 0)])

    # --- Ausgabe ---
    players = sorted({m["w"] for m in matches} | {m["l"] for m in matches})
    pid = {p: i for i, p in enumerate(players)}
    cut = f"{now.year - 2}-01-01"
    mrows = []
    for m in matches:
        if m["date"] < cut:
            continue
        mrows.append([m["tour"], m["date"], m["tn"], m["sf"], m["lv"], m["rd"], m["bo"], pid[m["w"]], pid[m["l"]],
                      m.get("wr"), m.get("lr"), m.get("score", ""), m.get("min"),
                      *(m["ws"] or [None] * 9), *(m["ls"] or [None] * 9),
                      m.get("ow"), m.get("ol"), m.get("xw"), m.get("xl")])
    ratings = {p: [round(elo[p]), round(elo_s.get((p, "H"), 1500)), round(elo_s.get((p, "C"), 1500)), round(elo_s.get((p, "G"), 1500)), cnt[p]]
               for p in elo if cnt.get(p, 0) >= 5}
    out = {"updated": now.isoformat(timespec="minutes"), "stats_until": stats_until, "results_until": results_until,
           "fixtures_note": fx_note, "fixtures_updated": fx_cache.get("t"), "track_start": TRACK_START,
           "players": players,
           "mcols": ["tour", "d", "tn", "sf", "lv", "rd", "bo", "w", "l", "wr", "lr", "score", "min",
                     *[f"w_{k}" for k in STATS], *[f"l_{k}" for k in STATS], "ow", "ol", "xw", "xl"],
           "matches": mrows,
           "fcols": ["tour", "start", "title", "sf", "bo", "a", "b", "oa", "ob", "xa", "xb", "pa", "pb", "pe", "pm", "sa", "sb"],
           "fixtures": fixtures, "ratings": ratings,
           "model": {"weights": [round(float(x), 4) for x in w_], "summary": summ,
                     "rcols": ["tour", "d", "sf", "w", "l", "p", "ow", "ol", "xw", "xl"], "rows": rows[-12000:]}}
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{len(mrows)} Matches, {len(fixtures)} kommende, Statistik bis {stats_until}, Ergebnisse bis {results_until}, Log-Loss {summ}")


def fetch_scores(key):
    """Ergebnisse der letzten 3 Tage (1 Abruf je laufendem Turnier)."""
    try:
        r = requests.get(f"{ODDS_API}/sports/", params={"apiKey": key}, timeout=30)
        sports = [s for s in r.json() if s.get("key", "").startswith(("tennis_atp", "tennis_wta")) and s.get("active")]
    except Exception as e:
        log(f"Odds API nicht erreichbar: {e}")
        return None
    rows = []
    for s in sports:
        try:
            r = requests.get(f"{ODDS_API}/sports/{s['key']}/scores/", params={"apiKey": key, "daysFrom": 3}, timeout=30)
            for ev in r.json():
                if not ev.get("completed") or not ev.get("scores"):
                    continue
                sc = {x["name"]: float(x.get("score") or 0) for x in ev["scores"]}
                a, b = ev.get("home_team"), ev.get("away_team")
                if a not in sc or b not in sc or sc[a] == sc[b]:
                    continue
                w, l = (a, b) if sc[a] > sc[b] else (b, a)
                rows.append({"tour": "atp" if s["key"].startswith("tennis_atp") else "wta", "date": ev.get("commence_time", "")[:10],
                             "tn": s.get("title", ""), "w": w, "l": l})
        except Exception as e:
            log(f"Ergebnisse {s.get('key')}: {e}")
    log(f"Gesammelte Ergebnisse: {len(rows)}")
    return rows


def fetch_odds_api(key, now):
    """Kommende ATP-/WTA-Matches mit Quoten (Pinnacle, wenn vorhanden, und beste Quote)."""
    try:
        r = requests.get(f"{ODDS_API}/sports/", params={"apiKey": key}, timeout=30)
        sports = [s for s in r.json() if s.get("key", "").startswith(("tennis_atp", "tennis_wta")) and s.get("active")]
    except Exception as e:
        log(f"Odds API nicht erreichbar: {e}")
        return None
    events = []
    for s in sports:
        try:
            r = requests.get(f"{ODDS_API}/sports/{s['key']}/odds/", timeout=30,
                             params={"apiKey": key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"})
            log(f"Odds API {s['key']}: noch {r.headers.get('x-requests-remaining')} Abrufe diesen Monat")
            for ev in r.json():
                a, b = ev.get("home_team"), ev.get("away_team")
                pin, best = {}, {}
                for bm in ev.get("bookmakers", []):
                    for mk in bm.get("markets", []):
                        if mk.get("key") != "h2h":
                            continue
                        for o in mk.get("outcomes", []):
                            best[o["name"]] = max(best.get(o["name"], 0), o["price"])
                            if bm.get("key") == "pinnacle":
                                pin[o["name"]] = o["price"]
                avg = pin or best
                events.append({"tour": "atp" if s["key"].startswith("tennis_atp") else "wta", "title": s.get("title", ""),
                               "start": ev.get("commence_time", ""), "a": a, "b": b,
                               "oa": avg.get(a), "ob": avg.get(b), "xa": best.get(a), "xb": best.get(b)})
        except Exception as e:
            log(f"Odds API {s.get('key')}: {e}")
    return {"t": now.isoformat(timespec="minutes"), "events": events}
