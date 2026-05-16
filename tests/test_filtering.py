import json

from app.database import open_database
from app.providers.competitor_shopify import CompetitorProduct
from app.services.filtering import (
    ProductFilterConfig,
    classify_product_status,
    get_active_filter_config,
    save_active_filter_config,
)


def make_product(title: str, product_type: str = "", tags: list[str] | None = None) -> CompetitorProduct:
    return CompetitorProduct(
        external_id="123",
        handle=title.lower().replace(" ", "-"),
        title=title,
        product_type=product_type,
        tags=tags or [],
        price=59.99,
        image_url=None,
        raw={"title": title},
    )


def test_default_filter_keeps_women_summer_products():
    product = make_product("Floral Maxi Dress", product_type="Dresses", tags=["Women", "Summer"])

    assert classify_product_status(product) == "ready_for_zendrop"


def test_default_filter_rejects_german_women_maxi_dress():
    product = make_product("Maxikleid Damen", product_type="SS - Womens - Dresses", tags=["VDMAX"])

    assert classify_product_status(product) == "skipped_language"


def test_default_filter_rejects_non_english_spring_summer_product_type():
    product = make_product("Orthopädische Schuhe Damen", product_type="SS - Womens - Shoes & Heels", tags=["VSORTHO"])

    assert classify_product_status(product) == "skipped_language"


def test_default_filter_rejects_male_products_before_season_match():
    product = make_product("Mens Summer Linen Shirt", product_type="Shirts", tags=["Summer"])

    assert classify_product_status(product) == "skipped_male"


def test_default_filter_rejects_products_without_women_signal():
    product = make_product("Unisex Waterproof Backpack", product_type="Bags", tags=["Travel"])

    assert classify_product_status(product) == "skipped_not_women"


def test_custom_filter_config_controls_summer_rules():
    product = make_product("Floral Maxi Dress", product_type="Dresses", tags=["Women", "Summer"])
    config = ProductFilterConfig(
        name="strict linen",
        women_keywords=["women"],
        male_keywords=["men"],
        summer_keywords=["linen"],
        exclude_keywords=[],
    )

    assert classify_product_status(product, config=config) == "skipped_season"


def test_active_filter_config_round_trip_is_database_backed(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    config = ProductFilterConfig(
        name="test profile",
        women_keywords=["women", "dress"],
        male_keywords=["men"],
        summer_keywords=["linen", "sandal"],
        exclude_keywords=["winter"],
    )

    with open_database(database_url) as database:
        save_active_filter_config(database, config)
        loaded_config = get_active_filter_config(database)
        raw_row = database.execute(
            "select women_keywords_json, active from filter_configs where name = ?",
            ("test profile",),
        ).fetchone()

    assert loaded_config == config
    assert json.loads(raw_row[0]) == ["women", "dress"]
    assert raw_row[1] == 1
