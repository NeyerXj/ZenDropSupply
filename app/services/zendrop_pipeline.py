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
    ) -> list[ZendropProductSummary]:
        search_result = await self.zendrop_client.search_products(keyword=keyword, limit=limit)
        for product in search_result.products:
            shipping_estimate = None
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
                    shipping_price_usd = excluded.shipping_price_usd,
                    shipping_estimated_delivery = excluded.shipping_estimated_delivery,
                    updated_at = current_timestamp
                """,
                (
                    product.product_id,
                    product.name,
                    product.description,
                    product.price_usd,
                    product.image,
                    json.dumps(product.model_dump(mode="json"), ensure_ascii=False),
                    shipping_country_code,
                    cheapest_shipping.price if cheapest_shipping else None,
                    cheapest_shipping.estimated_delivery if cheapest_shipping else None,
                ),
            )
        self.database.commit()
        return search_result.products
