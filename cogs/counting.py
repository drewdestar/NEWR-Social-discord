"""Counting game — slash setup/status commands + a passive message listener.

  /count_setup <channel> — staff-only: set the counting channel.
  /count — anyone: show the next expected number and current streak.

The listener (on_message) is NOT a command: it runs automatically in
the background for messages in the configured counting channel.
  * Only the exact next number counts: expected number + streak +
    last counter are tracked in the DB (counting_state). A correct
    number advances expected and streak and records its author.
  * The same user may NOT count twice in a row: a repeat count is
    ignored (not counted, no state change) and gets a gentle visible
    notice in the channel. User messages are never deleted.
  * A wrong NUMBER (from a different user than the last counter)
    gets a gentle reset message (streak resets, expected goes back
    to 1, last counter cleared). User messages are never deleted.
  * Non-numeric messages are ignored (chat in the channel does not
    break the count).
  * Bot messages are ignored.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
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
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "counting_channel", str(channel.id))
            with db._conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO counting_state (guild_id, expected, streak) "
                    "VALUES (?, 1, 0)", (interaction.guild.id,))
            await interaction.response.send_message(
                embed=ui.ok("Counting channel set",
                            f"Counting channel set to {channel.mention}. "
                            f"The count starts at 1."), ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="count", description="Show the current counting game status.")
    async def count(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            counting_id = db.get_int_setting(interaction.guild.id, "counting_channel")
            if counting_id is None:
                await interaction.response.send_message(
                    embed=ui.err("No counting channel",
                                 "No counting channel has been set yet. Ask staff to run "
                                 "/count_setup first."), ephemeral=True)
                return
            with db._conn() as conn:
                row = conn.execute(
                    "SELECT expected, streak, last_counter FROM counting_state "
                    "WHERE guild_id=?", (interaction.guild.id,)).fetchone()
            expected = row["expected"] if row else 1
            streak = row["streak"] if row else 0
            current = expected - 1 if streak > 0 else 0
            last = row["last_counter"] if row else None
            last_text = f"<@{last}>" if last else "nobody yet"
            await interaction.response.send_message(
                embed=ui.make("🔢 Counting status", "Current counting game status.",
                              ui.BLURPLE,
                              fields=[("Current number", str(current), True),
                                      ("Next number", str(expected), True),
                                      ("Current streak", str(streak), True),
                                      ("Last counted by", last_text, False)]),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
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
                    "SELECT expected, streak, last_counter FROM counting_state "
                    "WHERE guild_id=?", (message.guild.id,)).fetchone()
                expected = row["expected"] if row else 1
                streak = row["streak"] if row else 0
                last_counter = row["last_counter"] if row else None
                if last_counter is not None and last_counter == message.author.id:
                    # Repeat count: ignored, no state change at all.
                    pass
                elif number == expected:
                    conn.execute(
                        "INSERT INTO counting_state "
                        "(guild_id, expected, streak, last_counter) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(guild_id) DO UPDATE SET expected=excluded.expected, "
                        "streak=excluded.streak, last_counter=excluded.last_counter",
                        (message.guild.id, expected + 1, streak + 1, message.author.id))
                else:
                    conn.execute(
                        "INSERT INTO counting_state "
                        "(guild_id, expected, streak, last_counter) VALUES (?, 1, 0, NULL) "
                        "ON CONFLICT(guild_id) DO UPDATE SET expected=1, streak=0, "
                        "last_counter=NULL",
                        (message.guild.id,))
            if last_counter is not None and last_counter == message.author.id:
                try:
                    await message.channel.send(
                        embed=ui.warn("Wait your turn",
                                      f"Hold on, you can't count twice in a row. Wait for "
                                      f"someone else to count next. The next number is still "
                                      f"{expected}."))
                except Exception:
                    pass
            elif number != expected:
                try:
                    await message.channel.send(
                        embed=ui.warn("Count reset",
                                      f"Oops, {number} breaks the count. The correct number was "
                                      f"{expected}. Let's start again from 1! "
                                      f"(Previous streak: {streak})"))
                except Exception:
                    pass
        except Exception:
            print("[counting] message handling failed")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counting(bot))
