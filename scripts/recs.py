"""Offizielle Tages-Empfehlungen festhalten und nachträglich abrechnen ("Rückblick").

Ablauf je Lauf:
1. Für alle Spiele der nächsten 36 Stunden (alle Sportarten) die Tipps aus den Modellen rechnen.
2. Auswahl wie im Panel "Best of the Day" (Standard): erhöhte Wahrscheinlichkeit gegenüber Quote bzw. dem Üblichen,
   mindestens 3 Prozentpunkte, Quote 1,30 bis 3,00, je Spiel ein Tipp, je Tag die 20 stärksten.
3. Empfehlungen werden bis zum Anpfiff aktualisiert und dann eingefroren. Fällt ein Tipp vorher aus der Liste,
   wird er als "zurückgezogen" markiert und nicht gewertet.
4. Nach Spielende wird mit dem Ergebnis abgerechnet. Aufbewahrung: 21 Tage.
"""
import datetime as dt
import json
import math
import re
import unicodedata
from pathlib import Path

WINDOW_H = 36
QMIN, QMAX, MIN_SCORE, PER_DAY = 1.30, 3.00, 3.0, 20
KEEP_DAYS = 21
DEF_BASE = {"1": 45, "X": 26, "2": 29, "1X": 71, "X2": 55, "12": 74, "O15": 75, "O25": 52, "U25": 48, "U35": 70, "BY": 52, "BN": 48,
            "WH": 58, "WA": 42, "WB": 45, "H60": 45, "A60": 33, "O55": 50, "BT": 70}


CAT = {"1": "Sieg", "2": "Sieg", "X": "Remis", "WH": "Sieg", "WA": "Sieg", "WB": "Sieg", "1X": "Doppelte Chance", "X2": "Doppelte Chance", "12": "Doppelte Chance",
       "O15": "Über/Unter", "O25": "Über/Unter", "U25": "Über/Unter", "U35": "Über/Unter", "O55": "Über/Unter", "BY": "Beide treffen", "BN": "Beide treffen",
       "BT": "Beide treffen", "H60": "Sieg nach 60 Min.", "A60": "Sieg nach 60 Min."}
LEARN_DAYS, LEARN_MIN_N, LEARN_GAP, PENALTY, BONUS = 14, 12, 10.0, 3.0, 1.5


def log(*a):
    print("[Rückblick]", *a, flush=True)


def load(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return d


def toks(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if len(t) > 2 and t not in ("fc", "sc", "afc", "club", "real", "united", "city", "the")]


def same_team(a, b):
    ta, tb = set(toks(a)), set(toks(b))
    return bool(ta & tb) if ta and tb else a.strip().lower() == b.strip().lower()


def probs(lh, la, rho=0.0):
    N = 11
    pa = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(N)]
    pb = [math.exp(-la) * la ** k / math.factorial(k) for k in range(N)]
    s = p1 = px = p2 = o15 = o25 = o35 = bt = 0.0
    for i in range(N):
        for j in range(N):
            p = pa[i] * pb[j]
            if rho:
                if i == 0 and j == 0: p *= 1 - lh * la * rho
                elif i == 0 and j == 1: p *= 1 + lh * rho
                elif i == 1 and j == 0: p *= 1 + la * rho
                elif i == 1 and j == 1: p *= 1 - rho
            p = max(p, 0); s += p
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
            t = i + j
            o15 += p * (t > 1); o25 += p * (t > 2); o35 += p * (t > 3); bt += p * (i > 0 and j > 0)
    q = lambda x: x / s * 100
    return {"p1": q(p1), "px": q(px), "p2": q(p2), "o15": q(o15), "o25": q(o25), "o35": q(o35), "btts": q(bt)}


def football_tips(P, h, a):
    return [("1", f"1 · {h} gewinnt", P["p1"]), ("X", "X · Remis", P["px"]), ("2", f"2 · {a} gewinnt", P["p2"]),
            ("1X", f"1X · {h} verliert nicht", P["p1"] + P["px"]), ("X2", f"X2 · {a} verliert nicht", P["px"] + P["p2"]), ("12", "12 · kein Remis", P["p1"] + P["p2"]),
            ("O15", "Über 1,5 Tore", P["o15"]), ("O25", "Über 2,5 Tore", P["o25"]), ("U25", "Unter 2,5 Tore", 100 - P["o25"]), ("U35", "Unter 3,5 Tore", 100 - P["o35"]),
            ("BY", "Beide treffen: Ja", P["btts"]), ("BN", "Beide treffen: Nein", 100 - P["btts"])]


def won(sport, k, hs, as_, winner=None, home=None):
    if sport == "Tennis":
        return winner is not None and same_team(winner, home) == (k == "WA")
    t = hs + as_
    return {"1": hs > as_, "X": hs == as_, "2": hs < as_, "1X": hs >= as_, "X2": hs <= as_, "12": hs != as_, "O15": t > 1.5, "O25": t > 2.5,
            "U25": t < 2.5, "U35": t < 3.5, "BY": hs > 0 and as_ > 0, "BN": not (hs > 0 and as_ > 0), "WH": hs > as_, "WA": hs < as_,
            "H60": hs > as_, "A60": hs < as_, "O55": t > 5.5, "BT": hs > 0 and as_ > 0}.get(k)


def world_for(W, h, a):
    for g in W:
        if same_team(g["a"], h) and same_team(g["b"], a):
            return g, False
        if same_team(g["a"], a) and same_team(g["b"], h):
            return g, True
    return None, None


def candidates(D, now):
    """Alle Tipps der nächsten 36 Stunden aus allen Sportarten."""
    lim = (now + dt.timedelta(hours=WINDOW_H)).isoformat()
    nowi = now.isoformat()
    C = []
    rho = (D["model"].get("params") or {}).get("rho", -0.06)

    def add(sport, comp, start, h, a, q, tips, odds=None):
        if not (nowi <= start <= lim):
            return
        for k, lab, p in tips:
            if p is None or not (1 < p < 99):
                continue
            o = (odds or {}).get(k)
            C.append({"sport": sport, "comp": comp, "start": start, "h": h, "a": a, "q": q, "k": k, "lab": lab, "p": round(p, 1),
                      "odds": o[0] if o else None, "src": o[1] if o else None})

    # Fußball, tiefes Modell
    fb = D["football"]
    fc = fb.get("fcols", [])
    mfx = D["model"].get("fixtures", {})
    for r in fb.get("fixtures", []):
        f = dict(zip(fc, r))
        lam = mfx.get(f"{f['l']}|{f['d']}|{f['h']}|{f['a']}")
        if not lam:
            continue
        start = f"{f['d']}T{(f.get('t') or '15:00')}:00+00:00"
        odds = {k: (f.get(c), "bet365") for k, c in (("1", "bh"), ("X", "bd"), ("2", "ba"), ("O25", "bo"), ("U25", "bu")) if f.get(c)}
        add("Fußball", f["l"], start, f["h"], f["a"], 3, football_tips(probs(lam[0], lam[1], rho), f["h"], f["a"]), odds)
    # Fußball weltweit (inkl. Europacup)
    ag = D["allgames"]
    ac = ag.get("cols", [])
    for r in ag.get("games", []):
        g = dict(zip(ac, r))
        if g.get("q", 0) < 1 or g.get("q") == 3 or g.get("youth") or g.get("nat"):
            continue
        add("Fußball", g.get("comp", ""), g["start"], g["h"], g["a"], g["q"], football_tips(probs(g["lh"], g["la"], rho), g["h"], g["a"]))
    # Länderspiele
    for f in D["intl"].get("fixtures", []):
        add("Länderspiel", f.get("comp", ""), f["start"], f["h"], f["a"], 2, football_tips(probs(f["lh"], f["la"], rho), f["h"], f["a"]))
    # Basketball, Eishockey, Handball
    for sp, S in (D["sports"].get("sports") or {}).items():
        for g in S.get("games", []):
            if g.get("q", 0) < 1:
                continue
            tips = [("WH", f"{g['h']} gewinnt", g["p"]), ("WA", f"{g['a']} gewinnt", 100 - g["p"])]
            if sp == "ice-hockey":
                P = probs(g["eth"], g["eta"])
                tips += [("H60", "1 nach 60 Min.", P["p1"]), ("A60", "2 nach 60 Min.", P["p2"]), ("BT", "Beide treffen", P["btts"])]
            add(S.get("name", sp), g.get("comp", ""), g["start"], g["h"], g["a"], g["q"], tips)
    # Tennis
    tn = D["tennis"]
    tc = tn.get("fcols", [])
    for r in tn.get("fixtures", []):
        f = dict(zip(tc, r))
        if "utr" in (str(f.get("title")) + str(f.get("lv"))).lower() or (f.get("q") is not None and f["q"] < 1):
            continue
        oa, ob = f.get("xa") or f.get("oa"), f.get("xb") or f.get("ob")
        odds = {k: (v, "Markt") for k, v in (("WA", oa), ("WB", ob)) if v}
        add("Tennis", f.get("title", ""), f["start"], f["a"], f["b"], f.get("q", 3) or 0,
            [("WA", f"{f['a']} gewinnt", f["pm"]), ("WB", f"{f['b']} gewinnt", 100 - f["pm"])], odds)
    return C


def select(C, W, adj=None):
    base = {}
    for c in C:
        base.setdefault((c["sport"], c["k"]), []).append(c["p"])
    for c in C:
        arr = base[(c["sport"], c["k"])]
        b = sum(arr) / len(arr) if len(arr) >= 6 else DEF_BASE.get(c["k"], 50)
        c["base"] = round(b, 1)
        quote = c["odds"] or 100 / c["p"]
        c["quote"] = round(quote, 2)
        edge = c["p"] - 100 / c["odds"] if c["odds"] else None
        c["score"] = edge if edge is not None else (c["p"] - b) * 0.8
        why = []
        if edge is not None:
            why.append(f"Simulation {c['p']:.0f} %, die Quote {c['odds']:.2f} ({c['src']}) preist nur {100 / c['odds']:.0f} % ein")
        else:
            why.append(f"Simulation {c['p']:.0f} %, üblich sind bei diesem Tipp {b:.0f} %")
        g, flip = world_for(W, c["h"], c["a"])
        if g and c["k"] in ("1", "2", "X", "1X", "X2", "12", "WH", "WA", "WB"):
            ph, pa, pd = (g["pb"], g["pa"], g["pd"]) if flip else (g["pa"], g["pb"], g["pd"])
            tot = (ph or 0) + (pa or 0) + (pd or 0) or 1
            ph, pa, pd = [(x or 0) / tot * 100 for x in (ph, pa, pd)]
            wp = {"1": ph, "WH": ph, "WA": pa, "2": pa, "WB": pa, "X": pd, "1X": ph + pd, "X2": pa + pd, "12": ph + pa}[c["k"]]
            if wp >= c["p"] - 5:
                c["score"] += 2
                why.append(f"Weltmarkt ({g.get('src', 'Polymarket')}) bestätigt: {wp:.0f} %")
            else:
                why.append(f"Weltmarkt ({g.get('src', 'Polymarket')}) skeptischer: {wp:.0f} %")
        why.append({3: "Datenlage ●●● (tiefes Modell)", 2: "Datenlage ●●○ (Elo)", 1: "Datenlage ●○○ (wenig Daten)"}.get(c["q"], "Datenlage ○○○"))
        a = (adj or {}).get(f"{c['sport']}|{CAT.get(c['k'], c['k'])}")
        if a:
            c["score"] += a["delta"]
            why.append(f"Tages-Check: {CAT.get(c['k'], c['k'])} zuletzt {a['rate']:.0f} % statt {a['exp']:.0f} % ({'+' if a['delta'] > 0 else ''}{a['delta']:.1f} Punkte Reihung)")
        c["why"] = why
    best = {}
    for c in C:
        if c["quote"] < QMIN or c["quote"] > QMAX or c["score"] < MIN_SCORE:
            continue
        key = f"{c['sport']}|{c['start'][:10]}|{'/'.join(sorted(toks(c['h'])[-1:] + toks(c['a'])[-1:]))}"
        if key not in best or c["score"] > best[key]["score"]:
            best[key] = {**c, "key": key}
    by_day = {}
    for c in best.values():
        by_day.setdefault(c["start"][:10], []).append(c)
    out = []
    for d, arr in by_day.items():
        out += sorted(arr, key=lambda x: -x["score"])[:PER_DAY]
    return out


def results_index(D):
    """Ergebnisse aus allen Quellen: (Sportart, Datum, Heim, Gast, Tore Heim, Tore Gast, Sieger)."""
    R = []
    fb = D["football"]
    cols = fb.get("cols", [])
    for r in fb.get("matches", []):
        m = dict(zip(cols, r))
        if m.get("hg") is not None and m["d"] >= D["since"]:
            R.append(("Fußball", m["d"], m["h"], m["a"], m["hg"], m["ag"], None))
    for r in D["sf_results"].get("rows", []):
        if r[1] >= D["since"]:
            R.append(("Länderspiel" if (len(r) > 10 and r[10]) else "Fußball", r[1], r[4], r[5], r[6], r[7], None))
    for sp, name in (("basketball", "Basketball"), ("ice-hockey", "Eishockey"), ("handball", "Handball")):
        for r in D["ms_results"].get(sp, {}).get("rows", []):
            if r[1] >= D["since"]:
                R.append((name, r[1], r[4], r[5], r[6], r[7], None))
    for r in D["tennis_live"].get("rows", []):
        if r.get("date", "") >= D["since"]:
            R.append(("Tennis", r["date"], r["w"], r["l"], None, None, r["w"]))
    return R


def settle(item, R):
    d0 = dt.date.fromisoformat(item["start"][:10])
    sports = {"Fußball", "Länderspiel"} if item["sport"] in ("Fußball", "Länderspiel") else {item["sport"]}
    for sp, d, h, a, hs, as_, w in R:
        if sp not in sports:
            continue
        try:
            if abs((dt.date.fromisoformat(d) - d0).days) > 1:
                continue
        except ValueError:
            continue
        if item["sport"] == "Tennis":
            if {frozenset(toks(h)), frozenset(toks(a))} and ((same_team(h, item["h"]) and same_team(a, item["a"])) or (same_team(h, item["a"]) and same_team(a, item["h"]))):
                return {"res": f"Sieger {w}", "won": won("Tennis", item["k"], None, None, w, item["h"])}
            continue
        if same_team(h, item["h"]) and same_team(a, item["a"]):
            return {"res": f"{hs}:{as_}", "won": won(item["sport"], item["k"], hs, as_)}
    return None


def learn(items, now):
    """Anpassungen aus den letzten 14 Tagen: Tipp-Arten, die klar unter/über Erwartung liegen."""
    since = (now.date() - dt.timedelta(days=LEARN_DAYS)).isoformat()
    grp = {}
    for x in items:
        if x.get("won") is None or x["status"] != "aktiv" or x["start"][:10] < since:
            continue
        grp.setdefault(f"{x['sport']}|{CAT.get(x['k'], x['k'])}", []).append(x)
    adj = {}
    for k, arr in grp.items():
        if len(arr) < LEARN_MIN_N:
            continue
        rate = sum(1 for x in arr if x["won"]) / len(arr) * 100
        exp = sum(x["p"] for x in arr) / len(arr)
        sd = math.sqrt(sum(x["p"] / 100 * (1 - x["p"] / 100) for x in arr)) / len(arr) * 100
        gap = max(LEARN_GAP, 2 * sd)                   # nur reagieren, wenn die Abweichung deutlich über dem Zufall liegt
        if rate - exp <= -gap:
            adj[k] = {"delta": -PENALTY, "rate": rate, "exp": exp, "n": len(arr), "gap": round(gap, 1)}
        elif rate - exp >= gap:
            adj[k] = {"delta": BONUS, "rate": rate, "exp": exp, "n": len(arr), "gap": round(gap, 1)}
    return adj


def review_day(day, arr, adj):
    """Kritische Auswertung eines abgeschlossenen Tages."""
    st = [x for x in arr if x["status"] == "aktiv" and x.get("won") is not None]
    if not st:
        return None
    n, hits = len(st), sum(1 for x in st if x["won"])
    exp = sum(x["p"] for x in st) / n
    rate = hits / n * 100
    sd = math.sqrt(sum(x["p"] / 100 * (1 - x["p"] / 100) for x in st)) / n * 100       # Zufallsschwankung der Trefferquote
    diff = rate - exp
    verdict = ("im Zufallsbereich" if abs(diff) <= 1.5 * sd else "besser als erwartet" if diff > 0 else "schlechter als erwartet")

    def part(key):
        g = {}
        for x in st:
            g.setdefault(key(x), []).append(x)
        return [{"name": k, "n": len(v), "hits": sum(1 for x in v if x["won"]), "exp": round(sum(x["p"] for x in v) / len(v), 1)} for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))]

    wm = lambda x: "Weltmarkt bestätigt" if any("bestätigt" in w for w in x.get("why", [])) else "Weltmarkt skeptisch" if any("skeptischer" in w for w in x.get("why", [])) else "kein Weltmarkt"
    misses = sorted([x for x in st if not x["won"]], key=lambda x: -x["p"])[:3]
    notes = []
    if verdict == "im Zufallsbereich":
        notes.append(f"Abweichung {diff:+.0f} Punkte liegt im Zufallsbereich (±{1.5 * sd:.0f}). Kein Grund, etwas zu ändern.")
    elif verdict == "schlechter als erwartet":
        notes.append(f"{diff:+.0f} Punkte unter Erwartung, mehr als Zufall erklärt (±{1.5 * sd:.0f}). Die Tipp-Arten unten zeigen, wo es lag.")
    else:
        notes.append(f"{diff:+.0f} Punkte über Erwartung. Ein guter Tag ist noch kein Beweis, entscheidend ist die Reihe über Wochen.")
    for p_ in part(wm):
        if p_["name"] != "kein Weltmarkt" and p_["n"] >= 3:
            notes.append(f"{p_['name']}: {p_['hits']} von {p_['n']} richtig (erwartet {p_['exp']:.0f} %).")
    for m in misses:
        if m["p"] >= 65:
            skept = any("skeptischer" in w for w in m.get("why", []))
            notes.append(f"Fehlgriff mit {m['p']:.0f} %: {m['h']} – {m['a']}, {m['lab']}, Ergebnis {m.get('res', '?')}."
                         + (" Der Weltmarkt war skeptischer, das Signal hätte gewarnt." if skept else ""))
    return {"day": day, "n": n, "hits": hits, "rate": round(rate, 1), "exp": round(exp, 1), "band": round(1.5 * sd, 1), "verdict": verdict,
            "by_sport": part(lambda x: x["sport"]), "by_cat": part(lambda x: CAT.get(x["k"], x["k"])), "by_world": part(wm),
            "misses": [{"h": m["h"], "a": m["a"], "lab": m["lab"], "p": m["p"], "res": m.get("res")} for m in misses], "notes": notes,
            "adj": [{"key": k, **v} for k, v in adj.items()]}


def run(data_dir, now):
    data_dir = Path(data_dir)
    since = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    D = {"football": load(data_dir / "football.json", {}), "model": load(data_dir / "model.json", {}), "allgames": load(data_dir / "allgames.json", {}),
         "intl": load(data_dir / "international.json", {}), "sports": load(data_dir / "sports.json", {}), "tennis": load(data_dir / "tennis.json", {}),
         "sf_results": load(data_dir / "sf_results.json", {"rows": []}), "tennis_live": load(data_dir / "tennis_live_results.json", {"rows": []}),
         "ms_results": {sp: load(data_dir / f"ms_results_{sp}.json", {"rows": []}) for sp in ("basketball", "ice-hockey", "handball")}, "since": since}
    W = load(data_dir / "world.json", {}).get("games", [])
    hist = load(data_dir / "recs.json", {"items": []})
    items = {x["key"]: x for x in hist.get("items", []) if x.get("start", "")[:10] >= since}
    nowi = now.isoformat()
    adj = learn(list(items.values()), now)
    current = {c["key"]: c for c in select(candidates(D, now), W, adj)}
    stamp = now.isoformat(timespec="minutes")
    for k, c in current.items():
        old = items.get(k)
        if old and old["start"] <= nowi:
            continue                                            # nach Anpfiff eingefroren
        fields = {x: c[x] for x in ("sport", "comp", "start", "h", "a", "q", "k", "lab", "p", "quote", "odds", "src", "base", "why")}
        fields["score"] = round(c["score"], 1)
        if old:
            if old.get("k") != c["k"]:
                fields["changed"] = old.get("changed", 0) + 1
            old.update(fields); old["status"] = "aktiv"; old["upd"] = stamp
        else:
            items[k] = {"key": k, **fields, "status": "aktiv", "made": stamp, "upd": stamp}
    for k, x in items.items():
        if x["status"] == "aktiv" and x["start"] > nowi and k not in current:
            x["status"] = "zurückgezogen"
        elif x["status"] == "zurückgezogen" and k in current:
            x["status"] = "aktiv"
    R = results_index(D)
    done = 0
    for x in items.values():
        if x["status"] == "aktiv" and x["start"] <= nowi and "won" not in x:
            s = settle(x, R)
            if s and s["won"] is not None:
                x.update(s); x["settled"] = stamp; done += 1
    out = sorted(items.values(), key=lambda x: x["start"])
    # Tages-Check: jeder abgeschlossene Tag wird einmal kritisch ausgewertet (neu, sobald alle Ergebnisse da sind)
    reviews = {r["day"]: r for r in hist.get("reviews", []) if r["day"] >= since}
    by_day = {}
    for x in out:
        by_day.setdefault(x["start"][:10], []).append(x)
    today = now.date().isoformat()
    for d, arr in by_day.items():
        if d >= today:
            continue
        pending = any(x["status"] == "aktiv" and x.get("won") is None for x in arr)
        old_enough = (now - dt.datetime.fromisoformat(d + "T00:00:00+00:00")).total_seconds() > 60 * 3600
        if d in reviews and reviews[d].get("final"):
            continue
        if pending and not old_enough:
            continue
        r = review_day(d, arr, adj)
        if r:
            r["final"] = not pending
            r["made"] = stamp
            reviews[d] = r
    Path(data_dir / "recs.json").write_text(json.dumps({"t": stamp, "rules": {"qmin": QMIN, "qmax": QMAX, "min_score": MIN_SCORE, "per_day": PER_DAY},
                                                         "adjust": [{"key": k, **v} for k, v in adj.items()],
                                                         "reviews": sorted(reviews.values(), key=lambda r: r["day"], reverse=True),
                                                         "items": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    act = sum(1 for x in out if x["status"] == "aktiv" and x["start"] > nowi)
    st = [x for x in out if "won" in x]
    log(f"{act} offene Empfehlungen, {len(st)} abgerechnet ({sum(1 for x in st if x['won'])} richtig), {done} neu abgerechnet")
