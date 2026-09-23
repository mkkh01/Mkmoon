# -*- coding: utf-8 -*-
"""Challenge data: 15 pairs × 2021-09 .. 2026-09 (1m) from data.binance.vision.

Pairs = 8 generalization + 4 original research + 3 more validated majors.
Output: /home/user/ict_bt/data/ch1m/{SYM}/*.zip
Run: python tests/challenge_data.py
"""
import concurrent.futures as cf
import os
import urllib.request

OUT = "/home/user/ict_bt/data/ch1m"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "ADAUSDT", "DOGEUSDT", "ZECUSDT", "SUIUSDT", "NEARUSDT",
        "UNIUSDT", "TRXUSDT", "APTUSDT", "ARBUSDT", "AAVEUSDT"]

MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13)
          if "2021-09" <= f"{y}-{m:02d}" <= "2026-08"]
DAYS = [f"2026-09-{d:02d}" for d in range(1, 22)]
BASE = "https://data.binance.vision/data/spot"


def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 100:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mkmoon-challenge/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    jobs = []
    for sym in SYMS:
        d = os.path.join(OUT, sym)
        os.makedirs(d, exist_ok=True)
        for m in MONTHS:
            jobs.append((f"{BASE}/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip",
                         os.path.join(d, f"{sym}-{m}.zip")))
        for day in DAYS:
            jobs.append((f"{BASE}/daily/klines/{sym}/1m/{sym}-1m-{day}.zip",
                         os.path.join(d, f"{sym}-{day}.zip")))
    ok = fail = 0
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for good in ex.map(lambda j: fetch(*j), jobs):
            ok += good
            fail += (not good)
    print(f"downloaded ok={ok} fail={fail} (fails = months before listing, OK)",
          flush=True)
    print("CHALLENGE DATA READY", flush=True)
