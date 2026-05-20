from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import ZendropSettings


class ZendropMcpError(RuntimeError):
    pass


class ZendropImage(BaseModel):
    image_id: int | None = Field(default=None, alias="id")
    url: str


class ZendropProductSummary(BaseModel):
    product_id: int = Field(alias="id")
    name: str
    description: str | None = None
    price: str | None = None
    image: str | None = None
    images: list[ZendropImage] = Field(default_factory=list)

    @property
    def price_usd(self) -> float | None:
        if self.price is None:
            return None
        try:
            return float(self.price)
        except ValueError:
            return None


class ZendropSearchResult(BaseModel):
    total: int = 0
    products: list[ZendropProductSummary] = Field(default_factory=list)


class ZendropShippingOption(BaseModel):
    type: str
    price: float
    estimated_delivery: str


class ZendropShippingEstimate(BaseModel):
    product_id: int
    country_code: str
    shipping_options: list[ZendropShippingOption] = Field(default_factory=list)

    @property
    def cheapest_option(self) -> ZendropShippingOption | None:
        if not self.shipping_options:
            return None
        return min(self.shipping_options, key=lambda option: option.price)


@dataclass(slots=True)
class ZendropMcpClient:
    settings: ZendropSettings
    http_client: httpx.AsyncClient

    async def search_products(self, keyword: str, page: int = 1, limit: int = 60) -> ZendropSearchResult:
        payload = await self.call_tool("get_catalog_products", {"keyword": keyword, "page": page, "limit": limit})
        return ZendropSearchResult.model_validate(payload)

    async def get_shipping_estimate(self, product_id: int, country_code: str) -> ZendropShippingEstimate:
        payload = await self.call_tool(
            "get_catalog_shipping_estimate",
            {"product_id": product_id, "country_code": country_code.lower()},
        )
        return ZendropShippingEstimate.model_validate(payload)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.api_token:
            raise ZendropMcpError("ZENDROP_API_TOKEN is required")
        response = await self._post_with_retry(name=name, arguments=arguments)
        envelope = response.json()
        if error := envelope.get("error"):
            raise ZendropMcpError(error.get("message", "Zendrop MCP error"))
        for entry in envelope.get("result", {}).get("content", []):
            if entry.get("type") == "text":
                try:
                    return json.loads(entry.get("text", ""))
                except json.JSONDecodeError as error:
                    raise ZendropMcpError(f"Invalid Zendrop MCP JSON payload: {error}") from error
        raise ZendropMcpError("Zendrop MCP response did not include text content")

    async def _post_with_retry(self, name: str, arguments: dict[str, Any]) -> httpx.Response:
        waits = [2.0, 5.0, 10.0]
        for attempt in range(len(waits) + 1):
            response = await self.http_client.post(
                self.settings.api_url,
                headers={
                    "Authorization": f"Bearer {self.settings.api_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response
            if attempt >= len(waits):
                response.raise_for_status()
            await asyncio.sleep(waits[attempt])
        raise ZendropMcpError("Zendrop retry loop exhausted")
