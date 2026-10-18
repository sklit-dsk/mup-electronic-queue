from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "demo"
    database_url: str = "postgresql+psycopg://queue:queue@localhost:5432/queue"
    timezone: str = "Europe/Podgorica"
    data_dir: Path = Path("runtime-data")
    origin: str = "http://localhost:5173"
    session_secret: str = ""
    encryption_key: str = ""
    fingerprint_key: str = ""
    staff_password: str = ""
    sms_webhook_url: str = ""
    sms_webhook_token: str = ""
    document_reader: str = "tesseract"
    photo_retention_hours: int = 24
    record_retention_days: int = 0
    booking_horizon_days: int = 7
    enforce_hours: bool = False
    future_service_limit: bool = True
    recall_interval_seconds: int = Field(default=120, ge=1)

    @property
    def tz(self):
        return ZoneInfo(self.timezone)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.origin.split(",") if origin.strip()]

    def validate_mode(self):
        if self.app_env not in {"demo", "test", "pilot"}:
            raise RuntimeError("APP_ENV must be demo, test or pilot")
        if self.app_env == "pilot":
            required = (
                self.session_secret,
                self.encryption_key,
                self.fingerprint_key,
                self.staff_password,
                self.sms_webhook_url,
                self.sms_webhook_token,
            )
            if not all(required) or self.document_reader != "tesseract" or not self.enforce_hours:
                raise RuntimeError("Pilot requires real secrets, SMS adapter, OCR and enforced hours")
            if self.record_retention_days <= 0 or self.photo_retention_hours <= 0:
                raise RuntimeError("Pilot requires explicit retention settings")


@lru_cache
def settings():
    value = Settings()
    value.validate_mode()
    value.data_dir.mkdir(parents=True, exist_ok=True)
    return value
