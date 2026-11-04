# NEWR Social

Social features for NEWR LABS. Slash commands only.

Confessions (anonymous, with an optional staff vetting queue and a
blocked-word list), starboard (messages that hit the star threshold
get reposted to a board channel), a counting game channel, and
birthdays (one combined wish per day at 00:05 WIB, plus an optional
birthday role).

Confessions are anonymous by design: no author info in the embed or
in the vetting queue, and only a hashed ID is stored for rate
limiting (1 confession per 10 minutes per user). Birthdays are the
opposite, they're meant to be public.

Staff point each feature at a channel with `/confess_setup`,
`/starboard_setup`, `/count_setup`, and `/birthday_setup`. Until a
channel is set, that feature just stays quiet.

Run it:

```bash
~/workspace/bots/ops/run-newr-social.sh
```

Token goes in `.env` (`DISCORD_TOKEN`, `GUILD_ID` is NEWR LABS). The
script sets up the venv, refuses to start on an empty token, and logs
to `~/workspace/bots/logs/newr-social.log`. Data lives in `data.db`
(SQLite, local).
