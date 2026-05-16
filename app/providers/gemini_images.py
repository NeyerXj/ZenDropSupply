from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel

from app.config import GeminiSettings


class GeminiImageError(RuntimeError):
    pass


class GeneratedImageResult(BaseModel):
    prompt: str
    image_url: str | None = None
    image_path: str | None = None
    raw: dict[str, Any]


class GeminiImageClient:
    def __init__(self, settings: GeminiSettings, http_client: httpx.AsyncClient, output_dir: Path) -> None:
        self.settings = settings
        self.http_client = http_client
        self.output_dir = output_dir

    async def generate_product_image(self, prompt: str, image_urls: list[str]) -> GeneratedImageResult:
        return await self.generate_product_image_with_references(prompt=prompt, image_urls=image_urls, image_paths=[])

    async def generate_product_image_with_references(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        image_paths: list[Path] | None = None,
    ) -> GeneratedImageResult:
        if not self.settings.api_key:
            raise GeminiImageError("GEMINI_API_KEY is required")
        image_urls = image_urls or []
        image_paths = image_paths or []
        response = await self.http_client.post(
            f"{self.settings.api_url.rstrip('/')}/models/{self.settings.image_model}:generateContent",
            headers={
                "x-goog-api-key": self.settings.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            *[{"fileData": {"fileUri": image_url}} for image_url in image_urls if image_url],
                            *[inline_image_part(image_path) for image_path in image_paths if image_path],
                        ],
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": "4:3", "imageSize": "2K"},
                },
            },
            timeout=180,
        )
        if response.status_code >= 400:
            raise GeminiImageError(f"Gemini image generation failed: HTTP {response.status_code}")
        raw = response.json()
        image_path = self._persist_inline_image(raw)
        return GeneratedImageResult(prompt=prompt, image_path=str(image_path) if image_path else None, raw=raw)

    def _persist_inline_image(self, raw: dict[str, Any]) -> Path | None:
        for candidate in raw.get("candidates", []):
            content = candidate.get("content") or {}
            for part in content.get("parts", []):
                inline_data = part.get("inlineData") or part.get("inline_data")
                if not inline_data or not inline_data.get("data"):
                    continue
                mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
                suffix = ".jpg" if "jpeg" in mime_type else ".png"
                self.output_dir.mkdir(parents=True, exist_ok=True)
                image_path = self.output_dir / f"{uuid4().hex}{suffix}"
                image_path.write_bytes(base64.b64decode(inline_data["data"]))
                return image_path
        return None


def inline_image_part(image_path: Path) -> dict[str, Any]:
    suffix = image_path.suffix.lower()
    mime_type = "image/png" if suffix == ".png" else "image/jpeg"
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
        }
    }
