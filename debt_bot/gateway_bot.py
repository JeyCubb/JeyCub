from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from debt_bot.config import GatewaySettings
from debt_bot.logic import (
    BUTTON_TIMEOUT_SECONDS,
    build_all_list_content,
    build_all_list_snapshot_contents,
    build_person_list_content,
    is_all_list_message_content,
    is_list_message_content,
    is_person_list_message_content,
    normalize_entry_code,
    normalize_name,
    normalize_spaces,
    page_count_for,
    parse_amount_input,
    parse_user_entry_datetime,
    to_bot_timezone,
)
from debt_bot.sqlite_store import SQLiteDebtStore


@dataclass
class RenderedList:
    content: str
    view: Optional["ListPagerView"]


class PagerButton(discord.ui.Button["ListPagerView"]):
    def __init__(self, label: str, delta: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is None:
            return

        interaction_dt = to_bot_timezone(interaction.created_at)
        if interaction_dt > view.issued_at + timedelta(seconds=BUTTON_TIMEOUT_SECONDS):
            view.disable_all_items()
            await interaction.response.edit_message(content=view.last_content, view=view)
            view.stop()
            return

        view.page_index += self.delta
        rendered = view.render(interaction_dt)
        await interaction.response.edit_message(content=rendered.content, view=rendered.view)
        if interaction.message:
            view.message = interaction.message


class ListPagerView(discord.ui.View):
    def __init__(
        self,
        *,
        store: SQLiteDebtStore,
        actor_id_getter: Callable[[], int],
        guild_id: int,
        mode: str,
        issued_at: datetime,
        person_name: Optional[str] = None,
        page_index: int = 0,
    ) -> None:
        super().__init__(timeout=BUTTON_TIMEOUT_SECONDS)
        self.store = store
        self.actor_id_getter = actor_id_getter
        self.guild_id = guild_id
        self.mode = mode
        self.issued_at = issued_at
        self.person_name = person_name
        self.page_index = page_index
        self.last_content = ""
        self.message: Optional[discord.Message] = None

    def render(self, interaction_dt: datetime) -> RenderedList:
        if self.mode == "person":
            if not self.person_name:
                self.clear_items()
                self.last_content = "No utang."
                return RenderedList(self.last_content, None)

            self.store.apply_due_interest(
                guild_id=self.guild_id,
                name=self.person_name,
                as_of_dt=interaction_dt,
                created_by=self.actor_id_getter(),
            )
            rows = self.store.list_debts(
                guild_id=self.guild_id,
                name=self.person_name,
                include_paid=True,
            )
            if not rows:
                self.clear_items()
                self.last_content = f"No utang for **{normalize_name(self.person_name)}**."
                return RenderedList(self.last_content, None)

            self.person_name = str(rows[0]["name"])
            self.page_index = min(self.page_index, page_count_for(len(rows)) - 1)
            self._sync_buttons(len(rows))
            self.last_content = build_person_list_content(self.person_name, rows, self.page_index)
            return RenderedList(self.last_content, self if self.children else None)

        rows = self.store.list_debts(guild_id=self.guild_id, include_paid=True)
        if not rows:
            self.clear_items()
            self.last_content = "No unpaid utang."
            return RenderedList(self.last_content, None)

        self.page_index = min(self.page_index, page_count_for(len(rows)) - 1)
        self._sync_buttons(len(rows))
        self.last_content = build_all_list_content(rows, self.page_index)
        return RenderedList(self.last_content, self if self.children else None)

    def _sync_buttons(self, total_rows: int) -> None:
        self.clear_items()
        last_page = page_count_for(total_rows) - 1
        if self.page_index > 0:
            self.add_item(PagerButton("Prev", -1))
        if self.page_index < last_page:
            self.add_item(PagerButton("Next", 1))

    async def on_timeout(self) -> None:
        if not self.message or not self.children:
            return
        self.disable_all_items()
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            return


def run_bot(settings: GatewaySettings) -> None:
    store = SQLiteDebtStore(settings.database_path, settings.seed_database_path)
    store.ensure_schema()

    intents = discord.Intents.default()
    intents.messages = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    commands_synced = False

    def actor_id() -> int:
        user = bot.user
        return int(user.id) if user else 0

    async def delete_matching_bot_messages(
        channel: Optional[discord.abc.Messageable],
        predicate: Callable[[str], bool],
        *,
        keep_ids: Optional[set[int]] = None,
    ) -> None:
        if channel is None or bot.user is None or not hasattr(channel, "history"):
            return

        keep_ids = keep_ids or set()
        try:
            async for message in channel.history(limit=50):
                if message.author.id != bot.user.id:
                    continue
                if message.id in keep_ids:
                    continue
                if not predicate(message.content):
                    continue
                try:
                    await message.delete()
                except discord.HTTPException:
                    continue
        except discord.HTTPException:
            return

    async def delete_previous_person_lists(channel: Optional[discord.abc.Messageable]) -> None:
        await delete_matching_bot_messages(channel, is_person_list_message_content)

    async def delete_stale_all_lists(
        channel: Optional[discord.abc.Messageable],
        keep_ids: set[int],
    ) -> None:
        await delete_matching_bot_messages(
            channel,
            is_all_list_message_content,
            keep_ids=keep_ids,
        )

    async def publish_all_list_snapshot(
        channel: Optional[discord.abc.Messageable],
        guild_id: int,
    ) -> list[discord.Message]:
        if channel is None or not hasattr(channel, "send"):
            return []

        rows = store.list_debts(guild_id=guild_id, include_paid=True)
        messages: list[discord.Message] = []
        for content in build_all_list_snapshot_contents(rows):
            try:
                message = await channel.send(content)
            except discord.HTTPException:
                break
            messages.append(message)
        return messages

    async def refresh_all_list_snapshot(
        channel: Optional[discord.abc.Messageable],
        guild_id: int,
    ) -> None:
        sent_messages = await publish_all_list_snapshot(channel, guild_id)
        if not sent_messages:
            return
        await delete_stale_all_lists(channel, {message.id for message in sent_messages})

    async def refresh_visible_lists(
        channel: Optional[discord.abc.Messageable],
        guild_id: int,
        *,
        person_name: Optional[str] = None,
        interaction_dt: Optional[datetime] = None,
    ) -> None:
        rendered: Optional[RenderedList] = None
        if person_name and interaction_dt is not None:
            rendered = await render_person_list(guild_id, person_name, interaction_dt)

        await delete_previous_person_lists(channel)
        await refresh_all_list_snapshot(channel, guild_id)
        if rendered is not None:
            await send_channel_rendered(channel, rendered)

    async def send_channel_rendered(
        channel: Optional[discord.abc.Messageable],
        rendered: RenderedList,
    ) -> Optional[discord.Message]:
        if channel is None or not hasattr(channel, "send"):
            return None

        message = await channel.send(
            content=rendered.content,
            view=rendered.view,
        )
        if rendered.view is not None:
            rendered.view.message = message
        return message

    async def clear_deferred_response(interaction: discord.Interaction) -> None:
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            try:
                await interaction.edit_original_response(content="Updated.", view=None)
            except discord.HTTPException:
                return

    async def render_person_list(guild_id: int, person_name: str, interaction_dt: datetime) -> RenderedList:
        view = ListPagerView(
            store=store,
            actor_id_getter=actor_id,
            guild_id=guild_id,
            mode="person",
            issued_at=interaction_dt,
            person_name=normalize_name(person_name),
        )
        return view.render(interaction_dt)

    def autocomplete_names(
        *,
        include_paid: bool,
    ) -> Callable[[discord.Interaction, str], list[app_commands.Choice[str]]]:
        async def inner(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            if interaction.guild_id is None:
                return []
            return [
                app_commands.Choice(name=choice, value=choice)
                for choice in store.suggest_names(
                    interaction.guild_id,
                    current,
                    include_paid=include_paid,
                )
            ]

        return inner

    def autocomplete_dates(
        *,
        include_paid: bool,
    ) -> Callable[[discord.Interaction, str], list[app_commands.Choice[str]]]:
        async def inner(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            if interaction.guild_id is None:
                return []

            person_name = ""
            namespace = interaction.namespace
            if hasattr(namespace, "name") and namespace.name:
                person_name = str(namespace.name)

            return [
                app_commands.Choice(name=choice, value=choice)
                for choice in store.suggest_dates(
                    interaction.guild_id,
                    person_name,
                    current,
                    include_paid=include_paid,
                )
            ]

        return inner

    @bot.event
    async def on_ready() -> None:
        nonlocal commands_synced
        if commands_synced:
            return

        if settings.guild_id is not None:
            guild = discord.Object(id=settings.guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {settings.guild_id}.")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global command(s).")

        commands_synced = True
        if bot.user:
            print(f"Logged in as {bot.user} ({bot.user.id})")

    @bot.tree.command(name="utang", description="Add an utang entry")
    @app_commands.describe(
        name="Name of the person",
        amount="Amount in peso, like 11, -1, 11.5, or 11.50",
        reason="Reason for this debt",
        date_time="Optional past date/time, like 2026-03-14 20:30",
    )
    async def utang(
        interaction: discord.Interaction,
        name: str,
        amount: str,
        reason: str,
        date_time: Optional[str] = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        clean_name = normalize_name(name)
        clean_reason = normalize_spaces(reason)
        if not clean_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return
        if not clean_reason:
            await interaction.response.send_message("Reason cannot be empty.", ephemeral=True)
            return

        try:
            amount_value, amount_scale = parse_amount_input(amount)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if amount_value == 0:
            await interaction.response.send_message(
                "Amount cannot be 0. Use positive or negative values.",
                ephemeral=True,
            )
            return

        entry_dt = to_bot_timezone(interaction.created_at)
        if date_time and normalize_spaces(date_time):
            try:
                entry_dt = parse_user_entry_datetime(date_time)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        store.add_debt(
            guild_id=interaction.guild_id,
            name=clean_name,
            amount=amount_value,
            amount_scale=amount_scale,
            entry_dt=entry_dt,
            reason=clean_reason,
            created_by=interaction.user.id,
        )
        await refresh_visible_lists(
            interaction.channel,
            interaction.guild_id,
            person_name=clean_name,
            interaction_dt=to_bot_timezone(interaction.created_at),
        )
        await clear_deferred_response(interaction)

    @utang.autocomplete("name")
    async def utang_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=True)(interaction, current)

    @bot.tree.command(name="list", description="Show one person's utang list, or everyone if left blank")
    @app_commands.describe(name="Name to show")
    async def list_command(
        interaction: discord.Interaction,
        name: Optional[str] = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        interaction_dt = to_bot_timezone(interaction.created_at)
        if name and normalize_spaces(name):
            await refresh_visible_lists(
                interaction.channel,
                interaction.guild_id,
                person_name=name,
                interaction_dt=interaction_dt,
            )
            await clear_deferred_response(interaction)
            return

        await refresh_visible_lists(interaction.channel, interaction.guild_id)
        await clear_deferred_response(interaction)

    @list_command.autocomplete("name")
    async def list_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=True)(interaction, current)

    @bot.tree.command(name="clear", description="Clear all unpaid utang for one person")
    @app_commands.describe(name="Name of the person")
    async def clear(
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        display_name = normalize_name(name)
        if not display_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        cleared = store.clear_debts(interaction.guild_id, display_name)
        await refresh_visible_lists(interaction.channel, interaction.guild_id)
        message = f"Cleared **{display_name}**." if cleared else f"No unpaid entries for **{display_name}**."
        await interaction.edit_original_response(content=message, view=None)

    @clear.autocomplete("name")
    async def clear_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=False)(interaction, current)

    async def handle_entry_command(
        interaction: discord.Interaction,
        *,
        name: str,
        date: str,
        updater: Callable[[int, str, str], bool],
        missing_message: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        display_name = normalize_name(name)
        if not display_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return

        try:
            entry_code = normalize_entry_code(date)
        except ValueError:
            await interaction.response.send_message("Date must look like `03-14-26-G3`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        updated = updater(interaction.guild_id, display_name, entry_code)
        if not updated:
            await refresh_visible_lists(interaction.channel, interaction.guild_id)
            await interaction.edit_original_response(
                content=missing_message.format(name=display_name, entry_code=entry_code),
                view=None,
            )
            return

        await refresh_visible_lists(
            interaction.channel,
            interaction.guild_id,
            person_name=display_name,
            interaction_dt=to_bot_timezone(interaction.created_at),
        )
        await interaction.edit_original_response(content="Updated.", view=None)

    @bot.tree.command(name="remove", description="Remove one utang entry by date code")
    @app_commands.describe(name="Name of the person", date="Date from /list, like 03-14-26-G3")
    async def remove(
        interaction: discord.Interaction,
        name: str,
        date: str,
    ) -> None:
        await handle_entry_command(
            interaction,
            name=name,
            date=date,
            updater=store.remove_debt_by_code,
            missing_message="No entry found for **{name}** on `{entry_code}`.",
        )

    @remove.autocomplete("name")
    async def remove_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=True)(interaction, current)

    @remove.autocomplete("date")
    async def remove_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_dates(include_paid=True)(interaction, current)

    @bot.tree.command(name="paid", description="Mark one utang entry as paid by date code")
    @app_commands.describe(name="Name of the person", date="Date from /list, like 03-14-26-G3")
    async def paid(
        interaction: discord.Interaction,
        name: str,
        date: str,
    ) -> None:
        await handle_entry_command(
            interaction,
            name=name,
            date=date,
            updater=store.mark_paid_by_code,
            missing_message="No unpaid entry found for **{name}** on `{entry_code}`.",
        )

    @paid.autocomplete("name")
    async def paid_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=False)(interaction, current)

    @paid.autocomplete("date")
    async def paid_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_dates(include_paid=False)(interaction, current)

    @bot.tree.command(name="unpaid", description="Undo a paid mark by date code")
    @app_commands.describe(name="Name of the person", date="Date from /list, like 03-14-26-G3")
    async def unpaid(
        interaction: discord.Interaction,
        name: str,
        date: str,
    ) -> None:
        await handle_entry_command(
            interaction,
            name=name,
            date=date,
            updater=store.mark_unpaid_by_code,
            missing_message="No paid entry found for **{name}** on `{entry_code}`.",
        )

    @unpaid.autocomplete("name")
    async def unpaid_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_names(include_paid=True)(interaction, current)

    @unpaid.autocomplete("date")
    async def unpaid_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_dates(include_paid=True)(interaction, current)

    bot.run(settings.bot_token)
