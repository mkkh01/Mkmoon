# -*- coding: utf-8 -*-
"""LiveDetector — incremental, Causal, pure-python port of the validated event
engine (ict_bt/engine.py: run_trigger_detection + build_setup + structure_series).

Feeding rule: ONLY closed bars. One `add_bar()` call per confirmed candle close
per timeframe per symbol. Emits the same `setup` dicts as the backtest engine
(same keys / same semantics) so that live signals are literally the backtest
signals, streaming.

Everything here is a faithful port — do not "improve" the logic without
re-running the parity test (tests/test_parity.py) and the backtest.
"""
from collections import deque
import math

from .calc import (ATRSeries, SwingTracker, displacement_flags, body_ratio,
                   range_atr, amplitude_ok, session_label, z_pd, EPS)
from . import config as C

NAN = float("nan")


class PoolBook:
    """price-based liquidity proxies (engine.PoolBook)."""
    __slots__ = ("levels",)

    def __init__(self):
        self.levels = []  # price, side(-1 sell-side/+1 buy-side), src, born, status, meta

    def add(self, price, side, src, born, meta=None):
        if price is None or (isinstance(price, float) and not math.isfinite(price)):
            return
        self.levels.append({"price": float(price), "side": side, "src": src,
                            "born": int(born), "status": "fresh", "meta": meta or {}})

    def prune(self, bar_idx, max_age=C.POOL_MAX_AGE, max_per_side=C.POOL_MAX_PER_SIDE):
        self.levels = [x for x in self.levels
                       if x["status"] == "fresh" and (bar_idx - x["born"] <= max_age)]
        for sd in (-1, 1):
            cand = [x for x in self.levels if x["side"] == sd]
            if len(cand) > max_per_side:
                cand.sort(key=lambda x: x["born"])
                for x in cand[:len(cand) - max_per_side]:
                    x["status"] = "expired"
                self.levels = [x for x in self.levels if x["status"] == "fresh"]


class StructureTracker:
    """Incremental structure_series over confirmed swings (kind order preserved)."""

    def __init__(self, atr: ATRSeries):
        self.atr = atr
        self.state = 0
        self.prot_high = NAN
        self.prot_low = NAN
        self.sh_class = 0
        self.sl_class = 0
        self.last_sh = NAN
        self.last_sl = NAN
        self.ext_sh = NAN
        self.ext_sl = NAN
        self._events = []   # pending (conf_b, kind, idx, price)

    def add_event(self, kind, idx, price, conf_b):
        self._events.append((int(conf_b), int(kind), int(idx), float(price)))

    def process_bar(self, b):
        """Process all swing confirmations with conf_b <= b (sorted like engine)."""
        if self._events:
            self._events.sort(key=lambda x: (x[0], x[1], x[2]))
        while self._events and self._events[0][0] <= b:
            conf_b, kind, idx, price = self._events.pop(0)
            atr_ref = self.atr.at(min(conf_b, len(self.atr.vals) - 1))
            if kind == 0:   # swing high
                if not math.isfinite(self.last_sh):
                    self.sh_class = 0
                elif price > self.last_sh:
                    self.sh_class = 1
                elif price < self.last_sh:
                    self.sh_class = -1
                else:
                    self.sh_class = 0
                if (not math.isfinite(self.last_sh)) or amplitude_ok(price, self.last_sh, atr_ref):
                    self.ext_sh = price
                self.last_sh = price
                self.prot_high = price
            else:           # swing low
                if not math.isfinite(self.last_sl):
                    self.sl_class = 0
                elif price > self.last_sl:
                    self.sl_class = 1
                elif price < self.last_sl:
                    self.sl_class = -1
                else:
                    self.sl_class = 0
                if (not math.isfinite(self.last_sl)) or amplitude_ok(price, self.last_sl, atr_ref):
                    self.ext_sl = price
                self.last_sl = price
                self.prot_low = price
            if self.sh_class == 1 and self.sl_class == 1:
                self.state = 1
            elif self.sh_class == -1 and self.sl_class == -1:
                self.state = -1
            else:
                self.state = 0


class LiveDetector:
    """Full causal event engine for ONE timeframe of ONE symbol."""

    def __init__(self, tf_ms, with_setups=True):
        self.tf_ms = tf_ms
        self.with_setups = with_setups
        self.t = []        # open times (ms)
        self.o, self.h, self.l, self.c = [], [], [], []
        self.atr = ATRSeries(14)
        self.sw_int = SwingTracker(C.K_INT)
        self.sw_ext = SwingTracker(C.K_EXT)   # recorded parity (unused by gates)
        self.struct = StructureTracker(self.atr)
        self.book = PoolBook()
        self.cont_lock = {-1: -1, 1: -1}
        self.choch_lock_until = -1
        self.clusters = {-1: [], 1: []}       # equal-level clusters
        self.chains = []
        self.sweeps = []
        self.choch = []
        self.setups = []
        self.last_sh_price = NAN
        self.last_sl_price = NAN
        self.swing_high_hist = []
        self.swing_low_hist = []
        self.prev_day = None
        self.pdh = NAN
        self.pkl = NAN
        self.n_seen = 0

    # ---------------------------------------------------------------- helpers
    @property
    def n(self):
        return len(self.c)

    def atr_at(self, i):
        return self.atr.at(i)

    # ---------------------------------------------------------------- ingest
    def add_bar(self, t_open_ms, o, h, l, c, v=0.0):
        """Feed ONE fully-closed bar (t_open_ms = candle open, ms). Returns
        dict with new sweeps/choch/setups confirmed by THIS close."""
        b = self.n
        self.t.append(int(t_open_ms))
        self.o.append(o); self.h.append(h); self.l.append(l); self.c.append(c)
        t_close = int(t_open_ms) + self.tf_ms
        prev_c = self.c[b - 1] if b > 0 else None
        a = self.atr.add_bar(o, h, l, c, prev_c)
        self.n_seen += 1
        st_prev = self.struct.state     # state after bars <= b-1 (== state[b-1])

        # --- swings confirmed at this close ---
        for (kind, idx, price, conf) in self.sw_int.add_bar(h, l):
            self.struct.add_event(0 if kind == "sh" else 1, idx, price, conf)
        for (kind, idx, price, conf) in self.sw_ext.add_bar(h, l):
            pass  # external swings recorded in tracker only (parity)
        self.struct.process_bar(b)

        # --- displacement flags at this bar ---
        disp_up, disp_dn, br, ra = displacement_flags(o, h, l, c, a)

        # --- previous-day pools at UTC day change (bar open date) ---
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(t_open_ms / 1000.0, tz=timezone.utc).date()
        if self.prev_day is None:
            self.prev_day = d
        elif d != self.prev_day:
            if math.isfinite(self.pdh):
                self.book.add(self.pdh, +1, "prev_day_high", b, {"day": str(self.prev_day)})
            if math.isfinite(self.pkl):
                self.book.add(self.pkl, -1, "prev_day_low", b, {"day": str(self.prev_day)})
            self.pdh, self.pkl = NAN, NAN
            self.prev_day = d
        self.pdh = h if not math.isfinite(self.pdh) else max(self.pdh, h)
        self.pkl = l if not math.isfinite(self.pkl) else min(self.pkl, l)

        # --- new confirmed internal swings become pools (+ equal clusters) ---
        self._update_pools_at_confirmation(b)
        self.book.prune(b)

        delta = C.BREAK_DELTA_ATR * a
        pen_base = C.SWEEP_PEN_ATR * a

        # ---- sweep detection (§10) ----
        new_sweeps = []
        for p in self.book.levels:
            if p["status"] != "fresh":
                continue
            L = p["price"]
            if p["side"] == -1:        # sell-side: bullish sweep
                if l < L - pen_base:
                    if c > L:
                        p["status"] = "swept"
                        new_sweeps.append({
                            "b": b, "side": +1, "level": L, "src": p["src"],
                            "depth_atr": (L - l) / max(a, EPS),
                            "close_back_atr": (c - L) / max(a, EPS),
                            "sweep_low": l, "sweep_high": NAN, "t": t_close,
                        })
                    elif c < L:
                        p["status"] = "broken"
            else:                       # buy-side: bearish sweep
                if h > L + pen_base:
                    if c < L:
                        p["status"] = "swept"
                        new_sweeps.append({
                            "b": b, "side": -1, "level": L, "src": p["src"],
                            "depth_atr": (h - L) / max(a, EPS),
                            "close_back_atr": (L - c) / max(a, EPS),
                            "sweep_low": NAN, "sweep_high": h, "t": t_close,
                        })
                    elif c > L:
                        p["status"] = "broken"
        self.sweeps.extend(new_sweeps)

        session, ny_act = session_label(self.t[b])
        z = z_pd(c, self.struct.ext_sl, self.struct.ext_sh)

        # ---- continuation chains: sweep -> FVG pullback (§17 Setup C) ----
        for s_ in new_sweeps:
            sd = s_["side"]
            if b - self.cont_lock[sd] < C.CONT_LOCK_BARS:
                continue
            self.cont_lock[sd] = b
            swing_ref_c = self.last_sl_price if sd == 1 else self.last_sh_price
            self.chains.append({
                "side": sd, "m": b, "st_prev": st_prev,
                "sweep": s_, "swing_ref": swing_ref_c,
                "disp_same": bool(disp_up if sd == 1 else disp_dn),
                "br": float(br), "ra": float(ra),
                "atr": float(a), "z": float(z),
                "session": session, "ny": bool(ny_act),
                "htf1": None, "htf2": None, "open": True,
                "ctype": "cont",
            })

        # ---- CHOCH (§7) ----
        bull_choch = (st_prev != 1) and math.isfinite(self.struct.prot_high) and \
            (c > self.struct.prot_high + delta)
        bear_choch = (st_prev != -1) and math.isfinite(self.struct.prot_low) and \
            (c < self.struct.prot_low - delta)
        if b >= self.choch_lock_until and (bull_choch or bear_choch):
            side = +1 if bull_choch else -1
            self.choch.append({"b": b, "side": side, "st_prev": st_prev, "t": t_close})
            self.choch_lock_until = b + C.CHOCH_LOCK_BARS
            self.chains = [ch for ch in self.chains
                           if not (ch["ctype"] == "cont" and ch["m"] == b and ch["side"] == side)]
            swp = None
            for s_ in new_sweeps:
                if s_["side"] == side:
                    swp = s_
            if swp is None:
                for s_ in reversed(self.sweeps):
                    if s_["side"] == side and 0 <= b - s_["b"] <= C.W1:
                        swp = s_
                        break
            swing_ref = self.last_sl_price if side == 1 else self.last_sh_price
            self.chains.append({
                "side": side, "m": b, "st_prev": st_prev,
                "sweep": swp, "swing_ref": swing_ref,
                "disp_same": bool(disp_up if side == 1 else disp_dn),
                "br": float(br), "ra": float(ra),
                "atr": float(a), "z": float(z),
                "session": session, "ny": bool(ny_act),
                "htf1": None, "htf2": None, "open": True,
                "ctype": "mss",
            })

        # ---- attach FVG to open chains (§12) & emit setups ----
        out_setups = []
        if self.with_setups:
            for ch in self.chains:
                if not ch["open"]:
                    continue
                if b - ch["m"] > C.W2:
                    ch["open"] = False
                    continue
                if b < 2:
                    continue
                fbar = b - 1
                if not (ch["m"] - 1 <= fbar <= ch["m"] + C.W2):
                    continue
                lo, hi, sa = self._fvg_at(fbar, ch["side"])
                if lo is None:
                    continue
                setup = self._build_setup(ch, b, fbar, ch["side"], lo, hi, sa)
                out_setups.append(setup)
                self.setups.append(setup)
                ch["open"] = False
        self.chains = [ch for ch in self.chains if ch["open"]]

        return {"sweeps": new_sweeps, "setups": out_setups,
                "atr": a, "state": self.struct.state}

    # ------------------------------------------------------------- internals
    def _fvg_at(self, g, side):
        """Three-candle FVG formed at middle bar g (engine.detect_fvg)."""
        if g < 1 or g + 1 >= self.n:
            return None, None, None
        atr_g = self.atr.at(g)
        if side == 1:
            if self.l[g + 1] > self.h[g - 1]:
                lo, hi = self.h[g - 1], self.l[g + 1]
                return lo, hi, (hi - lo) / max(atr_g, EPS)
        else:
            if self.h[g + 1] < self.l[g - 1]:
                lo, hi = self.h[g + 1], self.l[g - 1]
                return lo, hi, (hi - lo) / max(atr_g, EPS)
        return None, None, None

    def _update_pools_at_confirmation(self, b):
        eq_tol = C.EQ_TOL_ATR
        a = self.atr.at(b)
        # swings confirmed exactly at b (SwingTracker.pending tail with conf == b)
        evs = [e for e in self.sw_int.pending if e[3] == b]
        for (kind, src_idx, price, conf) in evs:
            if kind == "sh":
                self.book.add(price, +1, "swing_high", b, {"src_idx": src_idx})
                self.last_sh_price = price
                self.swing_high_hist.append((b, price))
                self._cluster_join(+1, price, b, a, "eq_high")
            else:
                self.book.add(price, -1, "swing_low", b, {"src_idx": src_idx})
                self.last_sl_price = price
                self.swing_low_hist.append((b, price))
                self._cluster_join(-1, price, b, a, "eq_low")

    def _cluster_join(self, side, price, b, a, eq_src):
        cl = self.clusters[side]
        joined = None
        for cc in cl:
            if abs(price - cc["price"]) / max(a, EPS) <= C.EQ_TOL_ATR:
                cc["members"].append(price)
                cc["members"].sort()
                m = cc["members"]
                mid = m[len(m) // 2] if len(m) % 2 else 0.5 * (m[len(m) // 2 - 1] + m[len(m) // 2])
                cc["price"] = float(mid)
                joined = cc
                break
        if joined is None:
            cl.append({"members": [price], "price": price, "born": b})
            if len(cl) > 5:
                cl.pop(0)
        elif len(joined["members"]) == 2:
            self.book.add(joined["price"], side, eq_src, b, {"n": 2})

    def _build_setup(self, ch, conf_b, fbar, side, lo, hi, sa):
        m = ch["m"]
        swp = ch["sweep"]
        a_m = self.atr.at(m)
        disp_ok = ch["disp_same"]
        if m + 1 <= conf_b and ((side == 1 and self._disp_at(m + 1, 1)) or
                                (side == -1 and self._disp_at(m + 1, -1))):
            disp_ok = True
        sweep_extreme = NAN
        if swp is not None:
            sweep_extreme = swp.get("sweep_low", NAN) if side == 1 else swp.get("sweep_high", NAN)
        return {
            "side": side,
            "m": m, "fvg_bar": fbar, "conf_b": conf_b,
            "signal_t": int(self.t[m]) + self.tf_ms,       # MSS decision time (t_close[m])
            "ord_t": int(self.t[conf_b]) + self.tf_ms,     # order live (FVG confirmed)
            "fvg_lo": lo, "fvg_hi": hi, "fvg_size_atr": sa,
            "fvg_lag": fbar - m,
            "ctype": ch.get("ctype", "mss"),
            "has_sweep": swp is not None,
            "sweep_b": swp["b"] if swp else -1,
            "sweep_t": swp["t"] if swp else -1,
            "sweep_depth_atr": swp["depth_atr"] if swp else NAN,
            "sweep_close_back_atr": swp["close_back_atr"] if swp else NAN,
            "sweep_extreme": sweep_extreme,
            "sweep_src": swp["src"] if swp else "",
            "sweep_age": (m - swp["b"]) if swp else -1,
            "swing_ref": ch["swing_ref"],
            "disp_ok": bool(disp_ok),
            "disp_body_ratio": ch["br"], "disp_range_atr": ch["ra"],
            "st_prev": ch["st_prev"],
            "atr_mss": float(a_m),
            "z": ch["z"], "session": ch["session"], "ny": ch["ny"],
            "htf1": ch.get("htf1"), "htf2": ch.get("htf2"),
            "entry_bar_t": int(self.t[conf_b]) + self.tf_ms,
        }

    def _disp_at(self, i, side):
        if i >= self.n:
            return False
        o, h, l, c = self.o[i], self.h[i], self.l[i], self.c[i]
        br = body_ratio(o, h, l, c)
        ra = range_atr(h, l, self.atr.at(i))
        if side == 1:
            return br >= 0.60 and ra >= 1.00 and c > o
        return br >= 0.60 and ra >= 1.00 and c < o


class HTFStructure:
    """Minimal structure-state tracker for HTF context (1h/4h) — mirrors
    engine.htf_state_map (swings k=2 + structure_series)."""

    def __init__(self, tf_ms):
        self.tf_ms = tf_ms
        self.atr = ATRSeries(14)
        self.sw = SwingTracker(C.K_INT)
        self.struct = StructureTracker(self.atr)
        self._pc = None

    def add_bar(self, t_open_ms, o, h, l, c):
        b = len(self.atr.vals)
        prev_c = self._pc
        self.atr.add_bar(o, h, l, c, prev_c)
        self._pc = c
        for (kind, idx, price, conf) in self.sw.add_bar(h, l):
            self.struct.add_event(0 if kind == "sh" else 1, idx, price, conf)
        self.struct.process_bar(b)

    @property
    def state(self):
        return self.struct.state
