# Discord Small Debt Bot

This bot is back on the simpler Discord gateway setup:

- no public webhook URL
- no custom domain
- no `Interactions Endpoint URL`
- local SQLite storage again
- slash commands still work through `discord.py`

## Features

- `/grade`
- saved subject profiles like `Thermo`
- C++-style grade setup with mode, target grade, categories, and exam total
- required exam score lookup from saved subjects
- `/utang`
- `/list`
- `/paid`
- `/unpaid`
- `/remove`
- `/clear`
- Manila-based date codes like `03-14-26-G3`
- person list totals
- latest-first boxed tables
- paid amounts struck through in the person list
- 30-day `1% interest 30 days after` entries
- name and date autocomplete
- previous bot list messages get cleared before the next list is shown
- existing `debts.db` data can keep working

## Environment variables

```bash
DISCORD_TOKEN=your_bot_token_here
DEBT_DB_PATH=debts.db
DEBT_DB_SEED_PATH=seed_debts.db
DISCORD_GUILD_ID=your_server_id_here
BOT_TIMEZONE=Asia/Manila
```

`DISCORD_GUILD_ID` is optional but recommended while you are iterating so slash-command updates appear right away in one server.

`DEBT_DB_PATH` defaults to `debts.db`.

`DEBT_DB_SEED_PATH` defaults to `seed_debts.db`. If the main database file does not exist yet and the seed file is present, the bot copies the seed file first.

## Local setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in your token.

3. Run:

```bash
python bot.py
```

4. Invite the bot to your server with permission to:
   - read messages
   - read message history
   - send messages
   - use slash commands

On startup, the bot syncs slash commands automatically. If `DISCORD_GUILD_ID` is set, it syncs to that guild for faster updates. Otherwise it syncs globally.

## Wispbyte setup

This version is meant to be easier on free bot hosting:

1. Put your real token in `.env`
2. Keep the startup command pointing at `bot.py`
3. Restart the server
4. Do not set an `Interactions Endpoint URL` in Discord

If you want your old records on Wispbyte, upload `debts.db` or `seed_debts.db` manually. The repo ignores `*.db`, so GitHub clone alone will not carry your saved debt data.

## Commands

- `/grade score:<score> total:<total>`
- `/grade-save subject:<subject> mode:<prelim|term> target_grade:<percent> exam_total:<total> categories:<30|18/20,19/20;70|45/50> [previous_term_average]`
- `/grade-show subject:<subject>`
- `/grade-required subject:<subject>`
- `/utang name:<name> amount:<amount> reason:<reason> [date_time]`
- `/list`
- `/list name:<name>`
- `/paid name:<name> date:<date>`
- `/unpaid name:<name> date:<date>`
- `/remove name:<name> date:<date>`
- `/clear name:<name>`

## Notes

- `categories:` uses the format `weight|score/total,score/total;weight|score/total`
- Example: `30|18/20,19/20;70|45/50`
- Positive amount means they owe you.
- Negative amount means you owe them.
- `/utang amount:` accepts `11`, `-1`, `11.5`, or `11.50`.
- `/utang date_time:` accepts examples like `2026-03-14 20:30` or `03-14-26 8:30pm`.
- `/list` shows everyone latest-first and does not total the all-people view.
- Person-specific renders still trigger the 30-day interest check.
- `/clear` removes only unpaid entries and replies privately.
- Paging buttons time out after 30 minutes.
