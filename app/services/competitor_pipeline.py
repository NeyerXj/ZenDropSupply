from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import httpx

from app.providers.competitor_shopify import CompetitorProduct, CompetitorShopifyClient
from app.services.filtering import DEFAULT_FILTER_CONFIG, ProductFilterConfig, classify_product_status


class CompetitorPipeline:
    def __init__(
        self,
        database: sqlite3.Connection,
        client: CompetitorShopifyClient,
        image_storage_dir: Path,
        filter_config: ProductFilterConfig | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.image_storage_dir = image_storage_dir
        self.filter_config = filter_config or DEFAULT_FILTER_CONFIG

    async def scrape_store(self, store_url: str, pages: int, limit: int | None = None) -> list[CompetitorProduct]:
        products: list[CompetitorProduct] = []
        seen_handles: set[str] = set()
        for page in range(1, pages + 1):
            handles = await self.client.fetch_collection_handles(store_url=store_url, page=page)
            for handle in handles:
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
                product = await self.client.fetch_product(store_url=store_url, handle=handle)
                image_path = await self._download_image(product)
                self._persist_product(store_url=store_url, product=product, image_path=image_path)
                products.append(product)
                if limit is not None and len(products) >= limit:
                    self.database.commit()
                    return products
        self.database.commit()
        return products

    async def _download_image(self, product: CompetitorProduct) -> str | None:
        if not product.image_url:
            return None
        self.image_storage_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(urlparse(product.image_url).path).suffix or ".jpg"
        image_path = self.image_storage_dir / f"{product.handle}{suffix}"
        response = await self.client.http_client.get(product.image_url)
        response.raise_for_status()
        image_path.write_bytes(response.content)
        return str(image_path)

    def _persist_product(self, store_url: str, product: CompetitorProduct, image_path: str | None) -> None:
        self.database.execute(
            """
            insert into competitor_products (
                store_url,
                external_id,
                handle,
                title,
                product_type,
                tags_json,
                price,
                image_url,
                image_path,
                status,
                raw_json,
                updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            on conflict(store_url, handle) do update set
                external_id = excluded.external_id,
                title = excluded.title,
                product_type = excluded.product_type,
                tags_json = excluded.tags_json,
                price = excluded.price,
                image_url = excluded.image_url,
                image_path = excluded.image_path,
                status = excluded.status,
                raw_json = excluded.raw_json,
                updated_at = current_timestamp
            """,
            (
                store_url.rstrip("/"),
                product.external_id,
                product.handle,
                product.title,
                product.product_type,
                json.dumps(product.tags, ensure_ascii=False),
                product.price,
                product.image_url,
                image_path,
                classify_product_status(product, config=self.filter_config),
                json.dumps(product.raw, ensure_ascii=False),
            ),
        )
