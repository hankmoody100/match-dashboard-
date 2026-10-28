"""Rating-Modell für das Tendenz-Board.

Idee: Jedes Team hat einen Angriffs- und einen Abwehrwert. Erwartete Tore =
Heim- bzw. Auswärtsniveau der Liga x Angriff x Abwehrschwäche des Gegners.
Die Werte werden aus allen Spielen geschätzt: gegnerbereinigt, zeitgewichtet,
mit einer Mischung aus Toren und Schüssen aufs Tor als Grundlage und mit
Dixon-Coles-Korrektur für knappe Ergebnisse (0:0, 1:0, 0:1, 1:1).

Halbwertszeit, Schussanteil und Korrektur wählt das Modell selbst, indem es sich
an Spielen vor TRACK_START misst (walk-forward: jede Prognose nur mit Daten davor).
"""
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

TRACK_START = "2026-10-26"
GRID_HL = [60, 120, 240, 480]          # Halbwertszeit in Tagen
GRID_ALPHA = [0.15, 0.35, 0.6, 1.0]          # Anteil echter Tore (Rest: xG)
GRID_RHO = [x / 100 for x in range(-20, 6, 2)]
PRIOR = 4.0                             # Gewicht des Ligaschnitts (in Spielen)
ITERS = 30
WINDOW = 1100                           # Tage Rückblick
MAXG = 11
TUNE_EVERY_H = 24


def log(*a):
    print(*a, flush=True)


# ---------- Wahrscheinlichkeiten ----------
_K = np.arange(MAXG)
_FACT = np.array([math.factorial(int(i)) for i in _K], dtype=float)
_I, _J = np.indices((MAXG, MAXG))


def score_matrix(lh, la, rho):
    lh = np.asarray(lh, float)[:, None]
    la = np.asarray(la, float)[:, None]
    ph = np.exp(-lh) * lh ** _K / _FACT
    pa = np.exp(-la) * la ** _K / _FACT
    M = ph[:, :, None] * pa[:, None, :]
    l1, l2 = lh[:, 0], la[:, 0]
    M[:, 0, 0] *= 1 - l1 * l2 * rho
    M[:, 0, 1] *= 1 + l1 * rho
    M[:, 1, 0] *= 1 + l2 * rho
    M[:, 1, 1] *= 1 - rho
    M = np.clip(M, 0, None)
    return M / M.sum(axis=(1, 2))[:, None, None]


def outcomes(lh, la, rho):
    M = score_matrix(lh, la, rho)
    return {
        "p1": (M * (_I > _J)).sum((1, 2)), "px": (M * (_I == _J)).sum((1, 2)), "p2": (M * (_I < _J)).sum((1, 2)),
        "o25": (M * ((_I + _J) > 2)).sum((1, 2)), "btts": (M * ((_I > 0) & (_J > 0))).sum((1, 2)),
    }


# ---------- Schätzung ----------
def fit(hi, ai, th, ta, w, n, a0, d0):
    sw = w.sum()
    Hh, Ha = (w * th).sum() / sw, (w * ta).sum() / sw
    g0 = (Hh + Ha) / 2
    A, D = np.ones(n), np.ones(n)
    for _ in range(ITERS):
        num = np.bincount(hi, w * th, n) + np.bincount(ai, w * ta, n)
        den = np.bincount(hi, w * Hh * D[ai], n) + np.bincount(ai, w * Ha * D[hi], n)
        A = (num + PRIOR * g0 * a0) / (den + PRIOR * g0)
        num = np.bincount(ai, w * th, n) + np.bincount(hi, w * ta, n)
        den = np.bincount(ai, w * Hh * A[hi], n) + np.bincount(hi, w * Ha * A[ai], n)
        D = (num + PRIOR * g0 * d0) / (den + PRIOR * g0)
        Hh = (w * th).sum() / (w * A[hi] * D[ai]).sum()
        Ha = (w * ta).sum() / (w * A[ai] * D[hi]).sum()
    return A, D, Hh, Ha


class League:
    """Vorbereitete Arrays einer Liga."""

    def __init__(self, df):
        df = df.sort_values("d")
        self.df = df
        self.teams = sorted(set(df.h) | set(df.a))
        self.idx = {t: i for i, t in enumerate(self.teams)}
        self.n = len(self.teams)
        self.hi = df.h.map(self.idx).to_numpy()
        self.ai = df.a.map(self.idx).to_numpy()
        self.day = (pd.to_datetime(df.d) - pd.Timestamp("2000-01-01")).dt.days.to_numpy()
        self.gh = df.hg.to_numpy(float)
        self.ga = df.ag.to_numpy(float)
        # xG je Team und Spiel: echte Understat-Werte oder Schuss-xG (siehe understat.py)
        self.xh = pd.to_numeric(df["gxh"], errors="coerce").to_numpy(float) if "gxh" in df else np.full(len(df), np.nan)
        self.xa = pd.to_numeric(df["gxa"], errors="coerce").to_numpy(float) if "gxa" in df else np.full(len(df), np.nan)

    def state_at(self, c, hl, alpha):
        """Teamwerte mit allen Spielen vor Tag c."""
        tr = (self.day < c) & (self.day >= c - WINDOW)
        if tr.sum() < 60:
            return None
        hi, ai, d = self.hi[tr], self.ai[tr], self.day[tr]
        gh, ga, xh, xa = self.gh[tr], self.ga[tr], self.xh[tr], self.xa[tr]
        ok = ~np.isnan(xh) & ~np.isnan(xa)
        if alpha < 1 and ok.sum() > 40:
            # Grundlage: Mischung aus echten Toren und xG (Chancenqualität)
            th = np.where(ok, alpha * gh + (1 - alpha) * np.nan_to_num(xh), gh)
            ta = np.where(ok, alpha * ga + (1 - alpha) * np.nan_to_num(xa), ga)
        else:
            th, ta = gh, ga
        w = 0.5 ** ((c - d) / hl)
        # Aufsteiger: zuletzt keine oder erst seit kurzem Spiele -> schwächerer Startwert
        last = np.full(self.n, -1e9)
        first = np.full(self.n, 1e9)
        np.maximum.at(last, hi, d); np.maximum.at(last, ai, d)
        np.minimum.at(first, hi, d); np.minimum.at(first, ai, d)
        span = d.max() - d.min()
        new = (last < c - 300) | ((first > c - 200) & (span > 300))
        a0 = np.where(new, 0.85, 1.0)
        d0 = np.where(new, 1.15, 1.0)
        A, D, Hh, Ha = fit(hi, ai, th, ta, w, self.n, a0, d0)
        return A, D, Hh, Ha

    def walk(self, hl, alpha, start_day, end_day, step=7):
        """Prognosen für alle Spiele ab start_day, Woche für Woche neu geschätzt."""
        out_i, out_h, out_a = [], [], []
        if not len(self.day):
            return np.array([], int), np.array([]), np.array([])
        c = max(start_day, self.day.min() + 250)
        c -= (c + 5) % 7                       # auf Montag
        last = min(end_day, self.day.max() + 1)
        while c < last:
            test = np.where((self.day >= c) & (self.day < c + step))[0]
            if len(test):
                st = self.state_at(c, hl, alpha)
                if st:
                    A, D, Hh, Ha = st
                    out_i.append(test)
                    out_h.append(Hh * A[self.hi[test]] * D[self.ai[test]])
                    out_a.append(Ha * A[self.ai[test]] * D[self.hi[test]])
            c += step
        if not out_i:
            return np.array([], int), np.array([]), np.array([])
        return np.concatenate(out_i), np.concatenate(out_h), np.concatenate(out_a)


def logloss_1x2(P, y):
    p = np.stack([P["p1"], P["px"], P["p2"]], 1)[np.arange(len(y)), y]
    return float(-np.log(np.clip(p, 1e-9, 1)).mean())


def market_probs(df, cols):
    o = df[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    inv = 1 / o
    s = inv.sum(1, keepdims=True)
    return inv / s


def run(matches, cols, fixtures, fcols, data_dir, meta, now):
    data_dir = Path(data_dir)
    df = pd.DataFrame(matches, columns=cols)
    df = df[df.hg.notna() & df.ag.notna()].copy()
    if df.empty:
        return
    df["day"] = (pd.to_datetime(df.d) - pd.Timestamp("2000-01-01")).dt.days
    y = np.where(df.hg > df.ag, 0, np.where(df.hg == df.ag, 1, 2))
    df["y"] = y
    leagues = {l: League(g) for l, g in df.groupby("l")}
    track_day = (pd.Timestamp(TRACK_START) - pd.Timestamp("2000-01-01")).days
    today = (pd.Timestamp(now.date()) - pd.Timestamp("2000-01-01")).days
    out_path = data_dir / "model.json"
    old = {}
    try:
        old = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # 1) Einstellungen wählen (höchstens einmal am Tag)
    params = old.get("params")
    age = 1e9
    if params and old.get("tuned"):
        age = (now - dt.datetime.fromisoformat(old["tuned"])).total_seconds() / 3600
    if not params or age >= TUNE_EVERY_H or old.get("version") != 2:
        log("Modell: suche beste Einstellungen …")
        best = None
        tune_start = track_day - 730
        for hl in GRID_HL:
            for alpha in GRID_ALPHA:
                lhs, las, ys = [], [], []
                for L in leagues.values():
                    i, lh, la = L.walk(hl, alpha, tune_start, track_day)
                    if len(i):
                        lhs.append(lh); las.append(la); ys.append(L.df.y.to_numpy()[i] if "y" in L.df else None)
                if not lhs:
                    continue
                lh, la = np.concatenate(lhs), np.concatenate(las)
                yy = np.concatenate(ys)
                ll = logloss_1x2(outcomes(lh, la, -0.06), yy)
                log(f"  Halbwertszeit {hl} Tage, Toranteil {alpha}: Log-Loss {ll:.4f} ({len(yy)} Spiele)")
                if best is None or ll < best[0]:
                    best = (ll, hl, alpha, lh, la, yy)
        if best is None:
            log("Modell: zu wenige Daten.")
            return
        _, hl, alpha, lh, la, yy = best
        rho = min(GRID_RHO, key=lambda r: logloss_1x2(outcomes(lh, la, r), yy))
        params = {"hl": hl, "alpha": alpha, "rho": rho}
        tuned = now.isoformat(timespec="minutes")
        log(f"Modell: gewählt {params}")
    else:
        tuned = old["tuned"]

    hl, alpha, rho = params["hl"], params["alpha"], params["rho"]

    # 2) Walk-forward-Prognosen für alle Spiele (Grundlage für Modell-Check, Top 10, Erfolgsquote)
    rows, idx_all, lh_all, la_all = [], [], [], []
    for l, L in leagues.items():
        i, lh, la = L.walk(hl, alpha, 0, today + 1)
        if not len(i):
            continue
        P = outcomes(lh, la, rho)
        sub = L.df.iloc[i]
        for k, r in enumerate(sub.itertuples(index=False)):
            rows.append([r.l, r.d, r.h, r.a, round(float(lh[k]), 3), round(float(la[k]), 3),
                         round(float(P["p1"][k]) * 100, 1), round(float(P["px"][k]) * 100, 1),
                         round(float(P["p2"][k]) * 100, 1), round(float(P["o25"][k]) * 100, 1)])
        idx_all.append(sub.index.to_numpy()); lh_all.append(lh); la_all.append(la)

    # 3) Vergleich mit dem Markt (Spiele der letzten zwei Jahre vor TRACK_START)
    summary = {}
    if idx_all:
        ix = np.concatenate(idx_all)
        lh, la = np.concatenate(lh_all), np.concatenate(la_all)
        ev = df.loc[ix]
        sel = ((ev.day >= track_day - 730) & (ev.day < track_day)).to_numpy()
        P = outcomes(lh[sel], la[sel], rho)
        e = ev[sel]
        yy = e.y.to_numpy()
        res = {"n": int(sel.sum()), "model": logloss_1x2(P, yy)}
        for name, c in [("open", ["ph", "pd", "pa"]), ("close", ["oh", "od", "oa"])]:
            mp = market_probs(e, c)
            ok = ~np.isnan(mp).any(1)
            if ok.sum() > 100:
                res[name] = float(-np.log(np.clip(mp[ok][np.arange(ok.sum()), yy[ok]], 1e-9, 1)).mean())
                res["model_on_" + name] = logloss_1x2({k: v[ok] for k, v in P.items()}, yy[ok])
                res["n_" + name] = int(ok.sum())
        summary = res

    # 4) Aktuelle Prognosen für kommende Spiele
    fx = {}
    ratings = {}
    states = {}
    for r in fixtures:
        rec = dict(zip(fcols, r))
        L = leagues.get(rec["l"])
        if L is None:
            continue
        if rec["l"] not in states:
            states[rec["l"]] = L.state_at(today + 1, hl, alpha)
            st = states[rec["l"]]
            if st:
                A, D, Hh, Ha = st
                ratings[rec["l"]] = {t: [round(float(A[i]), 3), round(float(D[i]), 3)] for t, i in L.idx.items()}
        st = states[rec["l"]]
        if not st:
            continue
        A, D, Hh, Ha = st
        hi, ai = L.idx.get(rec["h"]), L.idx.get(rec["a"])
        ah, dh = (A[hi], D[hi]) if hi is not None else (0.85, 1.15)
        aa, da = (A[ai], D[ai]) if ai is not None else (0.85, 1.15)
        fx[f'{rec["l"]}|{rec["d"]}|{rec["h"]}|{rec["a"]}'] = [round(float(Hh * ah * da), 3), round(float(Ha * aa * dh), 3)]

    out = {"version": 2, "tuned": tuned, "updated": now.isoformat(timespec="minutes"), "params": params,
           "summary": summary, "track_start": TRACK_START, "fixtures": fx, "ratings": ratings,
           "rcols": ["l", "d", "h", "a", "lh", "la", "p1", "px", "p2", "o25"], "rows": rows}
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")
    log(f"Modell: {len(rows)} Rückrechnungen, {len(fx)} aktuelle Prognosen, Log-Loss {summary}")
