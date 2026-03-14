# Discord Small Debt Bot

This repo is now an interactions-only Discord bot built for free hosting:

- Discord outgoing webhooks instead of a 24/7 gateway connection
- FastAPI serverless app for Vercel
- Postgres storage for cloud persistence
- Same slash commands and list formatting as the old bot, as closely as possible

## What stayed the same

- `/utang`
- `/list`
- `/paid`
- `/unpaid`
- `/remove`
- `/clear`
- Manila-based date codes like `03-14-26-G3`
- Person list totals
- Latest-first boxed tables
- Paid amounts struck through in the person list
- 30-day `1% interest 30 days after` entries
- Name and date autocomplete
- Clearing previous bot list messages in the channel before showing the new one

## What changed

- The bot no longer logs into Discord through the gateway.
- Discord sends interactions to your HTTPS endpoint instead.
- The database is now Postgres through `DATABASE_URL`.
- Paging buttons are stateless so they work in serverless deployments.
- Button rows expire after 30 minutes when clicked again, then the buttons disappear.

## Stack

- Hosting: Vercel free tier
- Runtime: FastAPI + Python
- Database: any managed Postgres URL
- Good free options: Neon or Supabase Postgres

## Environment variables

```bash
DISCORD_APPLICATION_ID=your_application_id_here
DISCORD_PUBLIC_KEY=your_interactions_public_key_here
DISCORD_TOKEN=your_bot_token_here
DATABASE_URL=postgresql://postgres:password@db.example.com:5432/postgres?sslmode=require
DISCORD_GUILD_ID=your_server_id_here
BOT_TIMEZONE=Asia/Manila
HOST=0.0.0.0
PORT=8000
```

`DISCORD_GUILD_ID` is optional but recommended while you are iterating so command updates appear quickly.

## Local setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in your Discord and Postgres values.

3. Run the local webhook server:

```bash
python bot.py
```

4. Expose it with an HTTPS tunnel like `ngrok` or `cloudflared`.

5. In Discord Developer Portal:
   - open your application
   - go to `General Information`
   - copy the `Public Key` into `.env`
   - go to `Interactions Endpoint URL`
   - set it to your public HTTPS URL
   - Discord will validate the signature automatically

6. Register slash commands:

```bash
python scripts/register_commands.py
```

If `DISCORD_GUILD_ID` is set, commands register to that guild. Otherwise they register globally.

## Deploying to Vercel

1. Push this repo to GitHub.
2. Create a Vercel project from the repo.
3. Add the environment variables above in Vercel.
4. Deploy.
5. Set the Discord `Interactions Endpoint URL` to:

```text
https://your-project.vercel.app/api
```

6. Run command registration once:

```bash
python scripts/register_commands.py
```

## Migrating the old SQLite data

If you want to keep the current `debts.db`, point `DATABASE_URL` at your cloud Postgres and run:

```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite-path debts.db
```

The script reads the local SQLite rows and inserts them into Postgres while preserving:

- names
- date codes
- paid state
- amount scale
- interest flags

## Commands

- `/utang name:<name> amount:<amount> reason:<reason> [date_time]`
- `/list`
- `/list name:<name>`
- `/paid name:<name> date:<date>`
- `/unpaid name:<name> date:<date>`
- `/remove name:<name> date:<date>`
- `/clear name:<name>`

## Notes

- Positive amount means they owe you.
- Negative amount means you owe them.
- `/utang amount:` accepts `11`, `-1`, `11.5`, or `11.50`.
- `/utang date_time:` accepts examples like `2026-03-14 20:30` or `03-14-26 8:30pm`.
- `/list` shows everyone latest-first and does not total the all-people view.
- Person-specific renders still trigger the 30-day interest check.
- `/clear` removes only unpaid entries and replies ephemerally.
- The webhook server expects Discord-signed requests, so `DISCORD_PUBLIC_KEY` is required.
