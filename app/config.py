from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class ZendropSettings(BaseModel):
    api_token: str = ""
    api_url: str = "https://app.zendrop.com/mcp/v1"
    default_country_code: str = "ca"


class OpenAISettings(BaseModel):
    api_key: str = ""
    api_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"


class GeminiSettings(BaseModel):
    api_key: str = ""
    api_url: str = "https://generativelanguage.googleapis.com/v1beta"
    image_model: str = "gemini-3-pro-image-preview"


class ShopifySettings(BaseModel):
    store: str = ""
    access_token: str = ""
    api_version: str = "2026-04"
    graphql_url: str = ""


class AdminSettings(BaseModel):
    username: str = "admin"
    password: str = "admin"
    session_secret: str = "change-me"


class Settings(BaseModel):
    database_url: str = "sqlite:///storage/pipeline.db"
    storage_dir: Path = Path("storage")
    zendrop: ZendropSettings = Field(default_factory=ZendropSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    shopify: ShopifySettings = Field(default_factory=ShopifySettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///storage/pipeline.db"
    storage_dir: Path = Path("storage")
    zendrop_api_token: str = ""
    zendrop_api_url: str = "https://app.zendrop.com/mcp/v1"
    zendrop_default_country_code: str = "ca"
    openai_api_key: str = ""
    openai_api_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    gemini_api_key: str = ""
    gemini_api_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_image_model: str = "gemini-3-pro-image-preview"
    shopify_store: str = ""
    shopify_access_token: str = ""
    shopify_api_version: str = "2026-04"
    shopify_graphql_url: str = ""
    admin_username: str = "admin"
    admin_password: str = "admin"
    admin_session_secret: str = "change-me"

    def to_settings(self) -> Settings:
        return Settings(
            database_url=self.database_url,
            storage_dir=self.storage_dir,
            zendrop=ZendropSettings(
                api_token=self.zendrop_api_token,
                api_url=self.zendrop_api_url,
                default_country_code=self.zendrop_default_country_code,
            ),
            openai=OpenAISettings(
                api_key=self.openai_api_key,
                api_url=self.openai_api_url,
                model=self.openai_model,
            ),
            gemini=GeminiSettings(
                api_key=self.gemini_api_key,
                api_url=self.gemini_api_url,
                image_model=self.gemini_image_model,
            ),
            shopify=ShopifySettings(
                store=self.shopify_store,
                access_token=self.shopify_access_token,
                api_version=self.shopify_api_version,
                graphql_url=self.shopify_graphql_url,
            ),
            admin=AdminSettings(
                username=self.admin_username,
                password=self.admin_password,
                session_secret=self.admin_session_secret,
            ),
        )


def load_settings() -> Settings:
    return EnvironmentSettings().to_settings()
