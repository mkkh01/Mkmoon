# -*- coding: utf-8 -*-
"""Unit tests for the paper trader (pure python — no numpy/pandas).

Covers the critical validated semantics (§21 pessimistic protocol):
  * same-bar touch + close beyond edge -> CANCEL (pessimistic), no fill
  * fill-bar TP touch NOT credited; SL on fill bar active
  * MFE 0.35R -> lock (entry+0.30R) with retro stop on the locking bar
  * early abort on 1m close beyond FVG edge
  * TP exit + percent-compounding equity
  * FILL_MODE=instant fills at the touch tick

Run: python tests/test_trader.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.trader import PaperTrader  # noqa: E402
from app import config as C  # noqa: E402

T0 = 1_699_999_980_000          # minute-aligned


def mk_setup(ord_t=T0, side=1, lo=100.0, hi=100.4, sweep_low=99.0):
    return {
        "side": side, "m": 0, "fvg_bar": 1, "conf_b": 2,
        "signal_t": ord_t - 900_000, "ord_t": ord_t,
        "fvg_lo": lo, "fvg_hi": hi, "fvg_size_atr": 0.3, "fvg_lag": 1,
        "ctype": "mss", "has_sweep": True, "sweep_b": -3, "sweep_t": ord_t - 3 * 900_000,
        "sweep_depth_atr": 0.4, "sweep_close_back_atr": 0.3,
        "sweep_extreme": (sweep_low if side == 1 else 2 * (lo + 0.5 * (hi - lo)) - sweep_low),
        "sweep_src": "swing_low" if side == 1 else "swing_high", "sweep_age": 3,
        "swing_ref": (sweep_low - 0.5 if side == 1 else sweep_low + 0.5),
        "disp_ok": True, "disp_body_ratio": 0.8, "disp_range_atr": 1.2,
        "st_prev": -1 if side == 1 else 1, "atr_mss": 0.5,
        "z": 0.3, "session": "london", "ny": True, "htf1": 0, "htf2": 0,
        "entry_bar_t": ord_t,
    }


async def scenario_cancel_same_bar():
    t = PaperTrader()
    s = mk_setup()
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    assert o is not None, "order should be placed"
    b0 = T0 // 60_000
    # bar0: dips through the limit (100.2) AND closes below cancel_line (100.0)
    await t.on_price("TESTUSDT", 100.3, 100.32, T0, b0)
    await t.on_price("TESTUSDT", 100.1, 100.12, T0 + 10_000, b0)     # touch
    await t.on_price("TESTUSDT", 99.9, 99.92, T0 + 50_000, b0)       # close below edge
    await t.on_price("TESTUSDT", 100.0, 100.02, T0 + 61_000, b0 + 1)  # rollover -> decide
    assert o.status == "cancelled", f"expected cancel got {o.status}"
    assert not [p for p in t.positions.values() if p.exit_t is None]
    print("PASS scenario_cancel_same_bar")


async def scenario_fill_bar_lock_retro():
    """Fill bar rallies hard (MFE>=0.35R) but first wicked below the locked
    stop -> engine retro-stop on the locking bar (pessimistic)."""
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)      # limit 100.2 stop 98.9 R 1.3 tp 101.37 lock 100.59
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    assert o is not None
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)             # touch (low 100.15)
    await t.on_price("TESTUSDT", 101.5, 101.52, T0 + 30_000, b0)     # high 101.5 (MFE 1.0R)
    await t.on_price("TESTUSDT", 100.3, 100.32, T0 + 61_000, b0 + 1)  # rollover -> decide+retro
    pos = list(t.positions.values())
    assert len(pos) == 1
    p = pos[0]
    assert p.exit_t is not None, "retro lock stop should fire on the locking bar"
    assert p.exit_reason == "sl_lock", p.exit_reason
    assert p.locked
    print("PASS scenario_fill_bar_lock_retro")


async def scenario_abort():
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    # bar0: touch + close ABOVE the edge -> valid fill at 100.2
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)
    await t.on_price("TESTUSDT", 100.1, 100.12, T0 + 50_000, b0)
    await t.on_price("TESTUSDT", 100.2, 100.22, T0 + 61_000, b0 + 1)   # fill decides
    p = [p for p in t.positions.values() if p.exit_t is None][0]
    # bar1 closes below cancel_line (100.0) without touching stop/tp
    await t.on_price("TESTUSDT", 99.8, 99.82, T0 + 70_000, b0 + 1)
    await t.on_price("TESTUSDT", 100.2, 100.22, T0 + 121_000, b0 + 2)  # bar1 closes
    assert p.exit_t is not None and p.exit_reason == "abort", p.exit_reason
    print("PASS scenario_abort")


async def scenario_tp_and_equity():
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    # bar0: quiet fill (high 100.4 < lock 100.655) -> no lock, no TP
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)
    await t.on_price("TESTUSDT", 100.4, 100.42, T0 + 50_000, b0)
    # bar1 opens ABOVE the locked stop (engine retro-lock is in-bar pessimistic):
    # fill decides on the rollover tick; bar1 low must stay > 100.59
    await t.on_price("TESTUSDT", 100.7, 100.72, T0 + 61_000, b0 + 1)
    p = [p for p in t.positions.values() if p.exit_t is None][0]
    await t.on_price("TESTUSDT", 100.7, 100.72, T0 + 70_000, b0 + 1)   # lock at 0.35R
    # bar2: reach TP 101.37
    await t.on_price("TESTUSDT", 101.4, 101.42, T0 + 130_000, b0 + 2)
    assert p.exit_t is not None and p.exit_reason == "tp", p.exit_reason
    row = [r for r in t.closed if r["trade_id"] == p.id][0]
    assert row["net_R"] > 0.7, row["net_R"]
    assert t.wins == 1
    assert abs(t.equity - 100.0 * (1 + 0.01 * row["net_R"])) < 1e-9
    print(f"PASS scenario_tp_and_equity  net_R={row['net_R']:+.3f} equity={t.equity:.4f}")


async def scenario_instant_fill():
    t = PaperTrader()
    t.cfg = dict(C.CFG)
    t.cfg["fill_mode"] = "instant"
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.1, 100.12, T0, b0)     # touch -> instant fill
    pos = [p for p in t.positions.values() if p.exit_t is None]
    assert len(pos) == 1, "instant mode must fill at the touch tick"
    assert abs(pos[0].entry_px - 100.2) < 1e-9
    print("PASS scenario_instant_fill")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scenario_cancel_same_bar())
    loop.run_until_complete(scenario_fill_bar_lock_retro())
    loop.run_until_complete(scenario_abort())
    loop.run_until_complete(scenario_tp_and_equity())
    loop.run_until_complete(scenario_instant_fill())
    print("TRADER TESTS: ALL PASS ✓")
