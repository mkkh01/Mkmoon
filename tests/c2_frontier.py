# -*- coding: utf-8 -*-
"""c2 frontier: (1) win-floor vs WR table with positive-exp exit styles,
(2) slice search on the NO-ABORT (uncensored) pool — can any honest slice
reach WR_big 55-75%?  The 75% goal with wins >= 1.0R needs it.
"""
import datetime
import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, "/home/user/ict_bt")
import engine as E          # noqa: E402
import run_grid as RG       # noqa: E402

OUT = "/home/user/ict_bt/data/ch1m"
RES = "/home/user/ict_bt/results"
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
}
SEL = (1672531200000, 1735689600000)

FLOORS = {
    "F0.3_Y_BAL35": dict(early_abort=True, mfe_trig_r=0.35, lock_r=0.30, tp_r=0.9),
    "F0.3_tp0.5":   dict(early_abort=True, mfe_trig_r=0.0, lock_r=0.0, tp_r=0.5),
    "F0.5_lock":    dict(early_abort=True, mfe_trig_r=0.8, lock_r=0.55, tp_r=1.1),
    "F0.5_tp0.7":   dict(early_abort=True, mfe_trig_r=0.0, lock_r=0.0, tp_r=0.7),
    "F0.7_lock":    dict(early_abort=True, mfe_trig_r=1.0, lock_r=0.75, tp_r=1.4),
    "F0.7_tp1.0":   dict(early_abort=True, mfe_trig_r=0.0, lock_r=0.0, tp_r=1.0),
    "F1.0_lock":    dict(early_abort=True, mfe_trig_r=1.4, lock_r=1.15, tp_r=1.7),
    "F1.0_noabort": dict(early_abort=False, mfe_trig_r=0.0, lock_r=0.0, tp_r=1.2),
}
FLOOR_OF = {"F0.3_Y_BAL35": 0.3, "F0.3_tp0.5": 0.3, "F0.5_lock": 0.5,
            "F0.5_tp0.7": 0.5, "F0.7_lock": 0.7, "F0.7_tp1.0": 0.7,
            "F1.0_lock": 1.0, "F1.0_noabort": 1.0}

FEATS = ["z", "sweep_depth_atr", "sweep_close_back_atr", "fvg_size_atr",
         "fvg_lag", "disp_body_ratio", "disp_range_atr", "sweep_age", "atr_mss"]


def is_sel(t):
    return SEL[0] <= t < SEL[1]


if __name__ == "__main__":
    pool = {}          # name -> list of records
    for name in FLOORS:
        pool[name] = []
    for sym in SYMS:
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        for name, over in FLOORS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            for t in E.simulate_setups(m1, sel, cc):
                rec = {k: t.get(k) for k in (
                    "entry_t", "net_R", "mfe_R", "mae_R", "exit_reason", "hold_h")}
                rec.update({k: t.get(k) for k in FEATS})
                rec["sym"] = sym
                rec["hour"] = datetime.datetime.utcfromtimestamp(
                    t["entry_t"] / 1000).hour
                pool[name].append(rec)
        del cache, m1, setups
        print(f"{sym} done", flush=True)

    def wr(rs, floor):
        return 100 * np.mean([r["net_R"] >= floor for r in rs]) if rs else 0.0

    print("\n== FRONTIER: win floor vs WR (SEL | OOS) ==")
    print(f"{'config':<16} {'floor':>5} {'nS':>5} {'WRS%':>6} {'expS':>7} "
          f"{'nO':>5} {'WRO%':>6} {'expO':>7}")
    for name, fl in FLOOR_OF.items():
        rs = pool[name]
        s = [r for r in rs if is_sel(r["entry_t"])]
        o = [r for r in rs if not is_sel(r["entry_t"])]
        print(f"{name:<16} {fl:>5} {len(s):>5} {wr(s, fl):>6.1f} "
              f"{np.mean([r['net_R'] for r in s]):>7.3f} "
              f"{len(o):>5} {wr(o, fl):>6.1f} "
              f"{np.mean([r['net_R'] for r in o]):>7.3f}")

    # ---- slice search on the UNCENSORED pool (F1.0_noabort) ----
    rs = [r for r in pool["F1.0_noabort"] if is_sel(r["entry_t"])]
    print(f"\n== SLICE SEARCH on no-abort pool (SEL n={len(rs)}) ==")
    base = wr(rs, 1.0)
    print(f"base WR_big = {base:.1f}")
    cands = []
    for f in FEATS + ["hour", "sym"]:
        v = np.array([r[f] for r in rs], dtype=object if f == "sym" else float)
        if f in ("hour", "sym"):
            vals = sorted(set(v.tolist()))
            for x in vals:
                sl = [r for r in rs if r[f] == x]
                if len(sl) >= 60:
                    cands.append((wr(sl, 1.0), len(sl), f, f"=={x}"))
        else:
            for q in (0.2, 0.4, 0.6, 0.8):
                cut = float(np.quantile(v.astype(float), q))
                for side, keep in ((">=", lambda x: x >= cut),
                                   ("<=", lambda x: x <= cut)):
                    sl = [r for r in rs if keep(float(r[f]))]
                    if len(sl) >= 60:
                        cands.append((wr(sl, 1.0), len(sl), f,
                                      f"{side}{cut:.3f}"))
    cands.sort(reverse=True)
    print("top single slices:")
    for w_, n, f, cond in cands[:12]:
        print(f"  {f:<20} {cond:<14} WRbig={w_:5.1f} n={n}")
    # pairwise: intersect top-8 single conditions
    top = cands[:8]
    pairs = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            (w1, n1, f1, c1), (w2, n2, f2, c2) = top[i], top[j]

            def parse(cond, f):
                if cond.startswith("=="):
                    return lambda r: str(r[f]) == cond[2:]
                op, val = cond[:2], float(cond[2:])
                return (lambda r: float(r[f]) >= val) if op == ">=" else \
                       (lambda r: float(r[f]) <= val)
            p1, p2 = parse(c1, f1), parse(c2, f2)
            sl = [r for r in rs if p1(r) and p2(r)]
            if len(sl) >= 50:
                pairs.append((wr(sl, 1.0), len(sl), f1 + "&" + f2, c1 + " " + c2))
    pairs.sort(reverse=True)
    print("top pair slices:")
    for w_, n, f, cond in pairs[:10]:
        print(f"  {f:<28} {cond:<26} WRbig={w_:5.1f} n={n}")

    with open(os.path.join(RES, "c2_frontier.json"), "w") as f:
        json.dump({n: [{"entry_t": r["entry_t"], "net_R": r["net_R"]}
                       for r in pool[n]] for n in pool}, f)
    print("SAVED c2_frontier.json")
