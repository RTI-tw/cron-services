import os
from functools import lru_cache


class Settings:
    def __init__(self) -> None:
        self.gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "")
        self.gcs_bucket: str = (os.getenv("GCS_BUCKET") or "").strip()
        self.web_url_base: str = (os.getenv("WEB_URL_BASE") or "").strip().rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
