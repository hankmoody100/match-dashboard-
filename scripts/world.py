"""Was die Welt setzt: Prognosemärkte als unabhängige Quelle (Polymarket, öffentlich und ohne Key).

Auf Polymarket handeln Menschen weltweit mit echtem Geld auf Spielausgänge. Der Preis eines Anteils
(0 bis 1) entspricht der Wahrscheinlichkeit, die der Markt einem Ausgang gibt, das Handelsvolumen zeigt,
wie viel Geld dahinter steht. Wir lesen nur öffentliche Marktdaten (Gamma-API), keine Konten, keine Wetten.
"""
import datetime as dt
import json
import re
from pathlib import Path

import requests

GAMMA = "https://gamma-api.polymarket.com/events"
TAGS = ["sports", "soccer", "nba", "nhl", "tennis", "basketball", "hockey", "epl", "ucl", "bundesliga", "la-liga", "serie-a", "nfl"]


def log(*a):
    print("[Welt-Tipp]", *a, flush=True)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _list(x):
    if isinstance(x, list):
        return x
    try:
        return json.loads(x or "[]")
    except Exception:
        return []


def run(data_dir, now, hours_since):
    data_dir = Path(data_dir)
    path = data_dir / "world.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        old = {}
    if hours_since(old.get("t")) < 1:
        return
    seen, games = set(), []
    for tag in TAGS:
        for off in range(0, 1000, 200):
            try:
                r = requests.get(GAMMA, params={"tag_slug": tag, "active": "true", "closed": "false", "limit": 200, "offset": off},
                                 headers={"User-Agent": "tendenz-board/2.0"}, timeout=30)
                if r.status_code != 200:
                    log(f"{tag}: Status {r.status_code}")
                    break
                evs = r.json()
            except Exception as e:
                log(f"{tag}: {e}")
                break
            if not evs:
                break
            for ev in evs:
                eid = ev.get("id")
                if eid in seen:
                    continue
                seen.add(eid)
                title = ev.get("title") or ""
                m = re.match(r"^(.+?)\s+(?:vs\.?|v\.?|-|@)\s+(.+?)(?:\s*[-:(].*)?$", title, re.I)
                if not m:
                    continue
                a, b = m.group(1).strip(), m.group(2).strip()
                pa = pb = pd = None
                vol = 0.0
                for mk in ev.get("markets") or []:
                    if mk.get("closed"):
                        continue
                    outs, prices = _list(mk.get("outcomes")), [_num(x) for x in _list(mk.get("outcomePrices"))]
                    vol += _num(mk.get("volume")) or 0
                    q = (mk.get("question") or mk.get("groupItemTitle") or "").lower()
                    if len(outs) == 2 and None not in prices and set(o.lower() for o in outs) != {"yes", "no"}:
                        # Sieger-Markt mit zwei Namen
                        for o, p in zip(outs, prices):
                            if o.lower() in a.lower() or a.lower() in o.lower():
                                pa = p
                            elif o.lower() in b.lower() or b.lower() in o.lower():
                                pb = p
                    elif len(outs) == 2 and None not in prices:
                        yes = prices[[o.lower() for o in outs].index("yes")] if "yes" in [o.lower() for o in outs] else None
                        if yes is None:
                            continue
                        if "draw" in q or "tie" in q:
                            pd = yes
                        elif a.lower().split()[0] in q:
                            pa = yes if pa is None else pa
                        elif b.lower().split()[0] in q:
                            pb = yes if pb is None else pb
                if pa is None and pb is None:
                    continue
                games.append({"a": a, "b": b, "pa": pa, "pb": pb, "pd": pd, "vol": round(vol), "title": title,
                              "start": ev.get("startDate") or ev.get("endDate"), "url": f"https://polymarket.com/event/{ev.get('slug')}"})
    for g in games:
        g["src"] = "Polymarket"
    n_poly = len(games)
    games += kalshi()
    out = {"t": now.isoformat(timespec="minutes"), "games": games}
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"{n_poly} Spiele von Polymarket, {len(games) - n_poly} von Kalshi")
    return {"polymarket": n_poly, "kalshi": len(games) - n_poly}


KALSHI = "https://api.elections.kalshi.com/trade-api/v2/events"


def kalshi():
    """Zweiter Prognosemarkt (USA, reguliert). Öffentliche Marktdaten, ohne Konto."""
    out, cursor = [], None
    for _ in range(6):
        try:
            params = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = requests.get(KALSHI, params=params, headers={"User-Agent": "tendenz-board/2.0"}, timeout=30)
            if r.status_code != 200:
                log(f"Kalshi: Status {r.status_code}")
                break
            j = r.json()
        except Exception as e:
            log(f"Kalshi: {e}")
            break
        for ev in j.get("events", []):
            if (ev.get("category") or "").lower() != "sports":
                continue
            title = ev.get("title") or ""
            m = re.match(r"^(.+?)\s+(?:vs\.?|at|@)\s+(.+?)(?:\s*[-:(?].*)?$", title, re.I)
            if not m:
                continue
            a, b = m.group(1).strip(), m.group(2).strip()
            if re.search(r"\bat\b|@", title, re.I):
                a, b = b, a                                   # "Gast at Heim" -> Heim zuerst
            pa = pb = pd = None
            vol = 0
            for mk in ev.get("markets") or []:
                sub = (mk.get("yes_sub_title") or mk.get("subtitle") or "").lower()
                bid, ask, last = mk.get("yes_bid"), mk.get("yes_ask"), mk.get("last_price")
                p = ((bid + ask) / 2 if bid and ask else last) or None
                if p is None:
                    continue
                p = p / 100
                vol += mk.get("volume") or 0
                if "tie" in sub or "draw" in sub:
                    pd = p
                elif sub and (sub in a.lower() or a.lower().split()[-1] in sub):
                    pa = p
                elif sub and (sub in b.lower() or b.lower().split()[-1] in sub):
                    pb = p
            if pa is None and pb is None:
                continue
            out.append({"a": a, "b": b, "pa": pa, "pb": pb, "pd": pd, "vol": vol, "title": title, "start": ev.get("strike_date"),
                        "url": f"https://kalshi.com/events/{(ev.get('event_ticker') or '').lower()}", "src": "Kalshi"})
        cursor = j.get("cursor")
        if not cursor:
            break
    return out
