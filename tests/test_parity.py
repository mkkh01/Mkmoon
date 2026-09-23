# -*- coding: utf-8 -*-
"""PARITY TEST — live incremental detector vs the validated batch engine.

Two independent comparisons on real data (2026-06-01 .. 2026-09-21):

  A) engine re-run : ict_bt.engine.run_trigger_detection (batch, vectorized)
     vs app.detector.LiveDetector (incremental, streaming) — exact equality of
     setups AND of gate decisions (run_grid.filter_setups vs app.gates).

  B) validated artifacts: the SAVED setup pickles produced by the actual
     research run (ict_bt/data/setups/{SYM}_15m.pkl) — the live detector must
     reproduce every setup in the window (after a warm-up margin).

Run: python tests/fetch_parity_data.py   (once)
     python tests/test_parity.py [SYMBOL ...]
"""
import math
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, "/home/user/ict_bt")

import numpy as np  # noqa: E402
import engine as E  # noqa: E402
import run_grid as RG  # noqa: E402

from app.detector import LiveDetector  # noqa: E402
from app.gates import filter_setups as my_filter  # noqa: E402
from app import config as C  # noqa: E402

PARITY = "/home/user/ict_bt/data/parity"
SETUPS = "/home/user/ict_bt/data/setups"
WARMUP_MS = 10 * 86400_000        # ignore first 10 days (cold-start state)

CMP_KEYS = ["side", "fvg_lag", "ctype", "has_sweep", "sweep_age", "st_prev",
            "disp_ok", "session", "sweep_src"]


def key_ts(s):
    """identity by timestamps (window vs full-history indices differ)."""
    return (s["side"], int(s["signal_t"]), int(s["ord_t"]), s["ctype"],
            round(float(s["fvg_lo"]), 6), round(float(s["fvg_hi"]), 6))


def close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a))
    return bool(a) == bool(b) if isinstance(a, (bool, np.bool_)) else a == b


def load_window(sym):
    arr = np.load(os.path.join(PARITY, sym, "bars.npy"))
    return {"t": arr[:, 0], "o": arr[:, 1], "h": arr[:, 2],
            "l": arr[:, 3], "c": arr[:, 4], "v": arr[:, 5]}


def run_engine(m1):
    trig = E.tf_from_1m(m1, "15m")
    sw_int = E.find_swings(trig["h"], trig["l"], 2)
    sw_ext = E.find_swings(trig["h"], trig["l"], 3)
    det = E.run_trigger_detection(trig, sw_int, sw_ext)
    return trig, det["setups"]


def run_live(trig):
    live = LiveDetector(C.TRIG_MS)
    n = len(trig["c"])
    for i in range(n):
        live.add_bar(int(trig["t"][i]), float(trig["o"][i]), float(trig["h"][i]),
                     float(trig["l"][i]), float(trig["c"][i]), float(trig["v"][i]))
    return live.setups


def compare(batch, live, label, t_min):
    bmap = {key_ts(s): s for s in batch if s["ord_t"] >= t_min}
    lmap = {key_ts(s): s for s in live if s["ord_t"] >= t_min}
    missing = set(bmap) - set(lmap)
    extra = set(lmap) - set(bmap)
    bad = []
    for k in set(bmap) & set(lmap):
        s1, s2 = bmap[k], lmap[k]
        for f in CMP_KEYS:
            if not close(s1.get(f), s2.get(f)):
                bad.append((k[0], k[4], f, s1.get(f), s2.get(f)))
        for f in ["fvg_size_atr", "sweep_depth_atr", "sweep_close_back_atr",
                  "sweep_extreme", "atr_mss"]:
            v1, v2 = s1.get(f), s2.get(f)
            if not close(v1, v2, tol=1e-6):
                bad.append((k[0], k[4], f, v1, v2))
    ok = not missing and not extra and not bad
    print(f"[{label}] n_batch={len(bmap)} n_live={len(lmap)} missing={len(missing)} "
          f"extra={len(extra)} mismatches={len(bad)} -> {'PASS' if ok else 'FAIL'}")
    for m in list(missing)[:3]:
        print("   missing:", m)
    for m in list(extra)[:3]:
        print("   extra:", m)
    for b in bad[:6]:
        print("   mismatch:", b)
    return ok


def compare_gates(batch, live, label, t_min):
    bsel = {key_ts(s) for s in RG.filter_setups(batch, C.CFG) if s["ord_t"] >= t_min}
    lsel = {key_ts(s) for s in my_filter(live, C.CFG) if s["ord_t"] >= t_min}
    ok = bsel == lsel
    print(f"[{label}] gates batch={len(bsel)} live={len(lsel)} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("   only-batch:", list(bsel - lsel)[:3])
        print("   only-live :", list(lsel - bsel)[:3])
    return ok


def run_symbol(sym):
    m1 = load_window(sym)
    t0 = int(m1["t"][0])
    t_min = t0 + WARMUP_MS
    trig, batch = run_engine(m1)
    live = run_live(trig)
    print(f"== {sym}: window bars={len(trig['c'])} batch_setups={len(batch)} "
          f"live_setups={len(live)}")
    ok = compare(batch, live, f"{sym} A:vs-engine", t_min)
    ok &= compare_gates(batch, live, f"{sym} A:gates", t_min)

    # B) validated artifacts (full research run) over the same window
    pkl = os.path.join(SETUPS, f"{sym}_15m.pkl")
    if os.path.exists(pkl):
        with open(pkl, "rb") as f:
            saved = pickle.load(f)
        t_max = int(m1["t"][-1]) + C.TRIG_MS
        win = [s for s in saved if t_min <= s["ord_t"] <= t_max]
        ok &= compare(win, live, f"{sym} B:vs-saved-pkl", t_min)
    else:
        print(f"[{sym} B] saved pkl missing — skipped")
    return ok


if __name__ == "__main__":
    syms = sys.argv[1:] or ["BTCUSDT", "SOLUSDT", "XRPUSDT", "ETHUSDT"]
    allok = True
    for s in syms:
        allok &= run_symbol(s)
    print("PARITY:", "ALL PASS ✓" if allok else "FAILED ✗")
    sys.exit(0 if allok else 1)
