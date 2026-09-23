# -*- coding: utf-8 -*-
"""Setup gates — exact port of run_grid.filter_setups for the LOCKED config."""
import math

from . import config as C

NAN = float("nan")


def passes(setup, cfg=None):
    """True iff the setup survives the validated filter set (Y_BAL35)."""
    cfg = cfg or C.CFG

    # ctype
    if cfg.get("ctype", "both") != "both" and setup.get("ctype", "mss") != cfg["ctype"]:
        return False

    # sweep requirement + quality
    if cfg.get("require_sweep", True) and not setup["has_sweep"]:
        return False
    if setup["has_sweep"]:
        if setup["sweep_age"] > cfg.get("max_sweep_age", 8):
            return False
        if not (setup["sweep_depth_atr"] >= cfg.get("min_sweep_depth", 0.0)):
            return False
        if not (setup["sweep_close_back_atr"] >= cfg.get("close_back_min", 0.0)):
            return False

    # displacement (off in Y_BAL35)
    if cfg.get("require_disp", False) and not setup["disp_ok"]:
        return False

    # FVG lag / size
    if setup["fvg_lag"] > cfg.get("max_fvg_lag", 8):
        return False
    if not (setup["fvg_size_atr"] >= cfg.get("min_fvg_atr", 0.0)):
        return False
    if setup["fvg_lag"] < cfg.get("min_fvg_lag", -1):
        return False

    # HTF context filter (htf_mode == "none" in the locked config -> recorded only)
    htf_mode = cfg.get("htf_mode", "none")
    htf1 = setup.get("htf1") or 0
    htf2 = setup.get("htf2") or 0
    if htf_mode == "not_oppose":
        if setup["side"] == 1 and htf1 == -1:
            return False
        if setup["side"] == -1 and htf1 == 1:
            return False
    elif htf_mode == "align":
        if setup["side"] == 1 and htf1 != 1:
            return False
        if setup["side"] == -1 and htf1 != -1:
            return False
    elif htf_mode == "htf2_too":
        if setup["side"] == 1 and (htf1 == -1 or htf2 == -1):
            return False
        if setup["side"] == -1 and (htf1 == 1 or htf2 == 1):
            return False

    # premium / discount (only inside the dealing range; z_max = 1.0 -> off)
    z = setup["z"]
    z_max = cfg.get("z_max", 1.0)
    if not math.isnan(z) and 0.0 < z < 1.0:
        if setup["side"] == 1 and z > z_max:
            return False
        if setup["side"] == -1 and z < 1.0 - z_max:
            return False

    # structure precondition ("non_bullish" == unrestricted; "strict" filters)
    if cfg.get("choch_from", "non_bullish") == "strict":
        if setup["side"] == 1 and setup["st_prev"] != -1:
            return False
        if setup["side"] == -1 and setup["st_prev"] != 1:
            return False

    # sessions
    sess = cfg.get("sessions", "all")
    if sess == "no_asia" and setup["session"] == "asia":
        return False
    if sess == "ldn_ov" and setup["session"] not in ("london", "overlap"):
        return False
    if sess == "ldn_all" and setup["session"] not in ("london", "overlap", "outside"):
        return False

    return True


def filter_setups(setups, cfg=None):
    out = [s for s in setups if passes(s, cfg)]
    out.sort(key=lambda x: x["ord_t"])
    return out
