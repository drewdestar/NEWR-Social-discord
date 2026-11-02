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
- **Starboard:** tracking star reactions (`on_raw_reaction_add` and
  `on_raw_reaction_remove`), including live board-post edits and
  removals.
- **Counting game:** watching numbers in the configured counting
  channel (`on_message`, counting channel only).
- **Daily birthday check:** a background task at 00:05 WIB, including
  birthday-role sync when a role is configured.

## Commands (15 total, all slash commands)

| Command | Who | What it does |
|---|---|---|
| `/confess <message> [image]` | Anyone | Post an anonymous confession to the staff-set channel. Ephemeral confirmation only. Rate-limited: 1 per 10 minutes per user. Optional image attachment (image files only) is shown in the embed. If staff review is enabled, the confession goes to the pending queue instead of posting directly. Rejected by the blocked-word list with a generic error. |
| `/confess_setup <channel>` | Staff only | Set the confession channel. |
| `/confess_vetting <enabled>` | Staff only | Turn staff review on/off (default OFF: direct posting, unchanged behaviour). |
| `/confess_pending` | Staff only | List confessions awaiting review (ephemeral; IDs, text previews, and dates only — no authors shown). |
| `/confess_review <id> <approve\|reject>` | Staff only | Approve a pending confession (posts it anonymously) or reject it (deletes it). Review is slash-only; there is no staff queue channel and no approve/reject buttons, by design. |
| `/confess_blockwords <add\|remove\|list> [word]` | Staff only | Manage the staff-set blocked-word list for confessions (stored in SQLite, matched case-insensitively with word boundaries). |
| `/starboard_setup <channel> [threshold] [emojis]` | Staff only | Set the starboard channel and the counted-star threshold (1–20, default 3). `emojis` is an optional space-separated list of extra emojis beyond ⭐ (e.g. `🌟 ✨`); ⭐ always counts. Omitting `emojis` keeps the current list. |
| `/starboard_top` | Anyone | Show the top 10 tracked starboard posts and top 5 starred users by their last tracked star counts (visible to the channel). |
| `/count_setup <channel>` | Staff only | Set the counting channel. The count starts at 1. |
| `/count` | Anyone | Show the current counting status (ephemeral): current number, next number, current streak, and last counter. |
| `/birthday_set <MM-DD>` | Anyone | Save your birthday, e.g. `05-21`. |
| `/birthday_remove` | Anyone | Remove your own saved birthday. |
| `/birthday_list` | Staff only | List saved birthdays (ephemeral, staff only). |
| `/birthday_upcoming` | Anyone | Show birthdays in the next 30 days (ephemeral). |
| `/birthday_setup <channel> [role]` | Staff only | Set the channel for daily birthday wishes (posted at 00:05 WIB) and optionally a birthday role. Omitting `role` keeps the previously set role. On the 00:05 WIB check the role is given to members whose birthday is today and removed from members whose birthday has passed (best-effort; failures are swallowed). |

Staff gate: guild owner, or Administrator permission, or Manage Guild
permission, or a staff role name (Founder, Executive, Admin, Senior
Mod, Moderator, Team Lead, Helper, Security, Staff, Support — matched
by name at runtime, never by hardcoded role ID). See `staff.py`.

## How the features behave

- **Starboard:** when a message reaches the threshold of counted
  star reactions (⭐ plus any staff-configured extra emojis, summed
  across all configured emojis), an embed copy (author, text, first
  image attachment, jump link) is posted to the starboard channel and
  tracked in the DB (`starboard_posts`: board post ID, author,
  channel, last star count). While tracked, a star-count change edits
  the board post to show the new count. When the count drops below
  threshold, the board post is deleted and the tracking row is
  cleared, so the message can be re-posted if it rises to threshold
  again. Both reaction-add and reaction-remove events are handled.
  Bot reactions and the bot's own messages are ignored, and messages
  already in the starboard channel are ignored.
- **Starboard leaderboard:** `/starboard_top` ranks tracked posts by
  their last stored star count and users by the sum of their tracked
  posts' counts. Counts are tracked from this upgrade onward: posts
  tracked before the star-count/author columns existed have no stored
  count, are backfilled on their next reaction event, and are not
  ranked until then.
- **Counting game:** in the configured counting channel only the
  exact next number counts (expected number + streak + last counter
  in the DB). The same user may not count twice in a row: a repeat
  count is ignored (no state change, not counted) and gets a gentle
  visible notice in the channel. A wrong *number* from a different
  user gets a gentle reset message (streak resets, expected goes back
  to 1, last counter cleared). The bot never deletes user messages,
  and non-numeric chat is ignored so it does not break the count.
  `/count` shows the current number, next number, streak, and last
  counter.
- **Confessions and vetting:** by default (vetting OFF) confessions
  post directly, exactly as before. When staff turn vetting on with
  `/confess_vetting`, new confessions go to the pending queue
  (`confess_pending` table) instead; staff list them with
  `/confess_pending` and approve/reject with `/confess_review`.
  Approving posts the confession anonymously and moves the row to
  `confessions`; rejecting deletes the pending row. If approval fails
  (no channel set, channel missing, or no permission), the pending
  confession is kept and staff are told so. The rate limit is counted
  at submission time, so queued confessions cannot be used to bypass it.
- **Confession images:** `/confess` accepts one optional image
  attachment, shown in the posted embed (or kept in the pending record
  under vetting, then shown on approval). Non-image attachments are
  rejected with an ephemeral error.
- **Confession block-words:** staff manage the list with
  `/confess_blockwords`. Matching is case-insensitive with
  word-boundary-like guards (blocking `cat` does not reject
  `concatenate`). A blocked confession is rejected with a generic
  ephemeral error that never names the blocked word, and the rejection
  does not consume the rate limit.
- **Birthdays:** stored in SQLite (`birthdays` table: guild, user,
  month, day). At 00:05 Asia/Jakarta (WIB) the bot posts one combined
  birthday wish per guild to the configured channel, if any birthdays
  fall on that day. If no birthday channel is set, wishes are skipped
  silently, but birthday-role sync still runs when a role is
  configured. Feb 29 birthdays are wished on Feb 29 in leap years.
  `/birthday_upcoming` lists birthdays in the next 30 days (anyone,
  ephemeral). `/birthday_remove` deletes only the caller's own row.
- **Bad input never crashes the bot:** every command and listener
  wraps its work in try/except and replies with a generic ephemeral
  error (or silently skips, for listeners) instead of raising.

## Confession privacy (critical)

- The confession embed contains **no** author name, avatar, mention,
  user ID, or footer identifying the sender, and the ephemeral
  confirmation is generic ("posted anonymously" / "submitted for
  review").
- The vetting queue shows staff **no** author information either:
  `/confess_pending` lists only pending IDs, text previews, and
  dates, and `/confess_review` never reveals the author. Staff review
  is deliberately slash-only (no queue channel, no buttons) so pending
  confessions are never posted anywhere before approval.
- The database stores **only a truncated SHA-256 hash** of
  (fixed salt string + user ID) for rate-limit/abuse purposes.
  There is **no raw user ID column** on the `confessions` or
  `confess_pending` tables, so a stored confession cannot be traced
  back to a user from the row.
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
   permissions (plus `View Channel` where its channels live, and
   `Manage Roles` if a birthday role is used — the bot's role must
   sit above the birthday role).
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
   confessions) or skips silently (for the birthday check). Staff
   optionally run `/confess_vetting` and `/confess_blockwords` to
   enable review and set blocked words.

## Persistence

SQLite `data.db` in the bot directory (created on first start by
`db.init_db()`), with tables: `settings`, `starboard_posts`,
`counting_state`, `confessions`, `confess_pending`,
`confess_blockwords`, `confess_rate`, `birthdays`. Upgrades to an
existing database are applied by `init_db()` (new columns added only
if missing).

## Proxy note

Direct TLS to Discord fails from this VM, so `bot.py` passes the
same `_proxy_kwargs()` pattern as `newr-support` (proxy from
`HTTPS_PROXY`/`https_proxy`, credentials from the environment only)
to the bot constructor.

## Files

- `bot.py` — entrypoint, guild-only slash sync on ready.
- `db.py` — SQLite helpers, schema, and migrations.
- `staff.py` — staff gate (permission + role-name check).
- `cogs/confess.py` — `/confess`, `/confess_setup`,
  `/confess_vetting`, `/confess_pending`, `/confess_review`,
  `/confess_blockwords`.
- `cogs/starboard.py` — `/starboard_setup`, `/starboard_top` +
  reaction listeners (live edit/remove).
- `cogs/counting.py` — `/count_setup`, `/count` + counting listener.
- `cogs/birthday.py` — `/birthday_set`, `/birthday_remove`,
  `/birthday_list`, `/birthday_upcoming`, `/birthday_setup` +
  00:05 WIB daily check (wishes + birthday-role sync).
