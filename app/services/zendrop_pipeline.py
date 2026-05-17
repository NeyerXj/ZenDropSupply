from __future__ import annotations

import asyncio
import json
import sqlite3

from app.providers.zendrop import ZendropMcpClient, ZendropMcpError, ZendropProductSummary


class ZendropPipeline:
    def __init__(self, database: sqlite3.Connection, zendrop_client: ZendropMcpClient) -> None:
        self.database = database
        self.zendrop_client = zendrop_client

    async def search_and_store(
        self,
        keyword: str,
        limit: int = 20,
        country_code: str = "ca",
        pages: int = 1,
        fetch_shipping: bool = True,
    ) -> list[ZendropProductSummary]:
        products: list[ZendropProductSummary] = []
        for page in range(1, max(1, pages) + 1):
            search_result = await self.zendrop_client.search_products(keyword=keyword, page=page, limit=limit)
            products.extend(search_result.products)
        for product in products:
            shipping_estimate = None
            if fetch_shipping:
                try:
                    await asyncio.sleep(0.35)
                    shipping_estimate = await self.zendrop_client.get_shipping_estimate(
                        product_id=product.product_id,
                        country_code=country_code,
                    )
                except ZendropMcpError:
                    shipping_estimate = None
            cheapest_shipping = shipping_estimate.cheapest_option if shipping_estimate else None
            shipping_country_code = shipping_estimate.country_code if shipping_estimate else country_code
            raw_payload = product.model_dump(mode="json")
            existing_raw_json = self.database.execute(
                "select raw_json from zendrop_products where product_id = ?",
                (product.product_id,),
            ).fetchone()
            raw_payload["_ttd_search_queries"] = merge_raw_search_queries(raw_payload, keyword, existing_raw_json[0] if existing_raw_json else None)
            self.database.execute(
                """
                insert into zendrop_products (
                    product_id,
                    name,
                    description,
                    price_usd,
                    image_url,
                    raw_json,
                    shipping_country_code,
                    shipping_price_usd,
                    shipping_estimated_delivery,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(product_id) do update set
                    name = excluded.name,
                    description = excluded.description,
                    price_usd = excluded.price_usd,
                    image_url = excluded.image_url,
                    raw_json = excluded.raw_json,
                    shipping_country_code = excluded.shipping_country_code,
                    shipping_price_usd = coalesce(excluded.shipping_price_usd, zendrop_products.shipping_price_usd),
                    shipping_estimated_delivery = coalesce(excluded.shipping_estimated_delivery, zendrop_products.shipping_estimated_delivery),
                    updated_at = current_timestamp
                """,
                (
                    product.product_id,
                    product.name,
                    product.description,
                    product.price_usd,
                    product.image,
                    json.dumps(raw_payload, ensure_ascii=False),
                    shipping_country_code,
                    cheapest_shipping.price if cheapest_shipping else None,
                    cheapest_shipping.estimated_delivery if cheapest_shipping else None,
                ),
            )
        self.database.commit()
        return products


def merge_raw_search_queries(raw_payload: dict, keyword: str, existing_raw_json: str | None = None) -> list[str]:
    existing = raw_payload.get("_ttd_search_queries")
    queries = existing if isinstance(existing, list) else []
    try:
        existing_raw = json.loads(existing_raw_json or "{}")
    except json.JSONDecodeError:
        existing_raw = {}
    previous_queries = existing_raw.get("_ttd_search_queries")
    if isinstance(previous_queries, list):
        queries = [*previous_queries, *queries]
    result: list[str] = []
    seen: set[str] = set()
    for query in [*queries, keyword]:
        clean_query = str(query).strip().lower()
        if clean_query and clean_query not in seen:
            result.append(clean_query)
            seen.add(clean_query)
    return result
