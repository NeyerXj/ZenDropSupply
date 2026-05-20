from app.config import HarvesterSettings, Settings
from app.services.harvester import format_eta, normalize_keywords, worker_setup


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
