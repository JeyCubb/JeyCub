from dotenv import load_dotenv

from debt_bot.config import get_gateway_settings
from debt_bot.gateway_bot import run_bot


def main() -> None:
    load_dotenv()
    settings = get_gateway_settings()
    run_bot(settings)


if __name__ == "__main__":
    main()
