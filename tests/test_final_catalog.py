import json
from pathlib import Path

import pytest

from app.config import Settings
from app.database import open_database
from app.services.final_catalog import (
    FinalCatalogService,
    list_final_catalog_status,
    queue_final_image_jobs,
    queue_shopify_upload_jobs,
)


def insert_competitor_product(database, title="Floral Maxi Dress", image_path=None, image_url=None):
    database.execute(
        """
        insert into competitor_products (
            store_url, handle, title, price, image_url, image_path, tags_json, status, raw_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "https://example.com",
            title.lower().replace(" ", "-"),
            title,
            69.0,
            image_url,
            image_path,
            "[]",
            "ready_for_zendrop",
            "{}",
        ),
    )
    return database.execute("select id from competitor_products order by id desc limit 1").fetchone()[0]


def approve_product(database, product_id):
    database.execute(
        """
        insert into zendrop_products (product_id, name, raw_json)
        values (?, ?, '{}')
        """,
        (100000 + product_id, f"Zendrop {product_id}"),
    )
    database.execute(
        """
        insert into product_matches (
            competitor_product_id, zendrop_product_id, zendrop_match_score, status
        )
        values (?, ?, 90, 'approved')
        """,
        (product_id, 100000 + product_id),
    )


def test_final_catalog_status_reports_generated_images_and_shopify_media(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    storage_dir = tmp_path / "storage"
    source_image = storage_dir / "competitor_images" / "dress.jpg"
    final_image = storage_dir / "final_model_images" / "1" / "front.jpg"
    source_image.parent.mkdir(parents=True)
    final_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"source")
    final_image.write_bytes(b"generated")

    with open_database(database_url) as database:
        product_id = insert_competitor_product(database, image_path=str(source_image))
        approve_product(database, product_id)
        image_set_id = database.execute(
            """
            insert into final_image_sets (competitor_product_id, target_count, status, generated_count)
            values (?, 6, 'ready', 1)
            returning id
            """,
            (product_id,),
        ).fetchone()[0]
        database.execute(
            """
            insert into final_generated_images (
                image_set_id, competitor_product_id, shot_key, prompt, image_path, raw_json
            )
            values (?, ?, 'front', 'prompt', ?, '{}')
            """,
            (image_set_id, product_id, str(final_image)),
        )
        database.execute(
            """
            insert into shopify_draft_products (
                competitor_product_id, shopify_product_id, title, status, media_count, raw_json
            )
            values (?, 'gid://shopify/Product/1', 'Floral Maxi Dress', 'DRAFT', 6, '{}')
            """,
            (product_id,),
        )
        database.commit()

        products = list_final_catalog_status(database=database, storage_dir=storage_dir, limit=10)

    assert products == [
        {
            "competitor_product_id": product_id,
            "title": "Floral Maxi Dress",
            "price": 69.0,
            "source_image_url": "/media/competitor_images/dress.jpg",
            "image_status": "ready",
            "target_count": 6,
            "generated_count": 1,
            "shopify_product_id": "gid://shopify/Product/1",
            "shopify_status": "DRAFT",
            "media_count": 6,
        }
    ]


def test_final_catalog_status_hides_unapproved_source_products(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    storage_dir = tmp_path / "storage"

    with open_database(database_url) as database:
        visible_product_id = insert_competitor_product(database, title="Approved Dress")
        hidden_product_id = insert_competitor_product(database, title="Raw Dress")
        approve_product(database, visible_product_id)
        database.commit()

        products = list_final_catalog_status(database=database, storage_dir=storage_dir, limit=10)

    assert [product["competitor_product_id"] for product in products] == [visible_product_id]
    assert hidden_product_id not in [product["competitor_product_id"] for product in products]


def test_queue_final_image_jobs_skips_products_with_enough_images_or_running_job(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    with open_database(database_url) as database:
        ready_product_id = insert_competitor_product(database, title="Ready Dress")
        complete_product_id = insert_competitor_product(database, title="Complete Dress")
        running_product_id = insert_competitor_product(database, title="Running Dress")
        approve_product(database, ready_product_id)
        approve_product(database, complete_product_id)
        approve_product(database, running_product_id)
        complete_image_set_id = database.execute(
            "insert into final_image_sets (competitor_product_id, target_count, status) values (?, 6, 'ready') returning id",
            (complete_product_id,),
        ).fetchone()[0]
        for index in range(6):
            database.execute(
                """
                insert into final_generated_images (
                    image_set_id, competitor_product_id, shot_key, prompt, image_path, raw_json
                )
                values (?, ?, ?, 'prompt', ?, '{}')
                """,
                (complete_image_set_id, complete_product_id, f"shot_{index}", f"/tmp/{index}.jpg"),
            )
        database.execute(
            """
            insert into pipeline_jobs (stage, status, payload_json, result_json)
            values ('final_model_images', 'running', ?, '{}')
            """,
            (json.dumps({"competitor_product_id": running_product_id, "images_per_product": 6}),),
        )
        database.commit()

        queued = queue_final_image_jobs(database=database, limit=10, images_per_product=6)
        jobs = database.execute("select payload_json from pipeline_jobs where stage = 'final_model_images' and status = 'queued'").fetchall()

    assert queued == 1
    assert json.loads(jobs[0][0])["competitor_product_id"] == ready_product_id


def test_queue_shopify_jobs_requires_minimum_ready_images_and_skips_uploaded(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    with open_database(database_url) as database:
        ready_product_id = insert_competitor_product(database, title="Ready Dress")
        short_product_id = insert_competitor_product(database, title="Short Dress")
        uploaded_product_id = insert_competitor_product(database, title="Uploaded Dress")
        for product_id, image_count in [(ready_product_id, 6), (short_product_id, 4), (uploaded_product_id, 6)]:
            image_set_id = database.execute(
                "insert into final_image_sets (competitor_product_id, target_count, status) values (?, 6, 'ready') returning id",
                (product_id,),
            ).fetchone()[0]
            for index in range(image_count):
                database.execute(
                    """
                    insert into final_generated_images (
                        image_set_id, competitor_product_id, shot_key, prompt, image_path, raw_json
                    )
                    values (?, ?, ?, 'prompt', ?, '{}')
                    """,
                    (image_set_id, product_id, f"shot_{index}", f"/tmp/{product_id}-{index}.jpg"),
                )
        database.execute(
            """
            insert into shopify_draft_products (
                competitor_product_id, shopify_product_id, title, status, media_count, raw_json
            )
            values (?, 'gid://shopify/Product/2', 'Uploaded Dress', 'DRAFT', 6, '{}')
            """,
            (uploaded_product_id,),
        )
        database.commit()

        queued = queue_shopify_upload_jobs(database=database, limit=10, min_images=5)
        jobs = database.execute("select payload_json from pipeline_jobs where stage = 'shopify_draft_upload'").fetchall()

    assert queued == 1
    assert json.loads(jobs[0][0])["competitor_product_id"] == ready_product_id


@pytest.mark.asyncio
async def test_final_image_generation_uses_fake_images_by_default(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    storage_dir = tmp_path / "storage"
    with open_database(database_url) as database:
        product_id = insert_competitor_product(database, title="Red Maxi Dress")
        database.commit()

    service = FinalCatalogService(Settings(database_url=database_url, storage_dir=storage_dir))
    result = await service.generate_model_image_set(product_id, images_per_product=5)

    assert result["mode"] == "fake"
    with open_database(database_url) as database:
        image_set = database.execute(
            "select status, generated_count from final_image_sets where competitor_product_id = ?",
            (product_id,),
        ).fetchone()
        images = database.execute(
            "select image_path, raw_json from final_generated_images where competitor_product_id = ?",
            (product_id,),
        ).fetchall()
    assert image_set == ("ready", 5)
    assert len(images) == 5
    assert all(json.loads(raw_json)["mode"] == "fake" for _, raw_json in images)
    assert all((storage_dir / "final_model_images" / str(product_id) / Path(image_path).name).exists() for image_path, _ in images)
