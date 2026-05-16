from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3

from app.providers.competitor_shopify import CompetitorProduct


DEFAULT_WOMEN_KEYWORDS = [
    "women",
    "womens",
    "woman",
    "ladies",
    "lady",
    "female",
    "dress",
    "dresses",
    "skirt",
    "blouse",
    "bikini",
    "swimwear",
    "romper",
    "jumpsuit",
    "necklace",
    "bracelet",
]
DEFAULT_MALE_KEYWORDS = ["men", "mens", "male", "man", "boys", "boy"]
DEFAULT_SUMMER_KEYWORDS = [
    "summer",
    "dress",
    "maxi",
    "sandal",
    "sandals",
    "skirt",
    "blouse",
    "top",
    "shorts",
    "beach",
    "swim",
    "bikini",
    "linen",
    "vacation",
    "resort",
    "sleeveless",
    "strapless",
    "jewelry",
    "bracelet",
    "necklace",
]
DEFAULT_EXCLUDE_KEYWORDS = ["winter", "coat", "jacket", "hoodie", "sweater", "boots", "thermal", "fleece"]


@dataclass(frozen=True)
class ProductFilterConfig:
    name: str
    women_keywords: list[str]
    male_keywords: list[str]
    summer_keywords: list[str]
    exclude_keywords: list[str]


DEFAULT_FILTER_CONFIG = ProductFilterConfig(
    name="default",
    women_keywords=DEFAULT_WOMEN_KEYWORDS,
    male_keywords=DEFAULT_MALE_KEYWORDS,
    summer_keywords=DEFAULT_SUMMER_KEYWORDS,
    exclude_keywords=DEFAULT_EXCLUDE_KEYWORDS,
)


def classify_product_status(product: CompetitorProduct, config: ProductFilterConfig | None = None) -> str:
    filter_config = config or DEFAULT_FILTER_CONFIG
    searchable_text = normalize_text(" ".join([product.handle, product.title, product.product_type or "", *product.tags]))
    words = set(searchable_text.split())

    if matches_keywords(searchable_text, words, filter_config.male_keywords):
        return "skipped_male"
    if not matches_keywords(searchable_text, words, filter_config.women_keywords):
        return "skipped_not_women"
    if matches_keywords(searchable_text, words, filter_config.exclude_keywords):
        return "skipped_season"
    if not matches_keywords(searchable_text, words, filter_config.summer_keywords):
        return "skipped_season"
    return "ready_for_zendrop"


def get_active_filter_config(database: sqlite3.Connection) -> ProductFilterConfig:
    row = database.execute(
        """
        select name, women_keywords_json, male_keywords_json, summer_keywords_json, exclude_keywords_json
        from filter_configs
        where active
        order by updated_at desc, id desc
        limit 1
        """
    ).fetchone()
    if row is None:
        return DEFAULT_FILTER_CONFIG
    return ProductFilterConfig(
        name=row[0],
        women_keywords=json.loads(row[1]),
        male_keywords=json.loads(row[2]),
        summer_keywords=json.loads(row[3]),
        exclude_keywords=json.loads(row[4]),
    )


def save_active_filter_config(database: sqlite3.Connection, config: ProductFilterConfig) -> ProductFilterConfig:
    normalized_config = normalize_config(config)
    database.execute("update filter_configs set active = false")
    database.execute(
        """
        insert into filter_configs (
            name,
            women_keywords_json,
            male_keywords_json,
            summer_keywords_json,
            exclude_keywords_json,
            active,
            updated_at
        )
        values (?, ?, ?, ?, ?, true, current_timestamp)
        on conflict(name) do update set
            women_keywords_json = excluded.women_keywords_json,
            male_keywords_json = excluded.male_keywords_json,
            summer_keywords_json = excluded.summer_keywords_json,
            exclude_keywords_json = excluded.exclude_keywords_json,
            active = true,
            updated_at = current_timestamp
        """,
        (
            normalized_config.name,
            json.dumps(normalized_config.women_keywords, ensure_ascii=False),
            json.dumps(normalized_config.male_keywords, ensure_ascii=False),
            json.dumps(normalized_config.summer_keywords, ensure_ascii=False),
            json.dumps(normalized_config.exclude_keywords, ensure_ascii=False),
        ),
    )
    database.commit()
    return normalized_config


def normalize_config(config: ProductFilterConfig) -> ProductFilterConfig:
    return ProductFilterConfig(
        name=config.name.strip() or DEFAULT_FILTER_CONFIG.name,
        women_keywords=normalize_keywords(config.women_keywords),
        male_keywords=normalize_keywords(config.male_keywords),
        summer_keywords=normalize_keywords(config.summer_keywords),
        exclude_keywords=normalize_keywords(config.exclude_keywords),
    )


def normalize_keywords(keywords: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = normalize_text(keyword)
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def matches_keywords(searchable_text: str, words: set[str], keywords: list[str]) -> bool:
    for keyword in normalize_keywords(keywords):
        keyword_words = keyword.split()
        if len(keyword_words) == 1 and keyword_words[0] in words:
            return True
        if len(keyword_words) > 1 and keyword in searchable_text:
            return True
    return False
