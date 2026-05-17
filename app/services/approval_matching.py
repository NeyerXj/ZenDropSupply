from __future__ import annotations

import base64
import json
import mimetypes
import sqlite3
from pathlib import Path
import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from app.config import OpenAISettings
from app.services.dashboard import media_url_for_path
from app.services.pipeline_state import enqueue_pipeline_job
from app.services.search_terms import zendrop_search_text


COLOR_WORDS = {
    "black",
    "white",
    "grey",
    "gray",
    "red",
    "burgundy",
    "wine",
    "pink",
    "blue",
    "navy",
    "green",
    "beige",
    "brown",
    "cream",
    "purple",
    "yellow",
    "orange",
    "silver",
    "gold",
}

ATTRIBUTE_WORDS = {
    "strapless",
    "sleeveless",
    "long sleeve",
    "short sleeve",
    "off shoulder",
    "v neck",
    "slit",
    "side slit",
    "lace up",
    "slip on",
    "floral",
    "printed",
    "pleated",
    "ruffle",
    "orthopedic",
    "platform",
    "chunky",
    "running",
    "walking",
    "tennis",
    "wide",
    "heel",
    "heels",
}

VISION_PASS_CONFIDENCE = 0.80


def build_approval_matches(
    database: sqlite3.Connection,
    min_score: float = 62,
    openai_settings: OpenAISettings | None = None,
    storage_dir: Path | None = None,
    competitor_product_ids: list[int] | None = None,
) -> dict[str, int]:
    zendrop_rows = database.execute(
        """
        select product_id, name, price_usd, shipping_price_usd, image_url, raw_json
        from zendrop_products
        order by updated_at desc, product_id desc
        """
    ).fetchall()
    matches_created = build_zendrop_only_matches(
        database=database,
        zendrop_rows=zendrop_rows,
        min_score=min_score,
        openai_settings=openai_settings,
        storage_dir=storage_dir,
        competitor_product_ids=competitor_product_ids,
    )
    database.commit()
    return {"matches_created": matches_created}


def queue_approval_match_jobs(database: sqlite3.Connection, run_id: int | None = None) -> dict[str, int]:
    active_product_ids = set()
    active_rows = database.execute(
        """
        select payload_json
        from pipeline_jobs
        where stage = 'approval_match_product' and status in ('queued', 'running')
        """
    ).fetchall()
    for active_row in active_rows:
        try:
            payload = json.loads(active_row[0])
            if payload.get("competitor_product_id") is not None:
                active_product_ids.add(int(payload["competitor_product_id"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    rows = database.execute(
        """
        select cp.id
        from competitor_products cp
        where cp.status in ('ready_for_zendrop', 'zendrop_matched')
          and not exists (
            select 1 from product_matches pm
            where pm.competitor_product_id = cp.id
              and pm.status in ('approval_pending', 'approved', 'skipped')
          )
        order by cp.updated_at desc, cp.id desc
        """,
    ).fetchall()
    queued = 0
    for row in rows:
        product_id = int(row[0])
        if product_id in active_product_ids:
            continue
        enqueue_pipeline_job(
            database=database,
            run_id=run_id,
            stage="approval_match_product",
            payload={"competitor_product_id": product_id},
            priority=130,
        )
        queued += 1
    return {"jobs_queued": queued}


def build_zendrop_only_matches(
    database: sqlite3.Connection,
    zendrop_rows: list[tuple],
    min_score: float,
    openai_settings: OpenAISettings | None = None,
    storage_dir: Path | None = None,
    competitor_product_ids: list[int] | None = None,
) -> int:
    product_filter = ""
    parameters: list[Any] = []
    if competitor_product_ids:
        product_filter = "and cp.id in ({})".format(",".join("?" for _ in competitor_product_ids))
        parameters.extend(competitor_product_ids)
    competitor_rows = database.execute(
        f"""
        select cp.id, cp.title, cp.image_path
        from competitor_products cp
        where cp.status in ('ready_for_zendrop', 'zendrop_matched')
          {product_filter}
          and not exists (
            select 1 from product_matches pm
            where pm.competitor_product_id = cp.id
              and pm.status in ('approval_pending', 'approved', 'skipped')
          )
        order by cp.updated_at desc, cp.id desc
        """
        ,
        parameters,
    ).fetchall()
    matches_created = 0
    for competitor_product_id, competitor_title, competitor_image_path in competitor_rows:
        rejected_zendrop_ids = {
            row[0]
            for row in database.execute(
                """
                select zendrop_product_id
                from product_matches
                where competitor_product_id = ? and status = 'rejected'
                """,
                (competitor_product_id,),
            ).fetchall()
        }
        candidates = find_top_zendrop_matches(
            search_text=zendrop_search_text(competitor_title),
            zendrop_rows=zendrop_rows,
            min_score=min_score,
            excluded_product_ids=rejected_zendrop_ids,
            limit=5,
        )
        candidate, rejected_candidates = choose_vision_verified_candidate(
            competitor_title=competitor_title,
            competitor_image_path=competitor_image_path,
            candidates=candidates,
            openai_settings=openai_settings,
            storage_dir=storage_dir,
        )
        store_rejected_candidates(database, competitor_product_id, rejected_candidates)
        if candidate is None:
            continue
        zendrop_product_id, _name, score, price_usd, shipping_price_usd, selected_image_url, visual_status = candidate
        total_cost = (price_usd or 0) + (shipping_price_usd or 0)
        database.execute(
            """
            update zendrop_products
            set image_url = ?, updated_at = current_timestamp
            where product_id = ?
            """,
            (selected_image_url, zendrop_product_id),
        )
        database.execute(
            """
            insert into product_matches (
                competitor_product_id,
                zendrop_product_id,
                zendrop_match_score,
                visual_status,
                total_cost_usd,
                status,
                updated_at
            )
            values (?, ?, ?, ?, ?, 'approval_pending', current_timestamp)
            """,
            (competitor_product_id, zendrop_product_id, score, visual_status, total_cost),
        )
        matches_created += 1
    return matches_created


def find_best_zendrop_match(search_text: str, zendrop_rows: list[tuple], min_score: float) -> tuple | None:
    candidates = find_top_zendrop_matches(search_text, zendrop_rows, min_score, excluded_product_ids=set(), limit=1)
    if not candidates:
        return None
    product_id, _name, score, price_usd, shipping_price_usd, _image_url, _raw_json = candidates[0]
    return product_id, score, price_usd, shipping_price_usd


def find_top_zendrop_matches(
    search_text: str,
    zendrop_rows: list[tuple],
    min_score: float,
    excluded_product_ids: set[int],
    limit: int,
) -> list[tuple]:
    best_candidate = None
    best_score = 0.0
    required_score = max(min_score, category_min_score(product_category(search_text)))
    candidates = []
    for row in zendrop_rows:
        product_id, name, price_usd, shipping_price_usd, image_url, raw_json = normalize_zendrop_row(row)
        if int(product_id) in excluded_product_ids:
            continue
        score = score_zendrop_candidate(search_text, name)
        if score >= required_score:
            candidates.append((product_id, name, score, price_usd, shipping_price_usd, image_url, raw_json))
            if score > best_score:
                best_score = score
                best_candidate = candidates[-1]
    candidates.sort(key=lambda candidate: candidate[2], reverse=True)
    return candidates[:limit]


def choose_vision_verified_candidate(
    competitor_title: str,
    competitor_image_path: str | None,
    candidates: list[tuple],
    openai_settings: OpenAISettings | None,
    storage_dir: Path | None,
) -> tuple[tuple | None, list[dict[str, Any]]]:
    rejected_candidates: list[dict[str, Any]] = []
    for product_id, name, score, price_usd, shipping_price_usd, image_url, raw_json in candidates:
        product_rejected = False
        for candidate_image_url in zendrop_product_image_urls(image_url=image_url, raw_json=raw_json):
            verdict = verify_visual_match(
                competitor_title=competitor_title,
                competitor_image_path=competitor_image_path,
                zendrop_title=str(product_id),
                zendrop_name=name,
                zendrop_image_url=candidate_image_url,
                openai_settings=openai_settings,
            )
            if verdict["same_product"]:
                visual_status = "vision_pass" if verdict["source"] == "openai_vision" else "text_only"
                return (
                    product_id,
                    name,
                    score,
                    price_usd,
                    shipping_price_usd,
                    candidate_image_url,
                    visual_status,
                ), rejected_candidates
            product_rejected = True
        if product_rejected:
            rejected_candidates.append(
                {
                    "product_id": product_id,
                    "score": score,
                    "price_usd": price_usd,
                    "shipping_price_usd": shipping_price_usd,
                    "image_url": image_url,
                    "reason": "AI Vision rejected this Zendrop candidate for the current source product",
                }
            )
    return None, rejected_candidates


def store_rejected_candidates(
    database: sqlite3.Connection,
    competitor_product_id: int,
    rejected_candidates: list[dict[str, Any]],
) -> None:
    for candidate in rejected_candidates:
        total_cost = (candidate["price_usd"] or 0) + (candidate["shipping_price_usd"] or 0)
        existing_match = database.execute(
            """
            select id
            from product_matches
            where competitor_product_id = ? and zendrop_product_id = ?
            """,
            (competitor_product_id, candidate["product_id"]),
        ).fetchone()
        if existing_match:
            database.execute(
                """
                update product_matches
                set zendrop_match_score = ?,
                    visual_status = 'vision_rejected',
                    total_cost_usd = ?,
                    status = 'rejected',
                    updated_at = current_timestamp
                where id = ?
                """,
                (candidate["score"], total_cost, existing_match[0]),
            )
        else:
            database.execute(
                """
                insert into product_matches (
                    competitor_product_id,
                    zendrop_product_id,
                    zendrop_match_score,
                    visual_status,
                    total_cost_usd,
                    status,
                    updated_at
                )
                values (?, ?, ?, 'vision_rejected', ?, 'rejected', current_timestamp)
                """,
                (
                    competitor_product_id,
                    candidate["product_id"],
                    candidate["score"],
                    total_cost,
                ),
            )


def verify_visual_match(
    competitor_title: str,
    competitor_image_path: str | None,
    zendrop_title: str,
    zendrop_name: str,
    zendrop_image_url: str | None,
    openai_settings: OpenAISettings | None,
) -> dict[str, Any]:
    if not openai_settings or not openai_settings.api_key or not competitor_image_path or not zendrop_image_url:
        return {"same_product": True, "confidence": 0.0, "source": "text_only", "reason": "Vision skipped"}
    competitor_path = Path(competitor_image_path)
    if not competitor_path.exists():
        return {"same_product": True, "confidence": 0.0, "source": "text_only", "reason": "Competitor image missing"}
    try:
        with httpx.Client(timeout=60) as http_client:
            response = http_client.post(
                f"{openai_settings.api_url.rstrip('/')}/responses",
                headers={
                    "Authorization": f"Bearer {openai_settings.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": openai_settings.model,
                    "instructions": (
                        "You are a strict ecommerce product visual matcher. Return JSON only. "
                        "The second image must be a real product photo. Reject size charts, measurement tables, "
                        "text-heavy guide images, packaging-only images, or images where the wearable product is not visible. "
                        "Only pass when the images look at least 80% like the same sellable product. "
                        "Reject different color families, different silhouettes or cuts, different shoe construction, "
                        "different heel/sole style, different sleeve/strap style, different pattern, and different overall design."
                    ),
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        "Return JSON with keys same_product boolean, confidence number 0-1, "
                                        "zendrop_image_is_product_photo boolean, "
                                        "category_match, silhouette_match, color_match, pattern_match, closure_match booleans, "
                                        f"reason string. Competitor title: {competitor_title}. Zendrop title: {zendrop_name or zendrop_title}."
                                    ),
                                },
                                {"type": "input_image", "image_url": image_data_url(competitor_path)},
                                {"type": "input_image", "image_url": zendrop_image_url},
                            ],
                        }
                    ],
                    "text": {"format": {"type": "json_object"}},
                },
            )
        if response.status_code >= 400:
            return {"same_product": False, "confidence": 0.0, "source": "openai_vision", "reason": response.text[:300]}
        payload = parse_openai_json(response.json())
        same_product = is_strict_visual_match(payload)
        return {**payload, "same_product": same_product, "source": "openai_vision"}
    except Exception as error:
        return {"same_product": False, "confidence": 0.0, "source": "openai_vision", "reason": str(error)[:300]}


def is_strict_visual_match(payload: dict[str, Any]) -> bool:
    if not bool(payload.get("same_product")):
        return False
    if payload.get("zendrop_image_is_product_photo") is False:
        return False
    if float(payload.get("confidence") or 0) < VISION_PASS_CONFIDENCE:
        return False
    strict_keys = ("category_match", "silhouette_match", "color_match")
    return all(payload.get(key) is not False for key in strict_keys)


def score_zendrop_candidate(competitor_text: str, zendrop_name: str) -> float:
    competitor_category = product_category(competitor_text)
    zendrop_category = product_category(zendrop_name)
    if competitor_category and zendrop_category and competitor_category != zendrop_category:
        return 0.0
    score = float(fuzz.token_set_ratio(competitor_text, zendrop_name))
    competitor_colors = extract_color_terms(competitor_text)
    zendrop_colors = extract_color_terms(zendrop_name)
    if competitor_colors and zendrop_colors and competitor_colors.isdisjoint(zendrop_colors):
        score -= 28
    competitor_attributes = extract_terms(competitor_text, ATTRIBUTE_WORDS)
    zendrop_attributes = extract_terms(zendrop_name, ATTRIBUTE_WORDS)
    if incompatible_attributes(competitor_attributes, zendrop_attributes):
        score -= 35
    elif competitor_attributes and zendrop_attributes:
        overlap = competitor_attributes.intersection(zendrop_attributes)
        missing_ratio = 1 - (len(overlap) / max(len(competitor_attributes), 1))
        score -= min(24, missing_ratio * 18)
    return max(0.0, score)


def normalize_zendrop_row(row: tuple) -> tuple:
    if len(row) == 5:
        product_id, name, price_usd, shipping_price_usd, image_url = row
        return product_id, name, price_usd, shipping_price_usd, image_url, "{}"
    return row


def zendrop_product_image_urls(image_url: str | None, raw_json: str | None) -> list[str]:
    urls: list[str] = []
    if image_url:
        urls.append(image_url)
    try:
        raw = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    for image in raw.get("images") or []:
        url = image.get("url") if isinstance(image, dict) else None
        if url:
            urls.append(url)
    clean_urls: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            clean_urls.append(url)
            seen.add(url)
    return clean_urls


def product_category(text: str) -> str | None:
    word_list = normalized_words(text)
    words = set(word_list)
    phrase = " ".join(word_list)
    if words.intersection({"dress", "dresses", "gown"}) or "evening dress" in phrase:
        return "dress"
    if "skirt" in words:
        return "skirt"
    if words.intersection({"shoe", "shoes", "sneaker", "sneakers", "footwear", "sandal", "sandals"}):
        return "shoes"
    if words.intersection({"blouse", "shirt", "top", "pullover", "sweater"}):
        return "top"
    if words.intersection({"jacket", "blazer", "coat"}):
        return "outerwear"
    if words.intersection({"suit", "set", "tracksuit"}):
        return "set"
    return None


def category_min_score(category: str | None) -> float:
    return {
        "shoes": 82,
        "dress": 70,
        "top": 70,
        "outerwear": 74,
        "set": 74,
        "skirt": 76,
    }.get(category, 70)


def extract_terms(text: str, terms: set[str]) -> set[str]:
    normalized = " ".join(normalized_words(text))
    return {term for term in terms if term in normalized}


def extract_color_terms(text: str) -> set[str]:
    colors = extract_terms(text, COLOR_WORDS)
    normalized_colors = set()
    for color in colors:
        if color in {"burgundy", "wine"}:
            normalized_colors.add("red")
        elif color == "grey":
            normalized_colors.add("gray")
        elif color == "cream":
            normalized_colors.add("white")
        else:
            normalized_colors.add(color)
    return normalized_colors


def normalized_words(text: str) -> list[str]:
    return [word for word in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if word]


def incompatible_attributes(competitor_attributes: set[str], zendrop_attributes: set[str]) -> bool:
    incompatible_pairs = [
        ("lace up", "slip on"),
        ("slip on", "lace up"),
        ("strapless", "sleeveless"),
        ("strapless", "off shoulder"),
        ("floral", "solid"),
    ]
    return any(left in competitor_attributes and right in zendrop_attributes for left, right in incompatible_pairs)


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


def list_approval_cards(database: sqlite3.Connection, storage_dir: Path, limit: int = 100) -> list[dict]:
    rows = database.execute(
        """
        select
            pm.id,
            pm.status,
            pm.visual_status,
            pm.zendrop_match_score,
            pm.total_cost_usd,
            pm.manual_supplier_url,
            cp.id,
            cp.title,
            cp.price,
            cp.image_path,
            zp.product_id,
            zp.name,
            zp.image_url,
            zp.price_usd,
            zp.shipping_country_code,
            zp.shipping_price_usd,
            zp.shipping_estimated_delivery
        from product_matches pm
        join competitor_products cp on cp.id = pm.competitor_product_id
        join zendrop_products zp on zp.product_id = pm.zendrop_product_id
        where pm.status in ('approval_pending', 'approved')
        order by pm.updated_at desc, pm.id desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": row[0],
            "status": row[1],
            "visual_status": row[2],
            "zendrop_match_score": row[3],
            "manual_supplier_url": row[5],
            "competitor": {
                "id": row[6],
                "title": row[7],
                "price": row[8],
                "image_url": media_url_for_path(row[9], storage_dir),
            },
            "zendrop": {
                "product_id": row[10],
                "name": row[11],
                "image_url": row[12],
                "price_usd": row[13],
                "shipping_country_code": row[14],
                "shipping_price_usd": row[15],
                "shipping_estimated_delivery": row[16],
                "total_cost_usd": row[4],
            },
        }
        for row in rows
    ]
