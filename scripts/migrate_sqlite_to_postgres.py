import argparse
import os
import sqlite3
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

from debt_bot.logic import (
    INTEREST_REASON,
    build_entry_code,
    get_bot_timezone,
    infer_amount_scale,
    make_name_key,
    normalize_name,
    normalize_spaces,
    parse_created_at,
    to_bot_timezone,
)
from debt_bot.store import DebtStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate the local SQLite debts.db into Postgres.")
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("SQLITE_DEBT_DB_PATH", "debts.db"),
        help="Path to the existing SQLite database.",
    )
    return parser.parse_args()


def get_database_url() -> str:
    for name in ("DATABASE_URL", "DEBT_DATABASE_URL"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip().strip('"').strip("'")
    raise RuntimeError("Missing DATABASE_URL.")


def parse_sqlite_entry_at(raw_entry_at: Any, raw_created_at: Any) -> datetime:
    if raw_entry_at:
        return datetime.strptime(str(raw_entry_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=get_bot_timezone())
    return to_bot_timezone(parse_created_at(str(raw_created_at)))


def main() -> None:
    load_dotenv()
    args = parse_args()
    database_url = get_database_url()

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(
        """
        SELECT *
        FROM debts
        ORDER BY guild_id ASC, created_at ASC, id ASC
        """
    ).fetchall()

    store = DebtStore(database_url)
    imported = 0
    next_suffix_by_group: dict[tuple[int, str, str], int] = {}

    for row in rows:
        row_dict = dict(row)
        display_name = normalize_name(str(row_dict["name"]))
        name_key = str(row_dict.get("name_key") or make_name_key(display_name))
        entry_at = parse_sqlite_entry_at(row_dict.get("entry_at"), row_dict.get("created_at"))
        prefix_key = (
            int(row_dict["guild_id"]),
            name_key,
            entry_at.strftime("%m-%d-%y-%H"),
        )
        suffix_digit = next_suffix_by_group.get(prefix_key, entry_at.minute // 10)
        next_suffix_by_group[prefix_key] = suffix_digit + 1
        entry_code = str(row_dict.get("entry_code") or build_entry_code(entry_at, suffix_digit))
        normalized_reason = normalize_spaces(str(row_dict.get("reason") or ""))
        amount = row_dict.get("amount") or 0
        amount_scale = int(row_dict.get("amount_scale") or 0)
        if amount_scale == 0:
            amount_scale = infer_amount_scale(amount)
        is_interest = bool(row_dict.get("is_interest") or normalized_reason.casefold() == INTEREST_REASON.casefold())
        interest_applied = bool(row_dict.get("interest_applied") or is_interest)
        created_at = parse_created_at(str(row_dict.get("created_at") or "2000-01-01 00:00:00"))

        store.import_debt(
            {
                "guild_id": int(row_dict["guild_id"]),
                "name": display_name,
                "name_key": name_key,
                "entry_code": entry_code,
                "entry_at": entry_at,
                "amount": amount,
                "amount_scale": amount_scale,
                "reason": normalized_reason,
                "is_interest": is_interest,
                "interest_applied": interest_applied,
                "is_paid": bool(row_dict.get("is_paid") or 0),
                "created_by": int(row_dict.get("created_by") or 0),
                "created_at": created_at,
            }
        )
        imported += 1

    sqlite_conn.close()
    print(f"Imported {imported} debt rows from {args.sqlite_path}.")


if __name__ == "__main__":
    main()
