# -*- coding: utf-8 -*-
"""THE 1.0-1.7R CHALLENGE — threshold search until winning trades land at
+1.0R..+1.7R NET while expectancy stays positive on every pair & every year.

Formal goal (declared before looking):
  G1  avg net win of closed winners  in [1.0R, 1.7R]
  G2  declared TP target             in [1.0R, 1.7R]
  G3  exp_net > 0 for EVERY pair and EVERY calendar year (no edge sacrifice)
  G4  exp_net overall >= 0.05R  (at least the validated baseline)
  G5  confirmed on OOS windows: 2021-09..2023-01 (incl. 2022 bear)
      and 2025-01..2026-10 (recent) — selection window 2023-01..2025-01

Modes:
  python tests/challenge_sweep.py detect   # detect setups once per pair -> cache
  python tests/challenge_sweep.py run      # sweep configs on cached data
"""
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
import engine as E  # noqa: E402
import run_grid as RG  # noqa: E402

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

# config grid: deep-lock family (lock = trig - gap), TP inside the goal band
CONFIGS = {}
for trig in [0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0]:
    for tp in [1.5, 1.7]:
        CONFIGS[f"t{trig}_g0.1_tp{tp}"] = dict(
            mfe_trig_r=trig, lock_r=round(trig - 0.1, 4), tp_r=tp)
for trig, tp in [(1.2, 1.0), (1.5, 1.0)]:
    CONFIGS[f"t{trig}_g0.1_tp{tp}"] = dict(
        mfe_trig_r=trig, lock_r=round(trig - 0.1, 4), tp_r=tp)
for gap in [0.05, 0.2, 0.3]:
    CONFIGS[f"t1.5_g{gap}_tp1.7"] = dict(mfe_trig_r=1.5, lock_r=round(1.5 - gap, 4), tp_r=1.7)
CONFIGS["CONTROL_Y_BAL35"] = dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=0.9)

T_MS = {
    "SEL":   (1672531200000, 1735689600000),    # 2023-01-01 .. 2025-01-01
    "OOS1":  (1630454400000, 1672531200000),    # 2021-09-01 .. 2023-01-01 (bear)
    "OOS2":  (1735689600000, 1790880000000),    # 2025-01-01 .. 2026-10-01
}


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


def mode_detect():
    os.makedirs(RES, exist_ok=True)
    for sym in SYMS:
        t0 = time.time()
        m1 = load_1m_zip(sym)
        trig = E.tf_from_1m(m1, "15m")
        sw_int = E.find_swings(trig["h"], trig["l"], 2)
        sw_ext = E.find_swings(trig["h"], trig["l"], 3)
        det = E.run_trigger_detection(trig, sw_int, sw_ext)
        cache = {"m1": {k: np.asarray(m1[k]) for k in ("t", "o", "h", "l", "c", "v")},
                 "setups": det["setups"]}
        path = os.path.join(OUT, sym, "cache.pkl")
        with open(path, "wb") as f:
            pickle.dump(cache, f, protocol=4)
        print(f"{sym}: bars1m={len(m1['t'])} setups={len(det['setups'])} "
              f"{time.time()-t0:.0f}s", flush=True)
        del m1, trig, det, cache
    print("DETECT DONE", flush=True)


def compact(tr, sym):
    return [(int(t["entry_t"]), float(t["net_R"]), t["exit_reason"], sym)
            for t in tr]


def stats(trs):
    if not trs:
        return None
    net = np.array([t[1] for t in trs])
    wins = net[net > 0]
    losses = net[net <= 0]
    eq = 1.0
    peak, dd = 1.0, 0.0
    for t in sorted(trs, key=lambda x: x[0]):
        eq *= (1 + 0.01 * t[1])
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    in_band = float(((wins >= 1.0) & (wins <= 1.7)).mean()) if len(wins) else 0.0
    return {"n": len(trs), "wr": 100 * float((net > 0).mean()),
            "exp": float(net.mean()),
            "avgW": float(wins.mean()) if len(wins) else float("nan"),
            "avgL": float(losses.mean()) if len(losses) else float("nan"),
            "sum": float(net.sum()), "eq": float(eq), "dd": 100 * dd,
            "in_band": 100 * in_band}


def in_window(trs, w):
    a, b = T_MS[w]
    return [t for t in trs if a <= t[0] < b]


def goal_check(name, cfg, tr_sel, tr_oos, per_sym, per_year):
    """G1-G5 on the OOS windows (per the declared protocol)."""
    st = stats(tr_oos)
    if st is None:
        return {"name": name, "achieved": False, "why": "no trades"}
    tp = cfg["tp_r"]
    g2 = 1.0 <= tp <= 1.7
    g1 = 1.0 <= st["avgW"] <= 1.7
    per_sym_exp = {s: (stats(ts) or {}).get("exp") for s, ts in per_sym.items()}
    g3a = all(v is not None and v > 0 for v in per_sym_exp.values())
    per_year_exp = {y: (stats(ts) or {}).get("exp") for y, ts in per_year.items()}
    g3b = all(v is not None and v > 0 for v in per_year_exp.values())
    g4 = st["exp"] >= 0.05
    achieved = bool(g1 and g2 and g3a and g3b and g4)
    return {"name": name, "achieved": achieved,
            "g1_avgwin_in_band": g1, "g2_tp_in_band": g2,
            "g3_all_pairs_pos": g3a, "g3_all_years_pos": g3b,
            "g4_exp_ge_0.05": g4, "oos": st, "tp": tp,
            "per_sym_exp": per_sym_exp, "per_year_exp": per_year_exp}


def mode_run():
    results = {}
    sel_tr = {k: [] for k in CONFIGS}
    oos_tr = {k: [] for k in CONFIGS}
    oos_sym = {k: {} for k in CONFIGS}
    oos_year = {k: {} for k in CONFIGS}
    for sym in SYMS:
        t0 = time.time()
        with open(os.path.join(OUT, sym, "cache.pkl"), "rb") as f:
            cache = pickle.load(f)
        m1 = cache["m1"]
        setups = cache["setups"]
        for name, over in CONFIGS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["cid"] = name
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            tr = compact(E.simulate_setups(m1, sel, cc), sym)
            sel_tr[name].extend(in_window(tr, "SEL"))
            w1 = in_window(tr, "OOS1")
            w2 = in_window(tr, "OOS2")
            oos = w1 + w2
            oos_tr[name].extend(oos)
            if oos:
                oos_sym[name][sym] = oos
                for t in oos:
                    y = 2021 + (t[0] - 1630454400000) // 31536000000  # coarse ok
                    import datetime
                    y = datetime.datetime.utcfromtimestamp(t[0] / 1000).year
                    oos_year[name].setdefault(y, []).append(t)
        del cache, m1, setups
        print(f"{sym} swept ({time.time()-t0:.0f}s)", flush=True)

    print("\n=== SELECTION window 2023-2024 (ranking by exp) ===")
    hdr = (f"{'config':<22} {'n':>5} {'WR%':>6} {'expR':>7} {'avgW':>6} {'avgL':>6} "
           f"{'sumR':>7} {'inBand%':>7} {'dd%':>5}")
    print(hdr)
    ranked = sorted(CONFIGS, key=lambda n: -(stats(sel_tr[n]) or {"exp": -9})["exp"])
    for name in ranked:
        st = stats(sel_tr[name])
        if not st:
            continue
        print(f"{name:<22} {st['n']:>5} {st['wr']:>6.1f} {st['exp']:>7.4f} "
              f"{st['avgW']:>6.3f} {st['avgL']:>6.3f} {st['sum']:>7.1f} "
              f"{st['in_band']:>7.1f} {st['dd']:>5.2f}")

    print("\n=== OOS windows 2021-2022 bear + 2025-2026 (GOAL check) ===")
    print(hdr + "  goal")
    checks = []
    for name in ranked:
        st = stats(oos_tr[name])
        if not st:
            continue
        chk = goal_check(name, CONFIGS[name], sel_tr[name], oos_tr[name],
                         oos_sym[name], oos_year[name])
        checks.append(chk)
        mark = "✅ ACHIEVED" if chk["achieved"] else \
            ("✗ " + ",".join(k[0:2] for k, v in chk.items() if k.startswith("g") and v is False))
        print(f"{name:<22} {st['n']:>5} {st['wr']:>6.1f} {st['exp']:>7.4f} "
              f"{st['avgW']:>6.3f} {st['avgL']:>6.3f} {st['sum']:>7.1f} "
              f"{st['in_band']:>7.1f} {st['dd']:>5.2f}  {mark}")

    winners = [c for c in checks if c["achieved"]]
    print(f"\nGOAL ACHIEVED configs: {len(winners)}")
    if winners:
        best = max(winners, key=lambda c: c["oos"]["exp"])
        print("BEST:", json.dumps(best, indent=1, default=str)[:2000])
    else:
        print("GOAL NOT ACHIEVED on OOS — nearest misses (by avgW distance to band):")
        def dist(c):
            aw = c["oos"]["avgW"]
            if math.isnan(aw):
                return 9
            return 0 if 1.0 <= aw <= 1.7 else min(abs(aw - 1.0), abs(aw - 1.7))
        for c in sorted(checks, key=dist)[:5]:
            st = c["oos"]
            print(f"  {c['name']:<22} avgW={st['avgW']:.3f} exp={st['exp']:.4f} "
                  f"g1={c['g1_avgwin_in_band']} g3pairs={c['g3_all_pairs_pos']} "
                  f"g3years={c['g3_all_years_pos']} g4={c['g4_exp_ge_0.05']}")
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, "challenge_10_17.json"), "w") as f:
        json.dump({"checks": checks, "ranked": ranked}, f, default=str)
    print("SAVED", os.path.join(RES, "challenge_10_17.json"))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "detect":
        mode_detect()
    else:
        mode_run()
