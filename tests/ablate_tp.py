# -*- coding: utf-8 -*-
"""TP ablation — evidence for the '0.9R vs 1.5-1.7R' question.

Runs the VALIDATED engine on the 8-pair × 3y generalization data with different
TP targets / profit-lock policies. Research sandbox only.

Run: python tests/ablate_tp.py
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
SYMS = ["ZECUSDT", "SUIUSDT", "NEARUSDT", "UNIUSDT", "TRXUSDT",
        "APTUSDT", "ARBUSDT", "AAVEUSDT"]

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

VARIANTS = {
    "A: TP 0.9R  + lock +0.30R @0.35R  (Y_BAL35 المعتمد)": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=0.9),
    "B: TP 1.2R + lock +0.30R @0.35R": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=1.2),
    "C: TP 1.5R + lock +0.30R @0.35R": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=1.5),
    "D: TP 1.7R + lock +0.30R @0.35R": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=1.7),
    "E: TP 1.5R + lock +0.50R @0.60R   (مساحة أكبر للركض)": dict(mfe_trig_r=0.60, lock_r=0.50, tp_r=1.5),
    "F: TP 1.7R + lock +0.50R @0.60R": dict(mfe_trig_r=0.60, lock_r=0.50, tp_r=1.7),
    "G: TP 1.5R بدون قفل (قفل مبكر مُعطَّل)": dict(mfe_trig_r=0.0, lock_r=0.0, tp_r=1.5),
    "H: TP 1.7R بدون قفل": dict(mfe_trig_r=0.0, lock_r=0.0, tp_r=1.7),
    "I: TP 2.5R + lock +0.30R @0.35R": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=2.5),
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
    return {"t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
            "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}


def summarize(trades, label):
    net = np.array([t["net_R"] for t in trades]) if trades else np.zeros(1)
    gross = np.array([t["gross_R"] for t in trades]) if trades else np.zeros(1)
    wins = net[net > 0]
    losses = net[net <= 0]
    eq = 1.0
    peak, dd = 1.0, 0.0
    for t in sorted(trades, key=lambda x: x["entry_t"]):
        eq *= (1 + 0.01 * t["net_R"])
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    gl = gross[gross <= 0]
    pf = (gross[gross > 0].sum() / abs(gl.sum())) if len(gl) and gl.sum() != 0 else float("inf")
    row = {
        "label": label, "trades": len(trades),
        "wr_net": round(float((net > 0).mean()), 4) if len(trades) else None,
        "exp_net": round(float(net.mean()), 4) if len(trades) else None,
        "avg_win": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else None,
        "sum_net": round(float(net.sum()), 1),
        "pf": round(float(pf), 2),
        "final_eq_x": round(float(eq), 2),
        "max_dd_pct": round(100 * dd, 2),
        "tp": sum(1 for t in trades if t["exit_reason"] == "tp"),
        "lockwin": sum(1 for t in trades if t["exit_reason"] == "sl" and t["net_R"] > 0),
        "sl": sum(1 for t in trades if t["exit_reason"] == "sl" and t["net_R"] <= 0),
        "abort": sum(1 for t in trades if t["exit_reason"] == "abort"),
    }
    return row


if __name__ == "__main__":
    all_tr = {k: [] for k in VARIANTS}
    for sym in SYMS:
        m1 = load_1m_zip(sym)
        trig = E.tf_from_1m(m1, "15m")
        sw_int = E.find_swings(trig["h"], trig["l"], 2)
        sw_ext = E.find_swings(trig["h"], trig["l"], 3)
        det = E.run_trigger_detection(trig, sw_int, sw_ext)
        setups = det["setups"]
        for name, over in VARIANTS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["cid"] = name
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            tr = E.simulate_setups(m1, sel, cc)
            all_tr[name].extend(tr)
        print(f"{sym} done", flush=True)

    print()
    print(f"{'variant':<52} {'n':>5} {'WR%':>6} {'expR':>7} {'avgW':>6} {'avgL':>6} "
          f"{'sumR':>7} {'PF':>5} {'eq x':>6} {'dd%':>5} {'tp':>4} {'lkW':>4} {'sl':>5} {'ab':>4}")
    rows = {}
    for name in VARIANTS:
        r = summarize(all_tr[name], name)
        rows[name] = r
        print(f"{name:<52} {r['trades']:>5} {100*r['wr_net']:>6.1f} {r['exp_net']:>7.4f} "
              f"{r['avg_win']:>6.3f} {r['avg_loss']:>6.3f} {r['sum_net']:>7.1f} {r['pf']:>5.2f} "
              f"{r['final_eq_x']:>6.2f} {r['max_dd_pct']:>5.2f} {r['tp']:>4} {r['lockwin']:>4} "
              f"{r['sl']:>5} {r['abort']:>4}")
    with open("/home/user/ict_bt/results/tp_ablation.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\nSAVED /home/user/ict_bt/results/tp_ablation.json")
