# -*- coding: utf-8 -*-
"""LIVE SMOKE — boots the real app against live Binance data (+ real Redis when
REDIS_URL is set), runs one full cycle, exercises all 5 button responses.

Run: python tests/smoke_live.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("PORT", "8099")
os.environ.setdefault("STATUS_KEY", "smoke")


async def main():
    from app.main import App, _px  # noqa
    from app import pairs as P
    from app import config as C

    app = App()
    await app.build()
    print(f"BOOT OK: detectors={len(app.det)} redis_ok={app.store.redis_ok} "
          f"pg_ok={app.store.pg_ok} bot={'on' if C.BOT_TOKEN else 'off(no token)'}")
    if C.REDIS_URL and app.store.redis_ok:
        print("REDIS CONNECT ✓", app.store.redis_ms, "ms")
    elif C.REDIS_URL:
        print(f"WARN redis unreachable ({app.store.redis_err}) — validating KV path "
              f"with in-memory fakeredis instead")
        import fakeredis.aioredis as fa
        app.store.redis = fa.FakeRedis(decode_responses=True)
        app.store.redis_ok = True
    if app.store.redis_ok:
        await app.store.kv_set("smoke", {"hello": "mkmoon"})
        v = await app.store.kv_get("smoke")
        assert v and v["hello"] == "mkmoon"
        print("REDIS KV ROUNDTRIP ✓")

    # detectors must be fed after backfill
    d = app.det["BTCUSDT"]
    assert d.n > 250, f"BTCUSDT 15m bars {d.n}"
    n_setups = sum(len(app.det[s].setups) for s in P.ALL_SYMBOLS)
    n_signals = len(app.trader.signals)
    print(f"DETECTORS FED ✓ bars(BTC)={d.n} setups_all={n_setups} "
          f"signals_passing_gates={n_signals}")

    # a few fast ticks with live prices on active symbols
    for _ in range(3):
        active = app.trader.active_symbols()
        books = await app.market.fetch_books(active or P.TIER_A[:3])
        for sym, (bid, ask) in list(books.items())[:3]:
            await app.trader.on_price(sym, bid, ask, int(asyncio.get_event_loop().time() * 1000) + 1700000000000,
                                      0)
        await asyncio.sleep(0.2)
    print("FAST TICK OK ✓")

    # one full cycle (joint coupling + verdict + cycle report row)
    await app.run_cycle()
    rep = app.cycles.log[-1]
    print("CYCLE #", rep["cycle"], "| verdict:", rep["verdict"])
    print(app.cycles.summary_text(rep))

    # all five buttons
    for action in ("OPEN", "CLOSED", "PERF", "PRICES", "CYCLE"):
        txt = await app.responder(action)
        assert txt and len(txt) > 5
        print(f"BUTTON {action}: {len(txt)} chars ✓")
    m = app.market.health()
    print(f"MARKET: req={m['req']} MB={m['MB']} errors={m['errors']}")
    await app.store.close()
    await app.session.close()
    print("SMOKE: ALL PASS ✓")


if __name__ == "__main__":
    asyncio.run(main())
