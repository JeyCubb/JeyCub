from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from difflib import get_close_matches
from typing import Any, Optional

import discord

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


DATA_EVENT_PREFIX = "BOTDATA:"
DATA_CHANNEL_TOPIC = "Bot storage channel. Do not delete."


class GuildStoreError(RuntimeError):
    pass


@dataclass
class DebtEntry:
    name: str
    name_key: str
    entry_code: str
    entry_at: datetime
    amount: float
    amount_scale: int
    is_interest: bool
    interest_applied: bool
    reason: str
    is_paid: bool
    created_by: int
    created_at: datetime
    order_index: int = 0

    def to_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "name_key": self.name_key,
            "entry_code": self.entry_code,
            "entry_at": self.entry_at,
            "amount": self.amount,
            "amount_scale": self.amount_scale,
            "is_interest": self.is_interest,
            "interest_applied": self.interest_applied,
            "reason": self.reason,
            "is_paid": self.is_paid,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class GuildMemoryStore:
    def __init__(self) -> None:
        self.debts: dict[tuple[str, str], DebtEntry] = {}
        self.grade_subjects: dict[str, dict[str, Any]] = {}
        self._next_order_index = 1

    def clone_empty(self) -> GuildMemoryStore:
        return GuildMemoryStore()

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")

        if event_type == "debt_add":
            entry = DebtEntry(
                name=normalize_name(str(event["name"])),
                name_key=str(event["name_key"]),
                entry_code=str(event["entry_code"]),
                entry_at=datetime.fromisoformat(str(event["entry_at"])),
                amount=float(event["amount"]),
                amount_scale=int(event["amount_scale"]),
                is_interest=bool(event.get("is_interest")),
                interest_applied=bool(event.get("interest_applied")),
                reason=normalize_spaces(str(event["reason"])),
                is_paid=bool(event.get("is_paid")),
                created_by=int(event.get("created_by") or 0),
                created_at=datetime.fromisoformat(str(event["created_at"])),
                order_index=self._next_order_index,
            )
            self._next_order_index += 1
            self.debts[(entry.name_key, entry.entry_code)] = entry
            return

        if event_type == "debt_mark_paid":
            entry = self.debts.get((str(event["name_key"]), str(event["entry_code"])))
            if entry is not None:
                entry.is_paid = True
            return

        if event_type == "debt_mark_unpaid":
            entry = self.debts.get((str(event["name_key"]), str(event["entry_code"])))
            if entry is not None:
                entry.is_paid = False
            return

        if event_type == "debt_remove":
            self.debts.pop((str(event["name_key"]), str(event["entry_code"])), None)
            return

        if event_type == "debt_clear":
            name_key = str(event["name_key"])
            for debt_key in [key for key in self.debts if key[0] == name_key]:
                self.debts.pop(debt_key, None)
            return

        if event_type == "debt_mark_interest_applied":
            entry = self.debts.get((str(event["name_key"]), str(event["entry_code"])))
            if entry is not None:
                entry.interest_applied = True
            return

        if event_type == "grade_save":
            subject = normalize_spaces(str(event["subject"]))
            subject_key = make_name_key(subject)
            self.grade_subjects[subject_key] = {
                "subject": subject,
                "mode": normalize_spaces(str(event["mode"])),
                "target_grade": float(event["target_grade"]),
                "categories_text": normalize_spaces(str(event["categories_text"])),
                "exam_total": float(event["exam_total"]),
                "previous_term_average": float(event["previous_term_average"]),
                "updated_by": int(event.get("updated_by") or 0),
                "updated_at": str(event["updated_at"]),
            }

    def build_add_debt_event(
        self,
        *,
        name: str,
        amount: float,
        amount_scale: int,
        entry_dt: datetime,
        reason: str,
        created_by: int,
        is_interest: bool = False,
        interest_applied: bool = False,
    ) -> dict[str, Any]:
        display_name = normalize_name(name)
        name_key = make_name_key(display_name)
        bot_entry_dt = to_bot_timezone(entry_dt)
        created_at = datetime.now(timezone.utc)

        return {
            "type": "debt_add",
            "name": display_name,
            "name_key": name_key,
            "entry_code": self._next_entry_code(name_key, bot_entry_dt),
            "entry_at": bot_entry_dt.isoformat(),
            "amount": str(round_money(amount)),
            "amount_scale": amount_scale,
            "is_interest": is_interest,
            "interest_applied": interest_applied,
            "reason": normalize_spaces(reason),
            "is_paid": False,
            "created_by": created_by,
            "created_at": created_at.isoformat(),
        }

    def build_mark_paid_event(self, *, name: str, entry_code: str) -> Optional[dict[str, Any]]:
        entry = self.debts.get((make_name_key(name), normalize_spaces(entry_code)))
        if entry is None or entry.is_paid:
            return None
        return {
            "type": "debt_mark_paid",
            "name_key": entry.name_key,
            "entry_code": entry.entry_code,
        }

    def build_mark_unpaid_event(self, *, name: str, entry_code: str) -> Optional[dict[str, Any]]:
        entry = self.debts.get((make_name_key(name), normalize_spaces(entry_code)))
        if entry is None or not entry.is_paid:
            return None
        return {
            "type": "debt_mark_unpaid",
            "name_key": entry.name_key,
            "entry_code": entry.entry_code,
        }

    def build_remove_event(self, *, name: str, entry_code: str) -> Optional[dict[str, Any]]:
        entry = self.debts.get((make_name_key(name), normalize_spaces(entry_code)))
        if entry is None:
            return None
        return {
            "type": "debt_remove",
            "name_key": entry.name_key,
            "entry_code": entry.entry_code,
        }

    def build_clear_event(self, *, name: str) -> Optional[dict[str, Any]]:
        name_key = make_name_key(name)
        if not any(key[0] == name_key for key in self.debts):
            return None
        return {
            "type": "debt_clear",
            "name_key": name_key,
        }

    def build_grade_save_event(
        self,
        *,
        subject: str,
        mode: str,
        target_grade: float,
        categories_text: str,
        exam_total: float,
        previous_term_average: float,
        updated_by: int,
    ) -> dict[str, Any]:
        return {
            "type": "grade_save",
            "subject": normalize_spaces(subject),
            "mode": normalize_spaces(mode),
            "target_grade": target_grade,
            "categories_text": normalize_spaces(categories_text),
            "exam_total": exam_total,
            "previous_term_average": previous_term_average,
            "updated_by": updated_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_due_interest_events(
        self,
        *,
        name: str,
        as_of_dt: datetime,
        created_by: int,
    ) -> list[dict[str, Any]]:
        name_key = make_name_key(name)
        bot_as_of_dt = to_bot_timezone(as_of_dt)
        as_of_date = bot_as_of_dt.date()
        events: list[dict[str, Any]] = []

        for entry in sorted(self.debts.values(), key=lambda value: (value.entry_at, value.order_index)):
            if entry.name_key != name_key or entry.is_paid or entry.is_interest or entry.interest_applied:
                continue

            due_date = to_bot_timezone(entry.entry_at).date() + timedelta(days=INTEREST_DAYS)
            if as_of_date < due_date:
                continue

            interest_amount = round_money(Decimal(str(entry.amount)) * INTEREST_RATE)
            if interest_amount != 0:
                events.append(
                    {
                        "type": "debt_add",
                        "name": entry.name,
                        "name_key": entry.name_key,
                        "entry_code": self._next_entry_code(entry.name_key, bot_as_of_dt, events),
                        "entry_at": bot_as_of_dt.isoformat(),
                        "amount": str(interest_amount),
                        "amount_scale": infer_amount_scale(interest_amount),
                        "is_interest": True,
                        "interest_applied": True,
                        "reason": INTEREST_REASON,
                        "is_paid": False,
                        "created_by": created_by,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

            events.append(
                {
                    "type": "debt_mark_interest_applied",
                    "name_key": entry.name_key,
                    "entry_code": entry.entry_code,
                }
            )

        return events

    def list_debts(self, *, name: Optional[str] = None, include_paid: bool = False) -> list[dict[str, Any]]:
        rows = list(self.debts.values())
        if name:
            rows = [row for row in rows if row.name_key == make_name_key(name)]
        if not include_paid:
            rows = [row for row in rows if not row.is_paid]
        rows.sort(key=lambda row: (row.entry_at, row.order_index), reverse=True)
        return [row.to_row() for row in rows]

    def clear_debts(self, name: str) -> int:
        name_key = make_name_key(name)
        matching = [key for key in self.debts if key[0] == name_key]
        for key in matching:
            self.debts.pop(key, None)
        return len(matching)

    def suggest_names(self, current: str, *, include_paid: bool = True) -> list[str]:
        names_by_key: dict[str, str] = {}
        for entry in self.debts.values():
            if not include_paid and entry.is_paid:
                continue
            names_by_key.setdefault(entry.name_key, entry.name)

        names = sorted(names_by_key.values(), key=str.casefold)
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

    def suggest_dates(self, current: str, *, name: Optional[str], include_paid: bool = False) -> list[str]:
        prefix = normalize_spaces(current)
        rows = self.list_debts(name=name, include_paid=include_paid)
        codes = [str(row["entry_code"]) for row in rows]
        if not prefix:
            return codes[:MAX_AUTOCOMPLETE_CHOICES]
        return [code for code in codes if code.startswith(prefix)][:MAX_AUTOCOMPLETE_CHOICES]

    def get_grade_subject(self, subject: str) -> Optional[dict[str, Any]]:
        saved = self.grade_subjects.get(make_name_key(subject))
        if saved is None:
            return None
        return dict(saved)

    def suggest_subjects(self, current: str) -> list[str]:
        subjects = sorted(
            [str(item["subject"]) for item in self.grade_subjects.values()],
            key=str.casefold,
        )
        if not current.strip():
            return subjects[:MAX_AUTOCOMPLETE_CHOICES]

        key = make_name_key(current)
        prefix_matches = [subject for subject in subjects if make_name_key(subject).startswith(key)]
        if prefix_matches:
            return prefix_matches[:MAX_AUTOCOMPLETE_CHOICES]

        return get_close_matches(
            normalize_name(current),
            subjects,
            n=MAX_AUTOCOMPLETE_CHOICES,
            cutoff=0.75,
        )

    def _next_entry_code(
        self,
        name_key: str,
        entry_dt: datetime,
        pending_events: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        suffix_digit = entry_dt.minute // 10
        candidate = build_entry_code(entry_dt, suffix_digit)
        pending_codes = {
            str(event["entry_code"])
            for event in pending_events or []
            if event.get("type") == "debt_add" and event.get("name_key") == name_key
        }

        while (name_key, candidate) in self.debts or candidate in pending_codes:
            suffix_digit += 1
            candidate = build_entry_code(entry_dt, suffix_digit)
        return candidate


@dataclass
class GuildRuntimeState:
    channel: Optional[discord.TextChannel] = None
    loaded: bool = False
    store: GuildMemoryStore = field(default_factory=GuildMemoryStore)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DiscordChannelStoreManager:
    def __init__(self, bot: discord.Client, channel_name: str) -> None:
        self.bot = bot
        self.channel_name = normalize_spaces(channel_name).replace(" ", "-").lower() or "bot-data"
        self._states: dict[int, GuildRuntimeState] = {}

    async def ensure_guild_loaded(self, guild: discord.Guild) -> GuildRuntimeState:
        state = self._state_for(guild.id)
        async with state.lock:
            channel = await self._ensure_data_channel(guild)
            if state.loaded and state.channel and state.channel.id == channel.id:
                return state

            replay_store = GuildMemoryStore()
            async for message in channel.history(limit=None, oldest_first=True):
                event = parse_data_event(message.content)
                if event is None:
                    continue
                replay_store.apply_event(event)

            state.channel = channel
            state.store = replay_store
            state.loaded = True
            return state

    def list_debts(self, guild_id: int, *, name: Optional[str] = None, include_paid: bool = False) -> list[dict[str, Any]]:
        return self._require_loaded_state(guild_id).store.list_debts(name=name, include_paid=include_paid)

    def suggest_names(self, guild_id: int, current: str, *, include_paid: bool) -> list[str]:
        return self._require_loaded_state(guild_id).store.suggest_names(current, include_paid=include_paid)

    def suggest_dates(
        self,
        guild_id: int,
        current: str,
        *,
        name: Optional[str],
        include_paid: bool,
    ) -> list[str]:
        return self._require_loaded_state(guild_id).store.suggest_dates(
            current,
            name=name,
            include_paid=include_paid,
        )

    def get_grade_subject(self, guild_id: int, subject: str) -> Optional[dict[str, Any]]:
        return self._require_loaded_state(guild_id).store.get_grade_subject(subject)

    def suggest_subjects(self, guild_id: int, current: str) -> list[str]:
        return self._require_loaded_state(guild_id).store.suggest_subjects(current)

    async def add_debt(
        self,
        guild: discord.Guild,
        *,
        name: str,
        amount: float,
        amount_scale: int,
        entry_dt: datetime,
        reason: str,
        created_by: int,
    ) -> str:
        state = await self.ensure_guild_loaded(guild)
        async with state.lock:
            event = state.store.build_add_debt_event(
                name=name,
                amount=amount,
                amount_scale=amount_scale,
                entry_dt=entry_dt,
                reason=reason,
                created_by=created_by,
            )
            await self._append_event(guild, state, event)
            return str(event["entry_code"])

    async def apply_due_interest(
        self,
        guild_id: int,
        *,
        name: str,
        as_of_dt: datetime,
        created_by: int,
    ) -> int:
        state = self._require_loaded_state(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return 0

        async with state.lock:
            events = state.store.build_due_interest_events(
                name=name,
                as_of_dt=as_of_dt,
                created_by=created_by,
            )
            if not events:
                return 0

            added_count = 0
            for event in events:
                await self._append_event(guild, state, event)
                if event.get("type") == "debt_add":
                    added_count += 1
            return added_count

    async def mark_paid_by_code(self, guild: discord.Guild, *, name: str, entry_code: str) -> bool:
        return await self._apply_simple_event(
            guild,
            builder=lambda store: store.build_mark_paid_event(name=name, entry_code=entry_code),
        )

    async def mark_unpaid_by_code(self, guild: discord.Guild, *, name: str, entry_code: str) -> bool:
        return await self._apply_simple_event(
            guild,
            builder=lambda store: store.build_mark_unpaid_event(name=name, entry_code=entry_code),
        )

    async def remove_debt_by_code(self, guild: discord.Guild, *, name: str, entry_code: str) -> bool:
        return await self._apply_simple_event(
            guild,
            builder=lambda store: store.build_remove_event(name=name, entry_code=entry_code),
        )

    async def clear_debts(self, guild: discord.Guild, *, name: str) -> int:
        state = await self.ensure_guild_loaded(guild)
        async with state.lock:
            event = state.store.build_clear_event(name=name)
            if event is None:
                return 0
            matching_count = len([key for key in state.store.debts if key[0] == make_name_key(name)])
            await self._append_event(guild, state, event)
            return matching_count

    async def save_grade_subject(
        self,
        guild: discord.Guild,
        *,
        subject: str,
        mode: str,
        target_grade: float,
        categories_text: str,
        exam_total: float,
        previous_term_average: float,
        updated_by: int,
    ) -> None:
        state = await self.ensure_guild_loaded(guild)
        async with state.lock:
            event = state.store.build_grade_save_event(
                subject=subject,
                mode=mode,
                target_grade=target_grade,
                categories_text=categories_text,
                exam_total=exam_total,
                previous_term_average=previous_term_average,
                updated_by=updated_by,
            )
            await self._append_event(guild, state, event)

    async def _apply_simple_event(
        self,
        guild: discord.Guild,
        *,
        builder: Any,
    ) -> bool:
        state = await self.ensure_guild_loaded(guild)
        async with state.lock:
            event = builder(state.store)
            if event is None:
                return False
            await self._append_event(guild, state, event)
            return True

    async def _append_event(
        self,
        guild: discord.Guild,
        state: GuildRuntimeState,
        event: dict[str, Any],
    ) -> None:
        channel = state.channel or await self._ensure_data_channel(guild)
        payload = serialize_data_event(event)
        if len(payload) > 1900:
            raise GuildStoreError("This saved payload is too large for one Discord message.")
        try:
            await channel.send(payload)
        except discord.Forbidden as exc:
            raise GuildStoreError(
                f"I need permission to send messages in **#{channel.name}** to save data."
            ) from exc
        except discord.HTTPException as exc:
            raise GuildStoreError("Failed to write bot data to the Discord storage channel.") from exc
        state.store.apply_event(event)
        state.channel = channel

    async def _ensure_data_channel(self, guild: discord.Guild) -> discord.TextChannel:
        state = self._state_for(guild.id)
        if state.channel is not None:
            return state.channel

        existing = discord.utils.get(guild.text_channels, name=self.channel_name)
        if existing is not None:
            state.channel = existing
            return existing

        me = guild.me or guild.get_member(self.bot.user.id) if self.bot.user else None
        if me is None or not me.guild_permissions.manage_channels:
            raise GuildStoreError(
                f"Create a private text channel named **#{self.channel_name}** and give the bot "
                "View Channel, Send Messages, and Read Message History permissions."
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            ),
        }
        try:
            channel = await guild.create_text_channel(
                self.channel_name,
                topic=DATA_CHANNEL_TOPIC,
                overwrites=overwrites,
                reason="Create hidden storage channel for bot data",
            )
        except discord.HTTPException as exc:
            raise GuildStoreError("Failed to create the hidden Discord storage channel.") from exc

        state.channel = channel
        return channel

    def _require_loaded_state(self, guild_id: int) -> GuildRuntimeState:
        state = self._state_for(guild_id)
        if not state.loaded:
            raise GuildStoreError("Guild data is not loaded yet.")
        return state

    def _state_for(self, guild_id: int) -> GuildRuntimeState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildRuntimeState()
        return self._states[guild_id]


def serialize_data_event(event: dict[str, Any]) -> str:
    return DATA_EVENT_PREFIX + json.dumps(event, separators=(",", ":"), sort_keys=True)


def parse_data_event(content: str) -> Optional[dict[str, Any]]:
    if not content.startswith(DATA_EVENT_PREFIX):
        return None
    raw_payload = content[len(DATA_EVENT_PREFIX) :]
    try:
        loaded = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded
