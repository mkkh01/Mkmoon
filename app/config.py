# -*- coding: utf-8 -*-
"""
Mkmoon — configuration & LOCKED strategy parameters.

Strategy: ICT-DAY-15M-SLM-03 (candidate "M_DUAL") — MERGED dual-leg exits:
one entry -> two half-positions (50/50 risk):
  ⚡ FAST   leg: lock +0.30R @ 0.35R, TP +0.55R  (blind-OOS WR ~70%, avg win +0.18R)
  🎯 TANGIBLE leg: lock +0.85R @ 1.00R, TP +1.05R (wins land +0.71..+1.0R net;
     ~27% of entries = ~1-2 tangible 0.7-1.0R winners per day)
Merged expectancy +0.058R per 1% risk (blind OOS 2021-22/2025-26, 15 pairs).
Entry/detection rules identical to ICT-DAY-15M-SLM-01 (fully validated).
Built from the measured win-size x win-rate frontier (tests/c3_merge.py).
DO NOT tune live; frozen for paper validation.
"""
import os

APP_NAME = "Mkmoon"
VERSION = "1.0.0"
STRATEGY_ID = "ICT-DAY-15M-SLM-03 / M_DUAL"

# ---------------------------------------------------------------- timeframes --
TF_MS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
TRIGGER_TF = "15m"
TRIG_MS = TF_MS[TRIGGER_TF]
HTF_TFS = ("1h", "4h")

# swing parameters (validated §4) --------------------------------------------
K_INT = 2          # internal swings (pools / structure / HTF state)
K_EXT = 3          # external swings (recorded; not gating in this config)
EQ_TOL_ATR = 0.05  # equal-level cluster tolerance

# detection windows (validated) ----------------------------------------------
W1 = 8             # sweep -> MSS attach window (bars)
W2 = 8             # MSS -> FVG formation window (bars)
CHOCH_LOCK_BARS = 4
CONT_LOCK_BARS = 3
POOL_MAX_AGE = 400
POOL_MAX_PER_SIDE = 10
DISP_BODY_MIN = 0.60     # displacement body ratio
DISP_RANGE_MIN = 1.00    # displacement range in ATR
BREAK_DELTA_ATR = 0.02   # structure break buffer (delta)
SWEEP_PEN_ATR = 0.02     # sweep penetration beyond level
AMPLITUDE_MIN_ATR = 0.50 # external-swing amplitude filter

# -------------------------------------------------------- MERGED config M_DUAL --
# (dual-leg exits, see tests/c3_merge.py: 50/50 FAST + TANGIBLE halves)
CFG = {
    "cid": "M_DUAL",
    # ---- detection gates (run_grid.filter_setups) ----
    "require_sweep": True,
    "require_disp": False,
    "htf_mode": "none",          # HTF recorded, NOT a hard filter (ablation)
    "z_max": 1.0,                # premium/discount effectively off at 1.0
    "max_fvg_lag": 8,
    "min_fvg_atr": 0.0,
    "choch_from": "non_bullish",  # == no st_prev restriction (only "strict" filters)
    "max_sweep_age": 8,
    "min_sweep_depth": 0.0,
    "close_back_min": 0.0,
    "ctype": "both",             # mss + continuation chains
    # ---- execution ----
    "entry_mode": "limit",
    "entry_frac": 0.5,           # limit @ FVG mid
    "sl_mode": "struct",         # SL = sweep extreme (fallback swing) ∓ buf
    "sl_buf_atr": 0.20,
    "max_hold_h": 24,            # time stop
    "order_bars": 24,            # order lifetime = 24 × 15m = 6h
    "min_r_pct": 1.2,            # R band as % of entry price
    "max_r_pct": 3.0,
    "sessions": "no_asia",
    "tp_mode": "fixed",
    "inv_atr": 0.0,              # cancel line = FVG edge exactly
    "early_abort": True,         # 1m close beyond FVG edge
    # ---- M_DUAL: one entry -> two half-position exit profiles ----
    "legs": (
        {"id": "F", "ar": "⚡ سريع", "tp_r": 0.55, "mfe_trig_r": 0.35,
         "lock_r": 0.30, "weight": 0.5},
        {"id": "T", "ar": "🎯 ملموس", "tp_r": 1.05, "mfe_trig_r": 1.00,
         "lock_r": 0.85, "weight": 0.5},
    ),
    # flat keys = TANGIBLE leg (order display + single-leg fallback)
    "tp_r": 1.05,
    "mfe_trig_r": 1.00,
    "lock_r": 0.85,
    "optimistic": False,         # pessimistic ambiguity resolution (§21)
    # ---- costs (validated) ----
    "entry_fee_bps": 7.5,
    "exit_fee_bps": 7.5,
    "slip_entry_bps": 0.0,
    "slip_exit_bps": 2.0,
    # ---- risk model (virtual percent-compounding) ----
    "risk_pct": 1.0,             # risk 1% of virtual equity per trade
    # ---- fill protocol ----
    # "bar_close" (default): EXACT validated semantics (§21 pessimistic protocol):
    #   the 1m bar must TOUCH the limit and NOT close beyond the FVG edge; the
    #   fill is confirmed/executed at that close at the limit price (live level).
    # "instant": real bare-limit behaviour — fill at the touch tick; the same-bar
    #   close-beyond-edge case then resolves as an abort/SL exit instead of a no
    #   trade (slightly more conservative results than the validated backtest).
    "fill_mode": os.environ.get("FILL_MODE", "bar_close"),
}

# ------------------------------------------------------------- env / runtime --
def _env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"missing required env: {name}")
    return v

REDIS_URL = _env("REDIS_URL", required=False)
DATABASE_URL = _env("DATABASE_URL") or _env("SUPABASE_DB_URL")
SUPABASE_URL = _env("SUPABASE_URL")
BOT_TOKEN = _env("BOT_TOKEN")
CHAT_ID = _env("CHAT_ID")
STATUS_KEY = _env("STATUS_KEY", "mkmoon")
HEARTBEAT_MINUTES = int(_env("HEARTBEAT_MINUTES", "30"))
PORT = int(_env("PORT", "8080"))
CYCLE_RETENTION_DAYS = int(_env("CYCLE_RETENTION_DAYS", "30"))
BYTES_BUDGET_MB = int(_env("BYTES_BUDGET_MB", "500"))   # monthly egress/ingress guide
TIER_B_ENABLED = _env("TIER_B_ENABLED", "1") == "1"

# loops
FAST_TICK_S = 3          # order / position management tick
PRICE_CACHE_S = 30       # full market price cache refresh
CYCLE_S = 60             # "cycle" unit counter
BARS_LOOKBACK = {"15m": 300, "1h": 250, "4h": 90}   # boot backfill depth

REASON_AR = {
    "tp": "🎯 الهدف",
    "sl_lock": "🔒 قفل ربح",
    "sl": "🛑 وقف خسارة",
    "abort": "⚠️ إبطال مبكر (تجاوز الفجوة)",
    "time": "⏱️ إغلاق زمني (24 ساعة)",
    "expired": "⌛ أمر منتهي بدون تعبئة",
    "cancelled": "🚫 أمر ملغى (إبطال الفكرة)",
}
