from app.config import HarvesterSettings, Settings
from app.providers.zendrop import ZendropImage, ZendropProductSummary
from app.services.harvester import first_image_payload, first_product_image_url, format_eta, normalize_keywords, worker_setup


def test_normalize_keywords_keeps_empty_catalog_query_once():
    assert normalize_keywords(["", " maxi dress ", "maxi  dress", ""]) == ["", "maxi dress"]


def test_format_eta_uses_human_readable_units():
    assert format_eta(None) == "warming up"
    assert format_eta(45) == "45s"
    assert format_eta(900) == "15m"
    assert format_eta(7500) == "2h 5m"


def test_worker_setup_uses_private_postgres_host():
    settings = Settings(
        harvester=HarvesterSettings(
            controller_public_host="10.8.0.1",
            postgres_public_host="10.8.0.1",
        )
    )

    setup = worker_setup(settings)

    assert "10.8.0.1" in setup["database_url"]
    assert "docker compose -f docker-compose.worker.yml up -d --build" == setup["run"]


def test_first_image_payload_keeps_only_primary_image():
    product = ZendropProductSummary(
        id=1001,
        name="Dress",
        image="https://file.zendrop.com/primary.webp",
        images=[
            ZendropImage(id=10, url="https://file.zendrop.com/primary.webp"),
            ZendropImage(id=11, url="https://file.zendrop.com/secondary.webp"),
        ],
    )

    assert first_product_image_url(product) == "https://file.zendrop.com/primary.webp"
    assert first_image_payload(product) == [{"image_id": 10, "url": "https://file.zendrop.com/primary.webp"}]
