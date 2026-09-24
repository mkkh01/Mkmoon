# -*- coding: utf-8 -*-
"""PaperTrader — executes the LOCKED strategy with REAL market prices.

Faithful live port of engine.simulate_setups (Y_BAL35) with the same pessimistic
ambiguity rules, same cost model and same exit taxonomy — but streaming:

  * resting limit orders (FVG mid), lifetime 6h, cancel on 1m CLOSE beyond the
    FVG edge (inv_atr=0) — same-bar touch+close-below == CANCEL (pessimistic);
  * positions managed with: instant stop (loss-first), TP (not credited inside
    the fill 1m bar), MFE>=0.35R -> profit lock (entry+0.30R, retro-checked on
    the locking bar close), early abort on 1m CLOSE beyond the FVG edge,
    24h time stop;
  * virtual equity compounding in PERCENT: equity *= (1 + risk_pct/100 * net_R).

Exit reasons (Arabic labels in config.REASON_AR):
  tp, sl_lock, sl, abort, time  (+ order-level: expired, cancelled)
"""
import math
import time

from . import config as C

NAN = float("nan")


def _finite(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


class Order:
    __slots__ = ("id", "symbol", "tier", "side", "setup", "limit", "stop0", "cancel_line",
                 "tp", "R0", "placed_t", "ord_t_ms", "expire_ms", "bar_t", "bar_l", "bar_h",
                 "bar_c", "bar_o", "status", "note")

    def __init__(self, oid, symbol, tier, setup, limit, stop0, cancel_line, tp, R0, now_ms):
        self.id = oid
        self.symbol = symbol
        self.tier = tier
        self.side = setup["side"]
        self.setup = setup
        self.limit = limit
        self.stop0 = stop0
        self.cancel_line = cancel_line
        self.tp = tp
        self.R0 = R0
        self.placed_t = now_ms
        self.ord_t_ms = setup["ord_t"]
        self.expire_ms = setup["ord_t"] + C.CFG["order_bars"] * C.TRIG_MS  # 6h
        self.reset_bar(setup["ord_t"])
        self.status = "resting"    # resting | filled | cancelled | expired
        self.note = ""

    def reset_bar(self, bar_open_ms):
        self.bar_t = bar_open_ms
        self.bar_o = NAN
        self.bar_l = NAN
        self.bar_h = NAN
        self.bar_c = NAN


class Position:
    __slots__ = ("id", "symbol", "tier", "side", "order_id", "entry_t", "entry_px",
                 "stop", "locked_stop", "locked", "tp", "R", "cancel_line", "atr_m",
                 "fill_bar", "fill_bar_h", "fill_bar_l", "mfe", "mae", "amb",
                 "exit_t", "exit_px", "exit_reason", "bar_t", "bar_o", "bar_h", "bar_l",
                 "bar_c", "expire_t", "setup", "lock_bar_done",
                 "leg", "leg_ar", "mfe_trig_r", "lock_r", "risk_pct")

    def __init__(self, pid, symbol, tier, order, entry_px, now_ms, bar_open_ms,
                 leg=None):
        cfg = C.CFG
        leg = leg or {}
        self.id = pid
        self.symbol = symbol
        self.tier = tier
        self.side = order.side
        self.order_id = order.id
        self.leg = leg.get("id", "")
        self.leg_ar = leg.get("ar", "")
        self.mfe_trig_r = float(leg.get("mfe_trig_r", cfg["mfe_trig_r"]))
        self.lock_r = float(leg.get("lock_r", cfg["lock_r"]))
        self.risk_pct = cfg["risk_pct"] * float(leg.get("weight", 1.0))
        self.entry_t = bar_open_ms          # engine: entry_t = 1m bar open of the fill bar
        self.entry_px = entry_px
        self.stop = order.stop0
        self.locked_stop = entry_px + (self.lock_r * order.R0 if order.side == 1
                                       else -self.lock_r * order.R0)
        self.locked = False
        self.lock_bar_done = -1
        self.R = order.R0
        tp_r = float(leg.get("tp_r", cfg["tp_r"]))
        self.tp = entry_px + (tp_r * order.R0 if order.side == 1
                              else -tp_r * order.R0)
        self.cancel_line = order.cancel_line
        self.atr_m = order.setup["atr_mss"]
        self.fill_bar = bar_open_ms
        self.fill_bar_h = NAN
        self.fill_bar_l = NAN
        self.mfe = 0.0
        self.mae = 0.0
        self.amb = 0
        self.exit_t = None
        self.exit_px = None
        self.exit_reason = None
        self.bar_t = bar_open_ms
        self.bar_o = NAN
        self.bar_h = NAN
        self.bar_l = NAN
        self.bar_c = NAN
        self.expire_t = self.entry_t + int(cfg["max_hold_h"] * 3_600_000)
        self.setup = order.setup


class PaperTrader:
    """One instance for the whole app. Tier B is shadow-tracked (paper too but
    clearly labelled and excluded from 'recommendations')."""

    def __init__(self, notifier=None, ledger=None, tier_b_enabled=True):
        cfg = C.CFG
        self.cfg = cfg
        self.notifier = notifier          # async callable(event: str, payload: dict)
        self.ledger = ledger              # async sink (store)
        self.tier_b_enabled = tier_b_enabled
        self.orders = {}                  # id -> Order
        self.positions = {}               # id -> Position
        self.closed = []                  # finished trade dicts (in-memory ring)
        self.signals = []                 # all emitted setups (ring)
        self.busy_until = {}              # symbol -> exit_ms of last trade
        self.start_equity = 100.0         # percent-compounding virtual equity
        self.equity = 100.0
        self.peak_equity = 100.0
        self.max_dd_pct = 0.0
        self.wins = 0
        self.losses = 0
        self.sum_net_R = 0.0
        self.n_orders_placed = 0
        self.n_fills = 0
        self.n_cancels = 0
        self._oid = 0
        self._pid = 0
        self._last_bar_minute = {}        # symbol -> current 1m bucket (active only)
        self.events = []                  # recent lifecycle events (ring)

    # ------------------------------------------------------------- utilities
    def _next_oid(self):
        self._oid += 1
        return f"O{self._oid:05d}"

    def _next_pid(self):
        self._pid += 1
        return f"T{self._pid:05d}"

    def active_symbols(self):
        s = {o.symbol for o in self.orders.values() if o.status == "resting"}
        s |= {p.symbol for p in self.positions.values() if p.exit_t is None}
        return sorted(s)

    def snapshot(self):
        return {
            "equity_pct": round(self.equity, 4),
            "peak_pct": round(self.peak_equity, 4),
            "max_dd_pct": round(self.max_dd_pct, 3),
            "wins": self.wins, "losses": self.losses,
            "sum_net_R": round(self.sum_net_R, 3),
            "n_orders_placed": self.n_orders_placed,
            "n_fills": self.n_fills, "n_cancels": self.n_cancels,
            "open_positions": len([p for p in self.positions.values() if p.exit_t is None]),
            "resting_orders": len([o for o in self.orders.values() if o.status == "resting"]),
        }

    def _log_event(self, ev, payload):
        self.events.append({"t": int(time.time() * 1000), "ev": ev, **payload})
        if len(self.events) > 500:
            self.events = self.events[-300:]

    async def _notify(self, ev, payload):
        self._log_event(ev, payload)
        if self.notifier:
            try:
                await self.notifier(ev, payload)
            except Exception:
                pass

    async def _persist(self, kind, row):
        if self.ledger:
            try:
                await self.ledger(kind, row)
            except Exception:
                pass

    # ------------------------------------------------------ signal -> orders
    async def on_setup(self, setup, symbol, tier, now_ms):
        """Called at 15m close when a setup survives the gates + R band."""
        lo, hi = setup["fvg_lo"], setup["fvg_hi"]
        side = setup["side"]
        cfg = self.cfg
        if side == 1:
            limit = lo + cfg["entry_frac"] * (hi - lo)
        else:
            limit = hi - cfg["entry_frac"] * (hi - lo)
        atr_m = setup["atr_mss"]
        stop_ref = setup["sweep_extreme"] if _finite(setup["sweep_extreme"]) else setup["swing_ref"]
        if not _finite(stop_ref):
            await self._notify("setup_rejected", {"symbol": symbol, "why": "no_stop_ref", "side": side})
            return None
        stop0 = (stop_ref - cfg["sl_buf_atr"] * atr_m) if side == 1 else \
                (stop_ref + cfg["sl_buf_atr"] * atr_m)
        R0 = (limit - stop0) if side == 1 else (stop0 - limit)
        if not _finite(R0) or R0 <= 0:
            return None
        min_r = cfg["min_r_pct"] / 100.0 * limit
        max_r = cfg["max_r_pct"] / 100.0 * limit
        if R0 < min_r or R0 > max_r:
            self._log_event("setup_band_reject", {"symbol": symbol, "R_pct": 100 * R0 / limit})
            return None

        # one active trade / order chain per symbol (validated busy rule)
        if any(p.symbol == symbol and p.exit_t is None for p in self.positions.values()):
            return None
        bu = self.busy_until.get(symbol, -1)
        if setup["ord_t"] <= bu:
            return None
        resting = [o for o in self.orders.values()
                   if o.symbol == symbol and o.status == "resting"]
        if len(resting) >= 2:            # bounded concurrency (rare)
            return None

        cancel_line = (lo - cfg["inv_atr"] * atr_m) if side == 1 else (hi + cfg["inv_atr"] * atr_m)
        tp = limit + (cfg["tp_r"] * R0 if side == 1 else -cfg["tp_r"] * R0)
        order = Order(self._next_oid(), symbol, tier, setup, limit, stop0, cancel_line,
                      tp, R0, now_ms)
        self.orders[order.id] = order
        self.n_orders_placed += 1
        self.signals.append({
            "t": now_ms, "symbol": symbol, "tier": tier, "side": side,
            "ctype": setup["ctype"], "signal_t": setup["signal_t"], "ord_t": setup["ord_t"],
            "limit": limit, "stop": stop0, "tp": tp, "R0": R0,
            "sweep_src": setup["sweep_src"], "session": setup["session"],
            "fvg_lo": lo, "fvg_hi": hi, "atr": atr_m, "htf1": setup.get("htf1"),
            "htf2": setup.get("htf2"), "disp_ok": setup["disp_ok"], "z": setup["z"],
        })
        if len(self.signals) > 400:
            self.signals = self.signals[-250:]
        await self._persist("signal", dict(self.signals[-1], order_id=order.id))
        await self._notify("order_placed", {
            "order_id": order.id, "symbol": symbol, "tier": tier, "side": side,
            "limit": limit, "stop": stop0, "tp": tp, "R0": R0, "risk_pct": cfg["risk_pct"],
            "ctype": setup["ctype"], "entry_pct": 0.0,
            "tp_pct": cfg["risk_pct"] * cfg["tp_r"],
            "expire_ms": order.expire_ms,
        })
        await self._persist("order", {"order_id": order.id, "symbol": symbol, "tier": tier,
                                      "side": side, "limit": limit, "stop": stop0, "tp": tp,
                                      "R0": R0, "placed_t": now_ms, "expire_ms": order.expire_ms,
                                      "status": "resting", "signal_t": setup["signal_t"]})
        return order

    # ------------------------------------------------------------ fast tick
    async def on_price(self, symbol, bid, ask, now_ms, last_minute_bucket):
        """3s tick: (bid, ask) for an ACTIVE symbol. Manages orders+positions.
        Conservative fill/trigger policy: longs transact on the bid side."""
        for o in [o for o in self.orders.values()
                  if o.symbol == symbol and o.status == "resting"]:
            await self._order_tick(o, bid, ask, now_ms, last_minute_bucket)
        for p in [p for p in self.positions.values()
                  if p.symbol == symbol and p.exit_t is None]:
            await self._position_tick(p, bid, ask, now_ms, last_minute_bucket)

    async def _order_tick(self, o, bid, ask, now_ms, bucket):
        px_touch = bid if o.side == 1 else ask      # conservative executable side
        bar_open = bucket * 60_000
        if bar_open != o.bar_t:
            # 1m bar just closed -> fill / cancel decision (engine semantics)
            await self._order_bar_close(o, now_ms)
            if o.status != "resting":
                return
            o.reset_bar(bar_open)
        if math.isnan(o.bar_o):
            o.bar_o = px_touch
        o.bar_l = px_touch if math.isnan(o.bar_l) else min(o.bar_l, px_touch)
        o.bar_h = px_touch if math.isnan(o.bar_h) else max(o.bar_h, px_touch)
        o.bar_c = px_touch
        if now_ms >= o.expire_ms:
            o.status = "expired"
            o.note = "expired"
            self.n_cancels += 1
            await self._notify("order_expired", {"order_id": o.id, "symbol": o.symbol,
                                                 "tier": o.tier, "side": o.side})
            await self._persist("order", {"order_id": o.id, "status": "expired",
                                          "closed_t": now_ms})
            return
        # touch detection — instant mode fills at the touch (bare limit order);
        # bar_close mode (validated §21 pessimistic) defers the decision to close
        if o.side == 1 and o.bar_l <= o.limit:
            o.note = "touch"
            if self.cfg.get("fill_mode") == "instant":
                await self._fill(o, o.limit, bar_open)
                return
        elif o.side == -1 and o.bar_h >= o.limit:
            o.note = "touch"
            if self.cfg.get("fill_mode") == "instant":
                await self._fill(o, o.limit, bar_open)
                return

    async def _order_bar_close(self, o, now_ms):
        """Engine fill-scan rules on the closed 1m bar (pessimistic)."""
        if o.status != "resting":
            return
        side = o.side
        lo, hi = o.setup["fvg_lo"], o.setup["fvg_hi"]
        touched = o.note == "touch" and not math.isnan(o.bar_l)
        if side == 1:
            cancelled = (not math.isnan(o.bar_c)) and o.bar_c < o.cancel_line
        else:
            cancelled = (not math.isnan(o.bar_c)) and o.bar_c > o.cancel_line
        if touched and cancelled:
            # same bar touch + close beyond edge -> CANCEL, no fill (pessimistic)
            o.status = "cancelled"
            o.note = "invalid"
            self.n_cancels += 1
            await self._notify("order_cancelled", {"order_id": o.id, "symbol": o.symbol,
                                                   "tier": o.tier, "side": side,
                                                   "reason": C.REASON_AR["cancelled"]})
            await self._persist("order", {"order_id": o.id, "status": "cancelled",
                                          "closed_t": o.bar_t + 60_000})
            return
        if cancelled and not touched:
            o.status = "cancelled"
            o.note = "invalid"
            self.n_cancels += 1
            await self._notify("order_cancelled", {"order_id": o.id, "symbol": o.symbol,
                                                   "tier": o.tier, "side": side,
                                                   "reason": C.REASON_AR["cancelled"]})
            await self._persist("order", {"order_id": o.id, "status": "cancelled",
                                          "closed_t": o.bar_t + 60_000})
            return
        if touched and not cancelled:
            await self._fill(o, o.limit, o.bar_t)

    async def _fill(self, o, px, bar_open_ms):
        o.status = "filled"
        self.n_fills += 1
        # validated busy rule: cancel other resting orders of this symbol
        for other in [x for x in self.orders.values()
                      if x.symbol == o.symbol and x.id != o.id and x.status == "resting"]:
            other.status = "cancelled"
            other.note = "superseded"
            self.n_cancels += 1
            await self._persist("order", {"order_id": other.id, "status": "cancelled",
                                          "closed_t": bar_open_ms})
        legs = C.CFG.get("legs") or ({"id": "", "ar": "", "weight": 1.0},)
        created = []
        for leg in legs:
            pos = Position(self._next_pid(), o.symbol, o.tier, o, px,
                           bar_open_ms, bar_open_ms, leg=leg)
            # carry the fill-bar extremes tracked on the order (§21 fill-bar rules)
            pos.fill_bar_h = o.bar_h
            pos.fill_bar_l = o.bar_l
            pos.bar_o = o.bar_o if not math.isnan(o.bar_o) else px
            pos.bar_h = o.bar_h
            pos.bar_l = o.bar_l
            pos.bar_c = o.bar_c
            # §21 fill-bar semantics: the fill bar ran through the ORDER path — seed
            # MFE/MAE from its extremes and evaluate the lock trigger here (engine:
            # phase-B locked stop is active ON the locking bar = retro).
            side = pos.side
            if not math.isnan(o.bar_h) and not math.isnan(o.bar_l):
                if side == 1:
                    fav_m = (o.bar_h - px) / pos.R
                    adv_m = (o.bar_l - px) / pos.R
                    tp_touch = o.bar_h >= pos.tp
                else:
                    fav_m = (px - o.bar_l) / pos.R
                    adv_m = (px - o.bar_h) / pos.R
                    tp_touch = o.bar_l <= pos.tp
                pos.mfe = max(pos.mfe, fav_m)
                pos.mae = min(pos.mae, adv_m)
                if tp_touch:
                    pos.amb += 1          # fill-bar TP touch not credited (§21)
            if (not pos.locked) and pos.mfe >= pos.mfe_trig_r:
                pos.locked = True
                pos.lock_bar_done = pos.bar_t
            self.positions[f"{o.symbol}#{pos.leg or 'main'}"] = pos
            created.append(pos)
            await self._persist("trade", {
                "trade_id": pos.id, "order_id": o.id, "symbol": o.symbol,
                "tier": pos.tier, "side": pos.side, "entry_t": pos.entry_t,
                "entry_px": px, "stop": pos.stop, "locked_stop": pos.locked_stop,
                "tp": pos.tp, "R": pos.R, "leg": pos.leg, "leg_ar": pos.leg_ar,
                "risk_pct": pos.risk_pct,
                "status": "open", "signal_t": o.setup["signal_t"],
                "ctype": o.setup["ctype"],
            })
        legs_txt = " | ".join(
            f"{p.leg_ar} {p.tp:+.4g} (قفل {p.locked_stop:+.4g})" for p in created)
        await self._notify("trade_opened", {
            "trade_id": "+".join(p.id for p in created), "order_id": o.id,
            "symbol": o.symbol, "tier": o.tier,
            "side": created[0].side, "entry_px": px, "entry_t": created[0].entry_t,
            "stop": created[0].stop, "tp": created[-1].tp, "R": created[0].R,
            "risk_pct": self.cfg["risk_pct"],
            "tp_pct": self.cfg["risk_pct"] * float(C.CFG.get("legs", [{}])[-1].get("tp_r", 1.0)),
            "ctype": o.setup["ctype"],
            "legs_txt": legs_txt,
        })

    # --------------------------------------------------------- position tick
    async def _position_tick(self, p, bid, ask, now_ms, bucket):
        px = bid if p.side == 1 else ask           # executable side (conservative)
        bar_open = bucket * 60_000
        if bar_open != p.bar_t:
            await self._position_bar_close(p, now_ms)
            if p.exit_t is not None:
                return
            p.bar_t = bar_open
            p.bar_o = NAN
            p.bar_h = NAN
            p.bar_l = NAN
            p.bar_c = NAN
        if math.isnan(p.bar_o):
            p.bar_o = px
        p.bar_h = px if math.isnan(p.bar_h) else max(p.bar_h, px)
        p.bar_l = px if math.isnan(p.bar_l) else min(p.bar_l, px)
        p.bar_c = px

        side = p.side
        R = p.R
        fav = (px - p.entry_px) / R if side == 1 else (p.entry_px - px) / R
        p.mfe = max(p.mfe, fav)
        # adverse = running extreme against us, in R (engine: min of (low-entry)/R)
        adv = (px - p.entry_px) / R if side == 1 else (p.entry_px - px) / R
        p.mae = min(p.mae, adv)

        # --- instant SL (loss-first, pessimistic same-tick) ---
        active_stop = p.locked_stop if p.locked else p.stop
        hit_sl = (px <= active_stop) if side == 1 else (px >= active_stop)
        # --- instant TP (NOT credited inside the fill 1m bar) ---
        hit_tp = (px >= p.tp) if side == 1 else (px <= p.tp)
        in_fill_bar = (p.bar_t == p.fill_bar)
        if hit_sl:
            await self._close(p, px, now_ms,
                              "sl_lock" if p.locked else "sl", stop_level=active_stop)
            return
        if hit_tp:
            if in_fill_bar:
                p.amb += 1          # fill-bar TP touch not credited (§21 pessimistic)
            else:
                await self._close(p, p.tp, now_ms, "tp", stop_level=None)
                return

        # --- MFE lock trigger (instant at the touch tick; retro at bar close) ---
        if (not p.locked) and p.mfe >= p.mfe_trig_r:
            p.locked = True
            p.lock_bar_done = p.bar_t

    async def _position_bar_close(self, p, now_ms):
        """Bar-close layer: abort check, lock retro-check, time stop (engine rules)."""
        if p.exit_t is not None:
            return
        side = p.side
        c = p.bar_c
        # 1) early abort at 1m CLOSE beyond the FVG edge (no sl/tp on this bar)
        if self.cfg["early_abort"] and not math.isnan(c):
            if side == 1:
                aborted = c < p.cancel_line
            else:
                aborted = c > p.cancel_line
            if aborted:
                active_stop = p.locked_stop if p.locked else p.stop
                hit_stop = (p.bar_l <= active_stop) if side == 1 else (p.bar_h >= active_stop)
                hit_tp = (p.bar_h >= p.tp) if side == 1 else (p.bar_l <= p.tp)
                if not hit_stop and not hit_tp:
                    await self._close(p, c, now_ms, "abort", stop_level=None)
                    return
        # 2) lock retro-check on the locking bar (engine: locked stop applies to
        #    the whole MFE-trigger bar, pessimistic in-bar ordering)
        if p.locked and p.lock_bar_done == p.bar_t:
            active_stop = p.locked_stop
            hit = (p.bar_l <= active_stop) if side == 1 else (p.bar_h >= active_stop)
            if hit:
                # engine: sl at locked stop unless an earlier phase-A event existed
                # (exit price = worst of level vs bar OPEN — engine gap rule)
                hit_stop0 = (p.bar_l <= p.stop) if side == 1 else (p.bar_h >= p.stop)
                if hit_stop0:
                    await self._close(p, p.bar_o, now_ms,
                                      "sl", stop_level=p.stop)
                else:
                    await self._close(p, p.bar_o, now_ms,
                                      "sl_lock", stop_level=active_stop)
                return
        # 3) time stop: exit at close of the last bar inside the 24h window
        if p.bar_t + 60_000 >= p.expire_t:
            await self._close(p, c if not math.isnan(c) else p.entry_px, now_ms,
                              "time", stop_level=None)
            return
        # keep MFE in sync with bar extremes (retro lock for slow feeds)
        if not p.locked:
            bar_fav = ((p.bar_h - p.entry_px) if side == 1 else (p.entry_px - p.bar_l)) / p.R
            if not math.isnan(bar_fav) and bar_fav >= p.mfe_trig_r:
                p.locked = True
                # retro: locked stop applies to THIS bar too
                hit = (p.bar_l <= p.locked_stop) if side == 1 else (p.bar_h >= p.locked_stop)
                if hit:
                    await self._close(p, p.bar_o, now_ms,
                                      "sl_lock", stop_level=p.locked_stop)

    # ---------------------------------------------------------------- close
    async def _close(self, p, exit_px_raw, now_ms, reason, stop_level=None):
        cfg = self.cfg
        side = p.side
        slip_x = cfg["slip_exit_bps"] / 1e4
        fee_e = cfg["entry_fee_bps"] / 1e4
        fee_x = cfg["exit_fee_bps"] / 1e4
        # pessimistic gap handling on stop exits (engine: worse of level vs open)
        if stop_level is not None:
            exit_px_raw = (min(stop_level, exit_px_raw) if side == 1
                           else max(stop_level, exit_px_raw))
        fill_px = p.entry_px * (1 + cfg["slip_entry_bps"] / 1e4 * side)
        exit_px = exit_px_raw * (1 - slip_x * side)
        gross = (exit_px - fill_px) * side
        fees = (fill_px * fee_e + exit_px * fee_x)
        net = gross - fees
        R = p.R
        gross_R = gross / R
        net_R = net / R
        p.exit_t = now_ms
        p.exit_px = exit_px
        p.exit_reason = reason

        # virtual percent-compounding equity (§23 fixed-fractional, per-leg risk)
        self.equity *= (1.0 + (p.risk_pct / 100.0) * net_R)
        self.peak_equity = max(self.peak_equity, self.equity)
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100.0
        self.max_dd_pct = max(self.max_dd_pct, dd)
        if net_R > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.sum_net_R += net_R
        self.busy_until[p.symbol] = now_ms

        hold_h = (now_ms - p.entry_t) / 3.6e6
        row = {
            "trade_id": p.id, "order_id": p.order_id, "symbol": p.symbol, "tier": p.tier,
            "side": side, "entry_t": p.entry_t, "entry_px": p.entry_px,
            "stop": p.stop, "locked_stop": p.locked_stop, "tp": p.tp, "R": R,
            "exit_t": now_ms, "exit_px": exit_px, "exit_reason": reason,
            "gross_R": gross_R, "net_R": net_R, "fees": fees,
            "mfe_R": p.mfe, "mae_R": p.mae, "hold_h": hold_h, "ambiguous": p.amb,
            "equity_pct": self.equity, "locked": p.locked,
            "signal_t": p.setup["signal_t"], "ctype": p.setup["ctype"],
            "session": p.setup["session"], "status": "closed",
            "leg": p.leg, "leg_ar": p.leg_ar, "risk_pct": p.risk_pct,
        }
        self.closed.append(row)
        if len(self.closed) > 300:
            self.closed = self.closed[-200:]
        await self._persist("trade", row)
        await self._notify("trade_closed", row)

    # ------------------------------------------------------- order expiry at cycle
    async def on_cycle(self, now_ms):
        for o in [o for o in self.orders.values() if o.status == "resting"]:
            if now_ms >= o.expire_ms:
                o.status = "expired"
                self.n_cancels += 1
                await self._notify("order_expired", {"order_id": o.id, "symbol": o.symbol,
                                                     "tier": o.tier, "side": o.side})
                await self._persist("order", {"order_id": o.id, "status": "expired",
                                              "closed_t": now_ms})

    def mark_price(self, symbol, px):
        """Mark-to-market PnL% of open position (informational)."""
        p = next((p for p in self.positions.values()
                  if p.symbol == symbol and p.exit_t is None), None)
        if p is None or px is None:
            return None
        R = p.R
        fav = (px - p.entry_px) / R if p.side == 1 else (p.entry_px - px) / R
        return {"trade_id": p.id, "symbol": symbol, "side": p.side, "tier": p.tier,
                "entry_px": p.entry_px, "px": px, "pnl_R": round(fav, 3),
                "pnl_pct": round(fav * p.risk_pct, 3),
                "stop": p.locked_stop if p.locked else p.stop, "tp": p.tp,
                "entry_t": p.entry_t, "locked": p.locked, "leg_ar": p.leg_ar}
