import httpx
import pytest

from app.config import Settings, ZendropSettings
from app.database import open_database
from app.providers.zendrop import ZendropMcpClient, ZendropMcpError
from app.services.zendrop_pipeline import ZendropPipeline


@pytest.mark.asyncio
async def test_zendrop_pipeline_persists_search_product_and_shipping(tmp_path):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode("utf-8")
        requests.append(payload)
        if "get_catalog_products" in payload:
            response_text = (
                '{"total": 1, "products": [{"id": 2331830, "name": "Lace V-Neck Maxi Dress", '
                '"description": "<p>Dress</p>", "price": "12.39", '
                '"image": "https://file.zendrop.com/main.webp", "images": []}]}'
            )
        elif "get_catalog_shipping_estimate" in payload:
            response_text = (
                '{"product_id": 2331830, "country_code": "ca", '
                '"shipping_options": [{"type": "regular", "price": 10.05, "estimated_delivery": "9 days"}]}'
            )
        else:
            response_text = '{"id": 2331830, "name": "Lace V-Neck Maxi Dress"}'
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": response_text}]}},
        )

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pipeline.db'}",
        zendrop=ZendropSettings(api_token="secret-token"),
    )

    async with open_database(settings.database_url) as database:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            zendrop_client = ZendropMcpClient(settings=settings.zendrop, http_client=http_client)
            pipeline = ZendropPipeline(database=database, zendrop_client=zendrop_client)
            products = await pipeline.search_and_store(keyword="maxi dress", limit=1, country_code="ca")

        rows = database.execute("select product_id, name, price_usd, shipping_price_usd from zendrop_products").fetchall()

    assert len(products) == 1
    assert products[0].product_id == 2331830
    assert rows == [(2331830, "Lace V-Neck Maxi Dress", 12.39, 10.05)]
    assert any("get_catalog_products" in request for request in requests)
    assert any("get_catalog_shipping_estimate" in request for request in requests)


@pytest.mark.asyncio
async def test_zendrop_pipeline_persists_product_when_shipping_is_rate_limited(tmp_path, monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.zendrop_pipeline.asyncio.sleep", no_sleep)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pipeline.db'}",
        zendrop=ZendropSettings(api_token="secret-token"),
    )

    class FakeZendropClient:
        async def search_products(self, keyword, page=1, limit=20):
            class Product:
                product_id = 2331830
                name = "Lace V-Neck Maxi Dress"
                description = "<p>Dress</p>"
                price_usd = 12.39
                image = "https://file.zendrop.com/main.webp"

                def model_dump(self, mode):
                    return {"id": self.product_id, "name": self.name}

            class Result:
                products = [Product()]

            return Result()

        async def get_shipping_estimate(self, product_id, country_code):
            raise ZendropMcpError("rate limited")

    async with open_database(settings.database_url) as database:
        pipeline = ZendropPipeline(database=database, zendrop_client=FakeZendropClient())
        products = await pipeline.search_and_store(keyword="maxi dress", limit=1, country_code="ca")
        rows = database.execute("select product_id, name, price_usd, shipping_price_usd from zendrop_products").fetchall()

    assert len(products) == 1
    assert rows == [(2331830, "Lace V-Neck Maxi Dress", 12.39, None)]


@pytest.mark.asyncio
async def test_zendrop_pipeline_can_search_multiple_pages_without_shipping(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}")
    shipping_calls = []
    search_pages = []

    class FakeZendropClient:
        async def search_products(self, keyword, page=1, limit=20):
            search_pages.append(page)

            class Product:
                def __init__(self, product_id):
                    self.product_id = product_id
                    self.name = f"Product {product_id}"
                    self.description = None
                    self.price_usd = 10.0
                    self.image = "https://file.zendrop.com/product.webp"

                def model_dump(self, mode):
                    return {"id": self.product_id, "name": self.name}

            class Result:
                products = [Product(1000 + page)]

            return Result()

        async def get_shipping_estimate(self, product_id, country_code):
            shipping_calls.append(product_id)
            raise AssertionError("shipping should not be fetched during broad search")

    async with open_database(settings.database_url) as database:
        pipeline = ZendropPipeline(database=database, zendrop_client=FakeZendropClient())
        products = await pipeline.search_and_store(
            keyword="strapless maxi dress",
            limit=20,
            country_code="ca",
            pages=2,
            fetch_shipping=False,
        )
        rows = database.execute("select product_id, shipping_price_usd from zendrop_products order by product_id").fetchall()

    assert [product.product_id for product in products] == [1001, 1002]
    assert search_pages == [1, 2]
    assert shipping_calls == []
    assert rows == [(1001, None), (1002, None)]
