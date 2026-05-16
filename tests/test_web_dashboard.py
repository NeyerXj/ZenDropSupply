import json

from fastapi.testclient import TestClient

from app.config import Settings, ZendropSettings
from app.database import open_database
from app.web import create_app


def authenticated_client(settings):
    client = TestClient(create_app(settings=settings))
    response = client.post("/api/login", json={"username": settings.admin.username, "password": settings.admin.password})
    assert response.status_code == 200
    return client


def test_dashboard_page_serves_operational_shell(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    client = authenticated_client(settings)

    response = client.get("/")

    assert response.status_code == 200
    assert "TTD Pipeline Control" in response.text
    assert "Admin pipeline" in response.text


def test_dashboard_requires_login_for_api(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    client = TestClient(create_app(settings=settings))

    page_response = client.get("/")
    api_response = client.get("/api/summary")

    assert page_response.status_code == 200
    assert "Enter admin panel" in page_response.text
    assert api_response.status_code == 401


def test_dashboard_summary_counts_pipeline_records(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, tags_json, status, raw_json
            )
            values
                ('https://example.com', 'floral-maxi-dress', 'Floral Maxi Dress', '[]', 'ready_for_zendrop', '{}'),
                ('https://example.com', 'mens-jacket', 'Mens Jacket', '[]', 'skipped_male', '{}')
            """
        )
        database.execute(
            """
            insert into zendrop_products (product_id, name, raw_json, shipping_country_code)
            values (2331830, 'Lace V-Neck Maxi Dress', '{}', 'ca')
            """
        )
        database.execute(
            """
            insert into product_matches (
                competitor_product_id, zendrop_product_id, zendrop_match_score, status
            )
            values (1, 2331830, 90, 'approval_pending')
            """
        )
        database.commit()

    client = authenticated_client(settings)

    response = client.get("/api/summary")

    assert response.status_code == 200
    assert response.json() == {
        "preview_cards_total": 1,
        "competitor_total": 2,
        "ready_for_zendrop": 1,
        "zendrop_total": 1,
        "manual_approved_total": 0,
        "final_images_total": 0,
        "shopify_draft_total": 0,
        "status_counts": {
            "ready_for_zendrop": 1,
            "skipped_male": 1,
        },
    }


def test_dashboard_lists_candidates_and_updates_status(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    image_path = settings.storage_dir / "competitor_images" / "floral-maxi-dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "floral-maxi-dress",
                "Floral Maxi Dress",
                59.99,
                str(image_path),
                json.dumps(["Women", "Summer"]),
                "ready_for_zendrop",
                "{}",
            ),
        )
        product_id = database.execute("select id from competitor_products").fetchone()[0]
        database.commit()

    client = authenticated_client(settings)

    list_response = client.get("/api/competitor-products?status=ready_for_zendrop")
    update_response = client.post(
        f"/api/competitor-products/{product_id}/status",
        json={"status": "ready_for_zendrop"},
    )
    updated_response = client.get("/api/competitor-products?status=ready_for_zendrop")

    assert list_response.status_code == 200
    assert list_response.json()["products"][0]["title"] == "Floral Maxi Dress"
    assert list_response.json()["products"][0]["image_url"] == "/media/competitor_images/floral-maxi-dress.jpg"
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "ready_for_zendrop"
    assert updated_response.json()["products"][0]["status"] == "ready_for_zendrop"
    with open_database(settings.database_url) as database:
        jobs = database.execute("select stage, status, payload_json from pipeline_jobs order by id").fetchall()
    assert [job[0] for job in jobs] == ["zendrop_search"]
    assert all(job[1] == "queued" for job in jobs)


def test_dashboard_persists_filter_config(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    client = authenticated_client(settings)

    update_response = client.put(
        "/api/filter-config",
        json={
            "name": "summer canada",
            "women_keywords": ["women", "dress"],
            "male_keywords": ["men"],
            "summer_keywords": ["linen", "sandal"],
            "exclude_keywords": ["winter"],
        },
    )
    get_response = client.get("/api/filter-config")

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json() == {
        "name": "summer canada",
        "women_keywords": ["women", "dress"],
        "male_keywords": ["men"],
        "summer_keywords": ["linen", "sandal"],
        "exclude_keywords": ["winter"],
    }


def test_dashboard_creates_batch_pipeline_run(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    client = authenticated_client(settings)

    response = client.post(
        "/api/runs",
        json={
            "name": "velanora smoke",
            "store_urls": ["velanora-fashion.com", "https://example.com/"],
            "pages_requested": 5,
            "product_limit": 20,
        },
    )
    list_response = client.get("/api/runs")

    assert response.status_code == 200
    assert response.json()["run"]["raw_input"] == {
        "store_urls": ["https://velanora-fashion.com", "https://example.com"],
        "pages_requested": 5,
        "product_limit": 20,
    }
    assert response.json()["jobs_count"] == 2
    assert response.json()["jobs"][0]["payload"]["limit"] == 20
    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["name"] == "velanora smoke"


def test_dashboard_exposes_job_status_for_ui_state(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into pipeline_jobs (stage, status, priority, payload_json, result_json, error_message, locked_at)
            values
                ('zendrop_search', 'running', 120, '{"keyword":"dress"}', '{}', null, current_timestamp),
                ('approval_matching', 'failed', 130, '{}', '{}', 'bad match', current_timestamp)
            """
        )
        database.commit()
    client = authenticated_client(settings)

    response = client.get("/api/job-status")

    assert response.status_code == 200
    payload = response.json()
    assert {"stage": "zendrop_search", "status": "running", "count": 1} in payload["summary"]
    assert payload["active_jobs"][0]["stage"] == "zendrop_search"
    assert payload["active_jobs"][0]["locked_at"] is not None
    assert payload["failed_jobs"][0]["error_message"] == "bad match"


def test_dashboard_uploads_analytics_text_file(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    client = authenticated_client(settings)

    response = client.post(
        "/api/uploads/analytics-files",
        json={"filename": "analytics.txt", "content": "product html", "source_store_url": "https://example.com"},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    with open_database(settings.database_url) as database:
        row = database.execute(
            "select filename, source_store_url, storage_path from uploaded_analytics_files"
        ).fetchone()
    assert row[0] == "analytics.txt"
    assert row[1] == "https://example.com"
    assert (tmp_path / "storage" / "analytics_uploads").exists()


def test_dashboard_builds_and_lists_approval_cards(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    image_path = settings.storage_dir / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "floral-maxi-dress",
                "Floral Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (2331830, 'Floral Maxi Dress for Women', 12.5, 'https://file.zendrop.com/dress.webp', '{}', 'ca', 10.0)
            """
        )
        database.commit()

    client = authenticated_client(settings)

    build_response = client.post("/api/run/approval-matching")
    list_response = client.get("/api/approval-cards")

    assert build_response.status_code == 200
    assert build_response.json() == {"count": 1, "matches_created": 1}
    assert list_response.status_code == 200
    assert list_response.json()["cards"][0]["zendrop"]["total_cost_usd"] == 22.5


def test_dashboard_approves_card_and_queues_openai_content(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    with open_database(settings.database_url) as database:
        run_id = database.execute(
            "insert into pipeline_runs (name, status) values ('content run', 'approval_pending') returning id"
        ).fetchone()[0]
        database.execute(
            """
            insert into competitor_products (store_url, handle, title, tags_json, status, raw_json)
            values ('https://example.com', 'dress', 'Floral Dress', '[]', 'ready_for_zendrop', '{}')
            """
        )
        competitor_product_id = database.execute("select id from competitor_products").fetchone()[0]
        database.execute(
            "insert into zendrop_products (product_id, name, raw_json) values (2331830, 'Floral Dress', '{}')"
        )
        database.execute(
            """
            insert into product_matches (
                competitor_product_id, zendrop_product_id, zendrop_match_score, status
            )
            values (?, 2331830, 90, 'approval_pending')
            """,
            (competitor_product_id,),
        )
        product_match_id = database.execute("select id from product_matches").fetchone()[0]
        database.execute(
            """
            insert into pipeline_jobs (run_id, stage, status, payload_json, result_json)
            values (?, 'approval_matching', 'done', '{}', '{}')
            """,
            (run_id,),
        )
        database.commit()

    client = authenticated_client(settings)

    response = client.post(f"/api/approval-cards/{product_match_id}/status", json={"status": "approved"})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["content_job_queued"] is True
    with open_database(settings.database_url) as database:
        jobs = database.execute("select stage, status, payload_json from pipeline_jobs where stage = 'openai_content'").fetchall()
    assert len(jobs) == 1
    assert jobs[0][0] == "openai_content"
    assert jobs[0][1] == "queued"
    assert str(product_match_id) in jobs[0][2]

    repeat_response = client.post(f"/api/approval-cards/{product_match_id}/status", json={"status": "approved"})

    assert repeat_response.status_code == 200
    assert repeat_response.json()["content_job_queued"] is False
    with open_database(settings.database_url) as database:
        jobs_count = database.execute("select count(*) from pipeline_jobs where stage = 'openai_content'").fetchone()[0]
    assert jobs_count == 1


def test_dashboard_rejects_card_and_cancels_pending_approval_work(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}", storage_dir=tmp_path / "storage")
    with open_database(settings.database_url) as database:
        database.execute(
            """
            insert into competitor_products (store_url, handle, title, tags_json, status, raw_json)
            values ('https://example.com', 'dress', 'Floral Dress', '[]', 'ready_for_zendrop', '{}')
            """
        )
        competitor_product_id = database.execute("select id from competitor_products").fetchone()[0]
        database.execute(
            "insert into zendrop_products (product_id, name, raw_json) values (2331830, 'Floral Dress', '{}')"
        )
        database.execute(
            """
            insert into product_matches (
                competitor_product_id, zendrop_product_id, zendrop_match_score, status
            )
            values (?, 2331830, 90, 'approved')
            """,
            (competitor_product_id,),
        )
        product_match_id = database.execute("select id from product_matches").fetchone()[0]
        database.execute(
            """
            insert into pipeline_jobs (stage, status, payload_json, result_json)
            values ('openai_content', 'queued', ?, '{}')
            """,
            (json.dumps({"product_match_id": product_match_id}),),
        )
        database.execute(
            """
            insert into pipeline_jobs (stage, status, payload_json, result_json)
            values ('final_model_images', 'running', ?, '{}')
            """,
            (json.dumps({"competitor_product_id": competitor_product_id}),),
        )
        database.execute(
            """
            insert into generated_contents (product_match_id, title, description, size_chart_json, raw_json)
            values (?, 'Title', 'Description', '{}', '{}')
            """,
            (product_match_id,),
        )
        database.commit()

    client = authenticated_client(settings)

    response = client.post(f"/api/approval-cards/{product_match_id}/status", json={"status": "rejected"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["canceled_jobs"] == 2
    with open_database(settings.database_url) as database:
        match_status = database.execute("select status from product_matches where id = ?", (product_match_id,)).fetchone()[0]
        canceled_jobs = database.execute("select count(*) from pipeline_jobs where status = 'canceled'").fetchone()[0]
        content_rows = database.execute("select count(*) from generated_contents").fetchone()[0]
    assert match_status == "rejected"
    assert canceled_jobs == 2
    assert content_rows == 0


def test_zendrop_run_requires_api_token(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pipeline.db'}",
        storage_dir=tmp_path / "storage",
        zendrop=ZendropSettings(api_token=""),
    )
    client = authenticated_client(settings)

    response = client.post("/api/run/zendrop-search", json={"keyword": "maxi dress", "limit": 3, "country_code": "ca"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Zendrop API token is not configured"
