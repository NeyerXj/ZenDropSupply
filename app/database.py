from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


SCHEMA_LOCK_ID = 88420116
_schema_lock = Lock()
_schema_ready = False

SCHEMA_SQL = """
create table if not exists harvest_runs (
    id bigserial primary key,
    status text not null default 'queued',
    target_unique integer not null default 100000,
    requested_origin_country_code text not null default 'cn',
    destination_country_code text not null default 'us',
    category_id integer not null default 16,
    category_name text not null default 'Apparel & Accessories',
    first_image_only boolean not null default true,
    keywords_json text not null default '[""]',
    per_page_limit integer not null default 60,
    max_pages_per_keyword integer not null default 2000,
    fetch_shipping boolean not null default false,
    unique_products integer not null default 0,
    fetched_products integer not null default 0,
    duplicate_products integer not null default 0,
    pages_done integer not null default 0,
    pages_failed integer not null default 0,
    rate_limit_hits integer not null default 0,
    error_message text,
    started_at timestamp,
    completed_at timestamp,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists harvest_pages (
    id bigserial primary key,
    run_id bigint not null references harvest_runs(id) on delete cascade,
    keyword text not null,
    page integer not null,
    status text not null default 'queued',
    claimed_by text,
    claimed_until timestamp,
    product_count integer not null default 0,
    new_product_count integer not null default 0,
    duplicate_product_count integer not null default 0,
    duration_ms integer,
    error_message text,
    started_at timestamp,
    completed_at timestamp,
    created_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp,
    unique(run_id, keyword, page)
);

create table if not exists supply_products (
    product_id bigint primary key,
    name text not null,
    description text,
    price_usd double precision,
    image_url text,
    requested_origin_country_code text not null default 'cn',
    origin_verified boolean not null default false,
    destination_country_code text not null default 'us',
    raw_json jsonb not null default '{}',
    first_seen_at timestamp not null default current_timestamp,
    updated_at timestamp not null default current_timestamp
);

create table if not exists supply_product_images (
    id bigserial primary key,
    product_id bigint not null references supply_products(product_id) on delete cascade,
    image_url text not null,
    position integer not null default 0,
    created_at timestamp not null default current_timestamp,
    unique(product_id, image_url)
);

create table if not exists supply_shipping_estimates (
    id bigserial primary key,
    product_id bigint not null references supply_products(product_id) on delete cascade,
    destination_country_code text not null,
    shipping_type text,
    shipping_price_usd double precision,
    estimated_delivery text,
    raw_json jsonb not null default '{}',
    updated_at timestamp not null default current_timestamp,
    unique(product_id, destination_country_code)
);

create table if not exists harvest_workers (
    worker_id text primary key,
    status text not null default 'online',
    desired_status text not null default 'enabled',
    current_run_id bigint,
    current_page_id bigint,
    processed_pages integer not null default 0,
    processed_products integer not null default 0,
    last_error text,
    started_at timestamp not null default current_timestamp,
    heartbeat_at timestamp not null default current_timestamp
);

create index if not exists idx_harvest_pages_claim
    on harvest_pages(run_id, status, claimed_until, id);
create index if not exists idx_harvest_pages_recent
    on harvest_pages(run_id, completed_at desc);
create index if not exists idx_supply_products_destination
    on supply_products(destination_country_code);

alter table harvest_runs add column if not exists category_id integer not null default 16;
alter table harvest_runs add column if not exists category_name text not null default 'Apparel & Accessories';
alter table harvest_runs add column if not exists first_image_only boolean not null default true;
alter table harvest_workers add column if not exists desired_status text not null default 'enabled';
"""


@contextmanager
def open_database(database_url: str) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        ensure_schema(connection)
        yield connection
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_schema(connection: psycopg.Connection) -> None:
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute("select pg_advisory_lock(%s)", (SCHEMA_LOCK_ID,))
                cursor.execute(SCHEMA_SQL)
                cursor.execute("select pg_advisory_unlock(%s)", (SCHEMA_LOCK_ID,))
            connection.commit()
            _schema_ready = True
        except Exception:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute("select pg_advisory_unlock(%s)", (SCHEMA_LOCK_ID,))
            connection.commit()
            raise
