import json

from app.database import open_database
from app.services import approval_matching
from app.services.approval_matching import build_approval_matches, list_approval_cards, score_zendrop_candidate


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
                json.dumps({"id": 2331830}),
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
                        ]
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
                json.dumps({"images": [{"url": "https://file.zendrop.com/product.webp"}]}),
                "ca",
                6.29,
            ),
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=50)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")
        rejected_row = database.execute(
            """
            select zendrop_product_id, status, visual_status
            from product_matches
            where competitor_product_id = 1
            """
        ).fetchone()

    assert result == {"matches_created": 0}
    assert cards == []
    assert rejected_row == (3130392, "rejected", "vision_rejected")


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
            "silhouette_match": True,
            "color_match": True,
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
                (1001, 'Red Strapless Maxi Dress', 10.0, 'https://file.zendrop.com/first.webp', '{}', 'ca', 5.0),
                (1002, 'Red Strapless Maxi Dress', 12.0, 'https://file.zendrop.com/second.webp', '{}', 'ca', 6.0)
            """
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
            values (2807078, 'Women Elegant Maxi Dress', 18.0, 'https://file.zendrop.com/maxi.jpg', '{}', 'ca', 9.0)
            """
        )
        database.commit()

        result = build_approval_matches(database=database, min_score=35)
        cards = list_approval_cards(database=database, storage_dir=tmp_path / "storage")

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 2807078
    assert cards[0]["zendrop"]["total_cost_usd"] == 27.0


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
