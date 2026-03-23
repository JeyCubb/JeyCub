from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import Response
from fastapi.responses import JSONResponse

from debt_bot.config import Settings
from debt_bot.discord_api import DiscordApi
from debt_bot.logic import (
    build_all_list_content,
    build_list_components,
    build_person_list_content,
    extract_person_name_from_list_content,
    is_list_message_content,
    normalize_entry_code,
    normalize_name,
    normalize_spaces,
    page_count_for,
    pager_expired,
    parse_amount_input,
    parse_pager_custom_id,
    parse_user_entry_datetime,
    snowflake_to_datetime,
    to_bot_timezone,
)
from debt_bot.grade_logic import (
    calculate_percentage,
    compute_final_grade,
    compute_class_standing_from_categories,
    normalize_mode,
    parse_categories_input,
    percent_to_equivalence,
    required_exam_percentage_for_target,
)
from debt_bot.store import DebtStore


PING = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
APPLICATION_COMMAND_AUTOCOMPLETE = 4

PONG = 1
CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
UPDATE_MESSAGE = 7
APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8

EPHEMERAL_FLAG = 1 << 6


def build_message_response(
    content: str,
    *,
    ephemeral: bool = False,
    components: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"content": content}
    if ephemeral:
        data["flags"] = EPHEMERAL_FLAG
    if components is not None:
        data["components"] = components
    return {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": data}


def build_deferred_response(*, ephemeral: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE}
    if ephemeral:
        payload["data"] = {"flags": EPHEMERAL_FLAG}
    return payload


def build_update_message_response(content: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": UPDATE_MESSAGE,
        "data": {
            "content": content,
            "components": components,
        },
    }


def build_autocomplete_response(choices: list[str]) -> dict[str, Any]:
    return {
        "type": APPLICATION_COMMAND_AUTOCOMPLETE_RESULT,
        "data": {
            "choices": [{"name": choice, "value": choice} for choice in choices],
        },
    }


def flatten_options(options: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for option in options:
        nested_options = option.get("options") or []
        if nested_options:
            flattened.update(flatten_options(nested_options))
            continue
        flattened[option["name"]] = option.get("value")
    return flattened


def find_focused_option(options: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for option in options:
        nested_options = option.get("options") or []
        if nested_options:
            focused = find_focused_option(nested_options)
            if focused is not None:
                return focused
        if option.get("focused"):
            return option
    return None


def get_user_id(payload: dict[str, Any]) -> Optional[int]:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    user_id = user.get("id")
    if user_id is None:
        return None
    return int(user_id)


def get_guild_id(payload: dict[str, Any]) -> Optional[int]:
    guild_id = payload.get("guild_id")
    if guild_id is None:
        return None
    return int(guild_id)


class InteractionService:
    def __init__(self, settings: Settings, store: DebtStore) -> None:
        self.settings = settings
        self.store = store

    async def handle(self, payload: dict[str, Any]) -> Response:
        interaction_type = int(payload["type"])

        if interaction_type == PING:
            return JSONResponse({"type": PONG})

        if interaction_type == APPLICATION_COMMAND_AUTOCOMPLETE:
            return JSONResponse(self._handle_autocomplete(payload))

        if interaction_type == MESSAGE_COMPONENT:
            return JSONResponse(self._handle_component(payload))

        if interaction_type != APPLICATION_COMMAND:
            return JSONResponse(build_message_response("Unsupported interaction type.", ephemeral=True))

        async with DiscordApi(self.settings.application_id, self.settings.bot_token) as discord_api:
            return await self._handle_command(payload, discord_api)

    async def _handle_command(self, payload: dict[str, Any], discord_api: DiscordApi) -> Response:
        command_name = str(payload.get("data", {}).get("name", ""))

        if command_name == "utang":
            return await self._handle_utang(payload, discord_api)
        if command_name == "ping":
            return JSONResponse(build_message_response("Pong!"))
        if command_name == "grade":
            return await self._handle_grade(payload)
        if command_name == "grade-save":
            return await self._handle_grade_save(payload)
        if command_name == "grade-show":
            return self._handle_grade_show(payload)
        if command_name == "grade-required":
            return self._handle_grade_required(payload)
        if command_name == "list":
            return await self._handle_list(payload, discord_api)
        if command_name == "clear":
            return await self._handle_clear(payload, discord_api)
        if command_name == "remove":
            return await self._handle_entry_command(
                payload,
                discord_api,
                updater=self.store.remove_debt_by_code,
                missing_message="No unpaid entry found for **{name}** on `{entry_code}`.",
            )
        if command_name == "paid":
            return await self._handle_entry_command(
                payload,
                discord_api,
                updater=self.store.mark_paid_by_code,
                missing_message="No unpaid entry found for **{name}** on `{entry_code}`.",
            )
        if command_name == "unpaid":
            return await self._handle_entry_command(
                payload,
                discord_api,
                updater=self.store.mark_unpaid_by_code,
                missing_message="No paid entry found for **{name}** on `{entry_code}`.",
            )

        return JSONResponse(build_message_response("Unknown command.", ephemeral=True))

    async def _handle_utang(self, payload: dict[str, Any], discord_api: DiscordApi) -> Response:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return JSONResponse(build_message_response("This command only works in a server.", ephemeral=True))

        user_id = get_user_id(payload)
        if user_id is None:
            return JSONResponse(build_message_response("Could not identify user.", ephemeral=True))

        options = flatten_options(payload.get("data", {}).get("options") or [])
        clean_name = normalize_name(str(options.get("name", "")))
        clean_reason = normalize_spaces(str(options.get("reason", "")))
        if not clean_name:
            return JSONResponse(build_message_response("Name cannot be empty.", ephemeral=True))
        if not clean_reason:
            return JSONResponse(build_message_response("Reason cannot be empty.", ephemeral=True))

        try:
            amount_value, amount_scale = parse_amount_input(str(options.get("amount", "")))
        except ValueError as exc:
            return JSONResponse(build_message_response(str(exc), ephemeral=True))

        if amount_value == 0:
            return JSONResponse(
                build_message_response("Amount cannot be 0. Use positive or negative values.", ephemeral=True)
            )

        interaction_dt = to_bot_timezone(snowflake_to_datetime(payload["id"]))
        entry_dt = interaction_dt
        date_time = options.get("date_time")
        if date_time and normalize_spaces(str(date_time)):
            try:
                entry_dt = parse_user_entry_datetime(str(date_time))
            except ValueError as exc:
                return JSONResponse(build_message_response(str(exc), ephemeral=True))

        await discord_api.create_interaction_response(
            str(payload["id"]),
            str(payload["token"]),
            build_deferred_response(),
        )

        actor_id = int(self.settings.application_id)
        self.store.apply_due_interest(
            guild_id=guild_id,
            name=clean_name,
            as_of_dt=entry_dt,
            created_by=actor_id,
        )
        self.store.add_debt(
            guild_id=guild_id,
            name=clean_name,
            amount=amount_value,
            amount_scale=amount_scale,
            entry_dt=entry_dt,
            reason=clean_reason,
            created_by=user_id,
        )

        await self._clear_previous_lists(discord_api, payload)
        content, components = self._render_person_list(guild_id, clean_name, interaction_dt)
        await discord_api.edit_original_response(
            str(payload["token"]),
            {"content": content, "components": components},
        )
        return Response(status_code=202)

    async def _handle_grade(self, payload: dict[str, Any]) -> Response:
        options = flatten_options(payload.get("data", {}).get("options") or [])

        score = options.get("score")
        total = options.get("total")
        if score is None or total is None:
            return JSONResponse(build_message_response("Score and total are required.", ephemeral=True))

        try:
            score_value = float(score)
            total_value = float(total)
            percent = calculate_percentage(score_value, total_value)
        except ValueError as exc:
            return JSONResponse(build_message_response(str(exc), ephemeral=True))

        equivalence = percent_to_equivalence(percent)
        content = (
            f"Score: {score_value:.2f}/{total_value:.2f}\n"
            f"Grade: {percent:.2f}%\n"
            f"Equivalence: {equivalence}"
        )
        return JSONResponse(build_message_response(content))

    async def _handle_grade_save(self, payload: dict[str, Any]) -> Response:
        guild_id = get_guild_id(payload)
        user_id = get_user_id(payload)
        if guild_id is None or user_id is None:
            return JSONResponse(build_message_response("This command only works in a server.", ephemeral=True))

        options = flatten_options(payload.get("data", {}).get("options") or [])
        subject = str(options.get("subject", ""))
        mode = str(options.get("mode", ""))
        target_grade = float(options.get("target_grade") or 0)
        exam_total = float(options.get("exam_total") or 0)
        categories = str(options.get("categories") or "")
        previous_term_average = float(options.get("previous_term_average") or 0)

        try:
            normalized_mode = normalize_mode(mode)
            parsed_categories = parse_categories_input(categories)
        except ValueError as exc:
            return JSONResponse(build_message_response(str(exc), ephemeral=True))

        if exam_total <= 0:
            return JSONResponse(build_message_response("Exam total must be greater than 0.", ephemeral=True))
        if target_grade <= 0:
            return JSONResponse(build_message_response("Target grade must be greater than 0.", ephemeral=True))
        if normalized_mode == "term" and previous_term_average <= 0:
            return JSONResponse(
                build_message_response("Previous term average is required for term mode.", ephemeral=True)
            )

        class_standing, total_weight, _ = compute_class_standing_from_categories(parsed_categories)
        self.store.save_grade_subject(
            guild_id,
            subject,
            normalized_mode,
            target_grade,
            categories,
            exam_total,
            previous_term_average,
            user_id,
        )
        return JSONResponse(
            build_message_response(
                f"Saved **{normalize_name(subject)}**.\n"
                f"Target Grade: {target_grade:.2f}%\n"
                f"Class Standing: {class_standing:.2f}%\n"
                f"Total Category Weight: {total_weight:.2f}%"
            )
        )

    def _handle_grade_show(self, payload: dict[str, Any]) -> dict[str, Any]:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return build_message_response("This command only works in a server.", ephemeral=True)

        options = flatten_options(payload.get("data", {}).get("options") or [])
        subject = str(options.get("subject", ""))
        saved = self.store.get_grade_subject(guild_id, subject)
        if saved is None:
            return build_message_response(
                f"No saved grade profile found for **{normalize_name(subject)}**.",
                ephemeral=True,
            )

        parsed_categories = parse_categories_input(str(saved["categories_text"]))
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
        return build_message_response("\n".join(lines))

    def _handle_grade_required(self, payload: dict[str, Any]) -> dict[str, Any]:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return build_message_response("This command only works in a server.", ephemeral=True)

        options = flatten_options(payload.get("data", {}).get("options") or [])
        subject = str(options.get("subject", ""))
        saved = self.store.get_grade_subject(guild_id, subject)
        if saved is None:
            return build_message_response(
                f"No saved grade profile found for **{normalize_name(subject)}**.",
                ephemeral=True,
            )

        parsed_categories = parse_categories_input(str(saved["categories_text"]))
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
            return build_message_response(
                f"Subject: **{saved['subject']}**\n"
                f"You already meet the target grade `{float(saved['target_grade']):.2f}%` even with 0 on the exam."
            )
        if needed_percent > 100:
            return build_message_response(
                f"Subject: **{saved['subject']}**\n"
                f"It is not possible to reach `{float(saved['target_grade']):.2f}%` even with a perfect exam score."
            )

        final_percent = compute_final_grade(
            mode=str(saved["mode"]),
            class_standing=class_standing,
            exam_percent=needed_percent,
            previous_term_average=float(saved["previous_term_average"]),
        )
        return build_message_response(
            f"Subject: **{saved['subject']}**\n"
            f"Mode: {saved['mode']}\n"
            f"Target Grade: {float(saved['target_grade']):.2f}%\n"
            f"Required Exam Percentage: {needed_percent:.2f}%\n"
            f"Required Exam Score: {needed_score:.2f} / {exam_total:.2f}\n"
            f"Class Standing: {class_standing:.2f}%\n"
            f"Total Category Weight: {total_weight:.2f}%\n"
            f"Projected Final Grade: {final_percent:.2f}%\n"
            f"Projected Equivalence: {percent_to_equivalence(final_percent)}"
        )

    async def _handle_list(self, payload: dict[str, Any], discord_api: DiscordApi) -> Response:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return JSONResponse(build_message_response("This command only works in a server.", ephemeral=True))

        options = flatten_options(payload.get("data", {}).get("options") or [])
        requested_name = normalize_spaces(str(options.get("name", "")))
        interaction_dt = to_bot_timezone(snowflake_to_datetime(payload["id"]))

        await discord_api.create_interaction_response(
            str(payload["id"]),
            str(payload["token"]),
            build_deferred_response(),
        )
        await self._clear_previous_lists(discord_api, payload)

        if requested_name:
            content, components = self._render_person_list(guild_id, requested_name, interaction_dt)
        else:
            content, components = self._render_all_list(guild_id, interaction_dt)

        await discord_api.edit_original_response(
            str(payload["token"]),
            {"content": content, "components": components},
        )
        return Response(status_code=202)

    async def _handle_clear(self, payload: dict[str, Any], discord_api: DiscordApi) -> Response:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return JSONResponse(build_message_response("This command only works in a server.", ephemeral=True))

        options = flatten_options(payload.get("data", {}).get("options") or [])
        display_name = normalize_name(str(options.get("name", "")))
        if not display_name:
            return JSONResponse(build_message_response("Name cannot be empty.", ephemeral=True))

        await discord_api.create_interaction_response(
            str(payload["id"]),
            str(payload["token"]),
            build_deferred_response(ephemeral=True),
        )

        cleared = self.store.clear_debts(guild_id=guild_id, name=display_name)
        await self._clear_previous_lists(discord_api, payload)
        message = f"Cleared **{display_name}**." if cleared else f"No unpaid entries for **{display_name}**."
        await discord_api.edit_original_response(
            str(payload["token"]),
            {"content": message, "components": []},
        )
        return Response(status_code=202)

    async def _handle_entry_command(
        self,
        payload: dict[str, Any],
        discord_api: DiscordApi,
        *,
        updater: Callable[[int, str, str], bool],
        missing_message: str,
    ) -> Response:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return JSONResponse(build_message_response("This command only works in a server.", ephemeral=True))

        options = flatten_options(payload.get("data", {}).get("options") or [])
        display_name = normalize_name(str(options.get("name", "")))
        if not display_name:
            return JSONResponse(build_message_response("Name cannot be empty.", ephemeral=True))

        try:
            entry_code = normalize_entry_code(str(options.get("date", "")))
        except ValueError:
            return JSONResponse(build_message_response("Date must look like `03-14-26-G3`."))

        await discord_api.create_interaction_response(
            str(payload["id"]),
            str(payload["token"]),
            build_deferred_response(),
        )

        updated = updater(guild_id, display_name, entry_code)
        if not updated:
            await discord_api.edit_original_response(
                str(payload["token"]),
                {
                    "content": missing_message.format(name=display_name, entry_code=entry_code),
                    "components": [],
                },
            )
            return Response(status_code=202)

        interaction_dt = to_bot_timezone(snowflake_to_datetime(payload["id"]))
        await self._clear_previous_lists(discord_api, payload)
        content, components = self._render_person_list(guild_id, display_name, interaction_dt)
        await discord_api.edit_original_response(
            str(payload["token"]),
            {"content": content, "components": components},
        )
        return Response(status_code=202)

    def _handle_autocomplete(self, payload: dict[str, Any]) -> dict[str, Any]:
        guild_id = get_guild_id(payload)
        if guild_id is None:
            return build_autocomplete_response([])

        data = payload.get("data", {})
        command_name = str(data.get("name", ""))
        options = data.get("options") or []
        focused = find_focused_option(options)
        if focused is None:
            return build_autocomplete_response([])

        option_values = flatten_options(options)
        focused_name = focused.get("name")
        current_value = str(focused.get("value", ""))

        choices: list[str] = []
        if focused_name == "name" and command_name in {"utang", "list", "clear", "remove", "paid", "unpaid"}:
            choices = self.store.suggest_names(guild_id, current_value)
        elif focused_name == "subject" and command_name in {"grade-save", "grade-show", "grade-required"}:
            choices = self.store.suggest_subjects(guild_id, current_value)
        elif focused_name == "date" and command_name in {"remove", "paid"}:
            choices = self.store.suggest_dates(guild_id, option_values.get("name"), current_value)
        elif focused_name == "date" and command_name == "unpaid":
            choices = self.store.suggest_dates(
                guild_id,
                option_values.get("name"),
                current_value,
                include_paid=True,
            )

        return build_autocomplete_response(choices)

    def _handle_component(self, payload: dict[str, Any]) -> dict[str, Any]:
        custom_id = str(payload.get("data", {}).get("custom_id", ""))
        parsed = parse_pager_custom_id(custom_id)
        if parsed is None:
            return build_message_response("That list action is no longer valid. Run /list again.", ephemeral=True)

        mode, page_index, issued_at = parsed
        interaction_dt = to_bot_timezone(snowflake_to_datetime(payload["id"]))
        message = payload.get("message") or {}
        current_content = str(message.get("content", ""))

        if pager_expired(interaction_dt, issued_at):
            return build_update_message_response(current_content, [])

        guild_id = get_guild_id(payload)
        if guild_id is None:
            return build_message_response("This command only works in a server.", ephemeral=True)

        if mode == "person":
            person_name = extract_person_name_from_list_content(current_content)
            if not person_name:
                return build_message_response("That list action is no longer valid. Run /list again.", ephemeral=True)

            rows = self.store.list_debts(guild_id=guild_id, name=person_name, include_paid=True)
            if not rows:
                return build_update_message_response(f"No utang for **{normalize_name(person_name)}**.", [])

            last_page = page_count_for(len(rows)) - 1
            page_index = min(page_index, last_page)
            return build_update_message_response(
                build_person_list_content(rows[0]["name"], rows, page_index),
                build_list_components("person", page_index, len(rows), interaction_dt),
            )

        rows = self.store.list_debts(guild_id=guild_id, include_paid=True)
        if not rows:
            return build_update_message_response("No unpaid utang.", [])

        last_page = page_count_for(len(rows)) - 1
        page_index = min(page_index, last_page)
        return build_update_message_response(
            build_all_list_content(rows, page_index),
            build_list_components("all", page_index, len(rows), interaction_dt),
        )

    def _render_person_list(
        self,
        guild_id: int,
        person_name: str,
        interaction_dt: datetime,
    ) -> tuple[str, list[dict[str, Any]]]:
        actor_id = int(self.settings.application_id)
        self.store.apply_due_interest(
            guild_id=guild_id,
            name=person_name,
            as_of_dt=interaction_dt,
            created_by=actor_id,
        )
        rows = self.store.list_debts(guild_id=guild_id, name=person_name, include_paid=True)
        display_name = normalize_name(person_name)
        if not rows:
            return f"No utang for **{display_name}**.", []

        return (
            build_person_list_content(rows[0]["name"], rows, page_index=0),
            build_list_components("person", 0, len(rows), interaction_dt),
        )

    def _render_all_list(
        self,
        guild_id: int,
        interaction_dt: datetime,
    ) -> tuple[str, list[dict[str, Any]]]:
        rows = self.store.list_debts(guild_id=guild_id, include_paid=True)
        if not rows:
            return "No unpaid utang.", []
        return (
            build_all_list_content(rows, page_index=0),
            build_list_components("all", 0, len(rows), interaction_dt),
        )

    async def _clear_previous_lists(self, discord_api: DiscordApi, payload: dict[str, Any]) -> None:
        channel_id = payload.get("channel_id")
        if channel_id is None:
            return

        try:
            messages = await discord_api.get_channel_messages(str(channel_id), limit=50)
        except Exception:
            return

        application_id = self.settings.application_id
        for message in messages:
            content = str(message.get("content", ""))
            if not is_list_message_content(content):
                continue

            author_id = str((message.get("author") or {}).get("id", ""))
            message_application_id = str(message.get("application_id", ""))
            if author_id != application_id and message_application_id != application_id:
                continue

            message_id = message.get("id")
            if message_id is None:
                continue

            try:
                await discord_api.delete_channel_message(str(channel_id), str(message_id))
            except Exception:
                continue
