"""Birthdays — slash commands only, plus a passive daily check.

  /birthday_set <MM-DD>       — save your own birthday.
  /birthday_remove            — remove your own birthday.
  /birthday_list              — staff-only: list saved birthdays.
  /birthday_upcoming          — anyone: birthdays in the next 30 days.
  /birthday_setup <channel> [role] — staff-only: set the birthday
                                channel and, optionally, a birthday
                                role for the day (re-running without a
                                role keeps the previously set role).

The daily check is NOT a command: it runs automatically at 00:05
Asia/Jakarta (WIB) and, only if a birthday channel is configured,
posts one combined birthday wish per guild for members whose
month/day matches today in WIB. If a birthday role is configured,
the same check also gives the role to members whose birthday is
today and removes it from members whose birthday has passed — all
best-effort, failures swallowed, never crashing the check. If no
birthday channel is set, wishes are skipped silently, but role
cleanup still runs when a role is configured. Birthdays are not
anonymous (unlike confessions): they are listed only to staff
(/birthday_list, ephemeral) or wished publicly in the configured
channel, which is the point of the feature.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
from staff import require_staff

_WIB = ZoneInfo("Asia/Jakarta")
_MMDD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
# Days per month; February allows 29 (leap-day birthdays are wished
# on Feb 29 in leap years, and skipped silently in non-leap years).
_DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def days_until_birthday(month: int, day: int, today: date) -> int | None:
    """Days from today until the next occurrence of month/day.

    Returns None only if no occurrence exists within the next 5 years
    (impossible for a valid month/day, but kept safe). Feb 29 in a
    non-leap year is skipped to the next leap year.
    """
    for year in range(today.year, today.year + 6):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return (candidate - today).days
    return None


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
                await interaction.response.send_message(embed=ui.err("Birthdays", "Birthdays only work inside a server."), ephemeral=True)
                return
            raw = (date or "").strip()
            match = _MMDD.match(raw)
            if not match:
                await interaction.response.send_message(embed=ui.err("Invalid format", "Please use the MM-DD format, e.g. 05-21."), ephemeral=True)
                return
            month, day = int(match.group(1)), int(match.group(2))
            if day > _DAYS_IN_MONTH[month]:
                await interaction.response.send_message(
                    embed=ui.err("Invalid date", f"{raw} is not a valid date."), ephemeral=True)
                return
            with db._conn() as conn:
                conn.execute(
                    "INSERT INTO birthdays VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(guild_id, user_id) DO UPDATE SET month=excluded.month, "
                    "day=excluded.day",
                    (interaction.guild.id, interaction.user.id, month, day))
            await interaction.response.send_message(
                embed=ui.ok("Birthday saved", f"Birthday saved as {month:02d}-{day:02d}."),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_remove", description="Remove your saved birthday.")
    async def birthday_remove(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Birthdays", "Birthdays only work inside a server."), ephemeral=True)
                return
            with db._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM birthdays WHERE guild_id=? AND user_id=?",
                    (interaction.guild.id, interaction.user.id))
                removed = cur.rowcount
            if removed:
                await interaction.response.send_message(embed=ui.ok("Birthday removed", "Your birthday has been removed."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=ui.info("No birthday saved", "You don't have a birthday saved."), ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_list", description="Staff only: list saved birthdays.")
    async def birthday_list(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            with db._conn() as conn:
                rows = conn.execute(
                    "SELECT user_id, month, day FROM birthdays WHERE guild_id=? "
                    "ORDER BY month, day, user_id",
                    (interaction.guild.id,)).fetchall()
            if not rows:
                await interaction.response.send_message(embed=ui.info("No birthdays saved", "No birthdays saved yet."), ephemeral=True)
                return
            lines = [f"{r['month']:02d}-{r['day']:02d} — <@{r['user_id']}>"
                     for r in rows[:100]]
            if len(rows) > 100:
                lines.append(f"… and {len(rows) - 100} more.")
            await interaction.response.send_message(
                embed=ui.make("🎂 Saved birthdays", "\n".join(lines), ui.BLURPLE),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_upcoming", description="Show birthdays coming up in the next 30 days.")
    async def birthday_upcoming(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            today = datetime.now(_WIB).date()
            with db._conn() as conn:
                rows = conn.execute(
                    "SELECT user_id, month, day FROM birthdays WHERE guild_id=?",
                    (interaction.guild.id,)).fetchall()
            upcoming: list[tuple[int, int, int, int]] = []
            for row in rows:
                delta = days_until_birthday(row["month"], row["day"], today)
                if delta is not None and delta <= 30:
                    upcoming.append((delta, row["month"], row["day"], row["user_id"]))
            if not upcoming:
                await interaction.response.send_message(embed=ui.info("No upcoming birthdays", "No birthdays in the next 30 days."), ephemeral=True)
                return
            upcoming.sort()
            lines = ["🎂 Upcoming birthdays (next 30 days):"]
            for delta, month, day, user_id in upcoming[:50]:
                when = "today!" if delta == 0 else (
                    "tomorrow" if delta == 1 else f"in {delta} days")
                lines.append(f"{month:02d}-{day:02d} — <@{user_id}> ({when})")
            if len(upcoming) > 50:
                lines.append(f"… and {len(upcoming) - 50} more.")
            await interaction.response.send_message(
                embed=ui.make("🎂 Upcoming birthdays", "\n".join(lines[1:]), ui.SUCCESS),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="birthday_setup", description="Staff only: set the birthday wishes channel and optional birthday role.")
    @app_commands.describe(
        channel="Channel where daily birthday wishes will be posted.",
        role="Optional role given to members on their birthday and removed afterwards. Omit to keep the current role.")
    async def birthday_setup(self, interaction: discord.Interaction,
                             channel: discord.TextChannel,
                             role: discord.Role | None = None) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "birthday_channel", str(channel.id))
            if role is not None:
                db.set_setting(interaction.guild.id, "birthday_role", str(role.id))
                role_text = f" Birthday role: {role.mention}."
            else:
                existing = db.get_int_setting(interaction.guild.id, "birthday_role")
                role_text = (f" Birthday role unchanged."
                             if existing is not None else "")
            await interaction.response.send_message(
                embed=ui.ok("Birthday channel set",
                            f"Birthday channel set to {channel.mention}. "
                            f"Wishes are posted daily at 00:05 WIB.{role_text}"),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    async def _sync_birthday_role(self, guild: discord.Guild,
                                  role_id: int, today: datetime) -> None:
        """Best-effort birthday-role sync; every failure is swallowed."""
        try:
            role = guild.get_role(role_id)
            if role is None:
                try:
                    fetched_roles = await guild.fetch_roles()
                    role = next((r for r in fetched_roles if r.id == role_id), None)
                except Exception:
                    role = None
            if role is None:
                return
            with db._conn() as conn:
                rows = conn.execute(
                    "SELECT user_id, month, day FROM birthdays WHERE guild_id=?",
                    (guild.id,)).fetchall()
            saved = {r["user_id"]: (r["month"], r["day"]) for r in rows}
            today_ids = {uid for uid, (m, d) in saved.items()
                         if m == today.month and d == today.day}
            # Give the role to today's birthday members (fetch if uncached).
            for user_id in today_ids:
                try:
                    member = guild.get_member(user_id)
                    if member is None:
                        member = await guild.fetch_member(user_id)
                    if member is not None and role not in member.roles:
                        await member.add_roles(role, reason="Birthday today")
                except Exception:
                    continue
            # Remove it from cached members whose birthday is not today
            # (covers members who had it yesterday and anyone whose
            # birthday was removed; uncached members are best-effort only).
            for member in list(guild.members):
                try:
                    if role in member.roles and member.id not in today_ids:
                        await member.remove_roles(role, reason="Birthday passed")
                except Exception:
                    continue
        except Exception:
            pass

    @tasks.loop(time=dtime(hour=0, minute=5, tzinfo=_WIB))
    async def daily_birthday_check(self) -> None:
        # Passive background task, not a command.
        try:
            today = datetime.now(_WIB)
            for guild in list(self.bot.guilds):
                try:
                    channel_id = db.get_int_setting(guild.id, "birthday_channel")
                    role_id = db.get_int_setting(guild.id, "birthday_role")
                    if channel_id is None and role_id is None:
                        continue
                    if role_id is not None:
                        await self._sync_birthday_role(guild, role_id, today)
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
                        content=f"🎂 Happy birthday {mentions}!",
                        embed=ui.make("🎉 Wishing you a wonderful day",
                                      "May your year ahead be full of joy and celebration.",
                                      ui.SUCCESS))
                except Exception:
                    continue
        except Exception:
            print("[birthday] daily check failed")

    @daily_birthday_check.before_loop
    async def _before_daily_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Birthday(bot))
