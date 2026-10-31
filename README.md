# NEWR Social

A social-features Discord bot for the NEWR LABS server (guild ID
`1465422166790242377`). All user-facing text is in English.

## Slash commands only — no prefix commands

**Every command in this bot is a Discord slash command**
(`discord.app_commands`, guild-synced to NEWR LABS only). There is no
prefix, no `command_prefix`, and no feature that requires typing a
prefix. The bot is created with `command_prefix=None` and defines no
`@commands.command` anywhere.

The only behaviour that is not a slash command is passive and
automatic — it cannot be invoked by a user typing anything:
- **Starboard:** tracking ⭐ reactions (`on_raw_reaction_add`).
- **Counting game:** watching numbers in the configured counting
  channel (`on_message`, counting channel only).
- **Daily birthday check:** a background task at 00:05 WIB.

## Commands (7 total, all slash commands)

| Command | Who | What it does |
|---|---|---|
| `/confess <message>` | Anyone | Post an anonymous confession to the staff-set channel. Ephemeral confirmation only. Rate-limited: 1 per 10 minutes per user. |
| `/confess_setup <channel>` | Staff only | Set the confession channel. |
| `/starboard_setup <channel> [threshold]` | Staff only | Set the starboard channel and the ⭐ threshold (1–20, default 3). |
| `/count_setup <channel>` | Staff only | Set the counting channel. The count starts at 1. |
| `/birthday_set <MM-DD>` | Anyone | Save your birthday, e.g. `05-21`. |
| `/birthday_list` | Staff only | List saved birthdays (ephemeral, staff only). |
| `/birthday_setup <channel>` | Staff only | Set the channel for daily birthday wishes (posted at 00:05 WIB). |

Staff gate: guild owner, or Administrator permission, or Manage Guild
permission, or a staff role name (Founder, Executive, Admin, Senior
Mod, Moderator, Team Lead, Helper, Security, Staff, Support — matched
by name at runtime, never by hardcoded role ID). See `staff.py`.

## How the features behave

- **Starboard:** when a message reaches the threshold of ⭐ reactions,
  an embed copy (author, text, first image attachment, jump link) is
  posted to the starboard channel exactly once — posted message IDs
  are tracked in the DB. Bot reactions and the bot's own messages are
  ignored, and messages already in the starboard channel are ignored.
- **Counting game:** in the configured counting channel only the
  exact next number counts (expected number + streak in the DB).
  A wrong *number* gets a gentle reset message (streak resets,
  expected goes back to 1). The bot never deletes user messages, and
  non-numeric chat is ignored so it does not break the count.
- **Birthdays:** stored in SQLite (`birthdays` table: guild, user,
  month, day). At 00:05 Asia/Jakarta (WIB) the bot posts one combined
  birthday wish per guild to the configured channel, if any birthdays
  fall on that day. If no birthday channel is set, it skips silently.
  Feb 29 birthdays are wished on Feb 29 in leap years.
- **Bad input never crashes the bot:** every command and listener
  wraps its work in try/except and replies with a generic ephemeral
  error (or silently skips, for listeners) instead of raising.

## Confession privacy (critical)

- The confession embed contains **no** author name, avatar, mention,
  user ID, or footer identifying the sender, and the ephemeral
  confirmation is generic ("posted anonymously").
- The database stores **only a truncated SHA-256 hash** of
  (fixed salt string + user ID) for rate-limit/abuse purposes.
  There is **no raw user ID column** on the `confessions` table, so
  a stored confession cannot be traced back to a user from the row.
- No log line ever prints a confession author's user ID, username,
  or hash. Log lines for confessions are generic ("handling failed").
- The rate limit is 1 confession per 10 minutes per user, per guild,
  enforced on the hash. A hit gets an ephemeral "slow down" reply
  with the remaining time.
- Birthdays are *not* anonymous: they are listed only to staff
  (`/birthday_list`, ephemeral) or wished publicly in the configured
  birthday channel, which is the point of that feature.

## Setup

1. Create a Discord application and bot (Developer Portal), enable
   the **Server Members Intent** and **Message Content Intent**
   (reactions are covered by the default intents), and invite the bot
   to NEWR LABS with `applications.commands`, `Send Messages`,
   `Embed Links`, `Read Message History`, and `Add Reactions`
   permissions (plus `View Channel` where its channels live).
2. Copy `.env.example` to `.env` and put the bot token in
   `DISCORD_TOKEN` (`.env` is chmod 600; never commit it, never paste
   the token in chat). `GUILD_ID` defaults to NEWR LABS
   (`1465422166790242377`).
3. Run it (foreground, logs to `~/workspace/bots/logs/newr-social.log`):

   ```bash
   ~/workspace/bots/ops/run-newr-social.sh
   ```

   The script creates `~/workspace/bots/newr-social/.venv` if needed,
   installs `requirements.txt`, refuses to start if `DISCORD_TOKEN`
   is empty or a placeholder, and runs `python -u bot.py` from the
   bot directory so `.env` and `data.db` resolve correctly.
4. In Discord, staff run `/confess_setup`, `/starboard_setup`,
   `/count_setup`, and `/birthday_setup` to point each feature at a
   channel. Until a channel is set, that feature replies (for
   confessions) or skips silently (for the birthday check).

## Persistence

SQLite `data.db` in the bot directory (created on first start by
`db.init_db()`), with tables: `settings`, `starboard_posts`,
`counting_state`, `confessions`, `confess_rate`, `birthdays`.

## Proxy note

Direct TLS to Discord fails from this VM, so `bot.py` passes the
same `_proxy_kwargs()` pattern as `newr-support` (proxy from
`HTTPS_PROXY`/`https_proxy`, credentials from the environment only)
to the bot constructor.

## Files

- `bot.py` — entrypoint, guild-only slash sync on ready.
- `db.py` — SQLite helpers and schema.
- `staff.py` — staff gate (permission + role-name check).
- `cogs/confess.py` — `/confess`, `/confess_setup`.
- `cogs/starboard.py` — `/starboard_setup` + reaction listener.
- `cogs/counting.py` — `/count_setup` + counting listener.
- `cogs/birthday.py` — `/birthday_set`, `/birthday_list`,
  `/birthday_setup` + 00:05 WIB daily check.
