from dotenv import load_dotenv
import uvicorn

from debt_bot.config import get_settings


def main() -> None:
    load_dotenv()
    settings = get_settings()
    uvicorn.run("debt_bot.webhook:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
