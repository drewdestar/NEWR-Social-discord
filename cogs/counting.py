"""Counting game — slash setup command + a passive message listener.

  /count_setup <channel> — staff-only: set the counting channel.

The listener (on_message) is NOT a command: it runs automatically in
the background for messages in the configured counting channel.
  * Only the exact next number counts: expected number + streak are
    tracked in the DB (counting_state). A correct number advances both.
  * A wrong NUMBER gets a gentle reset message (streak resets,
    expected goes back to 1). User messages are never deleted.
  * Non-numeric messages are ignored (chat in the channel does not
    break the count).
  * Bot messages are ignored.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import db
from staff import require_staff


class Counting(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="count_setup", description="Staff only: set the counting channel.")
    @app_commands.describe(channel="Channel where the counting game will run.")
    async def count_setup(self, interaction: discord.Interaction,
                          channel: discord.TextChannel) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command only works inside a server.", ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "counting_channel", str(channel.id))
            with db._conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO counting_state (guild_id, expected, streak) "
                    "VALUES (?, 1, 0)", (interaction.guild.id,))
            await interaction.response.send_message(
                f"Counting channel set to {channel.mention}. "
                f"The count starts at 1.", ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Passive listener, not a command. Never delete user messages.
        try:
            if message.author.bot:
                return
            if message.guild is None:
                return
            counting_id = db.get_int_setting(message.guild.id, "counting_channel")
            if counting_id is None or message.channel.id != counting_id:
                return
            text = message.content.strip()
            # Only pure integers participate; other chat is ignored.
            try:
                number = int(text)
            except (TypeError, ValueError):
                return
            # Strict decimal form only: rejects "01", "+1", "1.0", "1x".
            if text != str(number):
                return
            with db._conn() as conn:
                row = conn.execute(
                    "SELECT expected, streak FROM counting_state WHERE guild_id=?",
                    (message.guild.id,)).fetchone()
                expected = row["expected"] if row else 1
                streak = row["streak"] if row else 0
                if number == expected:
                    conn.execute(
                        "INSERT INTO counting_state VALUES (?, ?, ?) "
                        "ON CONFLICT(guild_id) DO UPDATE SET expected=excluded.expected, "
                        "streak=excluded.streak",
                        (message.guild.id, expected + 1, streak + 1))
                else:
                    conn.execute(
                        "INSERT INTO counting_state VALUES (?, 1, 0) "
                        "ON CONFLICT(guild_id) DO UPDATE SET expected=1, streak=0",
                        (message.guild.id,))
            if number != expected:
                try:
                    await message.channel.send(
                        f"Oops! {number} breaks the count — the correct number was "
                        f"{expected}. Let's start again from 1! "
                        f"(Previous streak: {streak})")
                except Exception:
                    pass
        except Exception:
            print("[counting] message handling failed")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counting(bot))
