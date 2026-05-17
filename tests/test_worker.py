import pytest

from app.config import Settings
from app.database import open_database
from app.providers.openai_content import GeneratedProductContent, OpenAIContentClient
from app.services.pipeline_state import claim_next_pipeline_job, complete_pipeline_job, create_competitor_batch_run, enqueue_pipeline_job
from app.services.visual_search_queries import VISUAL_QUERY_CACHE_KEY
from app.services.worker import PipelineWorker
from app.services.zendrop_pipeline import ZendropPipeline


def test_claim_next_pipeline_job_marks_job_running(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        run = create_competitor_batch_run(database, "worker", ["example.com"], pages_requested=1)
        job = claim_next_pipeline_job(database)

    assert job["run_id"] == run["id"]
    assert job["stage"] == "competitor_scrape"
    assert job["status"] == "running"


def test_complete_pipeline_job_stores_result(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        run = create_competitor_batch_run(database, "worker", ["example.com"], pages_requested=1)
        job = claim_next_pipeline_job(database)
        completed = complete_pipeline_job(database, job["id"], {"ok": True})

    assert completed["status"] == "done"
    assert completed["result"] == {"ok": True}
    assert completed["run_id"] == run["id"]


@pytest.mark.asyncio
async def test_worker_process_next_job_completes_successful_dispatch(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    with open_database(settings.database_url) as database:
        run = create_competitor_batch_run(database, "worker", ["example.com"], pages_requested=1)

    async def fake_dispatch(self, job):
        return {"stage": job["stage"], "processed": True}

    monkeypatch.setattr(PipelineWorker, "dispatch", fake_dispatch)

    worker = PipelineWorker(settings=settings)
    job = await worker.process_next_job()

    with open_database(settings.database_url) as database:
        run_status = database.execute("select status from pipeline_runs where id = ?", (run["id"],)).fetchone()[0]

    assert job["status"] == "done"
    assert job["result"] == {"stage": "competitor_scrape", "processed": True}
    assert run_status == "approval_pending"


@pytest.mark.asyncio
async def test_worker_process_next_job_marks_failed_dispatch(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    with open_database(settings.database_url) as database:
        enqueue_pipeline_job(database, run_id=None, stage="bad_stage", payload={})

    async def fake_dispatch(self, job):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(PipelineWorker, "dispatch", fake_dispatch)

    worker = PipelineWorker(settings=settings)
    job = await worker.process_next_job()

    assert job["status"] == "failed"
    assert job["error_message"] == "provider failed"


@pytest.mark.asyncio
async def test_openai_content_job_queues_final_model_images_for_approved_product(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (store_url, handle, title, tags_json, status, raw_json)
            values ('https://example.com', 'dress', 'Floral Dress', '[]', 'ready_for_zendrop', '{}')
            """
        )
        competitor_product_id = database.execute("select id from competitor_products").fetchone()[0]
        database.execute("insert into zendrop_products (product_id, name, raw_json) values (42, 'Zendrop Dress', '{}')")
        database.execute(
            """
            insert into product_matches (
                competitor_product_id, zendrop_product_id, zendrop_match_score, status, total_cost_usd
            )
            values (?, 42, 92, 'approved', 12.0)
            """,
            (competitor_product_id,),
        )
        product_match_id = database.execute("select id from product_matches").fetchone()[0]
        database.commit()

    async def fake_generate_product_content(self, payload):
        return GeneratedProductContent(
            title="Ava Floral Dress",
            description="Premium description",
            size_chart={},
            price_usd=39.0,
            compare_at_price_usd=55.0,
            raw={"mode": "fake"},
        )

    monkeypatch.setattr(OpenAIContentClient, "generate_product_content", fake_generate_product_content)

    worker = PipelineWorker(settings=settings)
    await worker.run_openai_content({"id": 10, "run_id": 77}, {"product_match_id": product_match_id})

    with open_database(settings.database_url) as database:
        jobs = database.execute(
            "select stage, payload_json from pipeline_jobs where stage = 'final_model_images'"
        ).fetchall()

    assert len(jobs) == 1
    assert str(competitor_product_id) in jobs[0][1]


@pytest.mark.asyncio
async def test_zendrop_search_uses_cached_visual_queries_before_title_queries(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    seen_keywords = []

    async def fake_search_and_store(self, keyword, limit, country_code):
        seen_keywords.append(keyword)
        return []

    monkeypatch.setattr(ZendropPipeline, "search_and_store", fake_search_and_store)

    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (store_url, handle, title, tags_json, status, raw_json)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "shoe",
                "Orthopedic Shoes for Women",
                "[]",
                "ready_for_zendrop",
                f'{{"{VISUAL_QUERY_CACHE_KEY}": ["black slip-on shoes", "women orthopedic sneakers"]}}',
            ),
        )
        product_id = database.execute("select id from competitor_products").fetchone()[0]
        database.commit()

    worker = PipelineWorker(settings=settings)
    result = await worker.run_zendrop_search(
        {"id": 1, "run_id": 2},
        {
            "keyword": "women shoes",
            "keywords": ["women shoes", "orthopedic shoes"],
            "competitor_product_id": product_id,
            "limit": 6,
            "country_code": "ca",
        },
    )

    assert seen_keywords[:4] == [
        "black slip-on shoes",
        "black slip-on sneakers",
        "women orthopedic sneakers",
        "women shoes",
    ]
    assert result["keywords"] == seen_keywords
