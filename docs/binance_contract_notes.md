# Binance Spot contract notes

Official sources:
- https://developers.binance.com/en/docs/products/spot/rest-api
- https://developers.binance.com/en/docs/products/spot/web-socket-streams
- https://developers.binance.com/en/docs/products/spot/filters

Verified constraints:

1. Public market data should use the public market-data base endpoint where appropriate. REST timestamps are milliseconds by default. Signed requests require an API key, signature, timestamp, and recvWindow; the documented default recvWindow is 5000 ms and the maximum is 60000 ms.
2. HTTP 429 indicates a rate-limit violation; repeated violations can lead to HTTP 418 bans. HTTP 5XX responses for order-related requests have unknown execution status and must not be treated as a simple failure; status reconciliation is required before retrying a non-idempotent order.
3. Binance Spot WebSocket base endpoints are wss://stream.binance.com:9443 or :443. Symbols in stream names are lowercase. A connection is valid for 24 hours and should be replaced before/at disconnect. The server sends ping frames every 20 seconds; pong handling is required. Incoming-message limits and stream-count limits must be respected.
4. The implementation will initially consume REST klines for deterministic closed-candle snapshots. A later streaming adapter must persist the kline close flag and use only finalized candles for strategy decisions. WebSocket reconnects must trigger REST reconciliation before resuming new signals.
5. exchangeInfo filters are authoritative for price tick, quantity step/min/max, min/max notional, percent-price, market-lot-size, maximum orders, and position constraints. Decimal arithmetic and versioned exchangeInfo snapshots are required.

Safety decision: this first implementation is paper-first and does not send signed orders. Any future live adapter must be a separate module with explicit mode gating, status reconciliation, and exchange filter validation.
