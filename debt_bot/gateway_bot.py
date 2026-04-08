from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from debt_bot.config import GatewaySettings
from debt_bot.discord_store import DiscordChannelStoreManager, GuildStoreError
from debt_bot.grade_logic import (
    calculate_percentage,
    compute_final_grade,
    compute_class_standing_from_categories,
    normalize_mode,
    parse_categories_input,
    percent_to_equivalence,
    required_exam_percentage_for_target,
)
from debt_bot.logic import (
    BUTTON_TIMEOUT_SECONDS,
    build_all_list_content,
    build_person_list_content,
    is_list_message_content,
    normalize_entry_code,
    normalize_name,
    normalize_spaces,
    page_count_for,
    parse_amount_input,
    parse_user_entry_datetime,
    to_bot_timezone,
)


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
        rendered = await view.render(interaction_dt)
        await interaction.response.edit_message(content=rendered.content, view=rendered.view)
        if interaction.message:
            view.message = interaction.message


class ListPagerView(discord.ui.View):
    def __init__(
        self,
        *,
        data_manager: DiscordChannelStoreManager,
        actor_id_getter: Callable[[], int],
        guild_id: int,
        mode: str,
        issued_at: datetime,
        person_name: Optional[str] = None,
        page_index: int = 0,
    ) -> None:
        super().__init__(timeout=BUTTON_TIMEOUT_SECONDS)
        self.data_manager = data_manager
        self.actor_id_getter = actor_id_getter
        self.guild_id = guild_id
        self.mode = mode
        self.issued_at = issued_at
        self.person_name = person_name
        self.page_index = page_index
        self.last_content = ""
        self.message: Optional[discord.Message] = None

    async def render(self, interaction_dt: datetime) -> RenderedList:
        if self.mode == "person":
            if not self.person_name:
                self.clear_items()
                self.last_content = "No utang."
                return RenderedList(self.last_content, None)

            await self.data_manager.apply_due_interest(
                self.guild_id,
                name=self.person_name,
                as_of_dt=interaction_dt,
                created_by=self.actor_id_getter(),
            )
            rows = self.data_manager.list_debts(
                self.guild_id,
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

        rows = self.data_manager.list_debts(self.guild_id, include_paid=True)
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
    intents = discord.Intents.default()
    intents.messages = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    data_manager = DiscordChannelStoreManager(bot, settings.data_channel_name)
    commands_synced = False

    def actor_id() -> int:
        user = bot.user
        return int(user.id) if user else 0

    async def send_interaction_message(
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        if interaction.response.is_done():
            try:
                await interaction.followup.send(content, ephemeral=ephemeral)
            except discord.HTTPException:
                return
            return

        try:
            await interaction.response.send_message(content, ephemeral=ephemeral)
        except discord.HTTPException:
            return

    async def finish_deferred_response(interaction: discord.Interaction, content: str) -> None:
        try:
            await interaction.edit_original_response(content=content, view=None)
        except discord.HTTPException:
            try:
                await interaction.followup.send(content, ephemeral=True)
            except discord.HTTPException:
                return

    async def send_store_error(interaction: discord.Interaction, exc: GuildStoreError) -> None:
        if interaction.response.is_done():
            await finish_deferred_response(interaction, str(exc))
            return
        await send_interaction_message(interaction, str(exc), ephemeral=True)

    async def ensure_guild_ready(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await send_interaction_message(interaction, "This command only works in a server.", ephemeral=True)
            return False

        try:
            await data_manager.ensure_guild_loaded(interaction.guild)
        except GuildStoreError as exc:
            await send_store_error(interaction, exc)
            return False
        return True

    async def ensure_guild_ready_for_autocomplete(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False

        try:
            await data_manager.ensure_guild_loaded(interaction.guild)
        except GuildStoreError:
            return False
        return True

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

    async def delete_previous_list_messages(channel: Optional[discord.abc.Messageable]) -> None:
        await delete_matching_bot_messages(channel, is_list_message_content)

    async def render_person_list(guild_id: int, person_name: str, interaction_dt: datetime) -> RenderedList:
        view = ListPagerView(
            data_manager=data_manager,
            actor_id_getter=actor_id,
            guild_id=guild_id,
            mode="person",
            issued_at=interaction_dt,
            person_name=normalize_name(person_name),
        )
        return await view.render(interaction_dt)

    async def render_all_list(guild_id: int, interaction_dt: datetime) -> RenderedList:
        view = ListPagerView(
            data_manager=data_manager,
            actor_id_getter=actor_id,
            guild_id=guild_id,
            mode="all",
            issued_at=interaction_dt,
        )
        return await view.render(interaction_dt)

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

    async def refresh_visible_lists(
        channel: Optional[discord.abc.Messageable],
        guild_id: int,
        *,
        person_name: Optional[str] = None,
        interaction_dt: Optional[datetime] = None,
    ) -> None:
        if interaction_dt is None:
            interaction_dt = to_bot_timezone(datetime.now(timezone.utc))

        rendered_all = await render_all_list(guild_id, interaction_dt)
        rendered_person: Optional[RenderedList] = None
        if person_name:
            rendered_person = await render_person_list(guild_id, person_name, interaction_dt)

        await delete_previous_list_messages(channel)
        await send_channel_rendered(channel, rendered_all)
        if rendered_person is not None:
            await send_channel_rendered(channel, rendered_person)

    def build_name_autocomplete(
        *,
        include_paid: bool,
    ) -> Callable[[discord.Interaction, str], Awaitable[list[app_commands.Choice[str]]]]:
        async def inner(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            if interaction.guild_id is None:
                return []
            if not await ensure_guild_ready_for_autocomplete(interaction):
                return []

            return [
                app_commands.Choice(name=choice, value=choice)
                for choice in data_manager.suggest_names(
                    interaction.guild_id,
                    current,
                    include_paid=include_paid,
                )
            ]

        return inner

    def build_date_autocomplete(
        *,
        include_paid: bool,
    ) -> Callable[[discord.Interaction, str], Awaitable[list[app_commands.Choice[str]]]]:
        async def inner(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            if interaction.guild_id is None:
                return []
            if not await ensure_guild_ready_for_autocomplete(interaction):
                return []

            person_name = ""
            namespace = interaction.namespace
            if hasattr(namespace, "name") and namespace.name:
                person_name = str(namespace.name)

            return [
                app_commands.Choice(name=choice, value=choice)
                for choice in data_manager.suggest_dates(
                    interaction.guild_id,
                    current,
                    name=person_name,
                    include_paid=include_paid,
                )
            ]

        return inner

    async def autocomplete_subjects(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        if not await ensure_guild_ready_for_autocomplete(interaction):
            return []

        return [
            app_commands.Choice(name=choice, value=choice)
            for choice in data_manager.suggest_subjects(interaction.guild_id, current)
        ]

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
        if interaction.guild is None:
            await send_interaction_message(interaction, "This command only works in a server.", ephemeral=True)
            return
        if not await ensure_guild_ready(interaction):
            return

        clean_name = normalize_name(name)
        clean_reason = normalize_spaces(reason)
        if not clean_name:
            await send_interaction_message(interaction, "Name cannot be empty.", ephemeral=True)
            return
        if not clean_reason:
            await send_interaction_message(interaction, "Reason cannot be empty.", ephemeral=True)
            return

        try:
            amount_value, amount_scale = parse_amount_input(amount)
        except ValueError as exc:
            await send_interaction_message(interaction, str(exc), ephemeral=True)
            return

        if amount_value == 0:
            await send_interaction_message(
                interaction,
                "Amount cannot be 0. Use positive or negative values.",
                ephemeral=True,
            )
            return

        entry_dt = to_bot_timezone(interaction.created_at)
        if date_time and normalize_spaces(date_time):
            try:
                entry_dt = parse_user_entry_datetime(date_time)
            except ValueError as exc:
                await send_interaction_message(interaction, str(exc), ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await data_manager.add_debt(
                interaction.guild,
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
        except GuildStoreError as exc:
            await finish_deferred_response(interaction, str(exc))
            return

        await finish_deferred_response(interaction, f"Added utang entry for **{clean_name}**.")

    @bot.tree.command(name="grade", description="Calculate a grade percentage and equivalence")
    @app_commands.describe(
        score="Points earned",
        total="Total possible points",
    )
    async def grade(
        interaction: discord.Interaction,
        score: float,
        total: float,
    ) -> None:
        try:
            percent = calculate_percentage(score, total)
        except ValueError as exc:
            await send_interaction_message(interaction, str(exc), ephemeral=True)
            return

        equivalence = percent_to_equivalence(percent)
        await send_interaction_message(
            interaction,
            f"Score: {score:.2f}/{total:.2f}\n"
            f"Grade: {percent:.2f}%\n"
            f"Equivalence: {equivalence}",
            ephemeral=False,
        )

    @bot.tree.command(name="grade-save", description="Save a subject profile for later grade calculations")
    @app_commands.describe(
        subject="Subject name, like Thermo",
        mode="Use prelim or term",
        target_grade="Target final grade percent, like 90",
        exam_total="Final exam total score, like 50 or 100",
        categories="Categories like 30|18/20,19/20;70|45/50",
        previous_term_average="Previous term average percent for term mode",
    )
    async def grade_save(
        interaction: discord.Interaction,
        subject: str,
        mode: str,
        target_grade: float,
        exam_total: float,
        categories: str,
        previous_term_average: Optional[float] = None,
    ) -> None:
        if interaction.guild is None:
            await send_interaction_message(interaction, "This command only works in a server.", ephemeral=True)
            return
        if not await ensure_guild_ready(interaction):
            return

        try:
            normalized_mode = normalize_mode(mode)
            parsed_categories = parse_categories_input(categories)
        except ValueError as exc:
            await send_interaction_message(interaction, str(exc), ephemeral=True)
            return

        if exam_total <= 0:
            await send_interaction_message(interaction, "Exam total must be greater than 0.", ephemeral=True)
            return
        if target_grade <= 0:
            await send_interaction_message(interaction, "Target grade must be greater than 0.", ephemeral=True)
            return

        previous_value = previous_term_average or 0.0
        if normalized_mode == "term" and previous_value <= 0:
            await send_interaction_message(
                interaction,
                "Previous term average is required for term mode.",
                ephemeral=True,
            )
            return

        class_standing, total_weight, _ = compute_class_standing_from_categories(parsed_categories)
        try:
            await data_manager.save_grade_subject(
                interaction.guild,
                subject=subject,
                mode=normalized_mode,
                target_grade=target_grade,
                categories_text=categories,
                exam_total=exam_total,
                previous_term_average=previous_value,
                updated_by=interaction.user.id,
            )
        except GuildStoreError as exc:
            await send_store_error(interaction, exc)
            return

        await send_interaction_message(
            interaction,
            f"Saved **{normalize_name(subject)}**.\n"
            f"Mode: {normalized_mode}\n"
            f"Target Grade: {target_grade:.2f}%\n"
            f"Class Standing: {class_standing:.2f}%\n"
            f"Total Category Weight: {total_weight:.2f}%\n"
            f"Previous Term Average: {previous_value:.2f}%\n"
            f"Exam Total: {exam_total:.2f}",
            ephemeral=False,
        )

    @grade_save.autocomplete("subject")
    async def grade_save_subject_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_subjects(interaction, current)

    @bot.tree.command(name="grade-show", description="Show a saved subject profile")
    @app_commands.describe(subject="Saved subject name")
    async def grade_show(
        interaction: discord.Interaction,
        subject: str,
    ) -> None:
        if not await ensure_guild_ready(interaction):
            return

        saved = data_manager.get_grade_subject(interaction.guild_id, subject)
        if saved is None:
            await send_interaction_message(
                interaction,
                f"No saved grade profile found for **{normalize_name(subject)}**.",
                ephemeral=True,
            )
            return

        try:
            parsed_categories = parse_categories_input(str(saved["categories_text"]))
        except ValueError as exc:
            await send_interaction_message(interaction, str(exc), ephemeral=True)
            return

        class_standing, total_weight, category_averages = compute_class_standing_from_categories(parsed_categories)
        lines = [
            f"Subject: **{saved['subject']}**",
            f"Mode: {saved['mode']}",
            f"Target Grade: {float(saved['target_grade']):.2f}%",
            f"Previous Term Average: {float(saved['previous_term_average']):.2f}%",
            "",
        ]
        for index, (category, average) in enumerate(zip(parsed_categories, category_averages), start=1):
            lines.append(f"Category {index}: weight {category.weight:.2f}%, average {average:.2f}%")
        lines.extend(
            [
                "",
                f"Total Category Weight: {total_weight:.2f}%",
                f"Class Standing: {class_standing:.2f}%",
                f"Exam Total: {float(saved['exam_total']):.2f}",
            ]
        )
        await send_interaction_message(interaction, "\n".join(lines), ephemeral=False)

    @grade_show.autocomplete("subject")
    async def grade_show_subject_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_subjects(interaction, current)

    @bot.tree.command(name="grade-required", description="Show the required exam score for a saved subject")
    @app_commands.describe(subject="Saved subject name")
    async def grade_required(
        interaction: discord.Interaction,
        subject: str,
    ) -> None:
        if not await ensure_guild_ready(interaction):
            return

        saved = data_manager.get_grade_subject(interaction.guild_id, subject)
        if saved is None:
            await send_interaction_message(
                interaction,
                f"No saved grade profile found for **{normalize_name(subject)}**.",
                ephemeral=True,
            )
            return

        try:
            parsed_categories = parse_categories_input(str(saved["categories_text"]))
        except ValueError as exc:
            await send_interaction_message(interaction, str(exc), ephemeral=True)
            return

        class_standing, total_weight, _ = compute_class_standing_from_categories(parsed_categories)
        needed_percent = required_exam_percentage_for_target(
            mode=str(saved["mode"]),
            target_grade=float(saved["target_grade"]),
            class_standing=class_standing,
            previous_term_average=float(saved["previous_term_average"]),
        )
        exam_total = float(saved["exam_total"])
        needed_score = (needed_percent / 100) * exam_total

        if needed_percent <= 0:
            message = (
                f"Subject: **{saved['subject']}**\n"
                f"You already meet the target grade `{float(saved['target_grade']):.2f}%` even with 0 on the exam."
            )
        elif needed_percent > 100:
            message = (
                f"Subject: **{saved['subject']}**\n"
                f"It is not possible to reach `{float(saved['target_grade']):.2f}%` even with a perfect exam score."
            )
        else:
            final_percent = compute_final_grade(
                mode=str(saved["mode"]),
                class_standing=class_standing,
                exam_percent=needed_percent,
                previous_term_average=float(saved["previous_term_average"]),
            )
            message = (
                f"Subject: **{saved['subject']}**\n"
                f"Mode: {saved['mode']}\n"
                f"Target Grade: {float(saved['target_grade']):.2f}%\n"
                f"Previous Term Average: {float(saved['previous_term_average']):.2f}%\n"
                f"Class Standing: {class_standing:.2f}%\n"
                f"Total Category Weight: {total_weight:.2f}%\n"
                f"Required Exam Percentage: {needed_percent:.2f}%\n"
                f"Required Exam Score: {needed_score:.2f} / {exam_total:.2f}\n"
                f"Projected Final Grade: {final_percent:.2f}%\n"
                f"Projected Equivalence: {percent_to_equivalence(final_percent)}"
            )

        await send_interaction_message(interaction, message, ephemeral=False)

    @grade_required.autocomplete("subject")
    async def grade_required_subject_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await autocomplete_subjects(interaction, current)

    @utang.autocomplete("name")
    async def utang_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=True)(interaction, current)

    @bot.tree.command(name="list", description="Show one person's utang list, or everyone if left blank")
    @app_commands.describe(name="Name to show")
    async def list_command(
        interaction: discord.Interaction,
        name: Optional[str] = None,
    ) -> None:
        if not await ensure_guild_ready(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        interaction_dt = to_bot_timezone(interaction.created_at)
        try:
            if name and normalize_spaces(name):
                await refresh_visible_lists(
                    interaction.channel,
                    interaction.guild_id,
                    person_name=name,
                    interaction_dt=interaction_dt,
                )
                await finish_deferred_response(
                    interaction,
                    f"Posted utang list for **{normalize_name(name)}**.",
                )
                return

            await refresh_visible_lists(interaction.channel, interaction.guild_id, interaction_dt=interaction_dt)
        except GuildStoreError as exc:
            await finish_deferred_response(interaction, str(exc))
            return

        await finish_deferred_response(interaction, "Posted all utang entries.")

    @list_command.autocomplete("name")
    async def list_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=True)(interaction, current)

    @bot.tree.command(name="clear", description="Clear all utang for one person")
    @app_commands.describe(name="Name of the person")
    async def clear(
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        if interaction.guild is None:
            await send_interaction_message(interaction, "This command only works in a server.", ephemeral=True)
            return
        if not await ensure_guild_ready(interaction):
            return

        display_name = normalize_name(name)
        if not display_name:
            await send_interaction_message(interaction, "Name cannot be empty.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cleared = await data_manager.clear_debts(interaction.guild, name=display_name)
            await refresh_visible_lists(interaction.channel, interaction.guild_id)
        except GuildStoreError as exc:
            await finish_deferred_response(interaction, str(exc))
            return

        message = f"Cleared **{display_name}**." if cleared else f"No entries for **{display_name}**."
        await interaction.edit_original_response(content=message, view=None)

    @clear.autocomplete("name")
    async def clear_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=True)(interaction, current)

    async def handle_entry_command(
        interaction: discord.Interaction,
        *,
        name: str,
        date: str,
        updater: Callable[[discord.Guild, str, str], Awaitable[bool]],
        missing_message: str,
    ) -> None:
        if interaction.guild is None:
            await send_interaction_message(interaction, "This command only works in a server.", ephemeral=True)
            return
        if not await ensure_guild_ready(interaction):
            return

        display_name = normalize_name(name)
        if not display_name:
            await send_interaction_message(interaction, "Name cannot be empty.", ephemeral=True)
            return

        try:
            entry_code = normalize_entry_code(date)
        except ValueError:
            await send_interaction_message(interaction, "Date must look like `03-14-26-G3`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            updated = await updater(interaction.guild, display_name, entry_code)
            if not updated:
                await refresh_visible_lists(
                    interaction.channel,
                    interaction.guild_id,
                    person_name=display_name,
                    interaction_dt=to_bot_timezone(interaction.created_at),
                )
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
        except GuildStoreError as exc:
            await finish_deferred_response(interaction, str(exc))
            return

        await finish_deferred_response(interaction, f"Updated utang list for **{display_name}**.")

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
            updater=lambda guild, display_name, entry_code: data_manager.remove_debt_by_code(
                guild,
                name=display_name,
                entry_code=entry_code,
            ),
            missing_message="No entry found for **{name}** on `{entry_code}`.",
        )

    @remove.autocomplete("name")
    async def remove_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=True)(interaction, current)

    @remove.autocomplete("date")
    async def remove_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_date_autocomplete(include_paid=True)(interaction, current)

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
            updater=lambda guild, display_name, entry_code: data_manager.mark_paid_by_code(
                guild,
                name=display_name,
                entry_code=entry_code,
            ),
            missing_message="No unpaid entry found for **{name}** on `{entry_code}`.",
        )

    @paid.autocomplete("name")
    async def paid_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=False)(interaction, current)

    @paid.autocomplete("date")
    async def paid_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_date_autocomplete(include_paid=False)(interaction, current)

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
            updater=lambda guild, display_name, entry_code: data_manager.mark_unpaid_by_code(
                guild,
                name=display_name,
                entry_code=entry_code,
            ),
            missing_message="No paid entry found for **{name}** on `{entry_code}`.",
        )

    @unpaid.autocomplete("name")
    async def unpaid_name_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_name_autocomplete(include_paid=True)(interaction, current)

    @unpaid.autocomplete("date")
    async def unpaid_date_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await build_date_autocomplete(include_paid=True)(interaction, current)

    bot.run(settings.bot_token)
