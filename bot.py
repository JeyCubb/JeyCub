import os
from pathlib import Path

from debt_bot.config import get_gateway_settings
from debt_bot.gateway_bot import run_bot


def load_local_env() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    load_local_env()
    settings = get_gateway_settings()
    run_bot(settings)


if __name__ == "__main__":
    main()
