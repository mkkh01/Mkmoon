# -*- coding: utf-8 -*-
"""c2 probe: can ANY policy lift WR_big (net>=1.0R) toward 75%?

Measures, per variant, the UNCENSORED potential P(mfe_R>=1.15) and the real
WR_big with two exit styles (tp-only 1.2R; lock 1.15@1.4 + tp 1.7).
Levers: early_abort on/off, inv_atr (abort width), entry_mode (limit/reclaim/stop).
"""
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, "/home/user/ict_bt")
import engine as E          # noqa: E402
import run_grid as RG       # noqa: E402

OUT = "/home/user/ict_bt/data/ch1m"
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


def is_sel(t):
    return SEL[0] <= t < SEL[1]


VARIANTS = {
    "A_base_abort":        dict(),
    "B_no_abort":          dict(early_abort=False),
    "C_abort0.5":          dict(inv_atr=0.5),
    "D_abort1.5":          dict(inv_atr=1.5),
    "E_noabort_stop":      dict(early_abort=False, entry_mode="stop", touch_frac=0.5),
    "F_noabort_reclaim":   dict(early_abort=False, entry_mode="reclaim", touch_frac=0.5),
    "G_noabort_sl0.05":    dict(early_abort=False, sl_buf_atr=0.05),
    "H_noabort_ef0.25":    dict(early_abort=False, entry_frac=0.25),
}
EXITS = {
    "tp1.2only": dict(mfe_trig_r=0.0, lock_r=0.0, tp_r=1.2),
    "lock115":   dict(mfe_trig_r=1.4, lock_r=1.15, tp_r=1.7),
}

if __name__ == "__main__":
    agg = {}
    for sym in SYMS:
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1, setups = cache["m1"], cache["setups"]
        for vn, over in VARIANTS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            for en, ex in EXITS.items():
                cx = dict(cc)
                cx.update(ex)
                trs = E.simulate_setups(m1, sel, cx)
                for t in trs:
                    w = "SEL" if is_sel(t["entry_t"]) else "OOS"
                    a = agg.setdefault((vn, en, w), {"n": 0, "w": 0, "net": [],
                                                     "mfe": 0, "m115": 0})
                    a["n"] += 1
                    a["w"] += int(t["net_R"] >= 1.0)
                    a["net"].append(t["net_R"])
                    if en == "tp1.2only":
                        a["mfe"] += t["mfe_R"]
                        a["m115"] += int(t["mfe_R"] >= 1.15)
        del cache, m1, setups
        print(f"{sym} done", flush=True)

    print(f"\n{'variant':<22} {'exit':<10} {'win':>4} {'n':>5} {'WRbig%':>7} "
          f"{'expR':>7} {'P(mfe>=1.15)%':>13}")
    for vn in VARIANTS:
        for en in EXITS:
            for w in ("SEL", "OOS"):
                a = agg.get((vn, en, w))
                if not a or not a["n"]:
                    continue
                p115 = 100 * a["m115"] / a["n"] if en == "tp1.2only" else 0
                print(f"{vn:<22} {en:<10} {w:>4} {a['n']:>5} "
                      f"{100*a['w']/a['n']:>7.1f} {np.mean(a['net']):>7.3f} "
                      f"{p115:>13.1f}")
