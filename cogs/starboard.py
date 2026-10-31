"""Starboard — slash setup command + a passive reaction listener.

  /starboard_setup <channel> [threshold] — staff-only.

The listener (on_raw_reaction_add) is NOT a command: it runs
automatically in the background when a message gains ⭐ reactions.
When a message reaches >= threshold ⭐ reactions, an embed copy is
posted to the starboard channel exactly once (posted message IDs are
tracked in the DB). Bot reactions and the bot's own messages are
ignored, and a message already in the starboard channel is ignored
to avoid loops. Every failure path is swallowed so a bad reaction
event can never crash the bot.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import db
from staff import require_staff

_DEFAULT_THRESHOLD = 3


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="starboard_setup", description="Staff only: set the starboard channel and threshold.")
    @app_commands.describe(
        channel="Channel where popular messages will be reposted.",
        threshold="How many ⭐ reactions are needed (1-20, default 3).")
    async def starboard_setup(self, interaction: discord.Interaction,
                              channel: discord.TextChannel,
                              threshold: int = _DEFAULT_THRESHOLD) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "This command only works inside a server.", ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            if threshold < 1 or threshold > 20:
                await interaction.response.send_message(
                    "Threshold must be between 1 and 20.", ephemeral=True)
                return
            db.set_setting(interaction.guild.id, "starboard_channel", str(channel.id))
            db.set_setting(interaction.guild.id, "starboard_threshold", str(threshold))
            await interaction.response.send_message(
                f"Starboard set to {channel.mention} with a threshold of "
                f"{threshold} ⭐.", ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Something went wrong. Please try again later.", ephemeral=True)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        # Passive listener, not a command. Ignore everything that cannot
        # be a genuine user starring someone else's message.
        try:
            if payload.guild_id is None:
                return
            if self.bot.user is not None and payload.user_id == self.bot.user.id:
                return  # ignore the bot's own reactions
            if payload.emoji.name != "⭐":
                return
            guild = self.bot.get_guild(payload.guild_id)
            if guild is None:
                return
            starboard_id = db.get_int_setting(guild.id, "starboard_channel")
            if starboard_id is None or payload.channel_id == starboard_id:
                return
            threshold = db.get_int_setting(guild.id, "starboard_threshold",
                                           _DEFAULT_THRESHOLD) or _DEFAULT_THRESHOLD
            with db._conn() as conn:
                already = conn.execute(
                    "SELECT 1 FROM starboard_posts WHERE guild_id=? AND message_id=?",
                    (guild.id, payload.message_id)).fetchone()
            if already is not None:
                return  # posted once only
            channel = guild.get_channel(payload.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(payload.channel_id)
                except Exception:
                    return
            if not isinstance(channel, discord.TextChannel):
                return
            try:
                message = await channel.fetch_message(payload.message_id)
            except Exception:
                return
            if message.author.bot:
                return  # ignore the bot's own messages
            star_count = 0
            for reaction in message.reactions:
                try:
                    if reaction.emoji == "⭐":
                        star_count = reaction.count
                        break
                except Exception:
                    continue
            if star_count < threshold:
                return
            starboard = guild.get_channel(starboard_id)
            if starboard is None:
                try:
                    starboard = await self.bot.fetch_channel(starboard_id)
                except Exception:
                    return
            if not isinstance(starboard, discord.TextChannel):
                return
            embed = discord.Embed(
                description=message.content[:4000] if message.content else "(no text)",
                color=discord.Color.gold(),
                timestamp=message.created_at)
            embed.set_author(name=str(message.author),
                             icon_url=message.author.display_avatar.url
                             if message.author.display_avatar else None)
            embed.add_field(name="Original", value=f"[Jump to message]({message.jump_url})",
                            inline=False)
            embed.set_footer(text=f"⭐ {star_count}  •  #{channel.name}")
            image_url = None
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    image_url = attachment.url
                    break
            if image_url:
                embed.set_image(url=image_url)
            try:
                posted = await starboard.send(embed=embed)
            except Exception:
                return
            try:
                with db._conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO starboard_posts VALUES (?, ?, ?)",
                        (guild.id, message.id, posted.id))
            except Exception:
                pass
        except Exception:
            # A bad reaction event must never crash the bot.
            print("[starboard] reaction handling failed")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Starboard(bot))
