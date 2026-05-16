from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import OpenAISettings


class OpenAIContentError(RuntimeError):
    pass


class GeneratedProductContent(BaseModel):
    title: str
    description: str
    size_chart: dict[str, Any]
    price_usd: float
    compare_at_price_usd: float
    raw: dict[str, Any]


class OpenAIContentClient:
    def __init__(self, settings: OpenAISettings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http_client = http_client

    async def generate_product_content(self, payload: dict[str, Any]) -> GeneratedProductContent:
        if not self.settings.api_key:
            raise OpenAIContentError("OPENAI_API_KEY is required")
        total_cost = float(payload.get("total_cost_usd") or 0)
        minimum_price = round(total_cost * 3.0, 2)
        response = await self.http_client.post(
            f"{self.settings.api_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.model,
                "instructions": (
                    "Generate Shopify draft product content as strict JSON. "
                    "Use a premium Canadian women's boutique style. "
                    "Description must be 80-120 English words. "
                    "Price must be at least total_cost_usd * 3.0. "
                    "compare_at_price_usd must be 30-45 percent above price_usd."
                ),
                "input": json.dumps({**payload, "minimum_price_usd": minimum_price}, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
            },
            timeout=60,
        )
        if response.status_code >= 400:
            raise OpenAIContentError(f"OpenAI content generation failed: HTTP {response.status_code}")
        raw = response.json()
        parsed = parse_response_json(raw)
        price_usd = max(float(parsed.get("price_usd") or minimum_price), minimum_price)
        compare_at_price_usd = float(parsed.get("compare_at_price_usd") or round(price_usd * 1.35, 2))
        if compare_at_price_usd < price_usd * 1.3:
            compare_at_price_usd = round(price_usd * 1.35, 2)
        return GeneratedProductContent(
            title=str(parsed.get("title") or payload.get("competitor_title") or "Boutique Dress"),
            description=str(parsed.get("description") or ""),
            size_chart=dict(parsed.get("size_chart") or {}),
            price_usd=round(price_usd, 2),
            compare_at_price_usd=round(compare_at_price_usd, 2),
            raw=raw,
        )


def parse_response_json(raw: dict[str, Any]) -> dict[str, Any]:
    if text := raw.get("output_text"):
        return json.loads(text)
    for output in raw.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return json.loads(content["text"])
    raise OpenAIContentError("OpenAI response did not include JSON text")
