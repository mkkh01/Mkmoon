# -*- coding: utf-8 -*-
"""GENERALIZATION BACKTEST — the frozen Y_BAL35 config on 8 NEW pairs
(3 years: 2023-09-01 .. 2026-09-21), using the VALIDATED engine unchanged
(ict_bt/engine.py + run_grid.filter_setups + engine.simulate_setups).

This is offline research proof (development sandbox only — needs numpy/pandas).
The production app under app/ never imports numpy/pandas.

Run: python tests/backtest_generalization.py
"""
import io
import json
import os
import sys
import zipfile

import numpy as np

sys.path.insert(0, "/home/user/ict_bt")
import engine as E  # noqa: E402
import run_grid as RG  # noqa: E402

OUT = "/home/user/ict_bt/data/gen1m"
RES = "/home/user/ict_bt/results"
SYMS = ["ZECUSDT", "SUIUSDT", "NEARUSDT", "UNIUSDT", "TRXUSDT",
        "APTUSDT", "ARBUSDT", "AAVEUSDT"]

CFG = dict(RG.__dict__.get("CFG", {}))  # placeholder, real below
CFG = {
    "require_sweep": True, "require_disp": False, "htf_mode": "none", "z_max": 1.0,
    "max_fvg_lag": 8, "min_fvg_atr": 0.0, "choch_from": "non_bullish",
    "max_sweep_age": 8, "min_sweep_depth": 0.0, "close_back_min": 0.0,
    "ctype": "both", "entry_mode": "limit", "entry_frac": 0.5, "sl_mode": "struct",
    "sl_buf_atr": 0.2, "max_hold_h": 24, "order_bars": 24, "min_r_pct": 1.2,
    "max_r_pct": 3.0, "sessions": "no_asia", "tp_mode": "fixed", "tp_r": 0.9,
    "inv_atr": 0.0, "early_abort": True, "mfe_trig_r": 0.35, "lock_r": 0.3,
    "entry_fee_bps": 7.5, "exit_fee_bps": 7.5, "slip_entry_bps": 0.0,
    "slip_exit_bps": 2.0, "cid": "Y_BAL35", "tf_ms": 900_000,
}


def load_1m_zip(sym):
    d = os.path.join(OUT, sym)
    rows = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(d, fn)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                for line in io.TextIOWrapper(f, "utf-8"):
                    p = line.strip().split(",")
                    if len(p) < 6:
                        continue
                    t = int(p[0])
                    if t > 10**14:
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
    m1 = {"t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
          "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}
    return m1, len(uniq)


def run_symbol(sym):
    m1, n1 = load_1m_zip(sym)
    trig = E.tf_from_1m(m1, "15m")
    sw_int = E.find_swings(trig["h"], trig["l"], 2)
    sw_ext = E.find_swings(trig["h"], trig["l"], 3)
    det = E.run_trigger_detection(trig, sw_int, sw_ext)
    setups = det["setups"]
    for s in setups:
        s["symbol"] = sym
    cc = dict(CFG)
    cc["symbol"] = sym
    sel = RG.filter_setups(setups, cc)
    tr = E.simulate_setups(m1, sel, cc)
    for t in tr:
        t["symbol"] = sym
        t["split"] = RG.split_of(t["entry_t"])
    # fixed-fractional compounding at 1% risk (validated §23)
    eq = 1.0
    peak, max_dd = 1.0, 0.0
    for t in sorted(tr, key=lambda x: x["entry_t"]):
        eq *= (1 + 0.01 * t["net_R"])
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    net = np.array([t["net_R"] for t in tr]) if tr else np.zeros(1)
    gross = np.array([t["gross_R"] for t in tr]) if tr else np.zeros(1)
    gl = gross[gross <= 0]
    pf = (gross[gross > 0].sum() / abs(gl.sum())) if len(gl) and gl.sum() != 0 else float("inf")
    row = {
        "symbol": sym, "bars_1m": n1, "setups": len(setups), "signals": len(sel),
        "trades": len(tr), "wr_net": float((net > 0).mean()) if len(tr) else None,
        "exp_net": float(net.mean()) if len(tr) else None,
        "sum_net": float(net.sum()) if len(tr) else 0.0,
        "pf_gross": float(pf),
        "final_eq": eq, "max_dd_pct": 100 * max_dd,
        "exits": {k: sum(1 for t in tr if t["exit_reason"] == k)
                  for k in ("tp", "sl", "abort", "time")},
        "trades_day": len(tr) / max((m1["t"][-1] - m1["t"][0]) / 86400000.0, 1e-9),
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row, tr


if __name__ == "__main__":
    os.makedirs(RES, exist_ok=True)
    rows, all_tr = [], []
    for sym in SYMS:
        try:
            r, tr = run_symbol(sym)
            rows.append(r)
            all_tr.extend(tr)
        except Exception as e:
            print(f"{sym} ERROR {e!r}", flush=True)
    net = np.array([t["net_R"] for t in all_tr]) if all_tr else np.zeros(1)
    tot = {
        "pairs": len(rows), "trades": len(all_tr),
        "wr_net": float((net > 0).mean()) if len(all_tr) else None,
        "exp_net": float(net.mean()) if len(all_tr) else None,
        "sum_net": float(net.sum()),
        "positive_pairs": sum(1 for r in rows if (r["sum_net"] or 0) > 0),
        "equity_1pct": float(np.prod([1 + 0.01 * t["net_R"] for t in all_tr])) if all_tr else 1.0,
    }
    print("TOTAL:", json.dumps(tot, ensure_ascii=False), flush=True)
    with open(os.path.join(RES, "generalization_8pairs_3y.json"), "w") as f:
        json.dump({"per_pair": rows, "total": tot}, f, ensure_ascii=False, indent=1)
    print("SAVED", os.path.join(RES, "generalization_8pairs_3y.json"), flush=True)
