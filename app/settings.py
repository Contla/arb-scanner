from datetime import time
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from app.models import MarketType, Sport

ROOT = Path(__file__).resolve().parent.parent


class MatchConfig(BaseModel):
    kickoff_tolerance_min: int
    name_score_min: int


class BookConfig(BaseModel):
    enabled: bool = True
    interval_s: int = 60
    fee_pct: float = 0.0  # commission taken from winnings


class Settings(BaseSettings):
    """Settings from config.yaml, secrets from .env (env vars override both)."""

    model_config = SettingsConfigDict(
        yaml_file=ROOT / "config.yaml",
        env_file=ROOT / ".env",
        env_ignore_empty=True,
    )

    # config.yaml
    bankroll_mxn: float
    min_roi: float
    min_profit_mxn: float
    verify_roi_above: float
    min_minutes_to_kickoff: int
    match: MatchConfig
    sports: list[Sport]
    markets: list[MarketType]
    books: dict[str, BookConfig]
    quiet_hours: tuple[time, time] | None = None

    # .env
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    proxy_url: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, dotenv_settings, YamlConfigSettingsSource(settings_cls)
