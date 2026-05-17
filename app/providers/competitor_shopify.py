from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx
from pydantic import BaseModel, Field


class ProductLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.handles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlparse(href)
        path_parts = [part for part in parsed.path.split("/") if part]
        if "products" in path_parts:
            products_index = path_parts.index("products")
            if len(path_parts) <= products_index + 1:
                return
            handle = path_parts[products_index + 1]
            if handle and handle not in self.handles:
                self.handles.append(handle)


class CompetitorProduct(BaseModel):
    external_id: str | None
    handle: str
    title: str
    product_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    price: float | None = None
    image_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class CompetitorShopifyClient:
    http_client: httpx.AsyncClient

    async def fetch_collection_handles(self, store_url: str, page: int) -> list[str]:
        url = collection_page_url(store_url=store_url, page=page)
        response = await self.http_client.get(url)
        response.raise_for_status()
        parser = ProductLinkParser()
        parser.feed(response.text)
        return parser.handles

    async def fetch_product(self, store_url: str, handle: str) -> CompetitorProduct:
        url = urljoin(shop_base_url(store_url), f"products/{handle}.json")
        response = await self.http_client.get(url)
        response.raise_for_status()
        payload = response.json()
        product = payload["product"]
        variants = product.get("variants") or []
        image = product.get("image") or {}
        return CompetitorProduct(
            external_id=str(product.get("id")) if product.get("id") is not None else None,
            handle=product.get("handle") or handle,
            title=product["title"],
            product_type=product.get("product_type"),
            tags=_normalize_tags(product.get("tags")),
            price=_first_price(variants),
            image_url=image.get("src"),
            raw=product,
        )


def _normalize_tags(tags: Any) -> list[str]:
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    if isinstance(tags, str):
        return [tag.strip() for tag in tags.split(",") if tag.strip()]
    return []


def _first_price(variants: list[dict[str, Any]]) -> float | None:
    if not variants:
        return None
    price = variants[0].get("price")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def collection_page_url(store_url: str, page: int) -> str:
    parsed = urlparse(store_url)
    clean_path = parsed.path.rstrip("/")
    path_parts = [part for part in clean_path.split("/") if part]
    if "collections" in path_parts:
        path = clean_path
    else:
        locale_prefix = f"/{path_parts[0]}" if path_parts and len(path_parts[0]) == 2 else ""
        path = f"{locale_prefix}/collections/all"
    query = urlencode({"sort_by": "best-selling", "page": page})
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


def shop_base_url(store_url: str) -> str:
    parsed = urlparse(store_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    locale_prefix = f"/{path_parts[0]}" if path_parts and len(path_parts[0]) == 2 else ""
    return urlunparse((parsed.scheme, parsed.netloc, f"{locale_prefix}/", "", "", ""))
