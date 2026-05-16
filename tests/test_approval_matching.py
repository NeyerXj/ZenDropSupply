import json

from app.database import open_database
from app.services.approval_matching import build_approval_matches, list_approval_cards


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


def test_build_approval_matches_uses_zendrop_only(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    with open_database(database_url) as database:
        database.execute(
            """
            insert into competitor_products (
                store_url, handle, title, price, tags_json, status, raw_json
            )
            values ('https://example.com', 'maxi-kleid', 'Maxi Kleid Damen | Eleganter Fall', 79, '[]', 'ready_for_zendrop', '{}')
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


def test_build_approval_matches_keeps_low_confidence_zendrop_candidate_for_review(tmp_path):
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

    assert result == {"matches_created": 1}
    assert cards[0]["zendrop"]["product_id"] == 2809564
    assert cards[0]["visual_status"] == "review"
