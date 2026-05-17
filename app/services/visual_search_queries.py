from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import re
import sqlite3
from typing import Any

import httpx

from app.config import OpenAISettings


VISUAL_QUERY_CACHE_KEY = "_ttd_visual_search_queries"
MAX_VISUAL_SEARCH_QUERIES = 3
MAX_ZENDROP_SEARCH_QUERIES = 5


class OpenAIVisualSearchQueryBuilder:
    def __init__(self, settings: OpenAISettings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http_client = http_client

    async def generate(self, title: str, image_path: Path) -> list[str]:
        if not self.settings.api_key or not image_path.exists():
            return []
        response = await self.http_client.post(
            f"{self.settings.api_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.model,
                "instructions": (
                    "You write Zendrop catalog search queries for women's fashion sourcing. Return JSON only. "
                    "Use visual details from the image more than the title. Queries must be short English phrases, 2-6 words. "
                    "Prefer product type, color family, silhouette, neckline, closure, material, and special details. "
                    "Do not include brand names."
                ),
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": f'Return JSON: {{"queries": []}}. Source title: {title}'},
                            {"type": "input_image", "image_url": image_data_url(image_path)},
                        ],
                    }
                ],
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 180,
            },
        )
        if response.status_code >= 400:
            return []
        try:
            parsed = parse_openai_json(response.json())
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
        return clean_visual_queries(parsed.get("queries") or [])


def cached_visual_search_queries(raw_json: str | None) -> list[str]:
    try:
        raw = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return []
    queries = raw.get(VISUAL_QUERY_CACHE_KEY)
    if not isinstance(queries, list):
        return []
    return clean_visual_queries(queries)


def store_visual_search_queries(database: sqlite3.Connection, competitor_product_id: int, queries: list[str]) -> None:
    row = database.execute(
        "select raw_json from competitor_products where id = ?",
        (competitor_product_id,),
    ).fetchone()
    if row is None:
        return
    try:
        raw = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        raw = {}
    raw[VISUAL_QUERY_CACHE_KEY] = clean_visual_queries(queries)
    database.execute(
        "update competitor_products set raw_json = ?, updated_at = current_timestamp where id = ?",
        (json.dumps(raw, ensure_ascii=False), competitor_product_id),
    )
    database.commit()


def merge_search_queries(
    visual_queries: list[str],
    fallback_queries: list[str],
    limit: int = MAX_ZENDROP_SEARCH_QUERIES,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for query in [*expand_visual_queries(visual_queries), *fallback_queries]:
        clean_query = normalize_query(query)
        if clean_query and clean_query not in seen:
            result.append(clean_query)
            seen.add(clean_query)
        if len(result) >= limit:
            break
    return result


def expand_visual_queries(visual_queries: list[str]) -> list[str]:
    expanded: list[str] = []
    for query in visual_queries:
        normalized = normalize_query(query)
        if not normalized:
            continue
        expanded.append(normalized)
        if "orthopedic" in normalized and "shoes" in normalized and "sneakers" not in normalized:
            expanded.append(normalized.replace("shoes", "sneakers"))
        if "slip-on shoes" in normalized:
            expanded.append(normalized.replace("slip-on shoes", "slip-on sneakers"))
        if "slip on shoes" in normalized:
            expanded.append(normalized.replace("slip on shoes", "slip on sneakers"))
    return expanded


def clean_visual_queries(queries: list[Any]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = normalize_query(str(query))
        word_count = len(normalized.split())
        if not normalized or word_count < 2 or word_count > 6 or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
        if len(cleaned) >= MAX_VISUAL_SEARCH_QUERIES:
            break
    return cleaned


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def parse_openai_json(raw: dict[str, Any]) -> dict[str, Any]:
    if text := raw.get("output_text"):
        return json.loads(text)
    for output in raw.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return json.loads(content["text"])
    return {}
