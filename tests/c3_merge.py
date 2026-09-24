# -*- coding: utf-8 -*-
"""c3 MERGED dual-leg system: one entry stream -> half position exits with a
FAST profile (small frequent wins), half with a TANGIBLE profile (0.7-1.0R).
Measured on blind OOS (2021-22 + 2025-26). Legs simulated independently per
setup and paired by ord_t (live: one order, two linked sub-positions).

FAST  S   : lock 0.30 @ 0.35, tp 0.55, early-abort ON   (WR ~75-80%, wins ~0.15-0.4R)
TANGIBLE B candidates:
  B1      : lock 0.85 @ 1.00, tp 1.05, abort ON   (pure ask geometry)
  B1n     : lock 0.85 @ 1.05, tp 1.05, abort OFF  (ask, no abort)
  B2      : lock 0.90 @ 1.10, tp 1.15, abort ON   (band 0.7-1.09 wins)
  B3      : lock 0.90 @ 1.10, tp 1.40, abort ON   (X0.7 family)
  B4      : lock 1.20 @ 1.50, tp 1.70, abort ON   (Y_LOCK12)
"""
import json
import os
import pickle
import sys
import time

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

S_LEG = dict(early_abort=True, mfe_trig_r=0.35, lock_r=0.30, tp_r=0.55)
B_LEGS = {
    "B1_lock85t105":  dict(early_abort=True, mfe_trig_r=1.00, lock_r=0.85, tp_r=1.05),
    "B1n_noabort":    dict(early_abort=False, mfe_trig_r=1.05, lock_r=0.85, tp_r=1.05),
    "B2_lock90t115":  dict(early_abort=True, mfe_trig_r=1.10, lock_r=0.90, tp_r=1.15),
    "B3_lock90t140":  dict(early_abort=True, mfe_trig_r=1.10, lock_r=0.90, tp_r=1.40),
    "B4_YLOCK12":     dict(early_abort=True, mfe_trig_r=1.50, lock_r=1.20, tp_r=1.70),
}


def is_sel(t):
    return SEL[0] <= t < SEL[1]


def block(rows, days):
    if not rows:
        return "  (empty)"
    net = np.array([r[1] for r in rows])
    wins = net[net > 0]
    eq = 1.0
    peak, dd = 1.0, 0.0
    for r in sorted(rows, key=lambda x: x[0]):
        eq *= (1 + 0.01 * r[1])
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return (f"n={len(rows):>5} WR%={100*np.mean(net > 0):>5.1f} "
            f"exp={net.mean():>7.3f} avgW={wins.mean() if len(wins) else 0:>6.3f} "
            f"/day={len(rows)/days:>5.2f} dd%={100*dd:>5.1f}")


if __name__ == "__main__":
    res = {"S": [], "B": {k: [] for k in B_LEGS}}
    pairs = {k: [] for k in B_LEGS}      # (t, (s_net + b_net)/2) joined
    for sym in SYMS:
        t0 = time.time()
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        cc = dict(BASE)
        cc.update(S_LEG)
        cc["symbol"] = sym
        sel = RG.filter_setups(setups, cc)
        srows = {}
        for t in E.simulate_setups(m1, sel, cc):
            rec = (int(t["entry_t"]), float(t["net_R"]), sym, int(t["ord_t"]))
            res["S"].append(rec)
            srows[int(t["ord_t"])] = rec
        for bn, over in B_LEGS.items():
            cb = dict(BASE)
            cb.update(over)
            cb["symbol"] = sym
            for t in E.simulate_setups(m1, sel, cb):
                rec = (int(t["entry_t"]), float(t["net_R"]), sym, int(t["ord_t"]))
                res["B"][bn].append(rec)
                if int(t["ord_t"]) in srows:
                    tt = rec[0]
                    pairs[bn].append((tt, 0.5 * srows[int(t["ord_t"])][1]
                                      + 0.5 * rec[1], sym,
                                      srows[int(t["ord_t"])][1], rec[1]))
        del cache, m1, setups
        print(f"{sym} ({time.time()-t0:.0f}s)", flush=True)

    def win_b(net):
        return 0.7 <= net <= 1.0

    for tag, keep in (("SEL", is_sel), ("OOS", lambda t: not is_sel(t))):
        days = 730 if tag == "SEL" else 1461
        print(f"\n================ {tag} ================")
        s = [r for r in res["S"] if keep(r[0])]
        print(f"FAST  S          {block(s, days)}")
        for bn in B_LEGS:
            b = [r for r in res["B"][bn] if keep(r[0])]
            pb = [r for r in b if win_b(r[1])]
            pr = [r for r in pairs[bn] if keep(r[0])]
            mnet = np.array([r[1] for r in pr]) if pr else np.array([0.0])
            bnet = np.array([r[4] for r in pr]) if pr else np.array([0.0])
            print(f"  {bn:<15} {block(b, days)}  band0.7-1.0%={100*len(pb)/max(1,len(b)):>5.1f}")
            print(f"    MERGED 50/50  n={len(pr):>5} WR(merged>0)%="
                  f"{100*np.mean(mnet > 0):>5.1f} exp={mnet.mean():>7.3f} "
                  f"P(B produces 0.7-1.0)={100*np.mean([(0.7 <= x <= 1.0) for x in bnet]):>5.1f} "
                  f"/day={len(pr)/days:>5.2f}")

    with open(os.path.join(RES, "c3_merge.json"), "w") as f:
        json.dump({"S": res["S"],
                   "B": res["B"],
                   "pairs": {k: v for k, v in pairs.items()}}, f, default=str)
    print("\nSAVED c3_merge.json")
