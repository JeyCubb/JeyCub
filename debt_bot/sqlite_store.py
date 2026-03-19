import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from difflib import get_close_matches
from typing import Any, Mapping, Optional

from debt_bot.logic import (
    INTEREST_DAYS,
    INTEREST_RATE,
    INTEREST_REASON,
    MAX_AUTOCOMPLETE_CHOICES,
    build_entry_code,
    get_bot_timezone,
    infer_amount_scale,
    make_name_key,
    normalize_name,
    normalize_spaces,
    parse_created_at,
    parse_iso_date,
    round_money,
    to_bot_timezone,
)


SQLITE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class SQLiteDebtStore:
    def __init__(self, database_path: str, seed_path: Optional[str] = None) -> None:
        self.database_path = database_path
        self.seed_path = seed_path
        self._schema_ready = False

    def _ensure_database_file(self) -> None:
        db_path = os.path.abspath(self.database_path)
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(db_path):
            return
        if self.seed_path:
            seed_path = os.path.abspath(self.seed_path)
            if os.path.exists(seed_path) and seed_path != db_path:
                shutil.copyfile(seed_path, db_path)
                return
        open(db_path, "a", encoding="utf-8").close()

    def _connect(self) -> sqlite3.Connection:
        self._ensure_database_file()
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return

        with self._connect() as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'debts' LIMIT 1"
            ).fetchone()
            if not table_exists:
                self._create_schema(conn)
            else:
                self._migrate_schema(conn)

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_debts_guild_name_key_unpaid
                ON debts(guild_id, name_key, is_paid)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_debts_guild_name_key_entry_code
                ON debts(guild_id, name_key, entry_code)
                """
            )
            conn.commit()

        self._schema_ready = True

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                entry_code TEXT NOT NULL,
                entry_at TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                amount_scale INTEGER NOT NULL DEFAULT 0,
                is_interest INTEGER NOT NULL DEFAULT 0,
                interest_applied INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'peso',
                entry_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                is_paid INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '2000-01-01 00:00:00'
            )
            """
        )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"]): row
            for row in conn.execute("PRAGMA table_info(debts)").fetchall()
        }

        additions = (
            ("name_key", "TEXT"),
            ("entry_code", "TEXT"),
            ("entry_at", "TEXT"),
            ("amount_scale", "INTEGER NOT NULL DEFAULT 0"),
            ("is_interest", "INTEGER NOT NULL DEFAULT 0"),
            ("interest_applied", "INTEGER NOT NULL DEFAULT 0"),
            ("currency", "TEXT NOT NULL DEFAULT 'peso'"),
            ("entry_date", "TEXT"),
            ("is_paid", "INTEGER NOT NULL DEFAULT 0"),
            ("created_by", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT"),
        )
        for name, definition in additions:
            if name not in columns:
                conn.execute(f"ALTER TABLE debts ADD COLUMN {name} {definition}")

        rows = conn.execute(
            """
            SELECT id, guild_id, name, name_key, entry_code, entry_at, amount, amount_scale,
                   is_interest, interest_applied, reason, is_paid, created_by, created_at
            FROM debts
            ORDER BY guild_id ASC, created_at ASC, id ASC
            """
        ).fetchall()

        next_suffix_by_group: dict[tuple[int, str, str], int] = {}
        for row in rows:
            row_id = int(row["id"])
            display_name = normalize_name(str(row["name"] or ""))
            name_key = str(row["name_key"] or "").strip() or make_name_key(display_name)
            created_at = parse_created_at(str(row["created_at"] or "2000-01-01 00:00:00"))
            entry_at = self._parse_entry_at(row["entry_at"], row["created_at"])
            entry_code = normalize_spaces(str(row["entry_code"] or ""))
            if not entry_code:
                prefix_key = (
                    int(row["guild_id"] or 0),
                    name_key,
                    entry_at.strftime("%m-%d-%y-%H"),
                )
                suffix_digit = next_suffix_by_group.get(prefix_key, entry_at.minute // 10)
                entry_code = build_entry_code(entry_at, suffix_digit)
                while conn.execute(
                    """
                    SELECT 1
                    FROM debts
                    WHERE guild_id = ? AND name_key = ? AND entry_code = ? AND id != ?
                    LIMIT 1
                    """,
                    (int(row["guild_id"] or 0), name_key, entry_code, row_id),
                ).fetchone():
                    suffix_digit += 1
                    entry_code = build_entry_code(entry_at, suffix_digit)
                next_suffix_by_group[prefix_key] = suffix_digit + 1

            normalized_reason = normalize_spaces(str(row["reason"] or ""))
            is_interest = int(bool(row["is_interest"]) or normalized_reason.casefold() == INTEREST_REASON.casefold())
            amount_scale = int(row["amount_scale"] or 0)
            if amount_scale == 0:
                amount_scale = infer_amount_scale(row["amount"] or 0)
            interest_applied = int(bool(row["interest_applied"]) or bool(is_interest))
            entry_at_text = self._format_entry_at(entry_at)
            entry_date_text = to_bot_timezone(entry_at).strftime("%Y-%m-%d")
            created_at_text = self._format_created_at(created_at)

            conn.execute(
                """
                UPDATE debts
                SET name = ?,
                    name_key = ?,
                    entry_code = ?,
                    entry_at = ?,
                    amount_scale = ?,
                    is_interest = ?,
                    interest_applied = ?,
                    currency = 'peso',
                    entry_date = ?,
                    reason = ?,
                    is_paid = ?,
                    created_by = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    display_name,
                    name_key,
                    entry_code,
                    entry_at_text,
                    amount_scale,
                    is_interest,
                    interest_applied,
                    entry_date_text,
                    normalized_reason,
                    int(bool(row["is_paid"])),
                    int(row["created_by"] or 0),
                    created_at_text,
                    row_id,
                ),
            )

    def _parse_entry_at(self, raw_entry_at: Any, raw_created_at: Any) -> datetime:
        entry_at_text = normalize_spaces(str(raw_entry_at or ""))
        if entry_at_text:
            for fmt in (
                SQLITE_DATETIME_FORMAT,
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z",
            ):
                try:
                    parsed = datetime.strptime(entry_at_text, fmt)
                    if parsed.tzinfo is None:
                        return parsed.replace(tzinfo=get_bot_timezone())
                    return to_bot_timezone(parsed)
                except ValueError:
                    continue
        return to_bot_timezone(parse_created_at(str(raw_created_at or "2000-01-01 00:00:00")))

    def _format_entry_at(self, entry_at: datetime) -> str:
        return to_bot_timezone(entry_at).strftime(SQLITE_DATETIME_FORMAT)

    def _format_created_at(self, created_at: datetime) -> str:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc).strftime(SQLITE_DATETIME_FORMAT)

    def _next_entry_code(
        self,
        conn: sqlite3.Connection,
        guild_id: int,
        name_key: str,
        entry_dt: datetime,
    ) -> str:
        suffix_digit = entry_dt.minute // 10
        candidate = build_entry_code(entry_dt, suffix_digit)
        while conn.execute(
            """
            SELECT 1
            FROM debts
            WHERE guild_id = ? AND name_key = ? AND entry_code = ?
            LIMIT 1
            """,
            (guild_id, name_key, candidate),
        ).fetchone():
            suffix_digit += 1
            candidate = build_entry_code(entry_dt, suffix_digit)
        return candidate

    def add_debt(
        self,
        guild_id: int,
        name: str,
        amount: float,
        amount_scale: int,
        entry_dt: datetime,
        reason: str,
        created_by: int,
    ) -> str:
        self.ensure_schema()
        display_name = normalize_name(name)
        name_key = make_name_key(display_name)
        bot_entry_dt = to_bot_timezone(entry_dt)
        created_at = datetime.now(timezone.utc)

        with self._connect() as conn:
            entry_code = self._next_entry_code(conn, guild_id, name_key, bot_entry_dt)
            conn.execute(
                """
                INSERT INTO debts (
                    guild_id,
                    name,
                    name_key,
                    entry_code,
                    entry_at,
                    amount,
                    amount_scale,
                    is_interest,
                    interest_applied,
                    currency,
                    entry_date,
                    reason,
                    is_paid,
                    created_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'peso', ?, ?, 0, ?, ?)
                """,
                (
                    guild_id,
                    display_name,
                    name_key,
                    entry_code,
                    self._format_entry_at(bot_entry_dt),
                    float(round_money(amount)),
                    amount_scale,
                    bot_entry_dt.strftime("%Y-%m-%d"),
                    normalize_spaces(reason),
                    created_by,
                    self._format_created_at(created_at),
                ),
            )
            conn.commit()
            return entry_code

    def list_debts(self, guild_id: int, name: Optional[str] = None, include_paid: bool = False) -> list[dict[str, Any]]:
        self.ensure_schema()
        query = """
            SELECT id, guild_id, name, name_key, entry_code, entry_at, amount, amount_scale,
                   is_interest, interest_applied, currency, entry_date, reason, is_paid,
                   created_by, created_at
            FROM debts
            WHERE guild_id = ?
        """
        params: list[object] = [guild_id]

        if name:
            query += " AND name_key = ?"
            params.append(make_name_key(name))

        if not include_paid:
            query += " AND is_paid = 0"

        query += " ORDER BY entry_at DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["entry_at"] = self._parse_entry_at(item.get("entry_at"), item.get("created_at"))
            item["is_paid"] = bool(item.get("is_paid"))
            item["is_interest"] = bool(item.get("is_interest"))
            item["interest_applied"] = bool(item.get("interest_applied"))
            result.append(item)
        return result

    def apply_due_interest(
        self,
        guild_id: int,
        name: str,
        as_of_dt: datetime,
        created_by: int,
    ) -> int:
        self.ensure_schema()
        display_name = normalize_name(name)
        name_key = make_name_key(display_name)
        bot_as_of_dt = to_bot_timezone(as_of_dt)
        as_of_date = bot_as_of_dt.date()

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, entry_code, amount, amount_scale, entry_date, reason, is_interest, interest_applied
                FROM debts
                WHERE guild_id = ? AND name_key = ? AND is_paid = 0
                ORDER BY entry_date ASC, id ASC
                """,
                (guild_id, name_key),
            ).fetchall()

            if not rows:
                return 0

            added_count = 0
            for row in rows:
                if bool(row["is_interest"]) or bool(row["interest_applied"]):
                    continue

                due_date = parse_iso_date(str(row["entry_date"])) + timedelta(days=INTEREST_DAYS)
                if as_of_date < due_date:
                    continue

                interest_amount = round_money(Decimal(str(row["amount"])) * INTEREST_RATE)
                if interest_amount != 0:
                    entry_code = self._next_entry_code(conn, guild_id, name_key, bot_as_of_dt)
                    conn.execute(
                        """
                        INSERT INTO debts (
                            guild_id,
                            name,
                            name_key,
                            entry_code,
                            entry_at,
                            amount,
                            amount_scale,
                            is_interest,
                            interest_applied,
                            currency,
                            entry_date,
                            reason,
                            is_paid,
                            created_by,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 'peso', ?, ?, 0, ?, ?)
                        """,
                        (
                            guild_id,
                            display_name,
                            name_key,
                            entry_code,
                            self._format_entry_at(bot_as_of_dt),
                            float(interest_amount),
                            infer_amount_scale(interest_amount),
                            bot_as_of_dt.strftime("%Y-%m-%d"),
                            INTEREST_REASON,
                            created_by,
                            self._format_created_at(datetime.now(timezone.utc)),
                        ),
                    )
                    added_count += 1

                conn.execute("UPDATE debts SET interest_applied = 1 WHERE id = ?", (int(row["id"]),))

            conn.commit()
            return added_count

    def remove_debt_by_code(self, guild_id: int, name: str, entry_code: str) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM debts
                WHERE guild_id = ? AND name_key = ? AND entry_code = ?
                """,
                (guild_id, make_name_key(name), normalize_spaces(entry_code)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_paid_by_code(self, guild_id: int, name: str, entry_code: str) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE debts
                SET is_paid = 1
                WHERE guild_id = ? AND name_key = ? AND entry_code = ? AND is_paid = 0
                """,
                (guild_id, make_name_key(name), normalize_spaces(entry_code)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_unpaid_by_code(self, guild_id: int, name: str, entry_code: str) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE debts
                SET is_paid = 0
                WHERE guild_id = ? AND name_key = ? AND entry_code = ? AND is_paid = 1
                """,
                (guild_id, make_name_key(name), normalize_spaces(entry_code)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def clear_debts(self, guild_id: int, name: str) -> int:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM debts
                WHERE guild_id = ? AND name_key = ?
                """,
                (guild_id, make_name_key(name)),
            )
            conn.commit()
            return cursor.rowcount

    def suggest_names(self, guild_id: int, current: str, include_paid: bool = True) -> list[str]:
        self.ensure_schema()
        where_clause = "WHERE guild_id = ?"
        if not include_paid:
            where_clause += " AND is_paid = 0"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM (
                    SELECT MIN(name) AS name
                    FROM debts
                    """ + where_clause + """
                    GROUP BY name_key
                ) names
                ORDER BY lower(name)
                """,
                (guild_id,),
            ).fetchall()

        names = [str(row["name"]) for row in rows]
        if not current.strip():
            return names[:MAX_AUTOCOMPLETE_CHOICES]

        key = make_name_key(current)
        prefix_matches = [name for name in names if make_name_key(name).startswith(key)]
        if prefix_matches:
            return prefix_matches[:MAX_AUTOCOMPLETE_CHOICES]

        return get_close_matches(
            normalize_name(current),
            names,
            n=MAX_AUTOCOMPLETE_CHOICES,
            cutoff=0.75,
        )

    def suggest_dates(self, guild_id: int, name: Optional[str], current: str, include_paid: bool = False) -> list[str]:
        self.ensure_schema()
        prefix = normalize_spaces(current)
        query = """
            SELECT entry_code
            FROM debts
            WHERE guild_id = ?
        """
        params: list[object] = [guild_id]
        if name:
            query += " AND name_key = ?"
            params.append(make_name_key(name))
        if not include_paid:
            query += " AND is_paid = 0"
        query += " ORDER BY entry_at DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        codes = [str(row["entry_code"]) for row in rows]
        if not prefix:
            return codes[:MAX_AUTOCOMPLETE_CHOICES]
        return [code for code in codes if code.startswith(prefix)][:MAX_AUTOCOMPLETE_CHOICES]

    def import_debt(self, record: Mapping[str, object]) -> None:
        self.ensure_schema()
        display_name = normalize_name(str(record["name"]))
        name_key = str(record.get("name_key") or make_name_key(display_name))
        entry_at = self._parse_entry_at(record.get("entry_at"), record.get("created_at"))
        created_at = parse_created_at(str(record.get("created_at") or "2000-01-01 00:00:00"))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO debts (
                    guild_id,
                    name,
                    name_key,
                    entry_code,
                    entry_at,
                    amount,
                    amount_scale,
                    is_interest,
                    interest_applied,
                    currency,
                    entry_date,
                    reason,
                    is_paid,
                    created_by,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'peso', ?, ?, ?, ?, ?)
                """,
                (
                    int(record["guild_id"]),
                    display_name,
                    name_key,
                    str(record["entry_code"]),
                    self._format_entry_at(entry_at),
                    float(record["amount"]),
                    int(record.get("amount_scale") or 0),
                    int(bool(record.get("is_interest"))),
                    int(bool(record.get("interest_applied"))),
                    to_bot_timezone(entry_at).strftime("%Y-%m-%d"),
                    normalize_spaces(str(record["reason"])),
                    int(bool(record.get("is_paid"))),
                    int(record.get("created_by") or 0),
                    self._format_created_at(created_at),
                ),
            )
            conn.commit()
