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
