from app.database import open_database
from app.services.pipeline_state import create_competitor_batch_run, list_pipeline_jobs, list_pipeline_runs


def test_competitor_batch_run_persists_run_stores_and_jobs(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        run = create_competitor_batch_run(
            database=database,
            name="velanora smoke",
            store_urls=["https://velanora-fashion.com", "https://example.com/"],
            pages_requested=5,
        )
        runs = list_pipeline_runs(database)
        jobs = list_pipeline_jobs(database, run_id=run["id"])
        stores = database.execute(
            """
            select run_id, store_url, pages_requested, status
            from competitor_stores
            order by store_url
            """
        ).fetchall()

    assert run["status"] == "queued"
    assert runs[0]["name"] == "velanora smoke"
    assert [job["stage"] for job in jobs] == ["competitor_scrape", "competitor_scrape"]
    assert stores == [
        (run["id"], "https://example.com", 5, "queued"),
        (run["id"], "https://velanora-fashion.com", 5, "queued"),
    ]


def test_competitor_batch_run_dedupes_blank_and_duplicate_urls(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        run = create_competitor_batch_run(
            database=database,
            name="dedupe",
            store_urls=[" https://example.com/ ", "", "https://example.com", "https://second.com/path/"],
            pages_requested=3,
        )
        jobs = list_pipeline_jobs(database, run_id=run["id"])

    assert [job["payload"]["store_url"] for job in jobs] == ["https://example.com", "https://second.com/path"]
