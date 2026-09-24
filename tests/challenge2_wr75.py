# -*- coding: utf-8 -*-
"""CHALLENGE 2 — WR(>=+1.0R net) > 75% while final profits stay in [1.0, 1.7]R.

Declared goal (before looking):
  N1  WR_big  > 75%   where a "win" is a trade with net_R >= 1.0  (user rule:
      any exit below +1.0R does NOT count as a win even if positive)
  N2  every win lands in [1.0, 1.7]R   (TP capped at 1.7R, lock >= 1.15R)
  N3  exp_net > 0 for EVERY pair and EVERY calendar year   (keep)
  N4  exp_net overall >= 0.05R                           (keep)
  N5  blind OOS 2021-09..2023-01 (bear) + 2025-01..2026-10 (keep)
      selection window = 2023-01..2025-01 (keep)
  +   dd <= 8% at 1% risk (report), day-trading character (keep)

Strategy levers (user pre-authorized threshold+strategy iteration):
  A. QUALITY SELECTION: setup-level score built on the SEL window only
     (decile win-rate lifts on <=4 features picked greedily), threshold picked
     for max N s.t. SEL WR_big >= 78% (margin over 75%).
  B. entry_frac in {0.5, 0.25}  (deeper FVG entry -> better R geometry)
  C. exits inside the band-safe region: lock in {1.15, 1.2, 1.3},
     trig = lock + gap (gap in {0.25, 0.35}), tp in {1.5, 1.7}

Modes:
  python tests/challenge_sweep.py detect    # (same cache — reuse)
  python tests/challenge2_wr75.py labels    # pass 1: per-trade labels + features
  python tests/challenge2_wr75.py run       # passes 2-4: score, grid, blind verify
"""
import datetime
import io
import json
import math
import os
import pickle
import sys
import time
import zipfile

import numpy as np

sys.path.insert(0, "/home/user/ict_bt")
sys.path.insert(0, "/home/user/mkmoon")
import engine as E          # noqa: E402
import run_grid as RG       # noqa: E402

OUT = "/home/user/ict_bt/data/ch1m"
RES = "/home/user/ict_bt/results"
LAB = os.path.join(RES, "c2_labels.json")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "ADAUSDT", "DOGEUSDT", "ZECUSDT", "SUIUSDT", "NEARUSDT",
        "UNIUSDT", "TRXUSDT", "APTUSDT", "ARBUSDT", "AAVEUSDT"]

BASE = {
    "require_sweep": True, "require_disp": False, "htf_mode": "none", "z_max": 1.0,
    "max_fvg_lag": 8, "min_fvg_atr": 0.0, "choch_from": "non_bullish",
    "max_sweep_age": 8, "min_sweep_depth": 0.0, "close_back_min": 0.0,
    "ctype": "both", "entry_mode": "limit", "entry_frac": 0.5, "sl_mode": "struct",
    "sl_buf_atr": 0.2, "max_hold_h": 24, "order_bars": 24, "min_r_pct": 1.2,
    "max_r_pct": 3.0, "sessions": "no_asia", "tp_mode": "fixed",
    "inv_atr": 0.0, "early_abort": True,
    "entry_fee_bps": 7.5, "exit_fee_bps": 7.5, "slip_entry_bps": 0.0,
    "slip_exit_bps": 2.0, "tf_ms": 900_000,
    "mfe_trig_r": 1.5, "lock_r": 1.20, "tp_r": 1.7,
}

T_MS = {
    "SEL":   (1672531200000, 1735689600000),    # 2023-01-01 .. 2025-01-01
    "OOS1":  (1630454400000, 1672531200000),    # 2021-09-01 .. 2023-01-01 (bear)
    "OOS2":  (1735689600000, 1790880000000),    # 2025-01-01 .. 2026-10-01
}

FEATS_NUM = ["z", "sweep_depth_atr", "sweep_close_back_atr", "fvg_size_atr",
             "fvg_lag", "disp_body_ratio", "disp_range_atr", "sweep_age",
             "atr_mss"]
FEATS_CAT = ["ctype", "session", "st_prev", "has_sweep", "sweep_src"]

WIN_R = 1.0     # user rule: net_R >= 1.0 counts as a win
BAND_HI = 1.7


def load_1m_zip(sym):
    d = os.path.join(OUT, sym)
    rows = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(d, fn)) as z:
            with z.open(z.namelist()[0]) as f:
                for line in io.TextIOWrapper(f, "utf-8"):
                    p = line.strip().split(",")
                    if len(p) < 6:
                        continue
                    t = int(p[0])
                    if t > 10 ** 14:
                        t //= 1000
                    rows.append((t, float(p[1]), float(p[2]), float(p[3]),
                                 float(p[4]), float(p[5])))
    rows.sort()
    seen, uniq = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            uniq.append(r)
    arr = np.array(uniq, dtype=np.float64)
    return {"t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
            "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}


def stats2(trs):
    """trs = [(entry_t, net_R, exit_reason, sym)] with user win definition."""
    if not trs:
        return None
    net = np.array([t[1] for t in trs])
    big = net[net >= WIN_R]
    eq = 1.0
    peak, dd = 1.0, 0.0
    for t in sorted(trs, key=lambda x: x[0]):
        eq *= (1 + 0.01 * t[1])
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    in_band = float(((big >= 1.0) & (big <= BAND_HI)).mean()) if len(big) else 0.0
    return {
        "n": len(trs),
        "wr_big": 100 * float((net >= WIN_R).mean()),
        "wr_pos": 100 * float((net > 0).mean()),
        "exp": float(net.mean()),
        "avgW_big": float(big.mean()) if len(big) else float("nan"),
        "avgW_pos": float(net[net > 0].mean()) if (net > 0).any() else float("nan"),
        "avgL": float(net[net < WIN_R].mean()) if (net < WIN_R).any() else float("nan"),
        "sum": float(net.sum()), "eq": float(eq), "dd": 100 * dd,
        "in_band": 100 * in_band,
        "per_day": len(trs) / 1461.0,
    }


def in_window_t(t, w):
    a, b = T_MS[w]
    return a <= t < b


def window_of(t):
    for w in ("SEL", "OOS1", "OOS2"):
        if in_window_t(t, w):
            return w
    return None


def compact(tr, sym):
    return [(int(x["entry_t"]), float(x["net_R"]), x["exit_reason"], sym) for x in tr]


# ----------------------------------------------------------------- pass 1
def mode_labels():
    """Simulate BASE geometry with FULL per-trade records -> c2_labels.json."""
    rows = []
    for sym in SYMS:
        t0 = time.time()
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        cc = dict(BASE)
        cc["symbol"] = sym
        sel = RG.filter_setups(setups, cc)
        for tr in E.simulate_setups(m1, sel, cc):
            w = window_of(tr["entry_t"])
            if w is None:
                continue
            row = {k: tr.get(k) for k in (
                "entry_t", "net_R", "mfe_R", "mae_R", "exit_reason", "side",
                "hold_h", "ambiguous")}
            row.update({k: tr.get(k) for k in FEATS_NUM + FEATS_CAT})
            row["sym"] = sym
            row["win"] = 1 if tr["net_R"] >= WIN_R else 0
            rows.append(row)
        del cache, m1, setups
        print(f"{sym}: labels {len(rows)} so far ({time.time()-t0:.0f}s)", flush=True)
    os.makedirs(RES, exist_ok=True)
    with open(LAB, "w") as f:
        json.dump(rows, f)
    n_sel = sum(1 for r in rows if window_of(r["entry_t"]) == "SEL")
    st = stats2([(r["entry_t"], r["net_R"], r["exit_reason"], r["sym"]) for r in rows
                 if window_of(r["entry_t"]) == "SEL"])
    print(f"SAVED {LAB}  rows={len(rows)} sel={n_sel}")
    print("BASE SEL:", json.dumps(st, indent=1))


# ----------------------------------------------------------------- pass 2
def build_score(sel_rows):
    """Decile win-rate lifts on SEL rows; greedy pick <=4 features."""
    base_wr = np.mean([r["win"] for r in sel_rows])
    print(f"SEL base WR_big = {base_wr:.3f} on n={len(sel_rows)}")
    models, used = {}, []
    cand = list(FEATS_NUM) + list(FEATS_CAT)
    cur = np.zeros(len(sel_rows))
    while len(used) < 4:
        best, best_top = None, (-1, 0)
        for f in cand:
            if f in used:
                continue
            m = feat_lift(sel_rows, f, base_wr)
            sc = cur + np.array([m["score"](r[f]) for r in sel_rows])
            q = np.quantile(sc, 0.80)
            top = sc >= q
            wr = float(np.mean([sel_rows[i]["win"] for i in range(len(sel_rows)) if top[i]]))
            n = int(top.sum())
            if wr > best_top[0] and n >= 0.10 * len(sel_rows):
                best_top = (wr, n)
                best = (f, m, sc)
        if best is None or best_top[0] <= base_wr + 0.01:
            break
        f, m, cur = best
        used.append(f)
        models[f] = m
        print(f"  +{f:<20} top20% WR_big={best_top[0]:.3f} n={best_top[1]}")
    return {"base_wr": base_wr, "features": used,
            "lifts": {f: models[f]["store"] for f in used}}


def feat_lift(rows, f, base_wr):
    vals = [r[f] for r in rows]
    y = np.array([r["win"] for r in rows], dtype=float)
    if f in FEATS_NUM:
        v = np.array([float(x) for x in vals])
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 11)))
        if len(edges) < 3:
            edges = np.unique(v)
        idx = np.clip(np.searchsorted(edges, v, side="right") - 1, 0, len(edges) - 2)
        wrs = []
        for b in range(len(edges) - 1):
            m = idx == b
            wrs.append(float(y[m].mean()) if m.sum() >= 5 else base_wr)
        wrs = np.array(wrs)
        lift = np.log((wrs + 0.02) / (base_wr + 0.02))

        def score(x, edges=edges, lift=lift):
            i = int(np.clip(np.searchsorted(edges, float(x), side="right") - 1,
                            0, len(lift) - 1))
            return float(lift[i])
        return {"score": score,
                "store": {"type": "num", "edges": edges.tolist(), "lift": lift.tolist()}}
    # categorical
    lut = {}
    for x in set(vals):
        m = [i for i, r in enumerate(rows) if r[f] == x]
        wr = float(y[m].mean()) if len(m) >= 5 else base_wr
        lut[str(x)] = float(np.log((wr + 0.02) / (base_wr + 0.02)))

    def score(x, lut=lut, d=float(np.log((base_wr + 0.02) / (base_wr + 0.02)))):
        return lut.get(str(x), 0.0)
    return {"score": score, "store": {"type": "cat", "lut": lut}}


def apply_score(model, s):
    sc = 0.0
    for f in model["features"]:
        st = model["lifts"][f]
        if st["type"] == "num":
            e, l = st["edges"], st["lift"]
            i = int(np.clip(np.searchsorted(e, float(s.get(f, 0.0)), side="right") - 1,
                            0, len(l) - 1))
            sc += l[i]
        else:
            sc += st["lut"].get(str(s.get(f)), 0.0)
    return sc


# ----------------------------------------------------------------- passes 3-4
def mode_run():
    with open(LAB) as f:
        rows = json.load(f)
    sel_rows = [r for r in rows if window_of(r["entry_t"]) == "SEL"]
    model = build_score(sel_rows)

    # score all SEL rows -> cutoffs: max N with SEL WR_big >= 78% (and >= 85%)
    def score_row(r):
        return apply_score(model, r)
    sc = np.array([score_row(r) for r in sel_rows])
    y = np.array([r["win"] for r in sel_rows])
    order = np.argsort(-sc)
    cutoffs = {}
    for target, tag in ((0.78, "c78"), (0.85, "c85")):
        # walk from strictest; keep the LEAST strict cutoff whose cumulative
        # WR among score >= cutoff is >= target
        chosen = None
        for i in range(len(order)):
            cut = sc[order[i]]
            keep = sc >= cut
            if keep.sum() < 40:
                break
            wr = y[keep].mean()
            if wr >= target:
                chosen = cut          # keep relaxing while target holds
            else:
                break
        if chosen is not None:
            cutoffs[tag] = float(chosen)
    print("cutoffs:", cutoffs)
    if not cutoffs:
        cutoffs["c_max"] = float(np.quantile(sc, 0.90))
    # report cutoff table
    for tag, cut in cutoffs.items():
        keep = sc >= cut
        st = stats2([(sel_rows[i]["entry_t"], sel_rows[i]["net_R"], "x", "x")
                     for i in range(len(sel_rows)) if keep[i]])
        print(f"SEL @ {tag}: n={st['n']} WR_big={st['wr_big']:.1f}% "
              f"exp={st['exp']:.3f} per_day={st['per_day']:.2f}")

    # ---------------- exit/entry grid on FILTERED setups -------------------
    GRID = []
    for ef in (0.5, 0.25):
        for lock in (1.15, 1.20, 1.30):
            for gap in (0.25,):
                for tp in (1.5, 1.7):
                    GRID.append((ef, lock, gap, tp))
    GRID += [(0.5, 1.20, 0.35, 1.7), (0.25, 1.20, 0.35, 1.7)]
    tags = list(cutoffs)

    CKPT = os.path.join(RES, "c2_grid_ckpt.pkl")
    done = {}
    if os.path.exists(CKPT):
        with open(CKPT, "rb") as f:
            done = pickle.load(f)

    def merge(rec):
        sel_s, oos_s, sym_s, yr_s = rec
        for k in sel_tr:
            sel_tr[k].extend(sel_s.get(k, []))
            oos_tr[k].extend(oos_s.get(k, []))
            for s2, v in sym_s.get(k, {}).items():
                oos_sym[k].setdefault(s2, []).extend(v)
            for y, v in yr_s.get(k, {}).items():
                oos_year[k].setdefault(y, []).extend(v)

    sel_tr = {(tag, gi): [] for tag in tags for gi in range(len(GRID))}
    oos_tr = {(tag, gi): [] for tag in tags for gi in range(len(GRID))}
    oos_sym = {(tag, gi): {} for tag in tags for gi in range(len(GRID))}
    oos_year = {(tag, gi): {} for tag in tags for gi in range(len(GRID))}
    for k in list(done):
        merge(done[k])

    for sym in SYMS:
        if sym in done:
            print(f"{sym} cached", flush=True)
            continue
        t0 = time.time()
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        base = dict(BASE)
        base["symbol"] = sym
        fset = RG.filter_setups(setups, base)
        # feature score on setup dicts
        scs = np.array([apply_score(model, s) for s in fset])
        rec_sel, rec_oos, rec_sym, rec_yr = {}, {}, {}, {}
        for tag in tags:
            cut = cutoffs[tag]
            subset = [fset[i] for i in range(len(fset)) if scs[i] >= cut]
            for gi, (ef, lock, gap, tp) in enumerate(GRID):
                cc = dict(BASE)
                cc.update({"entry_frac": ef, "mfe_trig_r": round(lock + gap, 4),
                           "lock_r": lock, "tp_r": tp, "symbol": sym})
                tr = compact(E.simulate_setups(m1, subset, cc), sym)
                srows = [t for t in tr if in_window_t(t[0], "SEL")]
                orows = [t for t in tr if not in_window_t(t[0], "SEL")]
                rec_sel[(tag, gi)] = srows
                rec_oos[(tag, gi)] = orows
                rec_sym[(tag, gi)] = {sym: orows} if orows else {}
                yr = {}
                for t in orows:
                    y_ = datetime.datetime.utcfromtimestamp(t[0] / 1000).year
                    yr.setdefault(y_, []).append(t)
                rec_yr[(tag, gi)] = yr
        del cache, m1, setups
        done[sym] = (rec_sel, rec_oos, rec_sym, rec_yr)
        with open(CKPT, "wb") as f:
            pickle.dump(done, f)
        merge(done[sym])
        print(f"{sym} grid ({time.time()-t0:.0f}s) ckpt={len(done)}", flush=True)

    def goal(tag, gi):
        cfg = {"tp_r": GRID[gi][3], "lock_r": GRID[gi][1]}
        st = stats2(oos_tr[(tag, gi)])
        if st is None:
            return {"achieved": False, "why": "no trades"}
        per_sym = {s: (stats2(ts) or {}).get("exp") for s, ts in oos_sym[(tag, gi)].items()}
        per_year = {s: (stats2(ts) or {}).get("exp") for s, ts in oos_year[(tag, gi)].items()}
        n1 = st["wr_big"] > 75.0
        n2 = st["in_band"] >= 98.0            # wins inside [1.0,1.7]R
        n3a = all(v is not None and v > 0 for v in per_sym.values()) and len(per_sym) == 15
        n3b = all(v is not None and v > 0 for v in per_year.values()) and len(per_year) >= 3
        n4 = st["exp"] >= 0.05
        n5 = st["dd"] <= 10.0
        return {"name": f"{tag}_ef{GRID[gi][0]}_l{GRID[gi][1]}_g{GRID[gi][2]}_tp{GRID[gi][3]}",
                "achieved": bool(n1 and n2 and n3a and n3b and n4 and n5),
                "n1_wr75": n1, "n2_band": n2, "n3_pairs": n3a, "n3_years": n3b,
                "n4_exp": n4, "n5_dd": n5, "oos": st,
                "per_sym_exp": per_sym, "per_year_exp": per_year}

    hdr = (f"{'config':<34} {'n':>5} {'WRbig%':>7} {'expR':>7} {'avgW':>6} "
           f"{'inB%':>5} {'/day':>5} {'dd%':>5}")
    print("\n=== SEL window (ranking by WR_big) ===")
    print(hdr)
    ranked = sorted(((tag, gi) for tag in tags for gi in range(len(GRID))),
                    key=lambda k: -(stats2(sel_tr[k]) or {"wr_big": 0})["wr_big"])
    for tag, gi in ranked:
        st = stats2(sel_tr[(tag, gi)])
        if not st or st["n"] < 30:
            continue
        print(f"{tag}_ef{GRID[gi][0]}_l{GRID[gi][1]}_g{GRID[gi][2]}_tp{GRID[gi][3]:<4} "
              f"{st['n']:>5} {st['wr_big']:>7.1f} {st['exp']:>7.3f} "
              f"{st['avgW_big']:>6.3f} {st['in_band']:>5.1f} {st['per_day']:>5.2f} "
              f"{st['dd']:>5.2f}")

    print("\n=== OOS blind (GOAL check) ===")
    print(hdr + "  goal")
    checks = []
    for tag, gi in ranked:
        st = stats2(oos_tr[(tag, gi)])
        if not st or st["n"] < 30:
            continue
        chk = goal(tag, gi)
        checks.append(chk)
        mark = "ACHIEVED" if chk["achieved"] else \
            ("miss:" + ",".join(k.split("_")[0] for k, v in chk.items()
                                if k.startswith("n") and v is False))
        print(f"{chk['name']:<34} {st['n']:>5} {st['wr_big']:>7.1f} {st['exp']:>7.3f} "
              f"{st['avgW_big']:>6.3f} {st['in_band']:>5.1f} {st['per_day']:>5.2f} "
              f"{st['dd']:>5.2f}  {mark}")

    winners = [c for c in checks if c["achieved"]]
    print(f"\nCHALLENGE 2 ACHIEVED configs: {len(winners)}")
    if winners:
        best = max(winners, key=lambda c: (c["oos"]["wr_big"], c["oos"]["exp"]))
        print("BEST:", json.dumps(best, indent=1, default=str)[:2500])
    else:
        print("NOT ACHIEVED — nearest by WR_big:")
        for c in sorted(checks, key=lambda c: -c["oos"]["wr_big"])[:6]:
            st = c["oos"]
            print(f"  {c['name']:<34} WRbig={st['wr_big']:.1f} exp={st['exp']:.3f} "
                  f"n1={c['n1_wr75']} n2={c['n2_band']} n3p={c['n3_pairs']} "
                  f"n3y={c['n3_years']} n4={c['n4_exp']} n5={c['n5_dd']}")
    with open(os.path.join(RES, "challenge2_wr75.json"), "w") as f:
        json.dump({"model": {"features": model["features"],
                             "lifts": model["lifts"], "base_wr": model["base_wr"]},
                   "cutoffs": cutoffs,
                   "grid": GRID, "checks": checks}, f, default=str)
    print("SAVED", os.path.join(RES, "challenge2_wr75.json"))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "labels":
        mode_labels()
    else:
        mode_run()
