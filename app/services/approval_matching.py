from __future__ import annotations

import sqlite3
from pathlib import Path
import re

from rapidfuzz import fuzz

from app.services.dashboard import media_url_for_path
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


def build_approval_matches(database: sqlite3.Connection, min_score: float = 62) -> dict[str, int]:
    zendrop_rows = database.execute(
        """
        select product_id, name, price_usd, shipping_price_usd
        from zendrop_products
        order by updated_at desc, product_id desc
        """
    ).fetchall()
    matches_created = build_zendrop_only_matches(database=database, zendrop_rows=zendrop_rows, min_score=min_score)
    database.commit()
    return {"matches_created": matches_created}


def build_zendrop_only_matches(database: sqlite3.Connection, zendrop_rows: list[tuple], min_score: float) -> int:
    competitor_rows = database.execute(
        """
        select cp.id, cp.title
        from competitor_products cp
        where cp.status in ('ready_for_zendrop', 'zendrop_matched')
          and not exists (
            select 1 from product_matches pm
            where pm.competitor_product_id = cp.id
          )
        order by cp.updated_at desc, cp.id desc
        """
    ).fetchall()
    matches_created = 0
    for competitor_product_id, competitor_title in competitor_rows:
        candidate = find_best_zendrop_match(
            search_text=zendrop_search_text(competitor_title),
            zendrop_rows=zendrop_rows,
            min_score=min_score,
        )
        if candidate is None:
            continue
        zendrop_product_id, score, price_usd, shipping_price_usd = candidate
        total_cost = (price_usd or 0) + (shipping_price_usd or 0)
        visual_status = "review" if score < 55 else "pending"
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
    best_candidate = None
    best_score = 0.0
    required_score = max(min_score, category_min_score(product_category(search_text)))
    for product_id, name, price_usd, shipping_price_usd in zendrop_rows:
        score = score_zendrop_candidate(search_text, name)
        if score > best_score:
            best_score = score
            best_candidate = (product_id, score, price_usd, shipping_price_usd)
    if best_candidate is None or best_score < required_score:
        return None
    return best_candidate


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
