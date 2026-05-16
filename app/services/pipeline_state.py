from __future__ import annotations

import json
import sqlite3
from typing import Any


ACTIVE_PIPELINE_STAGES = (
    "competitor_scrape",
    "zendrop_search",
    "approval_matching",
    "openai_content",
    "gemini_images",
    "final_model_images",
    "shopify_draft_upload",
)


def create_competitor_batch_run(
    database: sqlite3.Connection,
    name: str,
    store_urls: list[str],
    pages_requested: int,
    product_limit: int | None = None,
) -> dict:
    normalized_urls = normalize_store_urls(store_urls)
    run_id = database.execute(
        """
        insert into pipeline_runs (name, status, raw_input_json, updated_at)
        values (?, 'queued', ?, current_timestamp)
        returning id
        """,
        (
            name.strip() or "Competitor batch",
            json.dumps(
                {"store_urls": normalized_urls, "pages_requested": pages_requested, "product_limit": product_limit},
                ensure_ascii=False,
            ),
        ),
    ).fetchone()[0]
    for store_url in normalized_urls:
        database.execute(
            """
            insert into competitor_stores (run_id, store_url, pages_requested, status, updated_at)
            values (?, ?, ?, 'queued', current_timestamp)
            on conflict(run_id, store_url) do update set
                pages_requested = excluded.pages_requested,
                status = excluded.status,
                updated_at = current_timestamp
            """,
            (run_id, store_url, pages_requested),
        )
        enqueue_pipeline_job(
            database=database,
            run_id=run_id,
            stage="competitor_scrape",
            payload={"store_url": store_url, "pages": pages_requested, "limit": product_limit},
        )
    database.commit()
    return get_pipeline_run(database, run_id)


def enqueue_pipeline_job(database: sqlite3.Connection, run_id: int, stage: str, payload: dict, priority: int = 100) -> dict:
    job_id = database.execute(
        """
        insert into pipeline_jobs (run_id, stage, status, priority, payload_json, updated_at)
        values (?, ?, 'queued', ?, ?, current_timestamp)
        returning id
        """,
        (run_id, stage, priority, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()[0]
    database.commit()
    return get_pipeline_job(database, job_id)


def get_pipeline_run(database: sqlite3.Connection, run_id: int) -> dict:
    row = database.execute(
        """
        select id, name, status, raw_input_json, created_at, updated_at
        from pipeline_runs
        where id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Pipeline run not found: {run_id}")
    return serialize_pipeline_run(row)


def get_pipeline_job(database: sqlite3.Connection, job_id: int) -> dict:
    row = database.execute(
        """
        select id, run_id, stage, status, priority, payload_json, result_json, error_message, created_at, updated_at
        from pipeline_jobs
        where id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Pipeline job not found: {job_id}")
    return serialize_pipeline_job(row)


def list_pipeline_runs(database: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = database.execute(
        """
        select id, name, status, raw_input_json, created_at, updated_at
        from pipeline_runs
        order by created_at desc, id desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [serialize_pipeline_run(row) for row in rows]


def list_pipeline_jobs(database: sqlite3.Connection, run_id: int, limit: int = 100) -> list[dict]:
    rows = database.execute(
        """
        select id, run_id, stage, status, priority, payload_json, result_json, error_message, created_at, updated_at
        from pipeline_jobs
        where run_id = ?
        order by priority asc, id asc
        limit ?
        """,
        (run_id, limit),
    ).fetchall()
    return [serialize_pipeline_job(row) for row in rows]


def get_pipeline_activity(database: sqlite3.Connection, limit: int = 20) -> dict[str, Any]:
    stage_placeholders = ",".join("?" for _ in ACTIVE_PIPELINE_STAGES)
    summary_rows = database.execute(
        f"""
        select stage, status, count(*)
        from pipeline_jobs
        where stage in ({stage_placeholders})
        group by stage, status
        order by stage, status
        """,
        ACTIVE_PIPELINE_STAGES,
    ).fetchall()
    active_rows = database.execute(
        f"""
        select id, run_id, stage, status, priority, payload_json, result_json, error_message,
            created_at, updated_at, locked_at
        from pipeline_jobs
        where status in ('queued', 'running')
          and stage in ({stage_placeholders})
        order by
            case status when 'running' then 0 when 'queued' then 1 else 2 end,
            priority asc,
            id asc
        limit ?
        """,
        (*ACTIVE_PIPELINE_STAGES, limit),
    ).fetchall()
    failed_rows = database.execute(
        f"""
        select id, run_id, stage, status, priority, payload_json, result_json, error_message,
            created_at, updated_at, locked_at
        from pipeline_jobs
        where status = 'failed'
          and stage in ({stage_placeholders})
        order by updated_at desc, id desc
        limit ?
        """,
        (*ACTIVE_PIPELINE_STAGES, limit),
    ).fetchall()
    return {
        "summary": [
            {"stage": row[0], "status": row[1], "count": row[2]}
            for row in summary_rows
        ],
        "active_jobs": [serialize_pipeline_activity_job(row) for row in active_rows],
        "failed_jobs": [serialize_pipeline_activity_job(row) for row in failed_rows],
    }


def claim_next_pipeline_job(database: sqlite3.Connection) -> dict | None:
    row = database.execute(
        """
        select id
        from pipeline_jobs
        where status = 'queued'
        order by priority asc, id asc
        limit 1
        """
    ).fetchone()
    if row is None:
        return None
    job_id = row[0]
    database.execute(
        """
        update pipeline_jobs
        set status = 'running', locked_at = current_timestamp, updated_at = current_timestamp
        where id = ?
        """,
        (job_id,),
    )
    database.commit()
    return get_pipeline_job(database, job_id)


def complete_pipeline_job(database: sqlite3.Connection, job_id: int, result: dict[str, Any]) -> dict:
    database.execute(
        """
        update pipeline_jobs
        set status = 'done', result_json = ?, error_message = null, updated_at = current_timestamp
        where id = ?
        """,
        (json.dumps(result, ensure_ascii=False), job_id),
    )
    database.commit()
    return get_pipeline_job(database, job_id)


def fail_pipeline_job(database: sqlite3.Connection, job_id: int, error_message: str) -> dict:
    database.execute(
        """
        update pipeline_jobs
        set status = 'failed', error_message = ?, updated_at = current_timestamp
        where id = ?
        """,
        (error_message, job_id),
    )
    database.commit()
    return get_pipeline_job(database, job_id)


def update_pipeline_run_status(database: sqlite3.Connection, run_id: int | None, status: str) -> None:
    if run_id is None:
        return
    database.execute(
        """
        update pipeline_runs
        set status = ?, updated_at = current_timestamp
        where id = ?
        """,
        (status, run_id),
    )
    database.commit()


def normalize_store_urls(store_urls: list[str]) -> list[str]:
    normalized_urls: list[str] = []
    seen: set[str] = set()
    for store_url in store_urls:
        normalized_url = normalize_store_url(store_url)
        if normalized_url and normalized_url not in seen:
            normalized_urls.append(normalized_url)
            seen.add(normalized_url)
    return normalized_urls


def normalize_store_url(store_url: str) -> str:
    value = store_url.strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def serialize_pipeline_run(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "status": row[2],
        "raw_input": json.loads(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


def serialize_pipeline_job(row) -> dict:
    return {
        "id": row[0],
        "run_id": row[1],
        "stage": row[2],
        "status": row[3],
        "priority": row[4],
        "payload": json.loads(row[5]),
        "result": json.loads(row[6]),
        "error_message": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


def serialize_pipeline_activity_job(row) -> dict:
    return {
        "id": row[0],
        "run_id": row[1],
        "stage": row[2],
        "status": row[3],
        "priority": row[4],
        "payload": json.loads(row[5]),
        "result": json.loads(row[6]),
        "error_message": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "locked_at": row[10],
    }
