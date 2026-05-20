from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import time
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.config import Settings
from app.providers.zendrop import ZendropMcpClient, ZendropProductSummary


DEFAULT_KEYWORDS = [""]
DEFAULT_CATEGORY_ID = 16
DEFAULT_CATEGORY_NAME = "Apparel & Accessories"
RATE_LIMIT_COOLDOWN_SECONDS = 180
EMPTY_PAGE_STOP_THRESHOLD = 20


@dataclass(frozen=True)
class HarvestRunRequest:
    target_unique: int = 100000
    requested_origin_country_code: str = "cn"
    destination_country_code: str = "us"
    keywords: list[str] | None = None
    category_id: int = DEFAULT_CATEGORY_ID
    category_name: str = DEFAULT_CATEGORY_NAME
    first_image_only: bool = True
    per_page_limit: int = 60
    max_pages_per_keyword: int = 5000
    fetch_shipping: bool = False


def normalize_keywords(keywords: list[str] | None) -> list[str]:
    raw_keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
    seen: set[str] = set()
    normalized_keywords: list[str] = []
    for keyword in raw_keywords:
        normalized = " ".join(str(keyword).strip().split())
        key = normalized.lower()
        if key not in seen:
            normalized_keywords.append(normalized)
            seen.add(key)
    return normalized_keywords or [""]


def create_run(connection: psycopg.Connection, request: HarvestRunRequest) -> dict[str, Any]:
    keywords = normalize_keywords(request.keywords)
    with connection.cursor() as cursor:
        row = cursor.execute(
            """
            insert into harvest_runs (
                status, target_unique, requested_origin_country_code, destination_country_code,
                category_id, category_name, first_image_only,
                keywords_json, per_page_limit, max_pages_per_keyword, fetch_shipping, updated_at
            )
            values ('queued', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, current_timestamp)
            returning id
            """,
            (
                request.target_unique,
                request.requested_origin_country_code.lower(),
                request.destination_country_code.lower(),
                request.category_id,
                request.category_name,
                request.first_image_only,
                json.dumps(keywords, ensure_ascii=False),
                request.per_page_limit,
                request.max_pages_per_keyword,
                request.fetch_shipping,
            ),
        ).fetchone()
        run_id = int(row["id"])
        seed_pages(cursor, run_id, keywords, request.max_pages_per_keyword)
    connection.commit()
    return get_run(connection, run_id)


def seed_pages(cursor: psycopg.Cursor, run_id: int, keywords: list[str], max_pages_per_keyword: int) -> None:
    for keyword in keywords:
        cursor.executemany(
            """
            insert into harvest_pages (run_id, keyword, page)
            values (%s, %s, %s)
            on conflict(run_id, keyword, page) do nothing
            """,
            [(run_id, keyword, page) for page in range(1, max_pages_per_keyword + 1)],
        )


def get_run(connection: psycopg.Connection, run_id: int) -> dict[str, Any]:
    row = connection.execute("select * from harvest_runs where id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Harvest run {run_id} does not exist")
    return serialize_run(row)


def latest_run(connection: psycopg.Connection) -> dict[str, Any] | None:
    row = connection.execute("select * from harvest_runs order by id desc limit 1").fetchone()
    return serialize_run(row) if row else None


def serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    run = dict(row)
    run["keywords"] = json.loads(run.pop("keywords_json") or "[]")
    run["progress"] = min(1.0, run["unique_products"] / max(1, run["target_unique"]))
    return run


def update_run_status(connection: psycopg.Connection, run_id: int, status: str) -> dict[str, Any]:
    if status not in {"queued", "running", "paused", "canceled"}:
        raise ValueError("Unsupported run status")
    completed_at_sql = ", completed_at = current_timestamp" if status == "canceled" else ""
    connection.execute(
        f"""
        update harvest_runs
        set status = %s, error_message = null, updated_at = current_timestamp {completed_at_sql}
        where id = %s
        """,
        (status, run_id),
    )
    connection.commit()
    return get_run(connection, run_id)


def update_worker_desired_status(connection: psycopg.Connection, worker_id: str, desired_status: str) -> dict[str, Any]:
    if desired_status not in {"enabled", "disabled"}:
        raise ValueError("Unsupported worker status")
    row = connection.execute(
        """
        update harvest_workers
        set desired_status = %s,
            status = case when %s = 'disabled' then 'disabled' else status end,
            current_run_id = case when %s = 'disabled' then null else current_run_id end,
            current_page_id = case when %s = 'disabled' then null else current_page_id end,
            heartbeat_at = current_timestamp
        where worker_id = %s
        returning *
        """,
        (desired_status, desired_status, desired_status, desired_status, worker_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Worker {worker_id} does not exist")
    connection.commit()
    return dict(row)


def dashboard_snapshot(connection: psycopg.Connection, settings: Settings) -> dict[str, Any]:
    run = latest_run(connection)
    workers = [
        dict(row)
        for row in connection.execute(
            """
            select worker_id, status, current_run_id, current_page_id, processed_pages, processed_products,
                   desired_status, last_error, started_at, heartbeat_at,
                   extract(epoch from (current_timestamp - heartbeat_at))::int as seconds_since_heartbeat
            from harvest_workers
            order by heartbeat_at desc
            """
        ).fetchall()
    ]
    if not run:
        return {
            "run": None,
            "workers": workers,
            "recent_pages": [],
            "metrics": empty_metrics(settings),
            "worker_setup": worker_setup(settings),
        }
    recent_pages = [
        dict(row)
        for row in connection.execute(
            """
            select keyword, page, status, claimed_by, product_count, new_product_count,
                   duplicate_product_count, duration_ms, error_message, updated_at
            from harvest_pages
            where run_id = %s
            order by updated_at desc, id desc
            limit 30
            """,
            (run["id"],),
        ).fetchall()
    ]
    metrics = calculate_metrics(connection, run, settings)
    return {
        "run": run,
        "workers": workers,
        "recent_pages": recent_pages,
        "metrics": metrics,
        "worker_setup": worker_setup(settings),
    }


def empty_metrics(settings: Settings) -> dict[str, Any]:
    return {
        "products_per_minute": 0.0,
        "pages_per_minute": 0.0,
        "duplicate_rate": 0.0,
        "eta_seconds": None,
        "eta_label": "no active run",
        "controller_url": f"http://{settings.harvester.controller_public_host}:{settings.harvester.controller_public_port}",
    }


def calculate_metrics(connection: psycopg.Connection, run: dict[str, Any], settings: Settings) -> dict[str, Any]:
    recent = connection.execute(
        """
        select coalesce(sum(product_count), 0) as products,
               coalesce(sum(new_product_count), 0) as new_products,
               coalesce(sum(duplicate_product_count), 0) as duplicates,
               count(*) as pages,
               extract(epoch from (max(completed_at) - min(started_at))) as seconds
        from harvest_pages
        where run_id = %s and status = 'done' and completed_at is not null and started_at is not null
          and completed_at > current_timestamp - interval '30 minutes'
        """,
        (run["id"],),
    ).fetchone()
    seconds = max(float(recent["seconds"] or 0), 1.0)
    products_per_minute = round((int(recent["product_count"] if "product_count" in recent else recent["products"]) / seconds) * 60, 2)
    new_products_per_minute = round((int(recent["new_products"] or 0) / seconds) * 60, 2)
    pages_per_minute = round((int(recent["pages"] or 0) / seconds) * 60, 2)
    duplicates = int(recent["duplicates"] or 0)
    products = int(recent["products"] or 0)
    duplicate_rate = round(duplicates / max(1, products), 4)
    remaining = max(0, int(run["target_unique"]) - int(run["unique_products"]))
    eta_seconds = int((remaining / new_products_per_minute) * 60) if new_products_per_minute > 0 else None
    return {
        "products_per_minute": products_per_minute,
        "pages_per_minute": pages_per_minute,
        "duplicate_rate": duplicate_rate,
        "eta_seconds": eta_seconds,
        "eta_label": format_eta(eta_seconds),
        "controller_url": f"http://{settings.harvester.controller_public_host}:{settings.harvester.controller_public_port}",
    }


def format_eta(seconds: int | None) -> str:
    if seconds is None:
        return "warming up"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rest_minutes = minutes % 60
    return f"{hours}h {rest_minutes}m"


def worker_setup(settings: Settings) -> dict[str, str]:
    database_url = (
        f"postgresql://zendrop:zendrop@{settings.harvester.postgres_public_host}:5434/zendrop_supply"
    )
    return {
        "database_url": database_url,
        "clone": "git clone https://github.com/NeyerXj/ZenDropSupply.git && cd ZenDropSupply",
        "env": f"DATABASE_URL={database_url}",
        "run": "docker compose -f docker-compose.worker.yml up -d --build",
    }


class HarvesterWorker:
    def __init__(self, settings: Settings, worker_id: str) -> None:
        self.settings = settings
        self.worker_id = worker_id

    async def run_forever(self) -> None:
        while True:
            processed = await self.process_once()
            if not processed:
                await async_sleep(self.settings.harvester.poll_seconds)

    async def process_once(self) -> bool:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as http_client:
            client = ZendropMcpClient(settings=self.settings.zendrop, http_client=http_client)
            from app.database import open_database

            with open_database(self.settings.database_url) as connection:
                self.register_worker(connection)
                if self.worker_disabled(connection):
                    self.heartbeat(connection, None, None, "disabled")
                    return False
                run = self.active_run(connection)
                if run is None:
                    self.heartbeat(connection, None, None, "idle")
                    return False
                page = self.claim_page(connection, run)
                if page is None:
                    maybe_complete_run(connection, run["id"])
                    return False
                self.heartbeat(connection, run["id"], page["id"], "running")
                await self.fetch_page(connection, client, run, page)
                return True

    def register_worker(self, connection: psycopg.Connection) -> None:
        connection.execute(
            """
            insert into harvest_workers (worker_id, status, desired_status, heartbeat_at)
            values (%s, 'online', 'enabled', current_timestamp)
            on conflict(worker_id) do update set
                status = case
                    when harvest_workers.desired_status = 'disabled' then 'disabled'
                    else 'online'
                end,
                heartbeat_at = current_timestamp
            """,
            (self.worker_id,),
        )
        connection.commit()

    def worker_disabled(self, connection: psycopg.Connection) -> bool:
        row = connection.execute(
            "select desired_status from harvest_workers where worker_id = %s",
            (self.worker_id,),
        ).fetchone()
        return bool(row and row["desired_status"] == "disabled")

    def heartbeat(
        self,
        connection: psycopg.Connection,
        run_id: int | None,
        page_id: int | None,
        status: str,
        error: str | None = None,
    ) -> None:
        connection.execute(
            """
            update harvest_workers
            set status = %s,
                current_run_id = %s,
                current_page_id = %s,
                last_error = %s,
                heartbeat_at = current_timestamp
            where worker_id = %s
            """,
            (status, run_id, page_id, error, self.worker_id),
        )
        connection.commit()

    def active_run(self, connection: psycopg.Connection) -> dict[str, Any] | None:
        row = connection.execute(
            """
            select *
            from harvest_runs
            where status in ('queued', 'running')
            order by id asc
            limit 1
            """
        ).fetchone()
        if row is None:
            return None
        if row["status"] == "queued":
            connection.execute(
                """
                update harvest_runs
                set status = 'running', started_at = coalesce(started_at, current_timestamp), updated_at = current_timestamp
                where id = %s
                """,
                (row["id"],),
            )
            connection.commit()
            row = connection.execute("select * from harvest_runs where id = %s", (row["id"],)).fetchone()
        return serialize_run(row)

    def claim_page(self, connection: psycopg.Connection, run: dict[str, Any]) -> dict[str, Any] | None:
        if run["unique_products"] >= run["target_unique"]:
            return None
        with connection.transaction():
            row = connection.execute(
                """
                select *
                from harvest_pages
                where run_id = %s
                  and (
                    status = 'queued'
                    or (status = 'running' and claimed_until < current_timestamp)
                    or (status = 'rate_limited' and claimed_until < current_timestamp)
                  )
                order by case when keyword = '' then 0 else 1 end, page asc, id asc
                for update skip locked
                limit 1
                """,
                (run["id"],),
            ).fetchone()
            if row is None:
                return None
            page = dict(row)
            connection.execute(
                """
                update harvest_pages
                set status = 'running',
                    claimed_by = %s,
                    claimed_until = current_timestamp + (%s || ' seconds')::interval,
                    started_at = current_timestamp,
                    updated_at = current_timestamp
                where id = %s
                """,
                (self.worker_id, self.settings.harvester.claim_seconds, page["id"]),
            )
        return page

    async def fetch_page(
        self,
        connection: psycopg.Connection,
        client: ZendropMcpClient,
        run: dict[str, Any],
        page: dict[str, Any],
    ) -> None:
        started = time.monotonic()
        try:
            result = await client.search_products(
                keyword=page["keyword"],
                page=int(page["page"]),
                limit=int(run["per_page_limit"]),
                category_id=int(run["category_id"]),
            )
            remaining = max(0, int(run["target_unique"]) - int(run["unique_products"]))
            products = result.products[:remaining]
            new_count = store_products(connection, run, products)
            if run["fetch_shipping"]:
                await store_shipping_estimates(connection, client, run, products)
            duplicate_count = max(0, len(products) - new_count)
            duration_ms = int((time.monotonic() - started) * 1000)
            mark_page_done(connection, page["id"], len(products), new_count, duplicate_count, duration_ms)
            self.heartbeat_increment(connection, len(products))
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                mark_page_rate_limited(connection, run["id"], page["id"], str(error))
                self.heartbeat(connection, None, None, "rate_limited", str(error)[:300])
                return
            mark_page_failed(connection, run["id"], page["id"], str(error))
            self.heartbeat(connection, run["id"], page["id"], "error", str(error)[:300])
        except Exception as error:
            mark_page_failed(connection, run["id"], page["id"], str(error))
            self.heartbeat(connection, run["id"], page["id"], "error", str(error)[:300])

    def heartbeat_increment(self, connection: psycopg.Connection, product_count: int) -> None:
        connection.execute(
            """
            update harvest_workers
            set processed_pages = processed_pages + 1,
                processed_products = processed_products + %s,
                current_page_id = null,
                status = 'online',
                heartbeat_at = current_timestamp
            where worker_id = %s
            """,
            (product_count, self.worker_id),
        )
        connection.commit()


def store_products(connection: psycopg.Connection, run: dict[str, Any], products: list[ZendropProductSummary]) -> int:
    new_count = 0
    for product in products:
        raw_json = product.model_dump(mode="json")
        if run["first_image_only"]:
            raw_json["images"] = first_image_payload(product)
        raw_json["_zendrop_supply"] = {
            "run_id": run["id"],
            "requested_origin_country_code": run["requested_origin_country_code"],
            "destination_country_code": run["destination_country_code"],
            "category_id": run["category_id"],
            "category_name": run["category_name"],
            "first_image_only": run["first_image_only"],
            "origin_verified": False,
        }
        row = connection.execute(
            """
            insert into supply_products (
                product_id, name, description, price_usd, image_url, requested_origin_country_code,
                origin_verified, destination_country_code, raw_json, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, false, %s, %s, current_timestamp)
            on conflict(product_id) do update set
                name = excluded.name,
                description = excluded.description,
                price_usd = excluded.price_usd,
                image_url = excluded.image_url,
                requested_origin_country_code = excluded.requested_origin_country_code,
                destination_country_code = excluded.destination_country_code,
                raw_json = excluded.raw_json,
                updated_at = current_timestamp
            returning (xmax = 0) as inserted
            """,
            (
                product.product_id,
                product.name,
                product.description,
                product.price_usd,
                product.image,
                run["requested_origin_country_code"],
                run["destination_country_code"],
                Jsonb(raw_json),
            ),
        ).fetchone()
        if row and row["inserted"]:
            new_count += 1
        upsert_images(connection, product, first_image_only=bool(run["first_image_only"]))
    connection.commit()
    return new_count


def first_image_payload(product: ZendropProductSummary) -> list[dict[str, Any]]:
    image_url = first_product_image_url(product)
    if not image_url:
        return []
    for image in product.images:
        if image.url == image_url:
            return [image.model_dump(mode="json")]
    return [{"url": image_url}]


def first_product_image_url(product: ZendropProductSummary) -> str | None:
    if product.image:
        return product.image
    for image in product.images:
        if image.url:
            return image.url
    return None


def upsert_images(connection: psycopg.Connection, product: ZendropProductSummary, first_image_only: bool = True) -> None:
    image_urls = []
    if product.image:
        image_urls.append(product.image)
    if first_image_only:
        image_urls = image_urls[:1] or [image.url for image in product.images[:1] if image.url]
    else:
        image_urls.extend(image.url for image in product.images if image.url)
    seen = set()
    for position, image_url in enumerate(image_urls):
        if image_url in seen:
            continue
        seen.add(image_url)
        connection.execute(
            """
            insert into supply_product_images (product_id, image_url, position)
            values (%s, %s, %s)
            on conflict(product_id, image_url) do update set position = excluded.position
            """,
            (product.product_id, image_url, position),
        )


async def store_shipping_estimates(
    connection: psycopg.Connection,
    client: ZendropMcpClient,
    run: dict[str, Any],
    products: list[ZendropProductSummary],
) -> None:
    for product in products:
        estimate = await client.get_shipping_estimate(product.product_id, run["destination_country_code"])
        cheapest = estimate.cheapest_option
        if cheapest is None:
            continue
        connection.execute(
            """
            insert into supply_shipping_estimates (
                product_id, destination_country_code, shipping_type, shipping_price_usd,
                estimated_delivery, raw_json, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, current_timestamp)
            on conflict(product_id, destination_country_code) do update set
                shipping_type = excluded.shipping_type,
                shipping_price_usd = excluded.shipping_price_usd,
                estimated_delivery = excluded.estimated_delivery,
                raw_json = excluded.raw_json,
                updated_at = current_timestamp
            """,
            (
                product.product_id,
                estimate.country_code,
                cheapest.type,
                cheapest.price,
                cheapest.estimated_delivery,
                Jsonb(estimate.model_dump(mode="json")),
            ),
        )
        connection.commit()


def mark_page_done(
    connection: psycopg.Connection,
    page_id: int,
    product_count: int,
    new_count: int,
    duplicate_count: int,
    duration_ms: int,
) -> None:
    row = connection.execute(
        """
        update harvest_pages
        set status = 'done',
            product_count = %s,
            new_product_count = %s,
            duplicate_product_count = %s,
            duration_ms = %s,
            error_message = null,
            completed_at = current_timestamp,
            updated_at = current_timestamp
        where id = %s
        returning run_id, keyword, page
        """,
        (product_count, new_count, duplicate_count, duration_ms, page_id),
    ).fetchone()
    if product_count == 0:
        mark_keyword_exhausted(connection, int(row["run_id"]), str(row["keyword"]), int(row["page"]))
    connection.execute(
        """
        update harvest_runs
        set unique_products = unique_products + %s,
            fetched_products = fetched_products + %s,
            duplicate_products = duplicate_products + %s,
            pages_done = pages_done + 1,
            updated_at = current_timestamp
        where id = %s
        """,
        (new_count, product_count, duplicate_count, row["run_id"]),
    )
    connection.commit()
    maybe_complete_run(connection, row["run_id"])


def mark_keyword_exhausted(connection: psycopg.Connection, run_id: int, keyword: str, page: int) -> None:
    empty_tail = connection.execute(
        """
        select count(*) as page_count,
               coalesce(sum(product_count), 0) as product_count
        from (
            select product_count
            from harvest_pages
            where run_id = %s and keyword = %s and status = 'done' and page <= %s
            order by page desc
            limit %s
        ) recent_pages
        """,
        (run_id, keyword, page, EMPTY_PAGE_STOP_THRESHOLD),
    ).fetchone()
    if (
        int(empty_tail["page_count"] or 0) >= EMPTY_PAGE_STOP_THRESHOLD
        and int(empty_tail["product_count"] or 0) == 0
    ):
        connection.execute(
            """
            update harvest_pages
            set status = 'exhausted',
                updated_at = current_timestamp
            where run_id = %s
              and keyword = %s
              and page > %s
              and status = 'queued'
            """,
            (run_id, keyword, page),
        )


def mark_page_rate_limited(connection: psycopg.Connection, run_id: int, page_id: int, message: str) -> None:
    connection.execute(
        """
        update harvest_pages
        set status = 'rate_limited',
            claimed_by = null,
            claimed_until = current_timestamp + (%s || ' seconds')::interval,
            error_message = %s,
            updated_at = current_timestamp
        where id = %s
        """,
        (RATE_LIMIT_COOLDOWN_SECONDS, message[:500], page_id),
    )
    connection.execute(
        """
        update harvest_runs
        set rate_limit_hits = rate_limit_hits + 1, error_message = %s, updated_at = current_timestamp
        where id = %s
        """,
        (message[:500], run_id),
    )
    connection.commit()


def mark_page_failed(connection: psycopg.Connection, run_id: int, page_id: int, message: str) -> None:
    connection.execute(
        """
        update harvest_pages
        set status = 'failed', error_message = %s, completed_at = current_timestamp, updated_at = current_timestamp
        where id = %s
        """,
        (message[:500], page_id),
    )
    connection.execute(
        """
        update harvest_runs
        set pages_failed = pages_failed + 1, error_message = %s, updated_at = current_timestamp
        where id = %s
        """,
        (message[:500], run_id),
    )
    connection.commit()


def maybe_complete_run(connection: psycopg.Connection, run_id: int) -> None:
    run = get_run(connection, run_id)
    if run["status"] not in {"queued", "running"}:
        return
    remaining = connection.execute(
        """
        select count(*) as count
        from harvest_pages
        where run_id = %s and status in ('queued', 'running')
        """,
        (run_id,),
    ).fetchone()["count"]
    if run["unique_products"] >= run["target_unique"] or int(remaining or 0) == 0:
        connection.execute(
            """
            update harvest_runs
            set status = 'completed', completed_at = current_timestamp, updated_at = current_timestamp
            where id = %s
            """,
            (run_id,),
        )
        connection.commit()


async def async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(max(0.0, seconds))


def utc_now() -> datetime:
    return datetime.now(UTC)
