"""Birthdays — slash commands only, plus a passive daily check.

  /birthday_set <MM-DD>       — save your own birthday.
  /birthday_list              — staff-only: list saved birthdays.
  /birthday_setup <channel>   — staff-only: set the birthday channel.

The daily check is NOT a command: it runs automatically at 00:05
Asia/Jakarta (WIB) and, only if a birthday channel is configured,
posts one combined birthday wish per guild for members whose
month/day matches today in WIB. If no birthday channel is set, it
skips silently. Birthdays are not anonymous (unlike confessions):
they are only listed to staff or wished publicly in the configured
channel, which is the point of the feature.
"""
from __future__ import annotations

import re
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from staff import require_staff

_WIB = ZoneInfo("Asia/Jakarta")
_MMDD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
# Days per month; February allows 29 (leap-day birthdays are wished
# on Feb 29 in leap years, and skipped silently in non-leap years).
_DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


class Birthday(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.daily_birthday_check.start()

    async def cog_unload(self) -> None:
        self.daily_birthday_check.cancel()

    @app_commands.command(name="birthday_set", description="Save your birthday (MM-DD).")
    @app_commands.describe(date="Your birthday as MM-DD, e.g. 05-21.")
    async def birthday_set(self, interaction: discord.Interaction, date: str) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Birthdays only work inside a server.", ephemeral=True)
                return
            raw = (date or "").strip()
            match = _MMDD.match(raw)
            if not match:
                await interaction.response.send_message(
                    "Please use the MM-DD format, e.g. 05-21.", ephemeral=True)
                return
            month, day = int(match.group(1)), int(match.group(2))
            if day > _DAYS_IN_MONTH[month]:
                await interaction.response.send_message(
                    f"{raw} is not a valid date.", ephemeral=True)
                return
            with db._conn() as conn:
                conn.execute(
                    "INSERT INTO birthdays VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(guild_id, user_id) DO UPDATE SET month=excluded.month, "
                    "day=excluded.day",
                    (interaction.guild.id, interaction.user.id, month, day))
            await interaction.response.send_message(
                f"Birthday saved as {month:02d}-{day:02d}.", ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_list", description="Staff only: list saved birthdays.")
    async def birthday_list(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command only works inside a server.", ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            with db._conn() as conn:
                rows = conn.execute(
                    "SELECT user_id, month, day FROM birthdays WHERE guild_id=? "
                    "ORDER BY month, day, user_id",
                    (interaction.guild.id,)).fetchall()
            if not rows:
                await interaction.response.send_message(
                    "No birthdays saved yet.", ephemeral=True)
                return
            lines = [f"{r['month']:02d}-{r['day']:02d} — <@{r['user_id']}>"
                     for r in rows[:100]]
            if len(rows) > 100:
                lines.append(f"… and {len(rows) - 100} more.")
            await interaction.response.send_message(
                "Birthdays:\n" + "\n".join(lines), ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_setup", description="Staff only: set the birthday wishes channel.")
    @app_commands.describe(channel="Channel where daily birthday wishes will be posted.")
    async def birthday_setup(self, interaction: discord.Interaction,
                             channel: discord.TextChannel) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command only works inside a server.", ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "birthday_channel", str(channel.id))
            await interaction.response.send_message(
                f"Birthday channel set to {channel.mention}. "
                f"Wishes are posted daily at 00:05 WIB.", ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass

    @tasks.loop(time=dtime(hour=0, minute=5, tzinfo=_WIB))
    async def daily_birthday_check(self) -> None:
        # Passive background task, not a command. Skips silently when
        # no birthday channel is configured for a guild.
        try:
            today = datetime.now(_WIB)
            for guild in list(self.bot.guilds):
                try:
                    channel_id = db.get_int_setting(guild.id, "birthday_channel")
                    if channel_id is None:
                        continue
                    with db._conn() as conn:
                        rows = conn.execute(
                            "SELECT user_id FROM birthdays WHERE guild_id=? "
                            "AND month=? AND day=?",
                            (guild.id, today.month, today.day)).fetchall()
                    if not rows:
                        continue
                    channel = guild.get_channel(channel_id)
                    if channel is None:
                        try:
                            channel = await self.bot.fetch_channel(channel_id)
                        except Exception:
                            continue
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    mentions = ", ".join(f"<@{r['user_id']}>" for r in rows)
                    await channel.send(
                        f"🎂 Happy birthday {mentions}! "
                        f"Wishing you a wonderful day! 🎉")
                except Exception:
                    continue
        except Exception:
            print("[birthday] daily check failed")

    @daily_birthday_check.before_loop
    async def _before_daily_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Birthday(bot))
