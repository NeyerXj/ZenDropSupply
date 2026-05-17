from __future__ import annotations

from dataclasses import dataclass
import json
import asyncio
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


class ZendropProductDetail(ZendropProductSummary):
    categories: list[dict[str, Any]] = Field(default_factory=list)


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

    async def search_products(
        self,
        keyword: str,
        page: int = 1,
        limit: int = 20,
        category_id: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> ZendropSearchResult:
        arguments: dict[str, Any] = {"keyword": keyword, "page": page, "limit": limit}
        if category_id is not None:
            arguments["category_id"] = category_id
        if min_price is not None:
            arguments["min_price"] = min_price
        if max_price is not None:
            arguments["max_price"] = max_price
        payload = await self.call_tool("get_catalog_products", arguments)
        return ZendropSearchResult.model_validate(payload)

    async def get_product(self, product_id: int) -> ZendropProductDetail:
        payload = await self.call_tool("get_catalog_product", {"product_id": product_id})
        return ZendropProductDetail.model_validate(payload)

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
        content = envelope.get("result", {}).get("content", [])
        for entry in content:
            if entry.get("type") == "text":
                text = entry.get("text", "")
                try:
                    return json.loads(text)
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
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            await asyncio.sleep(retry_after or waits[attempt])
        raise ZendropMcpError("Zendrop retry loop exhausted")


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
