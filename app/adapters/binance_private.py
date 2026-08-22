from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx


class UnknownExecutionStatus(RuntimeError):
    """The exchange may have accepted a non-idempotent request; reconcile before retry."""


class BinancePrivateClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, recv_window_ms: int = 5000):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.recv_window_ms = recv_window_ms
        self.client = httpx.AsyncClient(timeout=10.0, headers={"X-MBX-APIKEY": api_key})
        self.server_offset_ms = 0

    async def aclose(self) -> None:
        await self.client.aclose()

    async def sync_clock(self) -> int:
        response = await self.client.get(f"{self.base_url}/api/v3/time")
        response.raise_for_status()
        server_time = int(response.json()["serverTime"])
        self.server_offset_ms = server_time - int(time.time() * 1000)
        return self.server_offset_ms

    def _signed_params(self, params: dict[str, Any]) -> str:
        ordered = dict(params)
        ordered.setdefault("recvWindow", self.recv_window_ms)
        ordered["timestamp"] = int(time.time() * 1000) + self.server_offset_ms
        query = urlencode(ordered, doseq=True)
        signature = hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query}&signature={signature}"

    async def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        query = self._signed_params(params)
        response = await self.client.request(method, f"{self.base_url}{path}?{query}")
        if response.status_code >= 500:
            raise UnknownExecutionStatus(f"Binance returned {response.status_code}; reconcile order state")
        response.raise_for_status()
        return response.json()

    async def account(self) -> dict[str, Any]:
        return await self._signed_request("GET", "/api/v3/account", {})

    async def get_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        return await self._signed_request("GET", "/api/v3/order", {
            "symbol": symbol, "origClientOrderId": orig_client_order_id,
        })

    async def place_limit_buy(
        self,
        *,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        live_trading_enabled: bool,
        trading_mode: str,
    ) -> dict[str, Any]:
        if trading_mode != "live" or not live_trading_enabled:
            raise PermissionError("live order blocked: explicit live mode and LIVE_TRADING_ENABLED=true are required")
        return await self._signed_request("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": format(quantity, "f"),
            "price": format(price, "f"),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "ACK",
        })

    async def cancel_order(self, symbol: str, orig_client_order_id: str, live_trading_enabled: bool, trading_mode: str) -> dict[str, Any]:
        if trading_mode != "live" or not live_trading_enabled:
            raise PermissionError("live cancel blocked outside explicit live mode")
        return await self._signed_request("DELETE", "/api/v3/order", {
            "symbol": symbol, "origClientOrderId": orig_client_order_id,
        })
