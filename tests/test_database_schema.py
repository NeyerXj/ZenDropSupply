from app.database import is_postgres_url, open_database, postgres_sql_from_sqlite_placeholders


def test_pipeline_state_tables_are_created_for_sqlite(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        rows = database.execute(
            """
            select name
            from sqlite_master
            where type = 'table'
            order by name
            """
        ).fetchall()

    table_names = {row[0] for row in rows}
    assert {
        "pipeline_runs",
        "pipeline_jobs",
        "filter_configs",
        "competitor_stores",
        "uploaded_analytics_files",
        "product_matches",
        "generated_contents",
        "generated_images",
        "final_image_sets",
        "final_generated_images",
        "shopify_draft_products",
    }.issubset(table_names)

    with open_database(database_url) as database:
        match_columns = {
            row[1]
            for row in database.execute("pragma table_info(product_matches)").fetchall()
        }

    assert {
        "vision_confidence",
        "vision_reason",
        "vision_verdict_json",
    }.issubset(match_columns)


def test_postgres_url_detection_supports_vps_database_url():
    assert is_postgres_url("postgresql://ttd:secret@postgres:5432/ttd_pipeline")
    assert is_postgres_url("postgres://ttd:secret@postgres:5432/ttd_pipeline")
    assert not is_postgres_url("sqlite:///storage/pipeline.db")


def test_postgres_placeholder_conversion_keeps_existing_sql_shape():
    sql = "select * from competitor_products where store_url = ? and status = ? limit ?"

    assert postgres_sql_from_sqlite_placeholders(sql) == (
        "select * from competitor_products where store_url = %s and status = %s limit %s"
    )
