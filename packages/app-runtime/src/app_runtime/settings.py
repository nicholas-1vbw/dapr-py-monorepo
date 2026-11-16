"""Settings shared by all apps, read from `APP_*` environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Process settings. Apps subclass this to add their own `APP_*` fields."""

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    name: str = "app"
    """Default OpenTelemetry service name; `OTEL_SERVICE_NAME` overrides it."""
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    reload: bool = False
    """Restart on code changes (local development only)."""
    reload_dirs: list[str] = []
    """Directories watched when reloading; the launcher passes the app's source
    directories. Empty means the working directory."""
    startup_timeout_seconds: float = 60.0
    """How long to wait for required Dapr components before giving up."""
