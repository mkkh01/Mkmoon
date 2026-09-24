# -*- coding: utf-8 -*-
"""c2v2 score & stability: WRband (net in [0.7,1.0]) >= 70% at usable frequency.
Builds a log-lift score on the SEL window, scans thresholds, and reports the
(OOS-blind) stability of every point + explicit rule combos.
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
    "inv_atr": 0.0, "early_abort": False,
    "entry_fee_bps": 7.5, "exit_fee_bps": 7.5, "slip_entry_bps": 0.0,
    "slip_exit_bps": 2.0, "tf_ms": 900_000,
    "mfe_trig_r": 0.0, "lock_r": 0.0, "tp_r": 1.0,
}
SEL = (1672531200000, 1735689600000)

FEATS_NUM = ["z", "sweep_depth_atr", "sweep_close_back_atr", "fvg_size_atr",
             "fvg_lag", "disp_body_ratio", "disp_range_atr", "sweep_age",
             "atr_mss", "R_pct", "fill_h"]
FEATS_CAT = ["hour", "sym"]


def is_sel(t):
    return SEL[0] <= t < SEL[1]


def band(net):
    return 0.7 <= net <= 1.0


def stats(rs, days):
    if not rs:
        return None
    nets = [r["net_R"] for r in rs]
    wins = [r for r in rs if band(r["net_R"])]
    return {"n": len(rs), "wr": 100 * len(wins) / len(rs),
            "exp": float(np.mean(nets)),
            "per_day": len(rs) / days,
            "avgW": float(np.mean([r["net_R"] for r in wins])) if wins else 0.0}


if __name__ == "__main__":
    rows = []
    for sym in SYMS:
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        cc = dict(BASE)
        cc["symbol"] = sym
        sel = RG.filter_setups(setups, cc)
        for t in E.simulate_setups(m1, sel, cc):
            rec = {"entry_t": t["entry_t"], "net_R": t["net_R"], "sym": sym,
                   "R_pct": 100.0 * t["R"] / t["entry_px"],
                   "fill_h": (t["entry_t"] - t["ord_t"]) / 3.6e6,
                   "hour": datetime.datetime.fromtimestamp(
                       t["entry_t"] / 1000, datetime.UTC).hour}
            for k in ("z", "sweep_depth_atr", "sweep_close_back_atr",
                      "fvg_size_atr", "fvg_lag", "disp_body_ratio",
                      "disp_range_atr", "sweep_age", "atr_mss"):
                rec[k] = t.get(k)
            rows.append(rec)
        del cache, m1, setups
    print(f"rows={len(rows)}", flush=True)
    with open(os.path.join(RES, "c2v2_labels.json"), "w") as f:
        json.dump(rows, f, default=str)

    sel = [r for r in rows if is_sel(r["entry_t"])]
    oos = [r for r in rows if not is_sel(r["entry_t"])]
    base_wr = np.mean([band(r["net_R"]) for r in sel])
    print(f"SEL n={len(sel)} base WRband={100*base_wr:.1f}  "
          f"OOS n={len(oos)} WRband={100*np.mean([band(r['net_R']) for r in oos]):.1f}")

    # ---------------- score = mean log-lift over features ------------------
    def lifts(feat, cat):
        if cat:
            lut = {}
            for x in {str(r[feat]) for r in sel}:
                sl = [r for r in sel if str(r[feat]) == x]
                wr = np.mean([band(r["net_R"]) for r in sl]) if len(sl) >= 20 else base_wr
                lut[x] = float(np.log((wr + 0.02) / (base_wr + 0.02)))
            return ("cat", lut)
        v = np.array([float(r[feat]) for r in sel])
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 9)))
        if len(edges) < 3:
            return ("num", (np.array([-1e18, 1e18]), np.array([0.0, 0.0])))
        wrs = []
        for b in range(len(edges) - 1):
            sl = [r for r in sel if edges[b] <= float(r[feat]) < edges[b + 1] or
                  (b == len(edges) - 2 and float(r[feat]) == edges[-1])]
            wr = np.mean([band(r["net_R"]) for r in sl]) if len(sl) >= 5 else base_wr
            wrs.append(wr)
        lift = np.log((np.array(wrs) + 0.02) / (base_wr + 0.02))
        return ("num", (edges, lift))

    model = {}
    for f in FEATS_NUM:
        model[f] = lifts(f, False)
    for f in FEATS_CAT:
        model[f] = lifts(f, True)

    def score(r):
        s = 0.0
        for f, m in model.items():
            if m[0] == "cat":
                s += m[1].get(str(r[f]), 0.0)
            else:
                edges, lift = m[1]
                i = int(np.clip(np.searchsorted(edges, float(r[f]), side="right") - 1,
                                0, len(lift) - 1))
                s += float(lift[i])
        return s

    sc_s = np.array([score(r) for r in sel])
    sc_o = np.array([score(r) for r in oos])
    print("\n== SCORE THRESHOLD FRONTIER (threshold picked on SEL, OOS blind) ==")
    print(f"{'q%':>5} {'cut':>7} {'WRS%':>6} {'nS':>5} {'/dS':>5} {'expS':>7} "
          f"{'WRO%':>6} {'nO':>5} {'/dO':>5} {'expO':>7} {'pairsO':>6}")
    for q in [50, 60, 70, 75, 80, 85, 88, 90, 92, 94, 96, 97, 98, 99]:
        cut = float(np.quantile(sc_s, q / 100))
        ss = [sel[i] for i in range(len(sel)) if sc_s[i] >= cut]
        oo = [oos[i] for i in range(len(oos)) if sc_o[i] >= cut]
        S, O = stats(ss, 730), stats(oo, 1461)
        pairs = len({r["sym"] for r in oo})
        print(f"{q:>5} {cut:>7.2f} {S['wr']:>6.1f} {S['n']:>5} {S['per_day']:>5.2f} "
              f"{S['exp']:>7.3f} {O['wr']:>6.1f} {O['n']:>5} {O['per_day']:>5.2f} "
              f"{O['exp']:>7.3f} {pairs:>6}")

    # ---------------- explicit rule combos (SEL -> OOS stability) -----------
    RULES = {
        "hour12_fvg.13":   lambda r: r["hour"] == 12 and r["fvg_size_atr"] <= 0.133,
        "lowATR_fvg.13":   lambda r: r["atr_mss"] <= 0.0023 and r["fvg_size_atr"] <= 0.133,
        "lowATR_fvg.20":   lambda r: r["atr_mss"] <= 0.0023 and r["fvg_size_atr"] <= 0.196,
        "hrs01216_fvg.20": lambda r: r["hour"] in (0, 12, 16) and r["fvg_size_atr"] <= 0.196,
        "hrs01216_fvg.13": lambda r: r["hour"] in (0, 12, 16) and r["fvg_size_atr"] <= 0.133,
        "fastfill_disp.8": lambda r: r["fill_h"] <= 0.1 and r["disp_range_atr"] <= 0.81,
        "hrs01216_fast":   lambda r: r["hour"] in (0, 12, 16) and r["fill_h"] <= 0.1,
        "hrs01216_lowATR_fvg.20": lambda r: (r["hour"] in (0, 12, 16)
                                             and r["atr_mss"] <= 0.0023
                                             and r["fvg_size_atr"] <= 0.196),
        "lowATR_fvg.20_fast": lambda r: (r["atr_mss"] <= 0.0023
                                         and r["fvg_size_atr"] <= 0.196
                                         and r["fill_h"] <= 0.1),
    }
    print("\n== RULE COMBOS: SEL -> OOS stability ==")
    print(f"{'rule':<26} {'WRS%':>6} {'nS':>4} {'/dS':>5} {'WRO%':>6} {'nO':>4} "
          f"{'/dO':>5} {'expO':>7} {'pairsO':>6}")
    for name, fn in RULES.items():
        ss = [r for r in sel if fn(r)]
        oo = [r for r in oos if fn(r)]
        S, O = stats(ss, 730), stats(oo, 1461)
        if not S or not O:
            print(f"{name:<26}  (empty)")
            continue
        pairs = len({r["sym"] for r in oo})
        print(f"{name:<26} {S['wr']:>6.1f} {S['n']:>4} {S['per_day']:>5.2f} "
              f"{O['wr']:>6.1f} {O['n']:>4} {O['per_day']:>5.2f} "
              f"{O['exp']:>7.3f} {pairs:>6}")
    print("DONE")
