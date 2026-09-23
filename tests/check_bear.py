# -*- coding: utf-8 -*-
"""BEAR-REGIME CROSS-CHECK (out-of-sample for the lock sweep):

Fresh period NOT used in the sweep: 2021-09-01 .. 2023-08-31 (includes the 2022
bear market) on 4 original research pairs (BTC/ETH/SOL/XRP). Compares the
validated control against deep-lock configs selected on the 2023-2026 sample.

Run: python tests/check_bear.py
"""
import concurrent.futures as cf
import io
import json
import os
import sys
import urllib.request
import zipfile

import numpy as np

sys.path.insert(0, "/home/user/ict_bt")
import engine as E  # noqa: E402
import run_grid as RG  # noqa: E402

OUT = "/home/user/ict_bt/data/bear1m"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
MONTHS = [f"{y}-{m:02d}" for y in (2021, 2022, 2023) for m in range(1, 13)
          if "2021-09" <= f"{y}-{m:02d}" <= "2023-08"]
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

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

CONFIGS = {
    "CONTROL t0.35_l0.30_r0.9 (Y_BAL35)": dict(mfe_trig_r=0.35, lock_r=0.30, tp_r=0.9),
    "t0.6_l0.5_r1.5": dict(mfe_trig_r=0.6, lock_r=0.5, tp_r=1.5),
    "t0.8_l0.7_r1.5": dict(mfe_trig_r=0.8, lock_r=0.7, tp_r=1.5),
    "t1.0_l0.9_r1.5": dict(mfe_trig_r=1.0, lock_r=0.9, tp_r=1.5),
    "t0.8_l0.7_r1.7": dict(mfe_trig_r=0.8, lock_r=0.7, tp_r=1.7),
}


def fetch_all():
    jobs = []
    for sym in SYMS:
        d = os.path.join(OUT, sym)
        os.makedirs(d, exist_ok=True)
        for m in MONTHS:
            url = f"{BASE_URL}/{sym}/1m/{sym}-1m-{m}.zip"
            jobs.append((url, os.path.join(d, f"{sym}-{m}.zip")))

    def fetch(url, dest):
        if os.path.exists(dest) and os.path.getsize(dest) > 100:
            return True
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mkmoon-bear/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            print("FAIL", url, e, flush=True)
            return False

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        ok = sum(ex.map(lambda j: fetch(*j), jobs))
    print(f"fetched {ok}/{len(jobs)}", flush=True)


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
            "sum": float(net.sum()), "pf": float(pf), "eq": float(eq),
            "dd": 100 * dd, "tp": sum(1 for t in trades if t["exit_reason"] == "tp")}


if __name__ == "__main__":
    fetch_all()
    all_tr = {k: [] for k in CONFIGS}
    for sym in SYMS:
        m1 = load_1m_zip(sym)
        trig = E.tf_from_1m(m1, "15m")
        sw_int = E.find_swings(trig["h"], trig["l"], 2)
        sw_ext = E.find_swings(trig["h"], trig["l"], 3)
        det = E.run_trigger_detection(trig, sw_int, sw_ext)
        setups = det["setups"]
        for name, over in CONFIGS.items():
            cc = dict(BASE)
            cc.update(over)
            cc["cid"] = name
            cc["symbol"] = sym
            sel = RG.filter_setups(setups, cc)
            tr = E.simulate_setups(m1, sel, cc)
            for t in tr:
                t["symbol"] = sym
            all_tr[name].extend(tr)
        print(f"{sym} done", flush=True)

    print()
    print(f"{'config':<38} {'n':>5} {'WR%':>6} {'expR':>7} {'avgW':>6} {'avgL':>6} "
          f"{'sumR':>7} {'PF':>5} {'eq x':>6} {'dd%':>5} {'tp':>4}")
    rows = {}
    for name in CONFIGS:
        r = summarize(all_tr[name])
        rows[name] = r
        print(f"{name:<38} {r['n']:>5} {r['wr']:>6.1f} {r['exp']:>7.4f} {r['avgW']:>6.3f} "
              f"{r['avgL']:>6.3f} {r['sum']:>7.1f} {r['pf']:>5.2f} {r['eq']:>6.2f} "
              f"{r['dd']:>5.2f} {r['tp']:>4}")

    print("\n--- per-pair exp_net ---")
    names = list(CONFIGS)
    print(f"{'pair':<10}" + "".join(f"{n.split(' ')[0][:12]:>14}" for n in names))
    for s in SYMS:
        line = f"{s:<10}"
        for n in names:
            tr = [t for t in all_tr[n] if t.get("symbol") == s]
            net = np.array([t["net_R"] for t in tr]) if tr else np.zeros(1)
            line += f"{(net.mean() if len(tr) else 0):>14.4f}"
        print(line)

    print("\n--- per-year exp_net (all 4 pairs pooled) ---")
    import datetime
    print(f"{'year':<10}" + "".join(f"{n.split(' ')[0][:12]:>14}" for n in names))
    for y in (2021, 2022, 2023):
        line = f"{y:<10}"
        for n in names:
            tr = [t for t in all_tr[n]
                  if datetime.datetime.utcfromtimestamp(t["entry_t"] / 1000).year == y]
            net = np.array([t["net_R"] for t in tr]) if tr else np.zeros(1)
            line += f"{(net.mean() if len(tr) else 0):>14.4f}"
        print(line)

    with open("/home/user/ict_bt/results/bear_check.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\nSAVED /home/user/ict_bt/results/bear_check.json")
