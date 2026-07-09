from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "DataWise"

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_SERVER: str = "db"
    POSTGRES_PORT: str = "5432"
    POSTGRES_DB: str = "dsx_db"

    # If DATABASE_URL is set (e.g. injected from Azure Key Vault) it wins;
    # otherwise it's assembled from POSTGRES_* on the validator below.
    DATABASE_URL: str | None = None

    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme-devpassword"
    NEO4J_DATABASE: str = "neo4j"

    # Open-source data governance catalog (OpenMetadata). UI_URL is used to
    # build the browsable catalog_url stored on Job; API_URL is what the
    # backend/celery containers actually call (same server, different host
    # depending on whether it's browser-facing or container-to-container).
    OPENMETADATA_API_URL: str = "http://openmetadata_server:8585/api/v1"
    OPENMETADATA_UI_URL: str = "http://localhost:8585"
    OPENMETADATA_ADMIN_EMAIL: str = "admin@open-metadata.org"
    OPENMETADATA_ADMIN_PASSWORD: str = "admin"

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self


settings = Settings()
