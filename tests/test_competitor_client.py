import httpx
import pytest

from app.providers.competitor_shopify import CompetitorShopifyClient


@pytest.mark.asyncio
async def test_fetch_collection_handles_extracts_unique_product_handles():
    html = """
    <a href="/products/floral-maxi-dress">Dress</a>
    <a href="/en/products/linen-summer-dress">Localized Dress</a>
    <a href="https://example.com/products/summer-sandals?variant=1">Sandals</a>
    <a href="/products/floral-maxi-dress">Duplicate</a>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CompetitorShopifyClient(http_client=http_client)
        handles = await client.fetch_collection_handles("https://example.com", page=1)

    assert handles == ["floral-maxi-dress", "linen-summer-dress", "summer-sandals"]


@pytest.mark.asyncio
async def test_fetch_product_reads_shopify_json_shape():
    async def handler(request: httpx.Request) -> httpx.Response:
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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = CompetitorShopifyClient(http_client=http_client)
        product = await client.fetch_product("https://example.com", "floral-maxi-dress")

    assert product.external_id == "123"
    assert product.handle == "floral-maxi-dress"
    assert product.title == "Floral Maxi Dress"
    assert product.price == 59.99
    assert product.image_url == "https://cdn.example.com/dress.jpg"
