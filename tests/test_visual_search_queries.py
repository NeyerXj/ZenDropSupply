import json

import httpx
import pytest

from app.config import OpenAISettings
from app.services.visual_search_queries import (
    OpenAIVisualSearchQueryBuilder,
    cached_visual_search_queries,
    merge_search_queries,
    store_visual_search_queries,
)
from app.database import open_database


@pytest.mark.asyncio
async def test_visual_query_builder_returns_short_unique_queries(tmp_path):
    image_path = tmp_path / "shoe.jpg"
    image_path.write_bytes(b"image")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "queries": [
                            "black slip-on shoes",
                            "women orthopedic sneakers",
                            "black slip-on shoes",
                            "cushioned sole sneakers",
                            "this query has way too many words and should be skipped",
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        builder = OpenAIVisualSearchQueryBuilder(
            settings=OpenAISettings(api_key="secret-token"),
            http_client=http_client,
        )
        queries = await builder.generate(title="Orthopedic Shoes", image_path=image_path)

    assert queries == [
        "black slip-on shoes",
        "women orthopedic sneakers",
        "cushioned sole sneakers",
    ]


def test_visual_search_queries_are_cached_in_competitor_raw_json(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (store_url, handle, title, tags_json, status, raw_json)
            values ('https://example.com', 'shoe', 'Orthopedic Shoe', '[]', 'ready_for_zendrop', '{}')
            """
        )
        product_id = database.execute("select id from competitor_products").fetchone()[0]
        store_visual_search_queries(database, product_id, ["black slip-on shoes", "women orthopedic sneakers"])
        raw_json = database.execute("select raw_json from competitor_products where id = ?", (product_id,)).fetchone()[0]

    assert cached_visual_search_queries(raw_json) == ["black slip-on shoes", "women orthopedic sneakers"]


def test_merge_search_queries_prioritizes_visual_queries_and_caps_total():
    queries = merge_search_queries(
        visual_queries=["black slip-on shoes", "women orthopedic sneakers"],
        fallback_queries=["women shoes", "black slip-on shoes", "orthopedic shoes"],
        limit=4,
    )

    assert queries == [
        "black slip-on shoes",
        "black slip-on sneakers",
        "women orthopedic sneakers",
        "women shoes",
    ]


def test_merge_search_queries_adds_low_cost_shoe_variants():
    queries = merge_search_queries(
        visual_queries=["women orthopedic shoes", "black slip-on shoes"],
        fallback_queries=["women shoes"],
        limit=5,
    )

    assert queries == [
        "women orthopedic shoes",
        "women orthopedic sneakers",
        "black slip-on shoes",
        "black slip-on sneakers",
        "women shoes",
    ]
