"""NEWR Social bot entrypoint. Config comes only from environment variables.

Required: DISCORD_TOKEN.
Optional: GUILD_ID (defaults to NEWR LABS, 1465422166790242377),
          DB_PATH (defaults to data.db next to this file).
Run:  ~/workspace/bots/ops/run-newr-social.sh
      (or: pip install -r requirements.txt && python3 bot.py)

ALL user-triggered actions are Discord slash commands (app_commands),
guild-synced to NEWR LABS only. There is NO prefix command, NO
command_prefix, and NO feature that requires typing a prefix: the bot
is created with command_prefix=None and no @commands.command is
defined anywhere. The only non-command behaviour is passive and
automatic: starboard reaction tracking (on_raw_reaction_add),
the counting game (on_message in the configured channel only), and
the daily birthday check at 00:05 WIB. None of those can be invoked
as a command by a user.
"""
from __future__ import annotations

import asyncio
import os
import sys

import discord
from discord.ext import commands

try:
    from dotenv import load_dotenv  # optional, only if installed
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

COGS = ["cogs.confess", "cogs.starboard", "cogs.counting", "cogs.birthday"]
DEFAULT_GUILD_ID = 1465422166790242377  # NEWR LABS

try:
    GUILD_ID = int(os.environ["GUILD_ID"]) if os.environ.get("GUILD_ID") else DEFAULT_GUILD_ID
except ValueError:
    GUILD_ID = DEFAULT_GUILD_ID


def _proxy_kwargs() -> dict:
    """VM egress goes through an HTTP proxy; direct TLS to Discord fails
    here (SSL WRONG_VERSION_NUMBER), while aiohttp with trust_env works.
    discord.py does not read proxy env vars itself, so pass them explicitly.
    Copied from newr-support's _proxy_kwargs(). Credentials stay in the
    environment, never in code or logs."""
    from urllib.parse import unquote, urlparse

    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if not raw:
        return {}
    parsed = urlparse(raw)
    proxy = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 3128}"
    kwargs: dict = {"proxy": proxy}
    if parsed.username:
        import aiohttp

        kwargs["proxy_auth"] = aiohttp.BasicAuth(
            unquote(parsed.username),
            unquote(parsed.password or ""))
    return kwargs


class SocialBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True  # privileged: enable in Developer Portal
        intents.messages = True
        intents.message_content = True  # privileged: needed for counting
        intents.reactions = True  # needed for starboard ⭐ tracking
        # command_prefix=None: slash commands only, no prefix commands.
        super().__init__(command_prefix=None, intents=intents, help_command=None,
                         **_proxy_kwargs())
        self._synced = False

    async def setup_hook(self) -> None:
        db.init_db()
        for cog in COGS:
            await self.load_extension(cog)

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (id={self.user.id})")
        if self._synced:
            return
        self._synced = True
        guild = discord.Object(id=GUILD_ID)
        synced = await self.tree.sync(guild=guild)
        print(f"Synced {len(synced)} slash commands to guild {GUILD_ID}")

    async def on_app_command_error(self, interaction: discord.Interaction,
                                   error: app_commands.AppCommandError) -> None:
        # A failed slash command must never crash the bot.
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong. Please try again later.", ephemeral=True)
        except Exception:
            pass
        print(f"[error] slash command failed: {type(error).__name__}")


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. See .env.example.")
    asyncio.run(SocialBot().start(token))


if __name__ == "__main__":
    main()
