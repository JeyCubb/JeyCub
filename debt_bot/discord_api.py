from typing import Any

import httpx


DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordApi:
    def __init__(self, application_id: str, bot_token: str) -> None:
        self.application_id = application_id
        self.bot_token = bot_token
        self.client = httpx.AsyncClient(timeout=10.0)

    async def __aenter__(self) -> "DiscordApi":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    async def create_interaction_response(
        self,
        interaction_id: str,
        interaction_token: str,
        payload: dict[str, Any],
    ) -> None:
        response = await self.client.post(
            f"{DISCORD_API_BASE}/interactions/{interaction_id}/{interaction_token}/callback",
            json=payload,
        )
        response.raise_for_status()

    async def edit_original_response(self, interaction_token: str, payload: dict[str, Any]) -> None:
        params = {"with_components": "true"} if "components" in payload else None
        response = await self.client.patch(
            f"{DISCORD_API_BASE}/webhooks/{self.application_id}/{interaction_token}/messages/@original",
            params=params,
            json=payload,
        )
        response.raise_for_status()

    async def get_channel_messages(self, channel_id: str, limit: int = 50) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            params={"limit": limit},
            headers={"Authorization": f"Bot {self.bot_token}"},
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel_message(self, channel_id: str, message_id: str) -> None:
        response = await self.client.delete(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}",
            headers={"Authorization": f"Bot {self.bot_token}"},
        )
        response.raise_for_status()
