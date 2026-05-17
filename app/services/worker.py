from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.database import open_database
from app.providers.competitor_shopify import CompetitorShopifyClient
from app.providers.gemini_images import GeminiImageClient
from app.providers.openai_content import OpenAIContentClient
from app.providers.zendrop import ZendropMcpClient
from app.services.approval_matching import build_approval_matches, queue_approval_match_jobs
from app.services.competitor_pipeline import CompetitorPipeline
from app.services.filtering import get_active_filter_config
from app.services.final_catalog import FinalCatalogService
from app.services.pipeline_state import (
    claim_next_pipeline_job,
    complete_pipeline_job,
    enqueue_pipeline_job,
    fail_pipeline_job,
    update_pipeline_run_status,
)
from app.services.search_terms import zendrop_search_text
from app.services.zendrop_pipeline import ZendropPipeline


class PipelineWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def process_next_job(self) -> dict | None:
        with open_database(self.settings.database_url) as database:
            job = claim_next_pipeline_job(database)
        if job is None:
            return None
        try:
            result = await self.dispatch(job)
            with open_database(self.settings.database_url) as database:
                completed_job = complete_pipeline_job(database, job["id"], result)
                self._refresh_run_status(database, job["run_id"])
            return completed_job
        except Exception as error:
            with open_database(self.settings.database_url) as database:
                failed_job = fail_pipeline_job(database, job["id"], str(error))
                update_pipeline_run_status(database, job["run_id"], "failed")
            return failed_job

    async def dispatch(self, job: dict) -> dict[str, Any]:
        stage = job["stage"]
        payload = job["payload"]
        if stage == "competitor_scrape":
            return await self.run_competitor_scrape(job, payload)
        if stage == "zendrop_search":
            return await self.run_zendrop_search(job, payload)
        if stage == "approval_matching":
            return await self.run_approval_matching(job, payload)
        if stage == "approval_match_product":
            return await self.run_approval_match_product(job, payload)
        if stage == "openai_content":
            return await self.run_openai_content(job, payload)
        if stage == "gemini_images":
            return await self.run_gemini_images(job, payload)
        if stage == "final_model_images":
            return await self.run_final_model_images(job, payload)
        if stage == "shopify_draft_upload":
            return await self.run_shopify_draft_upload(job, payload)
        raise ValueError(f"Unsupported pipeline stage: {stage}")

    async def run_competitor_scrape(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            with open_database(self.settings.database_url) as database:
                pipeline = CompetitorPipeline(
                    database=database,
                    client=client,
                    image_storage_dir=self.settings.storage_dir / "competitor_images",
                    filter_config=get_active_filter_config(database),
                )
                products = await pipeline.scrape_store(
                    store_url=payload["store_url"],
                    pages=int(payload.get("pages") or 5),
                    limit=payload.get("limit"),
                )
                ready_rows = database.execute(
                    """
                    select id, title
                    from competitor_products
                    where store_url = ? and status = 'ready_for_zendrop'
                    order by updated_at desc, id desc
                    """,
                    (payload["store_url"].rstrip("/"),),
                ).fetchall()
                for product_id, title in ready_rows:
                    enqueue_pipeline_job(
                        database=database,
                        run_id=job["run_id"],
                        stage="zendrop_search",
                        payload={
                            "keyword": zendrop_search_text(title),
                            "competitor_product_id": product_id,
                            "source_title": title,
                            "limit": 5,
                            "country_code": self.settings.zendrop.default_country_code,
                        },
                        priority=110,
                    )
        return {"products_scraped": len(products), "ready_products": len(ready_rows)}

    async def run_zendrop_search(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as http_client:
            client = ZendropMcpClient(settings=self.settings.zendrop, http_client=http_client)
            with open_database(self.settings.database_url) as database:
                pipeline = ZendropPipeline(database=database, zendrop_client=client)
                products = await pipeline.search_and_store(
                    keyword=payload["keyword"],
                    limit=int(payload.get("limit") or 5),
                    country_code=payload.get("country_code") or self.settings.zendrop.default_country_code,
                )
                competitor_product_id = payload.get("competitor_product_id")
                if competitor_product_id:
                    enqueue_pipeline_job(
                        database=database,
                        run_id=job["run_id"],
                        stage="approval_match_product",
                        payload={"competitor_product_id": int(competitor_product_id)},
                        priority=130,
                    )
                else:
                    queue_approval_match_jobs(database=database, run_id=job["run_id"])
        return {"products_saved": len(products), "keyword": payload["keyword"]}

    async def run_approval_matching(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        with open_database(self.settings.database_url) as database:
            result = queue_approval_match_jobs(database=database, run_id=job["run_id"])
        return result

    async def run_approval_match_product(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        with open_database(self.settings.database_url) as database:
            result = build_approval_matches(
                database=database,
                openai_settings=self.settings.openai,
                storage_dir=self.settings.storage_dir,
                competitor_product_ids=[int(payload["competitor_product_id"])],
            )
        return result

    async def run_openai_content(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        match_payload = self._load_content_payload(int(payload["product_match_id"]))
        async with httpx.AsyncClient(timeout=60) as http_client:
            client = OpenAIContentClient(settings=self.settings.openai, http_client=http_client)
            content = await client.generate_product_content(match_payload)
        with open_database(self.settings.database_url) as database:
            database.execute(
                """
                insert into generated_contents (
                    product_match_id, title, description, size_chart_json, price_usd,
                    compare_at_price_usd, raw_json, status, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, 'ready_for_images', current_timestamp)
                on conflict(product_match_id) do update set
                    title = excluded.title,
                    description = excluded.description,
                    size_chart_json = excluded.size_chart_json,
                    price_usd = excluded.price_usd,
                    compare_at_price_usd = excluded.compare_at_price_usd,
                    raw_json = excluded.raw_json,
                    status = 'ready_for_images',
                    updated_at = current_timestamp
                """,
                (
                    payload["product_match_id"],
                    content.title,
                    content.description,
                    json.dumps(content.size_chart, ensure_ascii=False),
                    content.price_usd,
                    content.compare_at_price_usd,
                    json.dumps(content.raw, ensure_ascii=False),
                ),
            )
            enqueue_pipeline_job(
                database=database,
                run_id=job["run_id"],
                stage="final_model_images",
                payload={
                    "competitor_product_id": match_payload["competitor_product_id"],
                    "images_per_product": 6,
                },
                priority=300,
            )
            database.commit()
        return {"content_generated": 1, "product_match_id": payload["product_match_id"]}

    async def run_gemini_images(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        image_payload = self._load_image_payload(int(payload["product_match_id"]))
        prompt = build_image_prompt(image_payload)
        async with httpx.AsyncClient(timeout=180) as http_client:
            client = GeminiImageClient(
                settings=self.settings.gemini,
                http_client=http_client,
                output_dir=self.settings.storage_dir / "generated_images",
            )
            image = await client.generate_product_image(
                prompt=prompt,
                image_urls=[image_payload.get("zendrop_image_url")],
            )
        with open_database(self.settings.database_url) as database:
            database.execute(
                """
                insert into generated_images (
                    product_match_id, color_name, prompt, image_url, image_path, qc_status, raw_json, updated_at
                )
                values (?, ?, ?, ?, ?, 'review', ?, current_timestamp)
                """,
                (
                    payload["product_match_id"],
                    payload.get("color_name"),
                    image.prompt,
                    image.image_url,
                    image.image_path,
                    json.dumps(image.raw, ensure_ascii=False),
                ),
            )
            database.commit()
        return {"images_generated": 1, "product_match_id": payload["product_match_id"]}

    async def run_final_model_images(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        service = FinalCatalogService(self.settings)
        return await service.generate_model_image_set(
            competitor_product_id=int(payload["competitor_product_id"]),
            images_per_product=int(payload.get("images_per_product") or 6),
        )

    async def run_shopify_draft_upload(self, job: dict, payload: dict[str, Any]) -> dict[str, Any]:
        service = FinalCatalogService(self.settings)
        return await service.upload_shopify_draft(
            competitor_product_id=int(payload["competitor_product_id"]),
            min_images=int(payload.get("min_images") or 5),
        )

    def _load_content_payload(self, product_match_id: int) -> dict[str, Any]:
        with open_database(self.settings.database_url) as database:
            row = database.execute(
                """
                select
                    pm.id, pm.total_cost_usd, cp.id, cp.title, cp.price, zp.name, zp.price_usd, zp.shipping_price_usd
                from product_matches pm
                join competitor_products cp on cp.id = pm.competitor_product_id
                join zendrop_products zp on zp.product_id = pm.zendrop_product_id
                where pm.id = ?
                """,
                (product_match_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Product match not found: {product_match_id}")
        return {
            "product_match_id": row[0],
            "total_cost_usd": row[1],
            "competitor_product_id": row[2],
            "competitor_title": row[3],
            "competitor_price_usd": row[4],
            "supplier_title": row[5],
            "zendrop_name": row[5],
            "zendrop_price_usd": row[6],
            "zendrop_shipping_usd": row[7],
        }

    def _load_image_payload(self, product_match_id: int) -> dict[str, Any]:
        with open_database(self.settings.database_url) as database:
            row = database.execute(
                """
                select cp.title, zp.image_url, gc.title, gc.description
                from product_matches pm
                join competitor_products cp on cp.id = pm.competitor_product_id
                join zendrop_products zp on zp.product_id = pm.zendrop_product_id
                left join generated_contents gc on gc.product_match_id = pm.id
                where pm.id = ?
                """,
                (product_match_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Product match not found: {product_match_id}")
        return {
            "competitor_title": row[0],
            "zendrop_image_url": row[1],
            "generated_title": row[2],
            "generated_description": row[3],
        }

    def _refresh_run_status(self, database, run_id: int | None) -> None:
        if run_id is None:
            return
        queued_count = database.execute(
            "select count(*) from pipeline_jobs where run_id = ? and status in ('queued', 'running')",
            (run_id,),
        ).fetchone()[0]
        failed_count = database.execute(
            "select count(*) from pipeline_jobs where run_id = ? and status = 'failed'",
            (run_id,),
        ).fetchone()[0]
        if failed_count:
            update_pipeline_run_status(database, run_id, "failed")
        elif queued_count:
            update_pipeline_run_status(database, run_id, "running")
        else:
            update_pipeline_run_status(database, run_id, "approval_pending")


def build_image_prompt(payload: dict[str, Any]) -> str:
    title = payload.get("generated_title") or payload.get("competitor_title") or "women's fashion product"
    return (
        f"Create a premium boutique product photo for {title}. "
        "Use a clean studio look, 4:3 composition, 2K output, natural fabric detail, no text overlays."
    )


async def run_worker(settings: Settings, once: bool = False, poll_seconds: float = 2.0) -> None:
    worker = PipelineWorker(settings=settings)
    while True:
        job = await worker.process_next_job()
        if once:
            return
        if job is None:
            await asyncio.sleep(poll_seconds)
