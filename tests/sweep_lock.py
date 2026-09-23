# -*- coding: utf-8 -*-
"""Lock/TP fine sweep — locate the true peak BEYOND the original grid boundary
(original family grid capped lock_r at 0.40 and tp_r at 1.5).

8 pairs × 3y generalization data, validated engine & filters unchanged.
Run: python tests/sweep_lock.py
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

GRID = []
for trig, lock in [(0.5, 0.4), (0.6, 0.5), (0.7, 0.6), (0.8, 0.7), (0.9, 0.8),
                   (1.0, 0.9), (0.6, 0.4), (0.7, 0.5), (0.8, 0.5), (0.6, 0.55)]:
    GRID.append((f"t{trig}_l{lock}_r1.5", trig, lock, 1.5))
GRID += [("t0.6_l0.5_r0.9 (TP=هدفك القديم)", 0.6, 0.5, 0.9),
         ("t0.6_l0.5_r1.7 (TP=سقف رغبتك)", 0.6, 0.5, 1.7),
         ("t0.8_l0.7_r1.7", 0.8, 0.7, 1.7),
         ("t0.7_l0.6_r2.5", 0.7, 0.6, 2.5),
         ("CONTROL Y_BAL35 t0.35_l0.3_r0.9", 0.35, 0.3, 0.9)]


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


def summarize(trades):
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
    return {"n": len(trades), "wr": 100 * float((net > 0).mean()) if len(trades) else 0,
            "exp": float(net.mean()) if len(trades) else 0,
            "avgW": float(wins.mean()) if len(wins) else 0,
            "avgL": float(losses.mean()) if len(losses) else 0,
            "sum": float(net.sum()), "pf": float(pf),
            "eq": float(eq), "dd": 100 * dd,
            "tp": sum(1 for t in trades if t["exit_reason"] == "tp")}


if __name__ == "__main__":
    per_sym = {name: {s: [] for s in SYMS} for name, *_ in GRID}
    all_tr = {name: [] for name, *_ in GRID}
    for sym in SYMS:
        m1 = load_1m_zip(sym)
        trig = E.tf_from_1m(m1, "15m")
        sw_int = E.find_swings(trig["h"], trig["l"], 2)
        sw_ext = E.find_swings(trig["h"], trig["l"], 3)
        det = E.run_trigger_detection(trig, sw_int, sw_ext)
        setups = det["setups"]
        for name, tg, lk, tp in GRID:
            cc = dict(BASE)
            cc.update({"mfe_trig_r": tg, "lock_r": lk, "tp_r": tp, "cid": name,
                       "symbol": sym})
            sel = RG.filter_setups(setups, cc)
            tr = E.simulate_setups(m1, sel, cc)
            all_tr[name].extend(tr)
            per_sym[name][sym] = tr
        print(f"{sym} done", flush=True)

    print()
    hdr = f"{'variant':<38} {'n':>5} {'WR%':>6} {'expR':>7} {'avgW':>6} {'avgL':>6} {'sumR':>7} {'PF':>5} {'eq x':>7} {'dd%':>5} {'tp':>4}"
    print(hdr)
    rows = {}
    for name, tg, lk, tp in GRID:
        r = summarize(all_tr[name])
        rows[name] = r
        print(f"{name:<38} {r['n']:>5} {r['wr']:>6.1f} {r['exp']:>7.4f} {r['avgW']:>6.3f} "
              f"{r['avgL']:>6.3f} {r['sum']:>7.1f} {r['pf']:>5.2f} {r['eq']:>7.1f} {r['dd']:>5.2f} {r['tp']:>4}")

    print("\n--- per-pair exp_net (robustness check) ---")
    top = sorted(rows.items(), key=lambda kv: -kv[1]["exp"])[:4]
    names = [k for k, _ in top] + ["CONTROL Y_BAL35 t0.35_l0.3_r0.9"]
    print(f"{'pair':<10}" + "".join(f"{n.split(' ')[0][:12]:>14}" for n in names))
    for s in SYMS:
        line = f"{s:<10}"
        for n in names:
            tr = per_sym[n][s]
            net = np.array([t["net_R"] for t in tr]) if tr else np.zeros(1)
            line += f"{net.mean() if len(tr) else 0:>14.4f}"
        print(line)

    with open("/home/user/ict_bt/results/lock_sweep.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\nSAVED /home/user/ict_bt/results/lock_sweep.json")
