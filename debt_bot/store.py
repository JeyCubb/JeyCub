from datetime import datetime, timedelta, timezone
from decimal import Decimal
from difflib import get_close_matches
from typing import Mapping, Optional

import psycopg
from psycopg.rows import dict_row

from debt_bot.logic import (
    INTEREST_DAYS,
    INTEREST_RATE,
    INTEREST_REASON,
    MAX_AUTOCOMPLETE_CHOICES,
    build_entry_code,
    infer_amount_scale,
    make_name_key,
    normalize_name,
    normalize_spaces,
    parse_iso_date,
    round_money,
    to_bot_timezone,
)


class DebtStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._schema_ready = False

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        if hasattr(conn, "prepare_threshold"):
            conn.prepare_threshold = None
        return conn

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return

        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS debts (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    entry_code TEXT NOT NULL,
                    entry_at TIMESTAMPTZ NOT NULL,
                    amount NUMERIC(18, 2) NOT NULL,
                    amount_scale SMALLINT NOT NULL DEFAULT 0,
                    is_interest BOOLEAN NOT NULL DEFAULT FALSE,
                    interest_applied BOOLEAN NOT NULL DEFAULT FALSE,
                    currency TEXT NOT NULL DEFAULT 'peso',
                    entry_date DATE NOT NULL,
                    reason TEXT NOT NULL,
                    is_paid BOOLEAN NOT NULL DEFAULT FALSE,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
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

    def _next_entry_code(
        self,
        conn: psycopg.Connection,
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
            WHERE guild_id = %s AND name_key = %s AND entry_code = %s
            LIMIT 1
            """,
            (guild_id, name_key, candidate),
        ).fetchone():
            suffix_digit += 1
            candidate = build_entry_code(entry_dt, suffix_digit)
        return candidate

    def _insert_debt(
        self,
        conn: psycopg.Connection,
        guild_id: int,
        display_name: str,
        name_key: str,
        entry_code: str,
        amount: float | Decimal,
        amount_scale: int,
        entry_at: datetime,
        reason: str,
        created_by: int,
        is_interest: bool,
        interest_applied: bool,
        is_paid: bool = False,
        created_at: Optional[datetime] = None,
    ) -> None:
        bot_entry_at = to_bot_timezone(entry_at)
        stored_entry_at = bot_entry_at.astimezone(timezone.utc)
        stored_created_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, name_key, entry_code) DO NOTHING
            """,
            (
                guild_id,
                display_name,
                name_key,
                entry_code,
                stored_entry_at,
                round_money(amount),
                amount_scale,
                is_interest,
                interest_applied,
                "peso",
                bot_entry_at.date(),
                normalize_spaces(reason),
                is_paid,
                created_by,
                stored_created_at,
            ),
        )

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

        with self._connect() as conn:
            entry_code = self._next_entry_code(conn, guild_id, name_key, bot_entry_dt)
            self._insert_debt(
                conn=conn,
                guild_id=guild_id,
                display_name=display_name,
                name_key=name_key,
                entry_code=entry_code,
                amount=amount,
                amount_scale=amount_scale,
                entry_at=bot_entry_dt,
                reason=reason,
                created_by=created_by,
                is_interest=False,
                interest_applied=False,
            )
            conn.commit()
            return entry_code

    def list_debts(self, guild_id: int, name: Optional[str] = None, include_paid: bool = False) -> list[dict]:
        self.ensure_schema()
        query = """
            SELECT entry_code, name, amount, amount_scale, entry_date, reason, is_paid, entry_at
            FROM debts
            WHERE guild_id = %s
        """
        params: list[object] = [guild_id]

        if name:
            query += " AND name_key = %s"
            params.append(make_name_key(name))

        if not include_paid:
            query += " AND is_paid = FALSE"

        query += " ORDER BY entry_at DESC, id DESC"

        with self._connect() as conn:
            rows = list(conn.execute(query, params))

        for row in rows:
            row["entry_at"] = to_bot_timezone(row["entry_at"])
        return rows

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
                WHERE guild_id = %s AND name_key = %s AND is_paid = FALSE
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

                interest_amount = round_money(round_money(row["amount"]) * INTEREST_RATE)
                if interest_amount != 0:
                    entry_code = self._next_entry_code(conn, guild_id, name_key, bot_as_of_dt)
                    self._insert_debt(
                        conn=conn,
                        guild_id=guild_id,
                        display_name=display_name,
                        name_key=name_key,
                        entry_code=entry_code,
                        amount=interest_amount,
                        amount_scale=infer_amount_scale(interest_amount),
                        entry_at=bot_as_of_dt,
                        reason=INTEREST_REASON,
                        created_by=created_by,
                        is_interest=True,
                        interest_applied=True,
                    )
                    added_count += 1

                conn.execute(
                    "UPDATE debts SET interest_applied = TRUE WHERE id = %s",
                    (int(row["id"]),),
                )

            conn.commit()
            return added_count

    def remove_debt_by_code(self, guild_id: int, name: str, entry_code: str) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM debts
                WHERE guild_id = %s AND name_key = %s AND entry_code = %s AND is_paid = FALSE
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
                SET is_paid = TRUE
                WHERE guild_id = %s AND name_key = %s AND entry_code = %s AND is_paid = FALSE
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
                SET is_paid = FALSE
                WHERE guild_id = %s AND name_key = %s AND entry_code = %s AND is_paid = TRUE
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
                WHERE guild_id = %s AND name_key = %s AND is_paid = FALSE
                """,
                (guild_id, make_name_key(name)),
            )
            conn.commit()
            return cursor.rowcount

    def suggest_names(self, guild_id: int, current: str) -> list[str]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM (
                    SELECT MIN(name) AS name
                    FROM debts
                    WHERE guild_id = %s AND is_paid = FALSE
                    GROUP BY name_key
                ) names
                ORDER BY lower(name)
                """,
                (guild_id,),
            ).fetchall()

        names = [row["name"] for row in rows]
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
            WHERE guild_id = %s
        """
        params: list[object] = [guild_id]
        if name:
            query += " AND name_key = %s"
            params.append(make_name_key(name))
        if not include_paid:
            query += " AND is_paid = FALSE"
        query += " ORDER BY entry_at DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        codes = [row["entry_code"] for row in rows]
        if not prefix:
            return codes[:MAX_AUTOCOMPLETE_CHOICES]
        return [code for code in codes if code.startswith(prefix)][:MAX_AUTOCOMPLETE_CHOICES]

    def import_debt(self, record: Mapping[str, object]) -> None:
        self.ensure_schema()
        display_name = normalize_name(str(record["name"]))
        name_key = str(record["name_key"]) if record.get("name_key") else make_name_key(display_name)
        with self._connect() as conn:
            self._insert_debt(
                conn=conn,
                guild_id=int(record["guild_id"]),
                display_name=display_name,
                name_key=name_key,
                entry_code=str(record["entry_code"]),
                amount=Decimal(str(record["amount"])),
                amount_scale=int(record["amount_scale"] or 0),
                entry_at=record["entry_at"],  # type: ignore[arg-type]
                reason=str(record["reason"]),
                created_by=int(record["created_by"]),
                is_interest=bool(record["is_interest"]),
                interest_applied=bool(record["interest_applied"]),
                is_paid=bool(record["is_paid"]),
                created_at=record["created_at"],  # type: ignore[arg-type]
            )
            conn.commit()
