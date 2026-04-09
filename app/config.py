from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./monitor.db"
    environment: str = "development"

    # Simulator
    reading_interval_seconds: float = 2.0

    # Detection thresholds
    cusum_h: float = 5.0      # decision interval (normalised units)
    cusum_k: float = 0.5      # allowance
    zscore_window: int = 30   # readings in rolling window
    zscore_threshold: float = 3.0

    # Evidently batch window
    evidently_window: int = 60   # readings per report cycle

    # Cooldown between events (seconds)
    event_cooldown_seconds: int = 30

    # Claude explanation cache TTL (seconds)
    explanation_cache_ttl: int = 300


settings = Settings()
