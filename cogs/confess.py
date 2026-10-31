"""Anonymous confessions — slash commands only.

  /confess <message>        — post anonymously to the staff-set channel.
  /confess_setup <channel>  — staff-only: set the confession channel.

PRIVACY (critical, never weaken this):
  * The confession embed carries no author name, avatar, mention, ID,
    or footer that could identify the sender.
  * The ephemeral confirmation never echoes the message back in a way
    that links it publicly; it is a generic "posted" notice.
  * The database stores only a truncated SHA-256 hash of
    (fixed salt + user_id) for the 10-minute rate limit. There is no
    raw user_id column on the confessions table, and no log line ever
    prints a user ID, username, or hash for a confession.
  * Rate limit: 1 confession per 10 minutes per user (per guild),
    enforced on the hash. A hit gets an ephemeral "slow down" reply.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import db
from staff import require_staff

# Fixed salt string (not a secret, not a token): it only makes the stored
# hash non-trivially reversible from the hash alone. Changing it resets
# everyone's rate-limit window, which is harmless.
_SALT = "newr-social-confession-v1"
_RATE_LIMIT_SECONDS = 10 * 60
_MAX_LENGTH = 2000


def _author_hash(user_id: int) -> str:
    return hashlib.sha256(f"{_SALT}:{user_id}".encode()).hexdigest()[:32]


class Confess(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="confess", description="Post an anonymous confession.")
    @app_commands.describe(message="Your confession (posted anonymously, max 2000 characters).")
    async def confess(self, interaction: discord.Interaction, message: str) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Confessions only work inside a server.", ephemeral=True)
                return
            text = (message or "").strip()
            if not text:
                await interaction.response.send_message(
                    "Your confession cannot be empty.", ephemeral=True)
                return
            if len(text) > _MAX_LENGTH:
                await interaction.response.send_message(
                    f"Your confession is too long (max {_MAX_LENGTH} characters).",
                    ephemeral=True)
                return
            channel_id = db.get_int_setting(interaction.guild.id, "confession_channel")
            if channel_id is None:
                await interaction.response.send_message(
                    "No confession channel has been set yet. Ask staff to run "
                    "/confess_setup first.", ephemeral=True)
                return
            author_hash = _author_hash(interaction.user.id)
            # Rate limit: 1 per 10 minutes, by hash only.
            try:
                with db._conn() as conn:
                    row = conn.execute(
                        "SELECT last_at FROM confess_rate WHERE author_hash=? AND guild_id=?",
                        (author_hash, interaction.guild.id)).fetchone()
                if row is not None:
                    try:
                        last = datetime.fromisoformat(str(row["last_at"]).replace("Z", "+00:00"))
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                    except Exception:
                        elapsed = _RATE_LIMIT_SECONDS + 1
                    if elapsed < _RATE_LIMIT_SECONDS:
                        remaining = int(_RATE_LIMIT_SECONDS - elapsed)
                        mins, secs = divmod(remaining, 60)
                        await interaction.response.send_message(
                            f"Slow down — you can send another confession in "
                            f"{mins}m {secs}s.", ephemeral=True)
                        return
            except Exception:
                pass  # rate-limit lookup failure must not block a confession
            channel = interaction.guild.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                try:
                    fetched = await self.bot.fetch_channel(channel_id)
                    channel = fetched if isinstance(fetched, discord.TextChannel) else None
                except Exception:
                    channel = None
            if channel is None:
                await interaction.response.send_message(
                    "The configured confession channel could not be found. "
                    "Ask staff to run /confess_setup again.", ephemeral=True)
                return
            embed = discord.Embed(
                title="Anonymous Confession",
                description=text,
                color=discord.Color.purple(),
                timestamp=datetime.now(timezone.utc))
            # Deliberately NO author, NO footer identifying the sender.
            try:
                posted = await channel.send(embed=embed)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to post in the confession channel.",
                    ephemeral=True)
                return
            except Exception:
                await interaction.response.send_message(
                    "Something went wrong while posting your confession. "
                    "Please try again later.", ephemeral=True)
                return
            try:
                with db._conn() as conn:
                    conn.execute(
                        "INSERT INTO confessions (guild_id, author_hash, text, channel_id, message_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (interaction.guild.id, author_hash, text, channel.id,
                         posted.id, db.now()))
                    conn.execute(
                        "INSERT INTO confess_rate VALUES (?, ?, ?) "
                        "ON CONFLICT(author_hash, guild_id) DO UPDATE SET last_at=excluded.last_at",
                        (author_hash, interaction.guild.id, db.now()))
            except Exception:
                pass  # storage failure must not un-post or leak anything
            # Ephemeral confirmation only — nothing public names the sender.
            await interaction.response.send_message(
                "Your confession has been posted anonymously.", ephemeral=True)
        except Exception:
            # Never crash the bot on bad input; never leak the sender.
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass
            print("[confess] confession handling failed (details withheld for privacy)")

    @app_commands.command(name="confess_setup", description="Staff only: set the confession channel.")
    @app_commands.describe(channel="Channel where anonymous confessions will be posted.")
    async def confess_setup(self, interaction: discord.Interaction,
                            channel: discord.TextChannel) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command only works inside a server.", ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "confession_channel", str(channel.id))
            await interaction.response.send_message(
                f"Confession channel set to {channel.mention}.", ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Confess(bot))
