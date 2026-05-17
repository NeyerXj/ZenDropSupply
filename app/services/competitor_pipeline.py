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
        ready_products_count = 0
        seen_handles: set[str] = set()
        page = 1
        max_pages = max(pages, 50) if limit is not None else pages
        while page <= max_pages:
            handles = await self.client.fetch_collection_handles(store_url=store_url, page=page)
            new_handles = [handle for handle in handles if handle not in seen_handles]
            if not new_handles and page > pages:
                break
            for handle in handles:
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)
                product = await self.client.fetch_product(store_url=store_url, handle=handle)
                image_path = await self._download_image(product)
                status = self._persist_product(store_url=store_url, product=product, image_path=image_path)
                products.append(product)
                if status == "ready_for_zendrop":
                    ready_products_count += 1
                if limit is not None and ready_products_count >= limit:
                    self.database.commit()
                    return products
            if not handles:
                break
            page += 1
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

    def _persist_product(self, store_url: str, product: CompetitorProduct, image_path: str | None) -> str:
        status = classify_product_status(product, config=self.filter_config)
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
                status,
                json.dumps(product.raw, ensure_ascii=False),
            ),
        )
        return status
