from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.config import ShopifySettings


class ShopifyAdminError(RuntimeError):
    pass


class ShopifyAdminClient:
    def __init__(self, settings: ShopifySettings, http_client: httpx.AsyncClient | httpx.Client) -> None:
        self.settings = settings
        self.http_client = http_client

    @property
    def graphql_url(self) -> str:
        if self.settings.graphql_url:
            return self.settings.graphql_url
        if not self.settings.store:
            raise ShopifyAdminError("SHOPIFY_STORE is required")
        return f"https://{self.settings.store}/admin/api/{self.settings.api_version}/graphql.json"

    @property
    def headers(self) -> dict[str, str]:
        if not self.settings.access_token:
            raise ShopifyAdminError("SHOPIFY_ACCESS_TOKEN is required")
        return {"X-Shopify-Access-Token": self.settings.access_token, "Content-Type": "application/json"}

    async def create_draft_product_with_media(
        self,
        product: dict[str, Any],
        image_paths: list[Path],
        price: float,
        compare_at_price: float,
    ) -> dict[str, Any]:
        media = []
        for image_path in image_paths:
            resource_url = await self.upload_staged_image(image_path)
            media.append(
                {
                    "originalSource": resource_url,
                    "mediaContentType": "IMAGE",
                    "alt": product["title"][:120],
                }
            )
        created = await self.graphql(
            """
            mutation CreateProduct($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
              productCreate(product: $product, media: $media) {
                product {
                  id
                  title
                  status
                  media(first: 20) {
                    nodes { id alt mediaContentType preview { status } }
                  }
                  variants(first: 1) {
                    nodes { id title price compareAtPrice }
                  }
                }
                userErrors { field message }
              }
            }
            """,
            {"product": product, "media": media},
        )
        payload = created.get("data", {}).get("productCreate", {})
        if errors := payload.get("userErrors"):
            raise ShopifyAdminError(f"Shopify productCreate failed: {errors}")
        product_node = payload.get("product")
        if not product_node:
            raise ShopifyAdminError(f"Shopify productCreate did not return product: {created}")
        variants = product_node.get("variants", {}).get("nodes", [])
        if variants:
            await self.graphql(
                """
                mutation UpdateVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                    productVariants { id title price compareAtPrice }
                    userErrors { field message }
                  }
                }
                """,
                {
                    "productId": product_node["id"],
                    "variants": [
                        {
                            "id": variants[0]["id"],
                            "price": float(price),
                            "compareAtPrice": float(compare_at_price),
                        }
                    ],
                },
            )
        return product_node

    async def upload_staged_image(self, image_path: Path) -> str:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        staged = await self.graphql(
            """
            mutation StagedUpload($input: [StagedUploadInput!]!) {
              stagedUploadsCreate(input: $input) {
                stagedTargets {
                  url
                  resourceUrl
                  parameters { name value }
                }
                userErrors { field message }
              }
            }
            """,
            {
                "input": [
                    {
                        "filename": image_path.name,
                        "mimeType": mime_type,
                        "resource": "IMAGE",
                        "httpMethod": "POST",
                    }
                ]
            },
        )
        payload = staged.get("data", {}).get("stagedUploadsCreate", {})
        if errors := payload.get("userErrors"):
            raise ShopifyAdminError(f"Shopify staged upload failed: {errors}")
        target = payload["stagedTargets"][0]
        form_data = {parameter["name"]: parameter["value"] for parameter in target["parameters"]}
        upload_response = await self.http_client.post(
            target["url"],
            data=form_data,
            files={"file": (image_path.name, image_path.read_bytes(), mime_type)},
        )
        if upload_response.status_code not in (200, 201, 204):
            raise ShopifyAdminError(f"Shopify binary upload failed: HTTP {upload_response.status_code}")
        return target["resourceUrl"]

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = await self.http_client.post(
            self.graphql_url,
            headers=self.headers,
            json={"query": query, "variables": variables},
        )
        if response.status_code >= 400:
            raise ShopifyAdminError(f"Shopify GraphQL failed: HTTP {response.status_code}")
        payload = response.json()
        if payload.get("errors"):
            raise ShopifyAdminError(f"Shopify GraphQL errors: {payload['errors']}")
        return payload
