import os
import re
import textwrap
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PESO_SIGN = "\u20B1"
MAX_LIST_ROWS = 20
MAX_AUTOCOMPLETE_CHOICES = 25
MONEY_STEP = Decimal("0.01")
INTEREST_RATE = Decimal("0.01")
INTEREST_DAYS = 30
INTEREST_REASON = "1% interest 30 days after"
DEFAULT_BOT_TIMEZONE = "Asia/Manila"
DEFAULT_BOT_UTC_OFFSET = timedelta(hours=8)
BUTTON_TIMEOUT_SECONDS = 1800
DISCORD_EPOCH_MS = 1420070400000
DISCORD_MESSAGE_SOFT_LIMIT = 1900
PERSON_LIST_PREFIX = "Utang list for **"
ALL_LIST_PREFIX = "All utang"
ALL_LIST_BOX_PREFIX = "```text\nDate"
NBSP = "\u00A0"
LIST_INDENT = NBSP * 2
REASON_INDENT = LIST_INDENT + NBSP * 2
REASON_WRAP_WIDTH = 38
LIST_MESSAGE_PREFIXES = (
    PERSON_LIST_PREFIX,
    ALL_LIST_PREFIX,
    "No utang for **",
    "No utang for ",
    "No unpaid entries for **",
    "No unpaid utang.",
)


DebtRow = Mapping[str, Any]


def normalize_spaces(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_name(name: str) -> str:
    cleaned = normalize_spaces(name)
    return cleaned.title()


def make_name_key(name: str) -> str:
    return normalize_spaces(name).casefold()


def round_money(amount: float | Decimal) -> Decimal:
    return Decimal(str(amount)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def infer_amount_scale(amount: float | Decimal) -> int:
    value = round_money(amount)
    return 0 if value == value.to_integral() else 2


def format_peso(amount: float | Decimal, scale: Optional[int] = None) -> str:
    value = round_money(amount)
    absolute = abs(value)
    sign = "-" if value < 0 else ""

    if scale is None:
        if absolute == absolute.to_integral():
            body = f"{int(absolute):,}"
        else:
            whole, _, fraction = f"{absolute:.2f}".partition(".")
            fraction = fraction.rstrip("0")
            body = f"{int(whole):,}"
            if fraction:
                body += f".{fraction}"
    elif scale == 0:
        body = f"{int(absolute.to_integral_value(rounding=ROUND_HALF_UP)):,}"
    else:
        body = format(absolute, f",.{scale}f")

    return f"{sign}{PESO_SIGN}{body}"


def parse_amount_input(amount_text: str) -> tuple[float, int]:
    cleaned = normalize_spaces(amount_text).replace(PESO_SIGN, "").replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d{1,2})?|\.\d{1,2})", cleaned):
        raise ValueError("Amount must look like 11, -1, 11.5, or 11.50.")

    scale = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    try:
        amount = Decimal(cleaned).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Amount is invalid.") from exc

    return float(amount), scale


def parse_iso_date(value: str) -> date:
    return datetime.strptime(normalize_spaces(value), "%Y-%m-%d").date()


def parse_created_at(value: str) -> datetime:
    cleaned = normalize_spaces(value)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.strptime("2000-01-01 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def get_bot_timezone() -> tzinfo:
    timezone_name = os.getenv("BOT_TIMEZONE", DEFAULT_BOT_TIMEZONE).strip() or DEFAULT_BOT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == DEFAULT_BOT_TIMEZONE:
            return timezone(DEFAULT_BOT_UTC_OFFSET, name=DEFAULT_BOT_TIMEZONE)
        try:
            return ZoneInfo(DEFAULT_BOT_TIMEZONE)
        except ZoneInfoNotFoundError:
            return timezone(DEFAULT_BOT_UTC_OFFSET, name=DEFAULT_BOT_TIMEZONE)


def to_bot_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(get_bot_timezone())


def parse_user_entry_datetime(value: str) -> datetime:
    cleaned = normalize_spaces(value)
    lowered = cleaned.lower().replace(".", "")
    lowered = re.sub(r"\s*([ap]m)$", r"\1", lowered)

    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %I:%M%p",
        "%Y-%m-%d %I%p",
        "%m-%d-%y %H:%M",
        "%m-%d-%y %H:%M:%S",
        "%m-%d-%y %I:%M%p",
        "%m-%d-%y %I%p",
        "%m/%d/%y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %I:%M%p",
        "%m/%d/%y %I%p",
    ):
        try:
            return datetime.strptime(lowered, fmt).replace(tzinfo=get_bot_timezone())
        except ValueError:
            continue

    raise ValueError("Date/time must look like 2026-03-14 20:30 or 03-14-26 8:30pm.")


def snowflake_to_datetime(value: int | str) -> datetime:
    snowflake = int(value)
    timestamp_ms = (snowflake >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def hour_to_letter(hour: int) -> str:
    if hour == 0:
        return "X"
    if 1 <= hour <= 11:
        return chr(ord("A") + hour - 1)
    if hour == 12:
        return "L"
    return chr(ord("M") + (hour - 13))


def build_entry_code(entry_dt: datetime, suffix_digit: int) -> str:
    date_part = entry_dt.strftime("%m-%d-%y")
    return f"{date_part}-{hour_to_letter(entry_dt.hour)}{suffix_digit}"


def normalize_entry_code(date_text: str) -> str:
    cleaned = normalize_spaces(date_text)
    match = re.fullmatch(r"(\d{2})[-/](\d{2})[-/](\d{2})-([A-La-l])(\d+)", cleaned)
    if not match:
        raise ValueError("Date must look like 03-14-26-G3.")

    month, day, year, hour_letter, suffix_digit = match.groups()
    base_date = datetime.strptime(f"{month}-{day}-{year}", "%m-%d-%y").strftime("%m-%d-%y")
    return f"{base_date}-{hour_letter.upper()}{int(suffix_digit)}"


def clip_text(text: str, width: int) -> str:
    cleaned = normalize_spaces(text)
    if len(cleaned) <= width:
        return cleaned
    return cleaned[: width - 3] + "..."


def strikethrough_visible_text(text: str) -> str:
    return "".join(
        character + "\u0335\u2060" if character == PESO_SIGN or character.isalnum() else character
        for character in text
    )


def strikethrough_all_text(text: str) -> str:
    return "".join(character + "\u0336" if character != "\n" else character for character in text)


def page_count_for(total_items: int) -> int:
    return max(1, (total_items + MAX_LIST_ROWS - 1) // MAX_LIST_ROWS)


def slice_page(rows: list[DebtRow], page_index: int) -> list[DebtRow]:
    start = page_index * MAX_LIST_ROWS
    end = start + MAX_LIST_ROWS
    return rows[start:end]


def wrap_cell(text: str, width: int) -> list[str]:
    wrapped = textwrap.wrap(
        normalize_spaces(text),
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [""]


def split_reason_lines(reason: str, first_width: int, full_width: int) -> tuple[str, list[str]]:
    normalized = normalize_spaces(reason)
    wrapped_lines = wrap_cell(normalized, first_width)
    first_line = wrapped_lines[0]
    if not first_line:
        return "", []
    return first_line, wrapped_lines[1:]


def format_row_amount(row: DebtRow) -> str:
    return format_peso(row["amount"], int(row["amount_scale"] or 0))


def escape_markdown_text(text: str) -> str:
    return re.sub(r"([\\`*_~|])", r"\\\1", text)


def style_markdown_value(text: str, *, is_paid: bool) -> str:
    escaped_text = escape_markdown_text(text)
    if is_paid and escaped_text:
        return f"~~{escaped_text}~~"
    return escaped_text


def wrap_reason_text(reason: str) -> list[str]:
    wrapped = textwrap.wrap(
        normalize_spaces(reason),
        width=REASON_WRAP_WIDTH,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [""]


def build_markdown_entry_block(row: DebtRow, *, include_name: bool) -> str:
    paid_row = bool(row["is_paid"])
    date_text = escape_markdown_text(str(row["entry_code"]))
    amount_text = style_markdown_value(format_row_amount(row), is_paid=paid_row)

    top_parts = [date_text]
    if include_name:
        name_text = style_markdown_value(normalize_spaces(str(row["name"])), is_paid=paid_row)
        top_parts.append(name_text)
    top_parts.append(amount_text)

    block_lines = [f"{LIST_INDENT}" + f"{NBSP}|{NBSP}".join(top_parts)]
    for reason_line in wrap_reason_text(str(row["reason"])):
        block_lines.append(f"{REASON_INDENT}{style_markdown_value(reason_line, is_paid=paid_row)}")

    return "\n".join(block_lines)


def join_markdown_lines(
    title: str,
    entry_blocks: list[str],
    *,
    suffix_lines: Optional[list[str]] = None,
) -> str:
    lines = [title]
    lines.extend(entry_blocks)
    if suffix_lines:
        lines.append("")
        lines.extend(suffix_lines)
    return "\n".join(lines)


def chunk_markdown_rows(title: str, entry_blocks: list[str]) -> list[str]:
    if not entry_blocks:
        return [f"{title}\nNo utang."]

    reserve = len(title) + 12
    limit = max(200, DISCORD_MESSAGE_SOFT_LIMIT - reserve)
    chunks: list[list[str]] = []
    current_chunk: list[str] = []
    current_length = 0

    for entry_block in entry_blocks:
        addition = len(entry_block) + 1
        if current_chunk and current_length + addition > limit:
            chunks.append(current_chunk)
            current_chunk = [entry_block]
            current_length = addition
        else:
            current_chunk.append(entry_block)
            current_length += addition

    if current_chunk:
        chunks.append(current_chunk)

    if len(chunks) == 1:
        return [join_markdown_lines(title, chunks[0])]

    total_chunks = len(chunks)
    return [
        join_markdown_lines(f"{title} ({index}/{total_chunks})", chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]


def format_row_amount_cell(row: DebtRow, width: int) -> str:
    amount_text = format_row_amount(row)
    padded_amount = f"{amount_text:<{width}}"
    if bool(row["is_paid"]):
        padding = " " * max(width - len(amount_text), 0)
        return strikethrough_visible_text(amount_text) + padding
    return padded_amount


def format_row_text_cell(text: str, width: int, *, is_paid: bool) -> str:
    visible_text = text[:width]
    padding = " " * max(width - len(visible_text), 0)
    if is_paid:
        return strikethrough_visible_text(visible_text) + padding
    return visible_text + padding


def build_boxed_list(rows: list[DebtRow]) -> str:
    shown = rows
    date_width = max(len("Date"), max(len(str(row["entry_code"])) for row in shown))
    amount_width = max(len("Amount"), max(len(format_row_amount(row)) for row in shown))
    reason_width = 10
    header = f"{'Date':<{date_width}}|{'Amount':<{amount_width}}|{'Reason':<{reason_width}}"
    separator = f"{'-' * date_width}+{'-' * amount_width}+{'-' * reason_width}"
    lines = [header, separator]
    reason_indent = " " * (date_width + 1 + amount_width)

    for row in shown:
        first_reason, extra_reason_lines = split_reason_lines(str(row["reason"]), reason_width, len(header))
        amount_text = format_row_amount_cell(row, amount_width)
        reason_text = format_row_text_cell(first_reason, reason_width, is_paid=bool(row["is_paid"]))
        lines.append(
            f"{row['entry_code']:<{date_width}}|"
            f"{amount_text}|"
            f"{reason_text}"
        )
        for extra_reason_line in extra_reason_lines:
            extra_reason_text = format_row_text_cell(extra_reason_line, reason_width, is_paid=bool(row["is_paid"]))
            lines.append(f"{reason_indent}|{extra_reason_text}")

    return "```text\n" + "\n".join(lines) + "\n```"


def build_boxed_all_list(rows: list[DebtRow]) -> str:
    shown = rows
    date_width = max(len("Date"), max(len(str(row["entry_code"])) for row in shown))
    amount_width = max(len("Amount"), max(len(format_row_amount(row)) for row in shown))
    name_width = max(len("Name"), max(len(normalize_spaces(str(row["name"]))) for row in shown))
    reason_width = 10
    header = (
        f"{'Date':<{date_width}}|"
        f"{'Amount':<{amount_width}}|"
        f"{'Name':<{name_width}}|"
        f"{'Reason':<{reason_width}}"
    )
    separator = (
        f"{'-' * date_width}+"
        f"{'-' * amount_width}+"
        f"{'-' * name_width}+"
        f"{'-' * reason_width}"
    )
    lines = [header, separator]
    reason_indent = " " * (date_width + 1 + amount_width + 1 + name_width)

    for row in shown:
        amount_text = format_row_amount_cell(row, amount_width)
        name_text = normalize_spaces(str(row["name"]))
        first_reason, extra_reason_lines = split_reason_lines(str(row["reason"]), reason_width, len(header))
        paid_row = bool(row["is_paid"])
        name_cell = format_row_text_cell(name_text, name_width, is_paid=paid_row)
        reason_cell = format_row_text_cell(first_reason, reason_width, is_paid=paid_row)
        lines.append(
            f"{row['entry_code']:<{date_width}}|"
            f"{amount_text}|"
            f"{name_cell}|"
            f"{reason_cell}"
        )
        for extra_reason_line in extra_reason_lines:
            extra_reason_text = format_row_text_cell(extra_reason_line, reason_width, is_paid=paid_row)
            lines.append(f"{reason_indent}|{extra_reason_text}")

    return "```text\n" + "\n".join(lines) + "\n```"


def build_person_list_content(person_name: str, rows: list[DebtRow], page_index: int) -> str:
    page_rows = slice_page(rows, page_index)
    boxed_list = build_boxed_list(page_rows)
    total = sum(float(row["amount"]) for row in rows if not bool(row["is_paid"]))
    lines = [
        f"Utang list for **{person_name}**",
        boxed_list,
        f"Total: **{format_peso(total)}**",
    ]
    if len(rows) > MAX_LIST_ROWS:
        start = page_index * MAX_LIST_ROWS + 1
        end = min((page_index + 1) * MAX_LIST_ROWS, len(rows))
        lines.append(f"Showing {start}-{end} of {len(rows)} entries.")
    return "\n".join(lines)


def build_all_list_content(rows: list[DebtRow], page_index: int) -> str:
    page_rows = slice_page(rows, page_index)
    boxed_list = build_boxed_all_list(page_rows)
    lines = [boxed_list]
    if len(rows) > MAX_LIST_ROWS:
        start = page_index * MAX_LIST_ROWS + 1
        end = min((page_index + 1) * MAX_LIST_ROWS, len(rows))
        lines.append(f"Showing {start}-{end} of {len(rows)} entries.")
    return "\n".join(lines)


def build_all_list_snapshot_contents(rows: list[DebtRow]) -> list[str]:
    if not rows:
        return ["No utang."]

    chunks: list[list[DebtRow]] = []
    current_chunk: list[DebtRow] = []

    for row in rows:
        candidate_chunk = current_chunk + [row]
        candidate_content = build_boxed_all_list(candidate_chunk)
        if current_chunk and len(candidate_content) > DISCORD_MESSAGE_SOFT_LIMIT:
            chunks.append(current_chunk)
            current_chunk = [row]
        else:
            current_chunk = candidate_chunk

    if current_chunk:
        chunks.append(current_chunk)

    if len(chunks) == 1:
        return [build_boxed_all_list(chunks[0])]

    total_chunks = len(chunks)
    return [
        f"Page {index}/{total_chunks}\n{build_boxed_all_list(chunk)}"
        for index, chunk in enumerate(chunks, start=1)
    ]


def build_pager_custom_id(mode: str, page_index: int, issued_at: datetime) -> str:
    issued_at_seconds = int(issued_at.timestamp())
    return f"pager|{mode}|{page_index}|{issued_at_seconds}"


def parse_pager_custom_id(custom_id: str) -> Optional[tuple[str, int, datetime]]:
    match = re.fullmatch(r"pager\|(all|person)\|(\d+)\|(\d+)", custom_id)
    if not match:
        return None

    mode, page_text, issued_at_text = match.groups()
    return (
        mode,
        int(page_text),
        datetime.fromtimestamp(int(issued_at_text), tz=timezone.utc),
    )


def pager_expired(interaction_dt: datetime, issued_at: datetime) -> bool:
    return interaction_dt > issued_at + timedelta(seconds=BUTTON_TIMEOUT_SECONDS)


def build_list_components(mode: str, page_index: int, total_rows: int, issued_at: datetime) -> list[dict[str, Any]]:
    total_pages = page_count_for(total_rows)
    buttons: list[dict[str, Any]] = []

    if page_index > 0:
        buttons.append(
            {
                "type": 2,
                "style": 2,
                "label": "Prev",
                "custom_id": build_pager_custom_id(mode, page_index - 1, issued_at),
            }
        )
    if page_index < total_pages - 1:
        buttons.append(
            {
                "type": 2,
                "style": 2,
                "label": "Next",
                "custom_id": build_pager_custom_id(mode, page_index + 1, issued_at),
            }
        )

    if not buttons:
        return []

    return [{"type": 1, "components": buttons}]


def extract_person_name_from_list_content(content: str) -> Optional[str]:
    first_line = content.splitlines()[0] if content else ""
    match = re.fullmatch(r"Utang list for \*\*(.+)\*\*", first_line)
    if not match:
        return None
    return match.group(1)


def is_list_message_content(content: str) -> bool:
    return any(content.startswith(prefix) for prefix in LIST_MESSAGE_PREFIXES) or is_all_list_message_content(content)


def is_all_list_message_content(content: str) -> bool:
    return content.startswith(ALL_LIST_PREFIX) or content.startswith(ALL_LIST_BOX_PREFIX)


def is_person_list_message_content(content: str) -> bool:
    return is_list_message_content(content) and not is_all_list_message_content(content)
