# -*- coding: utf-8 -*-
"""Monitored pairs: Tier A = validated 10 (recommendations), Tier B = shadow 30.

Tier B is "تجريبي / خارج نطاق التحقق" — its data feeds research, but its signals
are only shadow-tracked and clearly labelled in Telegram output.

Selection date: 2026-09-23, by 24h quote volume on Binance USDT spot (data-api),
excluding stablecoins / pegged assets, leveraged tokens and junk tickers.
"""

TIER_A = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT", "AVAXUSDT",
]

TIER_B = [
    "ZECUSDT", "NEARUSDT", "UNIUSDT", "PEPEUSDT", "SUIUSDT",
    "BCHUSDT", "TAOUSDT", "TRXUSDT", "ENAUSDT", "XLMUSDT",
    "PENGUUSDT", "TRUMPUSDT", "WLDUSDT", "ONEUSDT", "ONDOUSDT",
    "HBARUSDT", "PUMPUSDT", "ARBUSDT", "DASHUSDT", "FILUSDT",
    "FETUSDT", "ASTERUSDT", "ZROUSDT", "AAVEUSDT", "PROVEUSDT",
    "APTUSDT", "INJUSDT", "BANKUSDT", "DOTUSDT", "ATOMUSDT",
]

ALL_SYMBOLS = TIER_A + TIER_B


def tier_of(sym):
    return "A" if sym in TIER_A else "B"


def is_tradable(sym, tier_b_enabled=True):
    """Only Tier A produces live recommendations; Tier B is shadow-tracked."""
    return sym in TIER_A or (tier_b_enabled and sym in TIER_B)


def label_ar(sym):
    return "معتمد" if tier_of(sym) == "A" else "تجريبي (خارج التحقق)"
