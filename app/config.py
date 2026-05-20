from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ZendropSettings(BaseModel):
    api_token: str = ""
    api_url: str = "https://app.zendrop.com/mcp/v1"


class HarvesterSettings(BaseModel):
    worker_id: str = "local-worker"
    poll_seconds: float = 2.0
    claim_seconds: int = 180
    controller_public_host: str = "127.0.0.1"
    controller_public_port: int = 8091
    postgres_public_host: str = "127.0.0.1"


class Settings(BaseModel):
    database_url: str = "postgresql://zendrop:zendrop@postgres:5432/zendrop_supply"
    zendrop: ZendropSettings = ZendropSettings()
    harvester: HarvesterSettings = HarvesterSettings()


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://zendrop:zendrop@postgres:5432/zendrop_supply"
    zendrop_api_token: str = ""
    zendrop_api_url: str = "https://app.zendrop.com/mcp/v1"
    harvester_worker_id: str = "local-worker"
    harvester_poll_seconds: float = 2.0
    harvester_claim_seconds: int = 180
    controller_public_host: str = "127.0.0.1"
    controller_public_port: int = 8091
    postgres_public_host: str = "127.0.0.1"

    def to_settings(self) -> Settings:
        return Settings(
            database_url=self.database_url,
            zendrop=ZendropSettings(api_token=self.zendrop_api_token, api_url=self.zendrop_api_url),
            harvester=HarvesterSettings(
                worker_id=self.harvester_worker_id,
                poll_seconds=self.harvester_poll_seconds,
                claim_seconds=self.harvester_claim_seconds,
                controller_public_host=self.controller_public_host,
                controller_public_port=self.controller_public_port,
                postgres_public_host=self.postgres_public_host,
            ),
        )


def load_settings() -> Settings:
    return EnvironmentSettings().to_settings()
