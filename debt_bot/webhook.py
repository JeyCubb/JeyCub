from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from debt_bot import BOT_SCHEMA_VERSION
from debt_bot.config import get_settings
from debt_bot.interactions import InteractionService
from debt_bot.security import verify_discord_request
from debt_bot.store import DebtStore


load_dotenv()

settings = get_settings()
store = DebtStore(settings.database_url)
store.ensure_schema()
service = InteractionService(settings, store)
app = FastAPI(title="Discord Debt Bot", version=BOT_SCHEMA_VERSION)


@app.get("/")
async def healthcheck() -> dict[str, object]:
    return {
        "ok": True,
        "service": "discord-debt-bot",
        "schema_version": BOT_SCHEMA_VERSION,
    }


@app.post("/")
async def discord_interactions(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    if not verify_discord_request(settings.public_key, signature, timestamp, body):
        raise HTTPException(status_code=401, detail="Invalid request signature.")

    payload = await request.json()
    return await service.handle(payload)
