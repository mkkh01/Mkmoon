# -*- coding: utf-8 -*-
"""Unit tests for the MERGED dual-leg paper trader (pure python).

M_DUAL geometry (R=1.3 in these fixtures, entry 100.2, stop 98.9):
  ⚡ F leg (50%): TP = +0.55R = 100.915 | lock +0.30R = 100.59 | trigger +0.35R = 100.655
  🎯 T leg (50%): TP = +1.05R = 101.565 | lock +0.85R = 101.305 | trigger +1.00R = 101.50

Covers: same-bar touch+close-beyond = CANCEL; fill-bar TP not credited;
retro lock on the locking bar; early abort; dual-leg TP path + equity;
instant fill creates both legs.

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


def open_legs(t):
    return {p.leg: p for p in t.positions.values() if p.exit_t is None}


def all_legs(t):
    return {p.leg: p for p in t.positions.values()}


async def scenario_cancel_same_bar():
    t = PaperTrader()
    s = mk_setup()
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    assert o is not None, "order should be placed"
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.3, 100.32, T0, b0)
    await t.on_price("TESTUSDT", 100.1, 100.12, T0 + 10_000, b0)      # touch
    await t.on_price("TESTUSDT", 99.9, 99.92, T0 + 50_000, b0)        # close below edge
    await t.on_price("TESTUSDT", 100.0, 100.02, T0 + 61_000, b0 + 1)   # rollover
    assert o.status == "cancelled", f"expected cancel got {o.status}"
    assert not open_legs(t)
    print("PASS scenario_cancel_same_bar")


async def scenario_fill_bar_lock_retro():
    """Fill bar spikes past BOTH TPs (not credited in fill bar) and wicks below
    both deep locks -> retro sl_lock on the locking bar for BOTH legs."""
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)   # limit 100.2 stop 98.9 R 1.3
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    assert o is not None
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)              # touch (low)
    await t.on_price("TESTUSDT", 102.5, 102.52, T0 + 30_000, b0)      # >= both TPs
    await t.on_price("TESTUSDT", 100.3, 100.32, T0 + 61_000, b0 + 1)   # rollover
    legs = all_legs(t)
    assert set(legs) == {"F", "T"}, f"legs {set(legs)}"
    for lg, p in legs.items():
        assert p.exit_t is not None, f"{lg} should be retro-stopped"
        assert p.exit_reason == "sl_lock", f"{lg}: {p.exit_reason}"
        assert p.locked, lg
        assert p.amb >= 1, f"{lg}: fill-bar TP touch must not be credited"
    print("PASS scenario_fill_bar_lock_retro")


async def scenario_abort():
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)
    await t.on_price("TESTUSDT", 100.4, 100.42, T0 + 50_000, b0)      # quiet fill
    await t.on_price("TESTUSDT", 100.2, 100.22, T0 + 61_000, b0 + 1)   # fill decides
    assert len(open_legs(t)) == 2
    await t.on_price("TESTUSDT", 99.8, 99.82, T0 + 70_000, b0 + 1)     # dip (no stop)
    await t.on_price("TESTUSDT", 100.2, 100.22, T0 + 121_000, b0 + 2)  # bar1 closes
    legs = all_legs(t)
    for lg, p in legs.items():
        assert p.exit_t is not None and p.exit_reason == "abort", f"{lg}: {p.exit_reason}"
    print("PASS scenario_abort")


async def scenario_tp_and_equity():
    """FAST leg banks +0.55R first; TANGIBLE leg climbs later to +1.05R.
    (Lows always stay above the T-lock 101.305 once the trigger bar opens.)"""
    t = PaperTrader()
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    # bar0: quiet fill (h 100.5 -> F MFE 0.23R: no lock; low 100.15: no stop)
    await t.on_price("TESTUSDT", 100.15, 100.17, T0, b0)
    await t.on_price("TESTUSDT", 100.5, 100.52, T0 + 30_000, b0)
    # bar1: slow climb below the F trigger (100.655)
    await t.on_price("TESTUSDT", 100.55, 100.57, T0 + 70_000, b0 + 1)   # fill decides
    legs = all_legs(t)
    F, T = legs["F"], legs["T"]
    assert F.exit_t is None and T.exit_t is None
    await t.on_price("TESTUSDT", 100.6, 100.62, T0 + 100_000, b0 + 1)
    # bar2: FAST leg reaches TP 100.915
    await t.on_price("TESTUSDT", 100.95, 100.97, T0 + 130_000, b0 + 2)
    assert F.exit_t is not None and F.exit_reason == "tp", F.exit_reason
    assert T.exit_t is None, "TANGIBLE leg must still run"
    # bar2-3: TANGIBLE climbs through its 1.0R trigger (101.5) without touching
    # its 101.305 lock, then hits TP 101.565
    await t.on_price("TESTUSDT", 101.32, 101.34, T0 + 160_000, b0 + 2)
    await t.on_price("TESTUSDT", 101.35, 101.37, T0 + 190_000, b0 + 3)
    await t.on_price("TESTUSDT", 101.55, 101.57, T0 + 220_000, b0 + 3)  # trigger -> lock
    await t.on_price("TESTUSDT", 101.6, 101.62, T0 + 230_000, b0 + 3)   # TP
    assert T.exit_t is not None and T.exit_reason == "tp", T.exit_reason
    rf = [r for r in t.closed if r["trade_id"] == F.id][0]
    rt = [r for r in t.closed if r["trade_id"] == T.id][0]
    assert rf["net_R"] > 0.3, rf["net_R"]                # 0.55R minus costs
    assert 0.7 < rt["net_R"] <= 1.0, rt["net_R"]         # 1.05R minus costs = band
    assert rf["leg"] == "F" and rt["leg"] == "T"
    assert t.wins == 2
    exp_eq = 100.0 * (1 + 0.005 * rf["net_R"]) * (1 + 0.005 * rt["net_R"])
    assert abs(t.equity - exp_eq) < 1e-9
    print(f"PASS scenario_tp_and_equity  F:{rf['net_R']:+.3f}R T:{rt['net_R']:+.3f}R "
          f"equity={t.equity:.4f}")


async def scenario_instant_fill():
    t = PaperTrader()
    t.cfg = dict(C.CFG)
    t.cfg["fill_mode"] = "instant"
    s = mk_setup(sweep_low=99.0)
    o = await t.on_setup(s, "TESTUSDT", "A", T0)
    b0 = T0 // 60_000
    await t.on_price("TESTUSDT", 100.1, 100.12, T0, b0)     # touch -> instant fill
    legs = open_legs(t)
    assert set(legs) == {"F", "T"}, f"legs {set(legs)}"
    for p in legs.values():
        assert abs(p.entry_px - 100.2) < 1e-9
    assert abs(legs["F"].tp - 100.915) < 1e-9
    assert abs(legs["T"].tp - 101.565) < 1e-9
    print("PASS scenario_instant_fill")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scenario_cancel_same_bar())
    loop.run_until_complete(scenario_fill_bar_lock_retro())
    loop.run_until_complete(scenario_abort())
    loop.run_until_complete(scenario_tp_and_equity())
    loop.run_until_complete(scenario_instant_fill())
    print("TRADER TESTS (M_DUAL): ALL PASS ✓")
