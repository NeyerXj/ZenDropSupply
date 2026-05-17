from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import Lock
from types import TracebackType
from typing import Any

import psycopg


POSTGRES_SCHEMA_LOCK = Lock()


SCHEMA = """
create table if not exists zendrop_products (
    product_id integer primary key,
    name text not null,
    description text,
    price_usd real,
    image_url text,
    raw_json text not null,
    shipping_country_code text,
    shipping_price_usd real,
    shipping_estimated_delivery text,
    updated_at text not null default current_timestamp
);

create table if not exists competitor_products (
    id integer primary key autoincrement,
    store_url text not null,
    external_id text,
    handle text not null,
    title text not null,
    product_type text,
    tags_json text not null,
    price real,
    image_url text,
    image_path text,
    status text not null,
    raw_json text not null,
    updated_at text not null default current_timestamp,
    unique(store_url, handle)
);

create table if not exists product_matches (
    id integer primary key autoincrement,
    competitor_product_id integer not null,
    zendrop_product_id integer not null,
    zendrop_match_score real not null,
    visual_status text not null default 'pending',
    vision_confidence real,
    vision_reason text,
    vision_verdict_json text not null default '{}',
    status text not null default 'approval_pending',
    manual_supplier_url text,
    total_cost_usd real,
    suggested_price_usd real,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(competitor_product_id) references competitor_products(id),
    foreign key(zendrop_product_id) references zendrop_products(product_id),
    unique(competitor_product_id, zendrop_product_id)
);

create table if not exists generated_contents (
    id integer primary key autoincrement,
    product_match_id integer not null unique,
    title text not null,
    description text not null,
    size_chart_json text not null default '{}',
    price_usd real,
    compare_at_price_usd real,
    raw_json text not null default '{}',
    status text not null default 'ready_for_images',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(product_match_id) references product_matches(id)
);

create table if not exists generated_images (
    id integer primary key autoincrement,
    product_match_id integer not null,
    color_name text,
    prompt text not null,
    image_url text,
    image_path text,
    qc_status text not null default 'pending',
    raw_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(product_match_id) references product_matches(id)
);

create table if not exists final_image_sets (
    id integer primary key autoincrement,
    competitor_product_id integer not null unique,
    target_count integer not null default 6,
    identity_image_path text,
    status text not null default 'queued',
    generated_count integer not null default 0,
    error_message text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(competitor_product_id) references competitor_products(id)
);

create table if not exists final_generated_images (
    id integer primary key autoincrement,
    image_set_id integer not null,
    competitor_product_id integer not null,
    shot_key text not null,
    prompt text not null,
    image_path text not null,
    qc_status text not null default 'ready',
    raw_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(image_set_id) references final_image_sets(id),
    foreign key(competitor_product_id) references competitor_products(id),
    unique(image_set_id, shot_key)
);

create table if not exists shopify_draft_products (
    id integer primary key autoincrement,
    competitor_product_id integer not null,
    shopify_product_id text not null unique,
    title text not null,
    status text not null,
    media_count integer not null default 0,
    raw_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(competitor_product_id) references competitor_products(id)
);

create table if not exists pipeline_runs (
    id integer primary key autoincrement,
    name text not null,
    status text not null,
    raw_input_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists pipeline_jobs (
    id integer primary key autoincrement,
    run_id integer,
    stage text not null,
    status text not null,
    priority integer not null default 100,
    payload_json text not null default '{}',
    result_json text not null default '{}',
    error_message text,
    locked_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(run_id) references pipeline_runs(id)
);

create table if not exists filter_configs (
    id integer primary key autoincrement,
    name text not null unique,
    women_keywords_json text not null,
    male_keywords_json text not null,
    summer_keywords_json text not null,
    exclude_keywords_json text not null,
    active integer not null default 0,
    updated_at text not null default current_timestamp
);

create table if not exists competitor_stores (
    id integer primary key autoincrement,
    run_id integer,
    store_url text not null,
    status text not null default 'queued',
    pages_requested integer not null default 5,
    raw_products_count integer not null default 0,
    filtered_products_count integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    foreign key(run_id) references pipeline_runs(id),
    unique(run_id, store_url)
);

create table if not exists uploaded_analytics_files (
    id integer primary key autoincrement,
    run_id integer,
    filename text not null,
    storage_path text not null,
    source_store_url text,
    parsed_products_count integer not null default 0,
    created_at text not null default current_timestamp,
    foreign key(run_id) references pipeline_runs(id)
);
"""

POSTGRES_SCHEMA = """
create table if not exists zendrop_products (
    product_id bigint primary key,
    name text not null,
    description text,
    price_usd double precision,
    image_url text,
    raw_json text not null,
    shipping_country_code text,
    shipping_price_usd double precision,
    shipping_estimated_delivery text,
    updated_at timestamp not null default current_timestamp
);

create table if not exists competitor_products (
    id bigserial primary key,
    store_url text not null,
    external_id text,
    handle text not null,
    title text not null,
    product_type text,
    tags_json text not null,
    price double precision,
    image_url text,
    image_path text,
    status text not null,
    raw_json text not null,
    updated_at timestamp not null default current_timestamp,
    unique(store_url, handle)
);

create table if not exists product_matches (
    id bigserial primary key,
    competitor_product_id bigint not null references competitor_products(id),
    zendrop_product_id bigint not null references zendrop_products(product_id),
    zendrop_match_score double precision not null,
    visual_status text not null default 'pending',
    vision_confidence double precision,
    vision_reason text,
    vision_verdict_json text not null default '{}',
    status text not null default 'approval_pending',
    manual_supplier_url text,
    total_cost_usd double precision,
    suggested_price_usd double precision,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp,
    unique(competitor_product_id, zendrop_product_id)
);

create table if not exists generated_contents (
    id bigserial primary key,
    product_match_id bigint not null unique references product_matches(id),
    title text not null,
    description text not null,
    size_chart_json text not null default '{}',
    price_usd double precision,
    compare_at_price_usd double precision,
    raw_json text not null default '{}',
    status text not null default 'ready_for_images',
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists generated_images (
    id bigserial primary key,
    product_match_id bigint not null references product_matches(id),
    color_name text,
    prompt text not null,
    image_url text,
    image_path text,
    qc_status text not null default 'pending',
    raw_json text not null default '{}',
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists final_image_sets (
    id bigserial primary key,
    competitor_product_id bigint not null unique references competitor_products(id),
    target_count integer not null default 6,
    identity_image_path text,
    status text not null default 'queued',
    generated_count integer not null default 0,
    error_message text,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists final_generated_images (
    id bigserial primary key,
    image_set_id bigint not null references final_image_sets(id),
    competitor_product_id bigint not null references competitor_products(id),
    shot_key text not null,
    prompt text not null,
    image_path text not null,
    qc_status text not null default 'ready',
    raw_json text not null default '{}',
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp,
    unique(image_set_id, shot_key)
);

create table if not exists shopify_draft_products (
    id bigserial primary key,
    competitor_product_id bigint not null references competitor_products(id),
    shopify_product_id text not null unique,
    title text not null,
    status text not null,
    media_count integer not null default 0,
    raw_json text not null default '{}',
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists pipeline_runs (
    id bigserial primary key,
    name text not null,
    status text not null,
    raw_input_json text not null default '{}',
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists pipeline_jobs (
    id bigserial primary key,
    run_id bigint references pipeline_runs(id),
    stage text not null,
    status text not null,
    priority integer not null default 100,
    payload_json text not null default '{}',
    result_json text not null default '{}',
    error_message text,
    locked_at timestamp,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists filter_configs (
    id bigserial primary key,
    name text not null unique,
    women_keywords_json text not null,
    male_keywords_json text not null,
    summer_keywords_json text not null,
    exclude_keywords_json text not null,
    active boolean not null default false,
    updated_at timestamp not null default current_timestamp
);

create table if not exists competitor_stores (
    id bigserial primary key,
    run_id bigint references pipeline_runs(id),
    store_url text not null,
    status text not null default 'queued',
    pages_requested integer not null default 5,
    raw_products_count integer not null default 0,
    filtered_products_count integer not null default 0,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp,
    unique(run_id, store_url)
);

create table if not exists uploaded_analytics_files (
    id bigserial primary key,
    run_id bigint references pipeline_runs(id),
    filename text not null,
    storage_path text not null,
    source_store_url text,
    parsed_products_count integer not null default 0,
    created_at timestamp not null default current_timestamp
);
"""


def is_postgres_url(database_url: str) -> bool:
    return database_url.startswith("postgresql://") or database_url.startswith("postgres://")


def postgres_sql_from_sqlite_placeholders(sql: str) -> str:
    return sql.replace("?", "%s")


def ensure_schema_migrations(connection: sqlite3.Connection | "PostgresConnection", database_url: str) -> None:
    if is_postgres_url(database_url):
        existing_columns = {
            row[0]
            for row in connection.execute(
                """
                select column_name
                from information_schema.columns
                where table_name = 'product_matches'
                """
            ).fetchall()
        }
        column_definitions = {
            "vision_confidence": "double precision",
            "vision_reason": "text",
            "vision_verdict_json": "text not null default '{}'",
        }
        for column_name, column_definition in column_definitions.items():
            if column_name not in existing_columns:
                connection.execute(f"alter table product_matches add column {column_name} {column_definition}")
        connection.commit()
        return

    existing_columns = {
        row[1]
        for row in connection.execute("pragma table_info(product_matches)").fetchall()
    }
    column_definitions = {
        "vision_confidence": "real",
        "vision_reason": "text",
        "vision_verdict_json": "text not null default '{}'",
    }
    for column_name, column_definition in column_definitions.items():
        if column_name not in existing_columns:
            connection.execute(f"alter table product_matches add column {column_name} {column_definition}")
    connection.commit()


def sqlite_path_from_url(database_url: str) -> Path:
    if database_url == "sqlite:///:memory:":
        return Path(":memory:")
    if database_url.startswith("sqlite:////"):
        return Path("/" + database_url.removeprefix("sqlite:////"))
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported database URL: {database_url}")


class DatabaseContext:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.connection: sqlite3.Connection | PostgresConnection | None = None

    def __enter__(self) -> sqlite3.Connection | "PostgresConnection":
        self.connection = self._connect()
        return self.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    async def __aenter__(self) -> sqlite3.Connection | "PostgresConnection":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc_value, traceback)

    def _connect(self) -> sqlite3.Connection | "PostgresConnection":
        if is_postgres_url(self.database_url):
            connection = psycopg.connect(self.database_url)
            with POSTGRES_SCHEMA_LOCK:
                with connection.cursor() as cursor:
                    cursor.execute(POSTGRES_SCHEMA)
                connection.commit()
            postgres_connection = PostgresConnection(connection)
            ensure_schema_migrations(postgres_connection, self.database_url)
            return postgres_connection

        database_path = sqlite_path_from_url(self.database_url)
        if str(database_path) != ":memory:":
            database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, check_same_thread=False)
        connection.execute("pragma journal_mode=wal")
        connection.executescript(SCHEMA)
        connection.commit()
        ensure_schema_migrations(connection, self.database_url)
        return connection


class PostgresConnection:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, parameters: Any = None) -> psycopg.Cursor:
        translated_sql = postgres_sql_from_sqlite_placeholders(sql)
        return self.connection.execute(translated_sql, parameters)

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def open_database(database_url: str) -> DatabaseContext:
    return DatabaseContext(database_url)
