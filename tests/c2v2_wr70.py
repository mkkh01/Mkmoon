# -*- coding: utf-8 -*-
"""CHALLENGE 2v2 — WR >= 70% with winning trades net in [0.7, 1.0]R,
1-2 trades/day acceptable (user's revised goal).

Declared:
  M1  share of trades with net_R in [0.7, 1.0] >= 70%
  M2  every counted win inside [0.7, 1.0]R
  M3  exp >= 0.05R, all pairs & years positive (keep)
  M4  blind OOS 2021-09..2023-01 + 2025-01..2026-10 (keep)
  M5  >= 0.7 trades/day (1-2/day target)           (user)

Geometry (band-safe for any R in 1.2-3%):
  lock_r 0.85 (net 0.71-0.79), tp_r 1.05 (net 0.91-0.99).
  NO early abort (RW math: cut at -1R lifts touch-rate to ~54%+edge).

Levers: exit/abort variants -> then quality+context slice search
(R_pct, fill_delay, hour, BTC momentum, ATR regime, sweep depth, fvg size).
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
OOS1 = (1630454400000, 1672531200000)
OOS2 = (1735689600000, 1790880000000)

VARIANTS = {
    "V1_noabort_lock85":  dict(early_abort=False, mfe_trig_r=1.05, lock_r=0.85, tp_r=1.05),
    "V2_noabort_tp10":    dict(early_abort=False, mfe_trig_r=0.0, lock_r=0.0, tp_r=1.0),
    "V3_abort_lock85":    dict(early_abort=True, mfe_trig_r=1.05, lock_r=0.85, tp_r=1.05),
    "V4_noabort_lock85t": dict(early_abort=False, mfe_trig_r=0.95, lock_r=0.85, tp_r=1.0),
    "V5_noabort_ef25":    dict(early_abort=False, entry_frac=0.25,
                               mfe_trig_r=1.05, lock_r=0.85, tp_r=1.05),
}

FEATS = ["z", "sweep_depth_atr", "sweep_close_back_atr", "fvg_size_atr",
         "fvg_lag", "disp_body_ratio", "disp_range_atr", "sweep_age",
         "atr_mss", "R_pct", "fill_h", "hour", "btc1h", "btc24h"]


def is_sel(t):
    return SEL[0] <= t < SEL[1]


def win_band(net):
    return 0.7 <= net <= 1.0


if __name__ == "__main__":
    # BTC context series (1h change, 24h change) from BTC cache
    with open(os.path.join(OUT, "BTCUSDT", "cache.pkl"), "rb") as f:
        btc = pickle.load(f)["m1"]
    bt, bc = btc["t"], btc["c"]

    def btc_ctx(t_ms):
        i = int(np.searchsorted(bt, t_ms, side="right")) - 1
        if i < 24 * 60:
            return 0.0, 0.0
        c0 = bc[i]
        c1 = bc[max(0, i - 60)]
        c24 = bc[max(0, i - 1440)]
        return (c0 / c1 - 1.0, c0 / c24 - 1.0)
    del btc

    pool = {v: [] for v in VARIANTS}
    for sym in SYMS:
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        for vn, over in VARIANTS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            for t in E.simulate_setups(m1, sel, cc):
                R_pct = 100.0 * t["R"] / t["entry_px"]
                fill_h = (t["entry_t"] - t["ord_t"]) / 3.6e6
                b1, b24 = btc_ctx(t["entry_t"])
                rec = {"entry_t": t["entry_t"], "net_R": t["net_R"],
                       "mfe_R": t["mfe_R"], "exit_reason": t["exit_reason"],
                       "sym": sym, "R_pct": R_pct, "fill_h": fill_h,
                       "hour": datetime.datetime.utcfromtimestamp(
                           t["entry_t"] / 1000).hour,
                       "btc1h": b1, "btc24h": b24}
                for k in ("z", "sweep_depth_atr", "sweep_close_back_atr",
                          "fvg_size_atr", "fvg_lag", "disp_body_ratio",
                          "disp_range_atr", "sweep_age", "atr_mss"):
                    rec[k] = t.get(k)
                pool[vn].append(rec)
        del cache, m1, setups
        print(f"{sym} done", flush=True)

    print("\n== VARIANTS: win = net in [0.7, 1.0] ==")
    print(f"{'variant':<20} {'win':>4} {'n':>5} {'WRband%':>8} {'WR>=0.7%':>8} "
          f"{'exp':>7} {'avgW':>6} {'inB%':>5} {'/day':>5}")
    for vn in VARIANTS:
        for w, tag in ((is_sel, "SEL"), ((lambda t: not is_sel(t)), "OOS")):
            rs = [r for r in pool[vn] if w(r["entry_t"])]
            if not rs:
                continue
            band = [r for r in rs if win_band(r["net_R"])]
            ge = [r for r in rs if r["net_R"] >= 0.7]
            days = 730 if tag == "SEL" else 1461
            print(f"{vn:<20} {tag:>4} {len(rs):>5} "
                  f"{100*len(band)/len(rs):>8.1f} {100*len(ge)/len(rs):>8.1f} "
                  f"{np.mean([r['net_R'] for r in rs]):>7.3f} "
                  f"{np.mean([r['net_R'] for r in ge]) if ge else 0:>6.3f} "
                  f"{100*len(band)/max(1,len(ge)):>5.1f} "
                  f"{len(rs)/days:>5.2f}")

    # ---- slice search on the best variant (SEL) ----
    best_v = max(VARIANTS, key=lambda vn: np.mean(
        [win_band(r["net_R"]) for r in pool[vn] if is_sel(r["entry_t"])] or [0]))
    rs = [r for r in pool[best_v] if is_sel(r["entry_t"])]
    print(f"\n== SLICE SEARCH on {best_v} (SEL n={len(rs)}) ==")
    base = 100 * np.mean([win_band(r["net_R"]) for r in rs])
    print(f"base WRband = {base:.1f}")

    def band_rate(sl):
        return 100 * np.mean([win_band(r["net_R"]) for r in sl]) if sl else 0.0

    cands = []
    for f in FEATS:
        if f in ("hour", "sym"):
            for x in sorted({r[f] for r in rs}):
                sl = [r for r in rs if r[f] == x]
                if len(sl) >= 40:
                    cands.append((band_rate(sl), len(sl), f, f"=={x}"))
        else:
            v = np.array([float(r[f]) for r in rs])
            for q in np.linspace(0.1, 0.9, 9):
                cut = float(np.quantile(v, q))
                for op, keep in ((">=", lambda x, c=cut: x >= c),
                                 ("<=", lambda x, c=cut: x <= c)):
                    sl = [r for r in rs if keep(float(r[f]))]
                    if len(sl) >= 40:
                        cands.append((band_rate(sl), len(sl), f,
                                      f"{op}{cut:.4g}"))
    cands.sort(reverse=True)
    print("top single slices:")
    for w_, n, f, cond in cands[:14]:
        print(f"  {f:<16} {cond:<12} WRband={w_:5.1f} n={n:>4} /day={n/730:.2f}")

    def parse(f, cond):
        if cond.startswith("=="):
            return lambda r: str(r[f]) == cond[2:]
        op, val = cond[:2], float(cond[2:])
        return (lambda r: float(r[f]) >= val) if op == ">=" else \
               (lambda r: float(r[f]) <= val)

    top = cands[:10]
    pairs = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            (w1, n1, f1, c1), (w2, n2, f2, c2) = top[i], top[j]
            p1, p2 = parse(f1, c1), parse(f2, c2)
            sl = [r for r in rs if p1(r) and p2(r)]
            if len(sl) >= 35:
                pairs.append((band_rate(sl), len(sl), f1 + "&" + f2, c1 + " " + c2))
    pairs.sort(reverse=True)
    print("top pair slices:")
    for w_, n, f, cond in pairs[:12]:
        print(f"  {f:<26} {cond:<24} WRband={w_:5.1f} n={n:>4} /day={n/730:.2f}")

    # triples of the top-6 pair predicates' features
    trip = []
    seen_f = []
    for w_, n, f, cond in top:
        if f not in seen_f:
            seen_f.append(f)
    preds = {f: parse(c, cc) for (w_, n, f, c), cc in
             [(x, x[3]) for x in top[:6]]}
    fl = list(preds)
    for i in range(len(fl)):
        for j in range(i + 1, len(fl)):
            for k in range(j + 1, len(fl)):
                ps = [preds[fl[i]], preds[fl[j]], preds[fl[k]]]
                sl = [r for r in rs if all(p(r) for p in ps)]
                if len(sl) >= 30:
                    trip.append((band_rate(sl), len(sl),
                                 fl[i] + "&" + fl[j] + "&" + fl[k]))
    trip.sort(reverse=True)
    print("top triple slices:")
    for w_, n, f in trip[:10]:
        print(f"  {f:<40} WRband={w_:5.1f} n={n:>4} /day={n/730:.2f}")

    with open(os.path.join(RES, "c2v2_labels.json"), "w") as f:
        json.dump({k: pool[k] for k in pool}, f, default=str)
    print("SAVED c2v2_labels.json  best:", best_v)
