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
        character + "\u0336" if character == PESO_SIGN or character.isalnum() else character
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
    first_lines = wrap_cell(normalized, first_width)
    first_line = first_lines[0]
    if not first_line:
        return "", []

    remainder = normalized[len(first_line):].lstrip()
    if not remainder:
        return first_line, []

    return first_line, wrap_cell(remainder, full_width)


def format_row_amount(row: DebtRow) -> str:
    return format_peso(row["amount"], int(row["amount_scale"] or 0))


def escape_markdown_text(text: str) -> str:
    return re.sub(r"([\\`*_~|])", r"\\\1", text)


def build_markdown_row(row: DebtRow, *, include_name: bool) -> str:
    parts = [format_row_amount(row)]
    if include_name:
        parts.append(normalize_spaces(str(row["name"])))
    parts.append(normalize_spaces(str(row["reason"])))

    details = " - ".join(escape_markdown_text(part) for part in parts if part)
    if bool(row["is_paid"]):
        details = f"~~{details}~~"
    return f"- `{row['entry_code']}` - {details}"


def join_markdown_lines(title: str, row_lines: list[str], *, suffix_lines: Optional[list[str]] = None) -> str:
    lines = [title]
    lines.extend(row_lines)
    if suffix_lines:
        lines.append("")
        lines.extend(suffix_lines)
    return "\n".join(lines)


def chunk_markdown_rows(title: str, row_lines: list[str]) -> list[str]:
    if not row_lines:
        return [f"{title}\nNo utang."]

    reserve = len(title) + 12
    limit = max(200, DISCORD_MESSAGE_SOFT_LIMIT - reserve)
    chunks: list[list[str]] = []
    current_chunk: list[str] = []
    current_length = 0

    for row_line in row_lines:
        addition = len(row_line) + 1
        if current_chunk and current_length + addition > limit:
            chunks.append(current_chunk)
            current_chunk = [row_line]
            current_length = addition
        else:
            current_chunk.append(row_line)
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
    return f"{amount_text:<{width}}"


def format_row_text_cell(text: str, width: int, *, is_paid: bool) -> str:
    visible_text = text[:width]
    return f"{visible_text:<{width}}"


def build_boxed_list(rows: list[DebtRow]) -> str:
    shown = rows
    date_width = max(len("Date"), max(len(str(row["entry_code"])) for row in shown))
    amount_width = max(len("Amount"), max(len(format_row_amount(row)) for row in shown))
    reason_width = 14
    show_status = any(bool(row["is_paid"]) for row in shown)
    status_width = len("Status")
    header = f"{'Date':<{date_width}}|{'Amount':<{amount_width}}|{'Reason':<{reason_width}}"
    separator = f"{'-' * date_width}+{'-' * amount_width}+{'-' * reason_width}"
    if show_status:
        header += f"|{'Status':<{status_width}}"
        separator += f"+{'-' * status_width}"
    lines = [header, separator]
    full_width = len(header)

    for row in shown:
        first_reason, extra_reason_lines = split_reason_lines(str(row["reason"]), reason_width, full_width)
        amount_text = format_row_amount_cell(row, amount_width)
        reason_text = format_row_text_cell(first_reason, reason_width, is_paid=False)
        line = (
            f"{row['entry_code']:<{date_width}}|"
            f"{amount_text}|"
            f"{reason_text}"
        )
        if show_status:
            status_text = "PAID" if bool(row["is_paid"]) else ""
            line += f"|{status_text:<{status_width}}"
        lines.append(line)
        lines.extend(extra_reason_lines)

    return "```text\n" + "\n".join(lines) + "\n```"


def build_boxed_all_list(rows: list[DebtRow]) -> str:
    shown = rows
    date_width = max(len("Date"), max(len(str(row["entry_code"])) for row in shown))
    amount_width = max(len("Amount"), max(len(format_row_amount(row)) for row in shown))
    name_width = min(8, max(4, max(len(str(row["name"])) for row in shown)))
    reason_width = 10
    show_status = any(bool(row["is_paid"]) for row in shown)
    status_width = len("Status")
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
    if show_status:
        header += f"|{'Status':<{status_width}}"
        separator += f"+{'-' * status_width}"
    lines = [header, separator]
    full_width = len(header)

    for row in shown:
        amount_text = format_row_amount_cell(row, amount_width)
        name_text = clip_text(str(row["name"]), name_width)
        first_reason, extra_reason_lines = split_reason_lines(str(row["reason"]), reason_width, full_width)
        paid_row = bool(row["is_paid"])
        name_cell = format_row_text_cell(name_text, name_width, is_paid=False)
        reason_cell = format_row_text_cell(first_reason, reason_width, is_paid=False)
        line = (
            f"{row['entry_code']:<{date_width}}|"
            f"{amount_text}|"
            f"{name_cell}|"
            f"{reason_cell}"
        )
        if show_status:
            status_text = "PAID" if paid_row else ""
            line += f"|{status_text:<{status_width}}"
        lines.append(line)
        lines.extend(extra_reason_lines)

    return "```text\n" + "\n".join(lines) + "\n```"


def build_person_list_content(person_name: str, rows: list[DebtRow], page_index: int) -> str:
    page_rows = slice_page(rows, page_index)
    row_lines = [build_markdown_row(row, include_name=False) for row in page_rows]
    total = sum(float(row["amount"]) for row in rows if not bool(row["is_paid"]))
    suffix_lines = [f"Total: **{format_peso(total)}**"]
    if len(rows) > MAX_LIST_ROWS:
        start = page_index * MAX_LIST_ROWS + 1
        end = min((page_index + 1) * MAX_LIST_ROWS, len(rows))
        suffix_lines.append(f"Showing {start}-{end} of {len(rows)} entries.")
    return join_markdown_lines(f"Utang list for **{person_name}**", row_lines, suffix_lines=suffix_lines)


def build_all_list_content(rows: list[DebtRow], page_index: int) -> str:
    page_rows = slice_page(rows, page_index)
    row_lines = [build_markdown_row(row, include_name=True) for row in page_rows]
    suffix_lines: list[str] = []
    if len(rows) > MAX_LIST_ROWS:
        start = page_index * MAX_LIST_ROWS + 1
        end = min((page_index + 1) * MAX_LIST_ROWS, len(rows))
        suffix_lines.append(f"Showing {start}-{end} of {len(rows)} entries.")
    return join_markdown_lines("All utang", row_lines, suffix_lines=suffix_lines or None)


def build_all_list_snapshot_contents(rows: list[DebtRow]) -> list[str]:
    row_lines = [build_markdown_row(row, include_name=True) for row in rows]
    return chunk_markdown_rows(ALL_LIST_PREFIX, row_lines)


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
    return any(content.startswith(prefix) for prefix in LIST_MESSAGE_PREFIXES)


def is_all_list_message_content(content: str) -> bool:
    return content.startswith(ALL_LIST_PREFIX)


def is_person_list_message_content(content: str) -> bool:
    return is_list_message_content(content) and not is_all_list_message_content(content)
