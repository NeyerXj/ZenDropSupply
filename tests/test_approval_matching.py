import json
from io import BytesIO

from PIL import Image, ImageDraw

from app.database import open_database
from app.services import approval_matching
from app.services.approval_matching import (
    build_approval_matches,
    is_likely_size_chart_image,
    list_approval_cards,
    score_zendrop_candidate,
    search_query_provenance_score,
)
from app.services.visual_search_queries import VISUAL_QUERY_CACHE_KEY


def valid_zendrop_raw(*image_urls: str, extra: dict | None = None) -> str:
    payload = {
        "images": [{"url": image_url} for image_url in image_urls],
        "description": "Size: XS S M L XL. Available colors: red, burgundy, black.",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload)


def full_visual_match(**overrides):
    payload = {
        "same_product": True,
        "confidence": 0.86,
        "source": "openai_vision",
        "zendrop_image_is_product_photo": True,
        "category_match": True,
        "silhouette_match": True,
        "color_match": True,
        "material_match": True,
        "length_match": True,
        "key_details_match": True,
        "season_match": True,
        "variant_match": True,
        "pattern_match": True,
        "closure_match": True,
    }
    payload.update(overrides)
    return payload


def test_likely_size_chart_image_detector_rejects_table_image():
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    for x_position in range(0, 421, 105):
        draw.line((x_position, 0, x_position, 300), fill="black", width=2)
    for y_position in range(0, 301, 50):
        draw.line((0, y_position, 420, y_position), fill="black", width=2)
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    assert is_likely_size_chart_image(buffer.getvalue()) is True


def test_likely_size_chart_image_detector_allows_product_like_photo():
    image = Image.new("RGB", (420, 300), "#d8c2aa")
    draw = ImageDraw.Draw(image)
    draw.ellipse((140, 30, 280, 260), fill="#1d2438")
    draw.rectangle((170, 130, 250, 290), fill="#1d2438")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    assert is_likely_size_chart_image(buffer.getvalue()) is False


def test_build_approval_matches_links_best_zendrop_candidate(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "floral-maxi-dress",
                "Floral Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2331830,
                "Floral Maxi Dress for Women",
                12.5,
                "https://file.zendrop.com/dress.webp",
                valid_zendrop_raw("https://file.zendrop.com/dress.webp", "https://file.zendrop.com/dress-back.webp"),
                "ca",
                10.0,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["competitor"]["title"] == "Floral Maxi Dress"
    assert cards[0]["competitor"]["image_url"] == "/media/competitor_images/dress.jpg"
    assert cards[0]["zendrop"]["product_id"] == 2331830
    assert cards[0]["zendrop"]["total_cost_usd"] == 22.5
    assert cards[0]["status"] == "approval_pending"


def test_build_approval_matches_skips_zendrop_size_chart_image(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        image_url = kwargs["zendrop_image_url"]
        return {
            "same_product": image_url.endswith("product.webp"),
            "confidence": 0.91,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": image_url.endswith("product.webp"),
            "category_match": image_url.endswith("product.webp"),
            "silhouette_match": image_url.endswith("product.webp"),
            "color_match": image_url.endswith("product.webp"),
            "material_match": image_url.endswith("product.webp"),
            "length_match": image_url.endswith("product.webp"),
            "key_details_match": image_url.endswith("product.webp"),
            "season_match": image_url.endswith("product.webp"),
            "variant_match": image_url.endswith("product.webp"),
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "halter-maxi-dress",
                "Halter Neck Mesh Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3130392,
                "Halter Neck Mesh Maxi Dress",
                6.06,
                "https://file.zendrop.com/size-chart.webp",
                json.dumps(
                        {
                            "images": [
                                {"url": "https://file.zendrop.com/size-chart.webp"},
                                {"url": "https://file.zendrop.com/product.webp"},
                            ],
                            "description": "Size: XS S M L XL. Available colors: red, burgundy, black.",
                        }
                    ),
                "ca",
                6.29,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        selected_image_url = database.execute(
            "select image_url from zendrop_products where product_id = 3130392"
        ).fetchone()[0]
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert selected_image_url == "https://file.zendrop.com/product.webp"
    assert cards[0]["zendrop"]["image_url"] == "https://file.zendrop.com/product.webp"


def test_build_approval_matches_does_not_create_card_when_vision_rejects(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return {
            "same_product": False,
            "confidence": 0.41,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "reason": "Needs manual review",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "halter-maxi-dress",
                "Halter Neck Mesh Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3130392,
                "Halter Neck Mesh Maxi Dress",
                6.06,
                "https://file.zendrop.com/product.webp",
                valid_zendrop_raw("https://file.zendrop.com/product.webp", "https://file.zendrop.com/product-back.webp"),
                "ca",
                6.29,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")
        rejected_row = database.execute(
            """
            select zendrop_product_id, status, visual_status, vision_confidence, vision_reason
            from product_matches
            where competitor_product_id = 1
            """
        ).fetchone()

    assert result == {"matches_created": 0}
    assert cards == []
    assert rejected_row == (3130392, "rejected", "vision_rejected", 0.41, "Needs manual review")


def test_build_approval_matches_creates_manual_review_card_for_fallback_vision_score(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return {
            "same_product": True,
            "confidence": 0.64,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": True,
            "silhouette_match": True,
            "color_match": True,
            "material_match": True,
            "length_match": True,
            "key_details_match": True,
            "season_match": True,
            "variant_match": True,
            "reason": "Close enough for manual approval",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "halter-maxi-dress",
                "Halter Neck Mesh Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3130392,
                "Halter Neck Mesh Maxi Dress",
                6.06,
                "https://file.zendrop.com/product.webp",
                valid_zendrop_raw("https://file.zendrop.com/product.webp", "https://file.zendrop.com/product-back.webp"),
                "ca",
                6.29,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")
        match_row = database.execute(
            """
            select status, visual_status, vision_confidence, vision_reason, vision_verdict_json
            from product_matches
            where competitor_product_id = 1
            """
        ).fetchone()

    assert result == {"matches_created": 1}
    assert cards[0]["status"] == "approval_pending"
    assert cards[0]["visual_status"] == "vision_review"
    assert cards[0]["vision_confidence"] == 0.64
    assert cards[0]["vision_reason"] == "Close enough for manual approval"
    assert match_row[0:4] == ("approval_pending", "vision_review", 0.64, "Close enough for manual approval")
    assert json.loads(match_row[4])["confidence"] == 0.64


def test_build_approval_matches_rejects_fallback_when_category_mismatches(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "shoe.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return {
            "same_product": False,
            "confidence": 0.65,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": False,
            "silhouette_match": False,
            "color_match": False,
            "reason": "Slides are not sneakers",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    source_raw = json.dumps({VISUAL_QUERY_CACHE_KEY: ["black slip-on shoes"]})
    zendrop_raw = json.dumps({"_ttd_search_queries": ["black slip-on shoes"]})

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "orthopedic-shoe",
                "Orthopedic Shoes for Pain Relief Women | Cushioned Sole & Easy Slip-On",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                source_raw,
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2218757,
                "Boys Girls Cloud Unicorn Slide Sandals Non-slip Cushioned Thick Sole",
                18.0,
                "https://file.zendrop.com/slides.webp",
                zendrop_raw,
                "ca",
                9.0,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=62)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 0}
    assert cards == []


def test_build_approval_matches_rejects_review_score_when_vision_says_not_same_product(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "jumpsuit.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return {
            "same_product": False,
            "confidence": 0.65,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": True,
            "silhouette_match": False,
            "color_match": False,
            "pattern_match": False,
            "closure_match": False,
            "reason": "One is a wide-leg jumpsuit and the other is a fitted slit dress.",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    source_raw = json.dumps({VISUAL_QUERY_CACHE_KEY: ["white wide leg jumpsuit"]})
    zendrop_raw = valid_zendrop_raw(
        "https://file.zendrop.com/dress.webp",
        "https://file.zendrop.com/dress-back.webp",
        extra={"_ttd_search_queries": ["white wide leg jumpsuit"]},
    )

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "wide-leg-jumpsuit",
                "Women's One-Piece Suit | Simple & Elegant | Form-Fitting Cut | Soft Fabric Blend",
                39.95,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                source_raw,
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2880001,
                "Hawthaw Women's Elegant Summer Backless Dress (One Piece)",
                7.38,
                "https://file.zendrop.com/dress.webp",
                zendrop_raw,
                "ca",
                8.43,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=62)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")
        rejected_row = database.execute(
            """
            select status, visual_status, vision_confidence, vision_reason
            from product_matches
            where competitor_product_id = 1
            """
        ).fetchone()

    assert result == {"matches_created": 0}
    assert cards == []
    assert rejected_row == (
        "rejected",
        "vision_rejected",
        0.65,
        "One is a wide-leg jumpsuit and the other is a fitted slit dress.",
    )


def test_build_approval_matches_rejects_near_review_when_color_mismatches(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return {
            "same_product": False,
            "confidence": 0.42,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": True,
            "silhouette_match": True,
            "color_match": False,
            "pattern_match": True,
            "closure_match": True,
            "reason": "Same strapless maxi silhouette, different color.",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    source_raw = json.dumps({VISUAL_QUERY_CACHE_KEY: ["strapless maxi dress"]})
    zendrop_raw = json.dumps({"_ttd_search_queries": ["strapless maxi dress"]})

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "strapless-maxi",
                "Women's Maxi Dress | Strapless Cut & Flowing Silhouette",
                52.95,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                source_raw,
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2213233,
                "GRACE KARIN Women Strapless Casual Loose Ruched Long Maxi Dress with Pockets Black",
                12.0,
                "https://file.zendrop.com/dress.webp",
                zendrop_raw,
                "ca",
                8.0,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=62)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 0}
    assert cards == []


def test_build_approval_matches_uses_zendrop_search_query_provenance(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "shoe.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        return full_visual_match(confidence=0.85, reason="Same black orthopedic slip-on sneaker")

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    source_raw = json.dumps({VISUAL_QUERY_CACHE_KEY: ["black slip-on shoes", "women orthopedic sneakers"]})
    zendrop_raw = json.dumps(
        {
            "_ttd_search_queries": ["women orthopedic slip on shoes"],
            "images": [
                {"url": "https://file.zendrop.com/shoe.webp"},
                {"url": "https://file.zendrop.com/shoe-side.webp"},
            ],
            "description": "Size: US 6 7 8 9 10. Available colors: black.",
        }
    )

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "orthopedic-shoe",
                "Orthopedic Shoes for Pain Relief Women | Cushioned Sole & Easy Slip-On",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                source_raw,
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2183718,
                "Aceptolcom Women Orthopedic Sneakers Walking ShoesMesh Breathable Arch Support Slip-on Light Air Cushion Orthopedic Sneakers Diabetic Shoes (8.5Black8.5)",
                18.0,
                "https://file.zendrop.com/shoe.webp",
                zendrop_raw,
                "ca",
                9.0,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=62)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 2183718
    assert cards[0]["vision_confidence"] == 0.85


def test_build_approval_matches_retries_next_candidate_after_rejected_zendrop_item(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        image_url = kwargs["zendrop_image_url"]
        return {
            "same_product": image_url.endswith("second.webp"),
            "confidence": 0.92,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": True,
            "silhouette_match": image_url.endswith("second.webp"),
            "color_match": image_url.endswith("second.webp"),
            "material_match": image_url.endswith("second.webp"),
            "length_match": image_url.endswith("second.webp"),
            "key_details_match": image_url.endswith("second.webp"),
            "season_match": image_url.endswith("second.webp"),
            "variant_match": image_url.endswith("second.webp"),
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "red-maxi-dress",
                "Red Strapless Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values
                (1001, 'Red Strapless Maxi Dress', 10.0, 'https://file.zendrop.com/first.webp', ?, 'ca', 5.0),
                (1002, 'Red Strapless Maxi Dress', 12.0, 'https://file.zendrop.com/second.webp', ?, 'ca', 6.0)
            """
            ,
            (
                valid_zendrop_raw("https://file.zendrop.com/first.webp", "https://file.zendrop.com/first-back.webp"),
                valid_zendrop_raw("https://file.zendrop.com/second.webp", "https://file.zendrop.com/second-back.webp"),
            ),
        )
        database.execute(
            """
            insert into product_matches (
                competitor_product_id,
                zendrop_product_id,
                zendrop_match_score,
                visual_status,
                status,
                total_cost_usd
            )
            values (1, 1001, 95, 'vision_rejected', 'rejected', 15.0)
            """
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 1002
    assert cards[0]["visual_status"] == "vision_pass"


def test_build_approval_matches_checks_more_than_first_five_candidates(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    image_path = tmp_path / "storage" / "competitor_images" / "dress.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")

    def fake_visual_match(**kwargs):
        image_url = kwargs["zendrop_image_url"]
        is_target = image_url.endswith("candidate-6.webp")
        return {
            "same_product": is_target,
            "confidence": 0.82 if is_target else 0.12,
            "source": "openai_vision",
            "zendrop_image_is_product_photo": True,
            "category_match": True,
            "silhouette_match": is_target,
            "color_match": is_target,
            "material_match": is_target,
            "length_match": is_target,
            "key_details_match": is_target,
            "season_match": is_target,
            "variant_match": is_target,
            "reason": "Candidate 6 is the first close visual match" if is_target else "Different dress",
        }

    monkeypatch.setattr(approval_matching, "verify_visual_match", fake_visual_match)

    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, image_path, tags_json, status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com",
                "red-maxi-dress",
                "Red Strapless Maxi Dress",
                79.0,
                str(image_path),
                "[]",
                "ready_for_zendrop",
                "{}",
            ),
        )
        for product_id in range(1001, 1008):
            database.execute(
                """
                insert into zendrop_products (
                    product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    "Red Strapless Maxi Dress",
                    10.0,
                    f"https://file.zendrop.com/candidate-{product_id - 1000}.webp",
                    valid_zendrop_raw(
                        f"https://file.zendrop.com/candidate-{product_id - 1000}.webp",
                        f"https://file.zendrop.com/candidate-{product_id - 1000}-back.webp",
                    ),
                    "ca",
                    5.0,
                ),
            )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 1006
    assert cards[0]["visual_status"] == "vision_pass"


def test_build_approval_matches_uses_zendrop_only(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, tags_json, status, raw_json
            )
            values ('https://example.com', 'maxi-dress', 'Women Elegant Maxi Dress', 79, '[]', 'ready_for_zendrop', '{}')
            """
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """
            ,
            (
                2807078,
                "Women Elegant Maxi Dress",
                18.0,
                "https://file.zendrop.com/maxi.jpg",
                valid_zendrop_raw("https://file.zendrop.com/maxi.jpg", "https://file.zendrop.com/maxi-back.jpg"),
                "ca",
                9.0,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=35)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 2807078
    assert cards[0]["zendrop"]["total_cost_usd"] == 27.0
    assert cards[0]["zendrop"]["suggested_price_usd"] == 81.0


def test_build_approval_matches_rejects_low_confidence_zendrop_candidate(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, tags_json, status, raw_json
            )
            values (
                'https://example.com',
                'maxi-kleid',
                'Maxi Kleid Damen | Eleganter Fall & Beinschlitz',
                39.95,
                '[]',
                'ready_for_zendrop',
                '{}'
            )
            """
        )
        database.execute(
            """
            insert into zendrop_products (
                product_id, name, price_usd, image_url, raw_json, shipping_country_code, shipping_price_usd
            )
            values (
                2809564,
                'Elegant Off-Shoulder Maxi Dress with Slit Design',
                10.12,
                'https://file.zendrop.com/maxi.jpg',
                '{}',
                'ca',
                9.10
            )
            """
        )
        database.commit()

        result = build_approval_matches(database=database)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 0}
    assert cards == []


def test_zendrop_match_score_rejects_wrong_category():
    score = score_zendrop_candidate(
        "Red strapless maxi dress with side slit",
        "Pink satin midi skirt for women",
    )

    assert score == 0


def test_zendrop_match_score_rejects_dress_pumps_for_dress_source():
    score = score_zendrop_candidate(
        "Red strapless maxi dress with side slit",
        "benassal Women Dress Pumps Orthotic Heels Orthopedic Bunion Shoes",
    )

    assert score == 0


def test_search_query_provenance_ignores_broad_gender_category_queries():
    score = search_query_provenance_score(
        ["women dress"],
        json.dumps({"_ttd_search_queries": ["women dress"]}),
    )

    assert score == 0


def test_search_query_provenance_boosts_specific_queries():
    score = search_query_provenance_score(
        ["black slip-on shoes"],
        json.dumps({"_ttd_search_queries": ["black slip-on shoes"]}),
    )

    assert score == 86


def test_zendrop_match_score_penalizes_wrong_shoe_style():
    score = score_zendrop_candidate(
        "White lace up platform sneakers for women",
        "Grey slip on orthopedic walking shoes for women",
    )

    assert score < 82


def test_zendrop_match_score_keeps_close_dress_match():
    score = score_zendrop_candidate(
        "Red strapless maxi dress with side slit",
        "Burgundy strapless maxi evening dress with side slit",
    )

    assert score >= 62


def test_zendrop_match_score_rejects_slides_for_shoe_source():
    score = score_zendrop_candidate(
        "black slip-on orthopedic shoes",
        "Boys Girls Cloud Unicorn Slide Sandals Non-slip Cushioned Thick Sole",
    )

    assert score == 0
