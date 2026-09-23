# -*- coding: utf-8 -*-
"""Download 3 years of 1m data (2023-09-01 .. 2026-09-21) for 8 NEW pairs
(none of them in the validated 10) to run the generalization backtest.

Output: /home/user/ict_bt/data/gen1m/{SYM}/{zip files}
Run: python tests/fetch_generalization_data.py
"""
import concurrent.futures as cf
import os
import urllib.request

OUT = "/home/user/ict_bt/data/gen1m"
SYMS = ["ZECUSDT", "SUIUSDT", "NEARUSDT", "UNIUSDT", "TRXUSDT",
        "APTUSDT", "ARBUSDT", "AAVEUSDT"]

MONTHS = []
for y in (2023, 2024, 2025, 2026):
    for m in range(1, 13):
        ms = f"{y}-{m:02d}"
        if "2023-09" <= ms <= "2026-08":
            MONTHS.append(ms)
DAYS = [f"2026-09-{d:02d}" for d in range(1, 22)]

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
        req = urllib.request.Request(url, headers={"User-Agent": "Mkmoon-gen/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return dest, True
    except Exception as e:
        return f"{url}: {e}", False


if __name__ == "__main__":
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
    print(f"downloaded ok={ok} fail={fail}", flush=True)
    print("GEN DATA DONE", flush=True)
