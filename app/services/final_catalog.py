from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.database import open_database
from app.providers.gemini_images import GeminiImageClient
from app.providers.shopify import ShopifyAdminClient


SHOT_KEYS = ["front", "three_quarter", "side", "walking", "seated", "detail"]


def list_final_catalog_status(database, storage_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    rows = database.execute(
        """
        select
            cp.id,
            cp.title,
            cp.price,
            cp.image_path,
            cp.image_url,
            coalesce(fis.status, 'not_started') as image_status,
            coalesce(fis.target_count, 6) as target_count,
            coalesce(count(fgi.id), 0) as generated_count,
            sdp.shopify_product_id,
            coalesce(sdp.status, '') as shopify_status,
            coalesce(sdp.media_count, 0) as media_count
        from competitor_products cp
        left join final_image_sets fis on fis.competitor_product_id = cp.id
        left join final_generated_images fgi on fgi.image_set_id = fis.id
        left join shopify_draft_products sdp on sdp.competitor_product_id = cp.id
        group by cp.id, cp.title, cp.price, cp.image_path, cp.image_url, fis.status, fis.target_count,
            sdp.shopify_product_id, sdp.status, sdp.media_count
        order by cp.id
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "competitor_product_id": row[0],
            "title": row[1],
            "price": row[2],
            "source_image_url": media_url(row[3], storage_dir) or row[4],
            "image_status": row[5],
            "target_count": row[6],
            "generated_count": row[7],
            "shopify_product_id": row[8],
            "shopify_status": row[9] or None,
            "media_count": row[10],
        }
        for row in rows
    ]


def queue_final_image_jobs(database, limit: int, images_per_product: int, run_id: int | None = None) -> int:
    active_product_ids = active_job_product_ids(database, "final_model_images")
    rows = database.execute(
        """
        select cp.id
        from competitor_products cp
        join product_matches pm on pm.competitor_product_id = cp.id and pm.status = 'approved'
        left join final_image_sets fis on fis.competitor_product_id = cp.id
        left join final_generated_images fgi on fgi.image_set_id = fis.id
        group by cp.id, fis.target_count
        having count(fgi.id) < ?
        order by cp.id
        """,
        (images_per_product,),
    ).fetchall()
    queued = 0
    for (product_id,) in rows:
        if product_id in active_product_ids:
            continue
        database.execute(
            """
            insert into pipeline_jobs (run_id, stage, status, priority, payload_json, result_json, updated_at)
            values (?, 'final_model_images', 'queued', 300, ?, '{}', current_timestamp)
            """,
            (run_id, json.dumps({"competitor_product_id": product_id, "images_per_product": images_per_product})),
        )
        queued += 1
        if queued >= limit:
            break
    database.commit()
    return queued


def queue_shopify_upload_jobs(database, limit: int, min_images: int, run_id: int | None = None) -> int:
    active_product_ids = active_job_product_ids(database, "shopify_draft_upload")
    rows = database.execute(
        """
        select cp.id
        from competitor_products cp
        join final_image_sets fis on fis.competitor_product_id = cp.id
        join final_generated_images fgi on fgi.image_set_id = fis.id
        left join shopify_draft_products sdp on sdp.competitor_product_id = cp.id and sdp.media_count >= ?
        where sdp.id is null
        group by cp.id
        having count(fgi.id) >= ?
        order by cp.id
        """,
        (min_images, min_images),
    ).fetchall()
    queued = 0
    for (product_id,) in rows:
        if product_id in active_product_ids:
            continue
        database.execute(
            """
            insert into pipeline_jobs (run_id, stage, status, priority, payload_json, result_json, updated_at)
            values (?, 'shopify_draft_upload', 'queued', 400, ?, '{}', current_timestamp)
            """,
            (run_id, json.dumps({"competitor_product_id": product_id, "min_images": min_images})),
        )
        queued += 1
        if queued >= limit:
            break
    database.commit()
    return queued


def active_job_product_ids(database, stage: str) -> set[int]:
    rows = database.execute(
        """
        select payload_json
        from pipeline_jobs
        where stage = ? and status in ('queued', 'running')
        """,
        (stage,),
    ).fetchall()
    product_ids: set[int] = set()
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        product_id = payload.get("competitor_product_id")
        if product_id is not None:
            product_ids.add(int(product_id))
    return product_ids


class FinalCatalogService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate_model_image_set(self, competitor_product_id: int, images_per_product: int = 6) -> dict[str, Any]:
        product = self._load_product(competitor_product_id)
        output_dir = self.settings.storage_dir / "final_model_images" / str(competitor_product_id)
        async with httpx.AsyncClient(timeout=240) as http_client:
            gemini = GeminiImageClient(self.settings.gemini, http_client, output_dir)
            image_set_id, identity_path = await self._ensure_identity_image(gemini, product, images_per_product)
            product_reference_paths = self._product_reference_paths(product)
            generated = 0
            for shot_key in SHOT_KEYS[:images_per_product]:
                if self._shot_exists(image_set_id, shot_key):
                    continue
                prompt = build_consistent_model_shot_prompt(product["title"], shot_key)
                image = await gemini.generate_product_image_with_references(
                    prompt=prompt,
                    image_urls=[] if product_reference_paths else ([product["image_url"]] if product.get("image_url") else []),
                    image_paths=([Path(identity_path)] if identity_path else []) + product_reference_paths,
                )
                if not image.image_path:
                    raise RuntimeError(f"Gemini did not return image for {shot_key}")
                self._save_final_image(
                    image_set_id=image_set_id,
                    competitor_product_id=competitor_product_id,
                    shot_key=shot_key,
                    prompt=prompt,
                    image_path=image.image_path,
                    raw=image.raw,
                )
                generated += 1
            self._mark_image_set_done(image_set_id)
        return {"competitor_product_id": competitor_product_id, "generated": generated, "target_count": images_per_product}

    async def upload_shopify_draft(self, competitor_product_id: int, min_images: int = 5) -> dict[str, Any]:
        product = self._load_product(competitor_product_id)
        images = self._load_final_images(competitor_product_id)
        if len(images) < min_images:
            raise RuntimeError(f"Not enough generated images: {len(images)}/{min_images}")
        price = round(max(float(product.get("price") or 39.99), 29.99), 2)
        compare_at_price = round(price * 1.35, 2)
        product_input = {
            "title": product["title"][:255],
            "descriptionHtml": build_shopify_description(product["title"]),
            "vendor": "TTD Pipeline",
            "productType": product.get("product_type") or "Fashion",
            "status": "DRAFT",
            "tags": ["ttd-pipeline", "final-generated", "model-set", "draft"],
        }
        async with httpx.AsyncClient(timeout=180) as http_client:
            shopify = ShopifyAdminClient(self.settings.shopify, http_client)
            product_node = await shopify.create_draft_product_with_media(
                product=product_input,
                image_paths=[Path(image["image_path"]) for image in images],
                price=price,
                compare_at_price=compare_at_price,
            )
        self._save_shopify_draft(competitor_product_id, product_node)
        return {
            "competitor_product_id": competitor_product_id,
            "shopify_product_id": product_node["id"],
            "media_count": len(product_node.get("media", {}).get("nodes", [])),
        }

    async def _ensure_identity_image(self, gemini: GeminiImageClient, product: dict[str, Any], target_count: int) -> tuple[int, str]:
        with open_database(self.settings.database_url) as database:
            row = database.execute(
                """
                select id, identity_image_path
                from final_image_sets
                where competitor_product_id = ?
                """,
                (product["id"],),
            ).fetchone()
            if row and row[1]:
                database.execute(
                    "update final_image_sets set target_count = ?, status = 'generating', updated_at = current_timestamp where id = ?",
                    (target_count, row[0]),
                )
                database.commit()
                return row[0], row[1]
            if row:
                image_set_id = row[0]
                database.execute(
                    "update final_image_sets set status = 'generating', target_count = ?, updated_at = current_timestamp where id = ?",
                    (target_count, image_set_id),
                )
            else:
                image_set_id = database.execute(
                    """
                    insert into final_image_sets (competitor_product_id, target_count, status, updated_at)
                    values (?, ?, 'generating', current_timestamp)
                    returning id
                    """,
                    (product["id"], target_count),
                ).fetchone()[0]
            database.commit()
        prompt = build_identity_prompt(product["title"])
        product_reference_paths = self._product_reference_paths(product)
        identity = await gemini.generate_product_image_with_references(
            prompt=prompt,
            image_urls=[] if product_reference_paths else ([product["image_url"]] if product.get("image_url") else []),
            image_paths=product_reference_paths,
        )
        if not identity.image_path:
            raise RuntimeError("Gemini did not return identity image")
        with open_database(self.settings.database_url) as database:
            database.execute(
                """
                update final_image_sets
                set identity_image_path = ?, status = 'generating', updated_at = current_timestamp
                where id = ?
                """,
                (identity.image_path, image_set_id),
            )
            database.commit()
        return image_set_id, identity.image_path

    def _load_product(self, competitor_product_id: int) -> dict[str, Any]:
        with open_database(self.settings.database_url) as database:
            row = database.execute(
                """
                select id, title, product_type, price, image_url, image_path
                from competitor_products
                where id = ?
                """,
                (competitor_product_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Competitor product not found: {competitor_product_id}")
        return {
            "id": row[0],
            "title": row[1],
            "product_type": row[2],
            "price": row[3],
            "image_url": row[4],
            "image_path": row[5],
        }

    def _product_reference_paths(self, product: dict[str, Any]) -> list[Path]:
        image_path = product.get("image_path")
        if not image_path:
            return []
        path = Path(image_path)
        return [path] if path.exists() else []

    def _shot_exists(self, image_set_id: int, shot_key: str) -> bool:
        with open_database(self.settings.database_url) as database:
            return (
                database.execute(
                    "select count(*) from final_generated_images where image_set_id = ? and shot_key = ?",
                    (image_set_id, shot_key),
                ).fetchone()[0]
                > 0
            )

    def _save_final_image(
        self,
        image_set_id: int,
        competitor_product_id: int,
        shot_key: str,
        prompt: str,
        image_path: str,
        raw: dict[str, Any],
    ) -> None:
        with open_database(self.settings.database_url) as database:
            database.execute(
                """
                insert into final_generated_images (
                    image_set_id, competitor_product_id, shot_key, prompt, image_path, qc_status, raw_json, updated_at
                )
                values (?, ?, ?, ?, ?, 'ready', ?, current_timestamp)
                on conflict(image_set_id, shot_key) do update set
                    prompt = excluded.prompt,
                    image_path = excluded.image_path,
                    qc_status = 'ready',
                    raw_json = excluded.raw_json,
                    updated_at = current_timestamp
                """,
                (image_set_id, competitor_product_id, shot_key, prompt, image_path, json.dumps(raw, ensure_ascii=False)),
            )
            database.commit()

    def _mark_image_set_done(self, image_set_id: int) -> None:
        with open_database(self.settings.database_url) as database:
            generated_count = database.execute(
                "select count(*) from final_generated_images where image_set_id = ?",
                (image_set_id,),
            ).fetchone()[0]
            database.execute(
                """
                update final_image_sets
                set status = 'ready', generated_count = ?, updated_at = current_timestamp
                where id = ?
                """,
                (generated_count, image_set_id),
            )
            database.commit()

    def _load_final_images(self, competitor_product_id: int) -> list[dict[str, Any]]:
        with open_database(self.settings.database_url) as database:
            rows = database.execute(
                """
                select shot_key, image_path
                from final_generated_images
                where competitor_product_id = ? and qc_status = 'ready'
                order by
                    case shot_key
                        when 'front' then 0
                        when 'three_quarter' then 1
                        when 'side' then 2
                        when 'walking' then 3
                        when 'seated' then 4
                        when 'detail' then 5
                        else 9
                    end,
                    id
                """,
                (competitor_product_id,),
            ).fetchall()
        return [{"shot_key": row[0], "image_path": row[1]} for row in rows]

    def _save_shopify_draft(self, competitor_product_id: int, product_node: dict[str, Any]) -> None:
        media_count = len(product_node.get("media", {}).get("nodes", []))
        with open_database(self.settings.database_url) as database:
            database.execute(
                "delete from shopify_draft_products where competitor_product_id = ?",
                (competitor_product_id,),
            )
            database.execute(
                """
                insert into shopify_draft_products (
                    competitor_product_id, shopify_product_id, title, status, media_count, raw_json, updated_at
                )
                values (?, ?, ?, ?, ?, ?, current_timestamp)
                """,
                (
                    competitor_product_id,
                    product_node["id"],
                    product_node["title"],
                    product_node["status"],
                    media_count,
                    json.dumps(product_node, ensure_ascii=False),
                ),
            )
            database.commit()


def build_identity_prompt(title: str) -> str:
    return (
        f"Create a realistic consistent fashion model identity for ecommerce photos wearing: {title}. "
        "This is the identity reference for a full product photo set. Preserve one same face, same hair, same body type, "
        "same skin tone, same age range, and same styling across future images. Full body, clean boutique studio, "
        "4:3 composition, no text, no watermark."
    )


def build_consistent_model_shot_prompt(title: str, shot_key: str) -> str:
    shot_copy = {
        "front": "front-facing full body pose",
        "three_quarter": "three-quarter standing pose",
        "side": "side profile standing pose",
        "walking": "natural walking pose",
        "seated": "seated editorial pose",
        "detail": "upper-body detail pose showing garment texture and fit",
    }.get(shot_key, "premium ecommerce pose")
    return (
        f"Generate a new ecommerce model photo for {title}. Use the provided identity image as the exact same model: "
        "same face, same hair, same body proportions, same skin tone, same age range. Use the product reference to preserve "
        f"the exact product design. Shot: {shot_copy}. Premium boutique studio lighting, 4:3, 2K, realistic, no text, no logo."
    )


def build_shopify_description(title: str) -> str:
    return (
        f"<p>{title} prepared as a premium boutique draft with a consistent generated model photo set.</p>"
        "<ul><li>Five to six model images generated for product review.</li>"
        "<li>Draft status keeps the item unpublished until manual approval.</li>"
        "<li>Price and compare-at price are prepared for merchandising.</li></ul>"
    )


def media_url(image_path: str | None, storage_dir: Path) -> str | None:
    if not image_path:
        return None
    path = Path(image_path)
    try:
        relative = path.relative_to(storage_dir)
    except ValueError:
        parts = path.parts
        if "storage" not in parts:
            return None
        relative = Path(*parts[parts.index("storage") + 1 :])
    return "/media/" + relative.as_posix()
