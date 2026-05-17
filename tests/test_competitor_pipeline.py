import httpx
import pytest

from app.config import Settings
from app.database import open_database
from app.providers.competitor_shopify import CompetitorShopifyClient
from app.services.competitor_pipeline import CompetitorPipeline


@pytest.mark.asyncio
async def test_competitor_pipeline_persists_product_and_image(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/all":
            return httpx.Response(200, text='<a href="/products/floral-maxi-dress">Dress</a>')
        if request.url.path == "/products/floral-maxi-dress.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 123,
                        "handle": "floral-maxi-dress",
                        "title": "Floral Maxi Dress",
                        "product_type": "Dresses",
                        "tags": ["Women", "Summer"],
                        "image": {"src": "https://cdn.example.com/dress.jpg"},
                        "variants": [{"price": "59.99"}],
                    }
                },
            )
        if request.url.path == "/dress.jpg":
            return httpx.Response(200, content=b"image-bytes")
        raise AssertionError(f"Unexpected URL: {request.url}")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")

    async with open_database(settings.database_url) as database:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            pipeline = CompetitorPipeline(
                database=database,
                client=client,
                image_storage_dir=tmp_path / "competitor_images",
            )
            products = await pipeline.scrape_store("https://example.com", pages=1, limit=1)

        rows = database.execute(
            "select store_url, handle, title, status, image_path from competitor_products"
        ).fetchall()

    assert len(products) == 1
    assert rows[0][0] == "https://example.com"
    assert rows[0][1] == "floral-maxi-dress"
    assert rows[0][2] == "Floral Maxi Dress"
    assert rows[0][3] == "ready_for_zendrop"
    assert (tmp_path / "competitor_images" / "floral-maxi-dress.jpg").read_bytes() == b"image-bytes"


@pytest.mark.asyncio
async def test_competitor_pipeline_limit_counts_ready_products_after_filters(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/all":
            return httpx.Response(
                200,
                text=(
                    '<a href="/products/mens-jacket">Mens Jacket</a>'
                    '<a href="/products/floral-maxi-dress">Dress</a>'
                    '<a href="/products/second-dress">Second Dress</a>'
                ),
            )
        if request.url.path == "/products/mens-jacket.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 11,
                        "handle": "mens-jacket",
                        "title": "Mens Jacket",
                        "product_type": "Jackets",
                        "tags": ["Men"],
                        "image": None,
                        "variants": [{"price": "49.99"}],
                    }
                },
            )
        if request.url.path == "/products/floral-maxi-dress.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 12,
                        "handle": "floral-maxi-dress",
                        "title": "Floral Maxi Dress",
                        "product_type": "Dresses",
                        "tags": ["Women", "Summer"],
                        "image": None,
                        "variants": [{"price": "59.99"}],
                    }
                },
            )
        if request.url.path == "/products/second-dress.json":
            raise AssertionError("Scraper should stop after one ready product")
        raise AssertionError(f"Unexpected URL: {request.url}")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")

    async with open_database(settings.database_url) as database:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            pipeline = CompetitorPipeline(
                database=database,
                client=client,
                image_storage_dir=tmp_path / "competitor_images",
            )
            products = await pipeline.scrape_store("https://example.com", pages=1, limit=1)

        rows = database.execute(
            "select handle, status from competitor_products order by id"
        ).fetchall()

    assert len(products) == 2
    assert rows == [
        ("mens-jacket", "skipped_male"),
        ("floral-maxi-dress", "ready_for_zendrop"),
    ]


@pytest.mark.asyncio
async def test_competitor_pipeline_continues_pages_until_ready_target_is_reached(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/all" and request.url.params.get("page") == "1":
            return httpx.Response(200, text='<a href="/products/mens-jacket">Mens Jacket</a>')
        if request.url.path == "/collections/all" and request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                text=(
                    '<a href="/products/floral-maxi-dress">Dress</a>'
                    '<a href="/products/second-dress">Second Dress</a>'
                ),
            )
        if request.url.path == "/products/mens-jacket.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 11,
                        "handle": "mens-jacket",
                        "title": "Mens Jacket",
                        "product_type": "Jackets",
                        "tags": ["Men"],
                        "image": None,
                        "variants": [{"price": "49.99"}],
                    }
                },
            )
        if request.url.path == "/products/floral-maxi-dress.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 12,
                        "handle": "floral-maxi-dress",
                        "title": "Floral Maxi Dress",
                        "product_type": "Dresses",
                        "tags": ["Women", "Summer"],
                        "image": None,
                        "variants": [{"price": "59.99"}],
                    }
                },
            )
        if request.url.path == "/products/second-dress.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 13,
                        "handle": "second-dress",
                        "title": "Second Summer Dress",
                        "product_type": "Dresses",
                        "tags": ["Women", "Summer"],
                        "image": None,
                        "variants": [{"price": "64.99"}],
                    }
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")

    async with open_database(settings.database_url) as database:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            pipeline = CompetitorPipeline(
                database=database,
                client=client,
                image_storage_dir=tmp_path / "competitor_images",
            )
            await pipeline.scrape_store("https://example.com", pages=1, limit=2)

        ready_count = database.execute(
            "select count(*) from competitor_products where status = 'ready_for_zendrop'"
        ).fetchone()[0]

    assert ready_count == 2


@pytest.mark.asyncio
async def test_competitor_pipeline_uses_collection_url_without_appending_all_collection(tmp_path):
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/en/collections/topy-damski":
            return httpx.Response(200, text='<a href="/en/products/summer-blouse">Blouse</a>')
        if request.url.path == "/en/products/summer-blouse.json":
            return httpx.Response(
                200,
                json={
                    "product": {
                        "id": 21,
                        "handle": "summer-blouse",
                        "title": "Summer Blouse Women",
                        "product_type": "Blouses",
                        "tags": ["Women", "Summer"],
                        "image": None,
                        "variants": [{"price": "39.99"}],
                    }
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")

    async with open_database(settings.database_url) as database:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = CompetitorShopifyClient(http_client=http_client)
            pipeline = CompetitorPipeline(
                database=database,
                client=client,
                image_storage_dir=tmp_path / "competitor_images",
            )
            await pipeline.scrape_store("https://example.com/en/collections/topy-damski", pages=1, limit=1)

        row = database.execute("select handle, status from competitor_products").fetchone()

    assert row == ("summer-blouse", "ready_for_zendrop")
    assert any("/en/collections/topy-damski?sort_by=best-selling&page=1" in url for url in requested_urls)
    assert all("/collections/topy-damski/collections/all" not in url for url in requested_urls)
