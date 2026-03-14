import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class Settings:
    application_id: str
    public_key: str
    bot_token: str
    database_url: str
    guild_id: Optional[str]
    host: str
    port: int


def _get_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        cleaned = value.strip().strip('"').strip("'")
        if cleaned:
            return cleaned
    return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    application_id = _get_env("DISCORD_APPLICATION_ID")
    public_key = _get_env("DISCORD_PUBLIC_KEY")
    bot_token = _get_env("DISCORD_TOKEN", "DISCORD_BOT_TOKEN")
    database_url = _get_env("DATABASE_URL", "DEBT_DATABASE_URL")

    missing = [
        name
        for name, value in (
            ("DISCORD_APPLICATION_ID", application_id),
            ("DISCORD_PUBLIC_KEY", public_key),
            ("DISCORD_TOKEN", bot_token),
            ("DATABASE_URL", database_url),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    port_text = _get_env("PORT", default="8000") or "8000"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer.") from exc

    return Settings(
        application_id=application_id or "",
        public_key=public_key or "",
        bot_token=bot_token or "",
        database_url=database_url or "",
        guild_id=_get_env("DISCORD_GUILD_ID"),
        host=_get_env("HOST", default="0.0.0.0") or "0.0.0.0",
        port=port,
    )
