# -*- coding: utf-8 -*-
"""Download a recent 1m window from data.binance.vision for parity testing.

Output: /home/user/ict_bt/data/parity/{SYM}/*.csv.gz  (kept as zips + parsed CSVs)
Run: python tests/fetch_parity_data.py
"""
import concurrent.futures as cf
import io
import os
import sys
import urllib.request
import zipfile

OUT = "/home/user/ict_bt/data/parity"
SYMS = ["BTCUSDT", "SOLUSDT", "XRPUSDT", "ETHUSDT"]
MONTHS = ["2026-06", "2026-07", "2026-08"]
DAYS = [f"2026-09-{d:02d}" for d in range(1, 23)]

BASE = "https://data.binance.vision/data/spot"


def urls_for(sym):
    out = []
    for m in MONTHS:
        out.append((f"{BASE}/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip", f"{sym}-{m}.zip"))
    for d in DAYS:
        out.append((f"{BASE}/daily/klines/{sym}/1m/{sym}-1m-{d}.zip", f"{sym}-{d}.zip"))
    return out


def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 100:
        return dest, True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mkmoon-parity/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return dest, True
    except Exception as e:
        return f"{url}: {e}", False


def extract_all():
    rows = {s: [] for s in SYMS}
    for sym in SYMS:
        d = os.path.join(OUT, sym)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".zip"):
                continue
            with zipfile.ZipFile(os.path.join(d, fn)) as z:
                name = z.namelist()[0]
                with z.open(name) as f:
                    for line in io.TextIOWrapper(f, "utf-8"):
                        p = line.strip().split(",")
                        if len(p) < 6:
                            continue
                        t = int(p[0])
                        if t > 10**14:        # daily files may be in µs
                            t //= 1000
                        rows[sym].append((t, float(p[1]), float(p[2]), float(p[3]),
                                          float(p[4]), float(p[5])))
    for sym in SYMS:
        rows[sym].sort()
        # de-dup
        seen = set()
        uniq = []
        for r in rows[sym]:
            if r[0] not in seen:
                seen.add(r[0])
                uniq.append(r)
        rows[sym] = uniq
        print(sym, "1m bars:", len(uniq), "from", uniq[0][0], "to", uniq[-1][0])
    import numpy as np
    for sym in SYMS:
        arr = np.array(rows[sym], dtype=np.float64)
        os.makedirs(os.path.join(OUT, sym), exist_ok=True)
        np.save(os.path.join(OUT, sym, "bars.npy"), arr)
    return rows


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    jobs = []
    for sym in SYMS:
        d = os.path.join(OUT, sym)
        os.makedirs(d, exist_ok=True)
        for url, fn in urls_for(sym):
            jobs.append((url, os.path.join(d, fn)))
    ok = fail = 0
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for res, good in ex.map(lambda j: fetch(*j), jobs):
            ok += good
            fail += (not good)
            if not good:
                print("FAIL", res, flush=True)
    print(f"downloaded ok={ok} fail={fail}")
    extract_all()
    print("PARITY DATA READY")
