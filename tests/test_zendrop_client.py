import httpx
import pytest

from app.config import ZendropSettings
from app.providers.zendrop import ZendropMcpClient, ZendropMcpError


@pytest.mark.asyncio
async def test_search_products_sends_json_rpc_tools_call():
    captured_request = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_request["authorization"] = request.headers.get("authorization")
        captured_request["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"total": 1, "products": [{"id": 2331830, "name": "Lace V-Neck Maxi Dress", "price": "12.39", "image": "https://file.zendrop.com/x.webp", "images": []}]}',
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    settings = ZendropSettings(api_token="secret-token")

    async with httpx.AsyncClient(transport=transport) as http_client:
        client = ZendropMcpClient(settings=settings, http_client=http_client)
        result = await client.search_products(keyword="maxi dress", limit=3)

    assert captured_request["authorization"] == "Bearer secret-token"
    assert b'"method":"tools/call"' in captured_request["payload"]
    assert b'"name":"get_catalog_products"' in captured_request["payload"]
    assert result.total == 1
    assert result.products[0].product_id == 2331830
    assert result.products[0].name == "Lace V-Neck Maxi Dress"


@pytest.mark.asyncio
async def test_get_shipping_estimate_normalizes_shipping_options():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"product_id": 2331830, "country_code": "ca", "shipping_options": [{"type": "regular", "price": 10.05, "estimated_delivery": "9 days"}]}',
                        }
                    ]
                },
            },
        )

    settings = ZendropSettings(api_token="secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ZendropMcpClient(settings=settings, http_client=http_client)
        result = await client.get_shipping_estimate(product_id=2331830, country_code="ca")

    assert result.product_id == 2331830
    assert result.country_code == "ca"
    assert result.shipping_options[0].price == 10.05
    assert result.shipping_options[0].estimated_delivery == "9 days"


@pytest.mark.asyncio
async def test_mcp_error_is_raised_for_json_rpc_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Unknown tool"}},
        )

    settings = ZendropSettings(api_token="secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = ZendropMcpClient(settings=settings, http_client=http_client)
        with pytest.raises(ZendropMcpError, match="Unknown tool"):
            await client.get_product(product_id=1)
