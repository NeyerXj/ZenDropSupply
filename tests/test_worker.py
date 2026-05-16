import pytest

from app.config import Settings
from app.database import open_database
from app.services.pipeline_state import claim_next_pipeline_job, complete_pipeline_job, create_competitor_batch_run, enqueue_pipeline_job
from app.services.worker import PipelineWorker


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
