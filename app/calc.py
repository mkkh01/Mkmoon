# -*- coding: utf-8 -*-
"""Pure-python port of the validated volatility / swing primitives (ict_bt/engine.py).

Exact semantics preserved:
  atr_series   : TR then rolling(14, min_periods=1).mean()
  find_swings  : rolling left/right window extremes; strict-left / soft-right ties;
                 pivot at i confirmed at i+k (fully causal)
  body_ratio / range_atr / displacement flags
  session labels from bar OPEN times (Europe/London + America/New_York)
"""
from datetime import timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
NEWYORK = ZoneInfo("America/New_York")
EPS = 1e-12


# ------------------------------------------------------------------ ATR ------
def tr_series(h, l, c):
    n = len(h)
    tr = [0.0] * n
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        pc = c[i - 1]
        tr[i] = max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc))
    return tr


class ATRSeries:
    """Incremental rolling-mean ATR (window 14) — mirrors pandas rolling(n,min_periods=1).mean()."""

    def __init__(self, n=14):
        self.n = n
        self.tr = []          # full history (kept; small)
        self.vals = []

    def add_bar(self, o, h, l, c, prev_c):
        tr = (h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        self.tr.append(tr)
        w = self.tr[-self.n:]
        self.vals.append(sum(w) / len(w))
        return self.vals[-1]

    @property
    def last(self):
        return self.vals[-1] if self.vals else float("nan")

    def at(self, i):
        return self.vals[i]


# ------------------------------------------------------- displacement -------
def body_ratio(o, h, l, c):
    rng = max(h - l, EPS)
    return abs(c - o) / rng


def range_atr(h, l, atr):
    return (h - l) / max(atr, EPS)


def displacement_flags(o, h, l, c, atr):
    br = body_ratio(o, h, l, c)
    ra = range_atr(h, l, atr)
    disp_up = (br >= 0.60) and (ra >= 1.00) and (c > o)
    disp_dn = (br >= 0.60) and (ra >= 1.00) and (c < o)
    return disp_up, disp_dn, br, ra


# ---------------------------------------------------------------- swings -----
class SwingTracker:
    """Incremental find_swings(h, l, k). Pivots confirmed exactly at i+k.

    Rule (engine.find_swings): pivot i is a swing high iff
        h[i] >  max(h[i-k .. i-1])   and   h[i] >= max(h[i+1 .. i+k])
    i.e. strictly extreme on the left, >= on the right. Analogous for lows.
    """

    def __init__(self, k):
        self.k = k
        self.h = []
        self.l = []
        self.pending = []     # confirmed swing dicts in confirmation order

    def add_bar(self, h, l):
        """Feed a CLOSED bar. Returns list of swings confirmed by this close."""
        self.h.append(h)
        self.l.append(l)
        n = len(self.h)
        out = []
        p = n - 1 - self.k              # pivot newly confirmable
        if p - self.k < 0:
            return out
        k = self.k
        hh, ll = self.h, self.l
        left_max = max(hh[p - k:p])
        left_min = min(ll[p - k:p])
        right_max = max(hh[p + 1:p + k + 1])
        right_min = min(ll[p + 1:p + k + 1])
        if hh[p] > left_max and hh[p] >= right_max:
            out.append(("sh", p, hh[p], p + k))
        if ll[p] < left_min and ll[p] <= right_min:
            out.append(("sl", p, ll[p], p + k))
        self.pending.extend(out)
        return out


def amplitude_ok(price, prev_price, atr_at_conf):
    return abs(price - prev_price) / max(atr_at_conf, EPS) >= 0.50


# --------------------------------------------------------------- sessions ----
def session_label(t_open_ms):
    """Labels of engine.session_labels for ONE bar open time. Returns (label, ny_act)."""
    from datetime import datetime
    dt = datetime.fromtimestamp(t_open_ms / 1000.0, tz=timezone.utc)
    lon = dt.astimezone(LONDON)
    ny = dt.astimezone(NEWYORK)
    lh = lon.hour + lon.minute / 60.0
    nh = ny.hour + ny.minute / 60.0
    lon_act = 8 <= lh < 17
    ny_act = 8 <= nh < 17
    if lon_act and ny_act:
        label = "overlap"
    elif lon_act:
        label = "london"
    elif ny_act:
        label = "new_york"
    else:
        label = "outside"
    if dt.hour < 8 and label == "outside":
        label = "asia"
    return label, ny_act


def z_pd(price, lo, hi):
    if hi - lo <= EPS:
        return float("nan")
    return (price - lo) / (hi - lo)
