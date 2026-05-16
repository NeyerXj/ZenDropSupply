from __future__ import annotations

import asyncio

import httpx
import typer
from rich.console import Console
from rich.table import Table

from app.config import load_settings
from app.database import open_database
from app.providers.zendrop import ZendropMcpClient
from app.providers.competitor_shopify import CompetitorShopifyClient
from app.services.competitor_pipeline import CompetitorPipeline
from app.services.filtering import get_active_filter_config
from app.services.zendrop_pipeline import ZendropPipeline

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    pass


@app.command("zendrop-search")
def zendrop_search(
    keyword: str = typer.Argument(..., help="Keyword to search in Zendrop catalog."),
    limit: int = typer.Option(5, min=1, max=60, help="Number of products to fetch."),
    country_code: str | None = typer.Option(None, help="ISO 2-letter country code for shipping estimate."),
) -> None:
    asyncio.run(_zendrop_search(keyword=keyword, limit=limit, country_code=country_code))


@app.command("competitor-scrape")
def competitor_scrape(
    store_url: str = typer.Argument("https://lozendafashion.com", help="Shopify competitor store URL."),
    pages: int = typer.Option(1, min=1, help="Number of best-selling collection pages to read."),
    limit: int | None = typer.Option(None, min=1, help="Optional max product count for smoke runs."),
) -> None:
    asyncio.run(_competitor_scrape(store_url=store_url, pages=pages, limit=limit))


async def _zendrop_search(keyword: str, limit: int, country_code: str | None) -> None:
    settings = load_settings()
    destination_country = country_code or settings.zendrop.default_country_code
    async with httpx.AsyncClient(timeout=30) as http_client:
        zendrop_client = ZendropMcpClient(settings=settings.zendrop, http_client=http_client)
        with open_database(settings.database_url) as database:
            pipeline = ZendropPipeline(database=database, zendrop_client=zendrop_client)
            products = await pipeline.search_and_store(
                keyword=keyword,
                limit=limit,
                country_code=destination_country,
            )
            rows = database.execute(
                """
                select product_id, name, price_usd, shipping_price_usd, shipping_estimated_delivery
                from zendrop_products
                where product_id in ({})
                order by updated_at desc
                """.format(",".join("?" for _ in products)),
                [product.product_id for product in products],
            ).fetchall()

    table = Table(title=f"Zendrop search: {keyword}")
    table.add_column("ID", justify="right")
    table.add_column("Name")
    table.add_column("Price USD", justify="right")
    table.add_column(f"Ship {destination_country.upper()}", justify="right")
    table.add_column("ETA")
    for product_id, name, price_usd, shipping_price_usd, estimated_delivery in rows:
        table.add_row(
            str(product_id),
            name,
            f"{price_usd:.2f}" if price_usd is not None else "-",
            f"{shipping_price_usd:.2f}" if shipping_price_usd is not None else "-",
            estimated_delivery or "-",
        )
    console.print(table)


async def _competitor_scrape(store_url: str, pages: int, limit: int | None) -> None:
    settings = load_settings()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http_client:
        client = CompetitorShopifyClient(http_client=http_client)
        with open_database(settings.database_url) as database:
            filter_config = get_active_filter_config(database)
            pipeline = CompetitorPipeline(
                database=database,
                client=client,
                image_storage_dir=settings.storage_dir / "competitor_images",
                filter_config=filter_config,
            )
            products = await pipeline.scrape_store(store_url=store_url, pages=pages, limit=limit)
            rows = database.execute(
                """
                select handle, title, status, image_path
                from competitor_products
                where store_url = ?
                order by updated_at desc
                limit ?
                """,
                (store_url.rstrip("/"), len(products) or 1),
            ).fetchall()

    table = Table(title=f"Competitor scrape: {store_url}")
    table.add_column("Handle")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Image")
    for handle, title, status, image_path in rows:
        table.add_row(handle, title, status, image_path or "-")
    console.print(table)


if __name__ == "__main__":
    app()
