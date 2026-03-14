import os

import httpx
from dotenv import load_dotenv

from debt_bot.commands import build_command_definitions


DISCORD_API_BASE = "https://discord.com/api/v10"


def require_env(name: str, *aliases: str) -> str:
    for candidate in (name, *aliases):
        value = os.getenv(candidate)
        if value and value.strip():
            return value.strip().strip('"').strip("'")
    raise RuntimeError(f"Missing required environment variable: {name}")


def main() -> None:
    load_dotenv()

    application_id = require_env("DISCORD_APPLICATION_ID")
    bot_token = require_env("DISCORD_TOKEN", "DISCORD_BOT_TOKEN")
    guild_id = (os.getenv("DISCORD_GUILD_ID") or "").strip()
    commands = build_command_definitions()

    if guild_id:
        url = f"{DISCORD_API_BASE}/applications/{application_id}/guilds/{guild_id}/commands"
        scope = f"guild {guild_id}"
    else:
        url = f"{DISCORD_API_BASE}/applications/{application_id}/commands"
        scope = "global"

    response = httpx.put(
        url,
        headers={"Authorization": f"Bot {bot_token}"},
        json=commands,
        timeout=30.0,
    )
    response.raise_for_status()

    print(f"Registered {len(response.json())} slash commands to {scope}.")


if __name__ == "__main__":
    main()
