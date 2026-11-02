"""Starboard — slash setup/leaderboard commands + passive reaction listeners.

  /starboard_setup <channel> [threshold] [emojis] — staff-only.
  /starboard_top — anyone: top tracked posts and top users by stars.

The listeners (on_raw_reaction_add / on_raw_reaction_remove) are NOT
commands: they run automatically when a message gains or loses a
configured star emoji.
  * When a message reaches >= threshold counted stars, an embed copy
    is posted to the starboard channel and tracked in the DB.
  * While it stays tracked, any star-count change edits the board
    post (footer count + image/text) to reflect the new count.
  * When the count drops below threshold, the board post is deleted
    and the tracking row is cleared, so the message can be re-posted
    if it rises to threshold again.
  * Counted emojis: ⭐ always counts, plus any extra emojis staff
    configured via /starboard_setup (stored as a JSON list in
    settings under starboard_emojis). A count sums the reactions for
    all configured emojis on the message.
Bot reactions and the bot's own messages are ignored, and a message
already in the starboard channel is ignored to avoid loops. Every
failure path is swallowed so a bad reaction event can never crash
the bot.
"""
from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from staff import require_staff

_DEFAULT_THRESHOLD = 3
_DEFAULT_EMOJIS = ["⭐"]


def parse_emojis(raw: str | None) -> list[str]:
    """Parse a space-separated emoji string into a de-duplicated list.

    Unicode emojis pass through as-is; custom emojis are expected in
    their Discord form, e.g. <:name:123456789>. ⭐ is always added by
    the caller, so it is kept here too if present and de-duplicated.
    """
    if not raw:
        return []
    seen: list[str] = []
    for token in raw.split():
        token = token.strip()
        if token and token not in seen:
            seen.append(token)
    return seen


def configured_emojis(guild_id: int) -> list[str]:
    """Configured star emojis for a guild; always includes ⭐ first."""
    raw = db.get_setting(guild_id, "starboard_emojis")
    emojis: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                emojis = [str(e) for e in parsed if str(e).strip()]
        except Exception:
            emojis = []
    if "⭐" not in emojis:
        emojis = ["⭐"] + emojis
    return emojis[:10]


def _emoji_key(emoji) -> str:
    """Comparable key for a reaction emoji (unicode char or <:name:id>)."""
    try:
        return str(emoji)
    except Exception:
        return getattr(emoji, "name", "") or ""


def count_stars(message: discord.Message, emojis: list[str]) -> int:
    """Total reactions on a message for any configured star emoji."""
    wanted = set(emojis)
    total = 0
    for reaction in getattr(message, "reactions", []) or []:
        try:
            if _emoji_key(reaction.emoji) in wanted:
                total += int(reaction.count)
        except Exception:
            continue
    return total


def build_starboard_embed(message: discord.Message, star_count: int,
                          source_channel: discord.abc.GuildChannel) -> discord.Embed:
    embed = discord.Embed(
        description=message.content[:4000] if message.content else "(no text)",
        color=ui.GOLD,
        timestamp=message.created_at)
    embed.set_author(name=str(message.author),
                     icon_url=message.author.display_avatar.url
                     if message.author.display_avatar else None)
    embed.add_field(name="Original", value=f"[Jump to message]({message.jump_url})",
                    inline=False)
    embed.set_footer(text=f"⭐ {star_count}  •  #{source_channel.name}  •  {ui.FOOTER}")
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            embed.set_image(url=attachment.url)
            break
    return embed


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="starboard_setup", description="Staff only: set the starboard channel, threshold, and extra star emojis.")
    @app_commands.describe(
        channel="Channel where popular messages will be reposted.",
        threshold="How many counted star reactions are needed (1-20, default 3).",
        emojis="Optional extra emojis beyond ⭐, space-separated (e.g. '🌟 ✨'). Omit to keep the current list.")
    async def starboard_setup(self, interaction: discord.Interaction,
                              channel: discord.TextChannel,
                              threshold: int = _DEFAULT_THRESHOLD,
                              emojis: str | None = None) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            if threshold < 1 or threshold > 20:
                await interaction.response.send_message(embed=ui.err("Invalid threshold", "Threshold must be between 1 and 20."), ephemeral=True)
                return
            db.set_setting(interaction.guild.id, "starboard_channel", str(channel.id))
            db.set_setting(interaction.guild.id, "starboard_threshold", str(threshold))
            if emojis is not None:
                parsed = parse_emojis(emojis)
                if not parsed:
                    await interaction.response.send_message(
                        embed=ui.err("No valid emojis",
                                     "No valid extra emojis were given. Provide emojis "
                                     "separated by spaces, e.g. '🌟 ✨'."), ephemeral=True)
                    return
                # ⭐ always counts; the stored list is the extras plus ⭐.
                stored = ["⭐"] + [e for e in parsed if e != "⭐"]
                if len(stored) > 10:
                    await interaction.response.send_message(embed=ui.err("Too many emojis", "Too many emojis (max 10 including ⭐)."), ephemeral=True)
                    return
                db.set_setting(interaction.guild.id, "starboard_emojis",
                               json.dumps(stored))
            current = configured_emojis(interaction.guild.id)
            await interaction.response.send_message(
                embed=ui.panel("⭐ Starboard set",
                               f"Starboard set to {channel.mention} with a threshold of "
                               f"{threshold}. Counted emojis: {' '.join(current)}.", ui.GOLD),
                ephemeral=True)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="starboard_top", description="Show the top starboard posts and top starred users.")
    async def starboard_top(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            with db._conn() as conn:
                posts = conn.execute(
                    "SELECT message_id, starboard_message_id, author_id, "
                    "channel_id, star_count FROM starboard_posts "
                    "WHERE guild_id=? AND star_count IS NOT NULL "
                    "ORDER BY star_count DESC, message_id ASC LIMIT 10",
                    (interaction.guild.id,)).fetchall()
                users = conn.execute(
                    "SELECT author_id, SUM(star_count) AS total FROM starboard_posts "
                    "WHERE guild_id=? AND star_count IS NOT NULL "
                    "AND author_id IS NOT NULL "
                    "GROUP BY author_id ORDER BY total DESC, author_id ASC LIMIT 5",
                    (interaction.guild.id,)).fetchall()
            if not posts:
                await interaction.response.send_message(
                    embed=ui.info("No ranked posts yet",
                                  "No ranked starboard posts yet. Star counts are tracked "
                                  "from now on; posts tracked before this upgrade have no "
                                  "stored count and are not ranked."), ephemeral=True)
                return
            lines = ["⭐ **Top starboard posts**"]
            for rank, row in enumerate(posts, start=1):
                if row["channel_id"]:
                    link = (f"https://discord.com/channels/{interaction.guild.id}/"
                            f"{row['channel_id']}/{row['message_id']}")
                    target = f"[jump]({link})"
                else:
                    target = f"message {row['message_id']}"
                author = f"<@{row['author_id']}>" if row["author_id"] else "unknown author"
                lines.append(f"{rank}. ⭐ {row['star_count']} — {author} ({target})")
            if users:
                lines.append("")
                lines.append("🏆 **Top starred users**")
                for rank, row in enumerate(users, start=1):
                    lines.append(f"{rank}. <@{row['author_id']}> — ⭐ {row['total']}")
            lines.append("")
            lines.append("_Counts are the last tracked counts for posts on the "
                         "starboard; posts tracked before this upgrade are not ranked._")
            await interaction.response.send_message(
                embed=ui.make("⭐ Top starboard posts", "\n".join(lines[1:]), ui.GOLD))
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    async def _sync_board_post(self, guild: discord.Guild,
                               payload: discord.RawReactionActionEvent,
                               is_add: bool) -> None:
        """Shared add/remove handling: post, edit, or remove as needed."""
        if payload.guild_id is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return
        emojis = configured_emojis(guild.id)
        # Raw payload emoji: PartialEmoji; its str() matches reaction keys.
        payload_key = _emoji_key(payload.emoji)
        # For unicode payloads, name holds the character itself.
        if payload_key not in emojis and getattr(payload.emoji, "name", None) not in emojis:
            return
        starboard_id = db.get_int_setting(guild.id, "starboard_channel")
        if starboard_id is None or payload.channel_id == starboard_id:
            return
        threshold = db.get_int_setting(guild.id, "starboard_threshold",
                                       _DEFAULT_THRESHOLD) or _DEFAULT_THRESHOLD
        with db._conn() as conn:
            tracked = conn.execute(
                "SELECT starboard_message_id, author_id, channel_id, star_count "
                "FROM starboard_posts WHERE guild_id=? AND message_id=?",
                (guild.id, payload.message_id)).fetchone()
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
            return  # original gone/unreadable: leave the board post alone
        if message.author.bot:
            return
        star_count = count_stars(message, emojis)

        starboard = guild.get_channel(starboard_id)
        if starboard is None:
            try:
                starboard = await self.bot.fetch_channel(starboard_id)
            except Exception:
                return
        if not isinstance(starboard, discord.TextChannel):
            return

        if tracked is None:
            if star_count < threshold:
                return
            embed = build_starboard_embed(message, star_count, channel)
            try:
                posted = await starboard.send(embed=embed)
            except Exception:
                return
            try:
                with db._conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO starboard_posts "
                        "(guild_id, message_id, starboard_message_id, "
                        "author_id, channel_id, star_count) VALUES (?, ?, ?, ?, ?, ?)",
                        (guild.id, message.id, posted.id, message.author.id,
                         channel.id, star_count))
            except Exception:
                pass
            return

        # Already tracked: edit, or remove if it fell below threshold.
        board_id = tracked["starboard_message_id"]
        if star_count < threshold:
            try:
                board_msg = await starboard.fetch_message(board_id)
                await board_msg.delete()
            except Exception:
                pass  # board post already gone: still clear the row
            try:
                with db._conn() as conn:
                    conn.execute(
                        "DELETE FROM starboard_posts WHERE guild_id=? AND message_id=?",
                        (guild.id, payload.message_id))
            except Exception:
                pass
            return

        # Still at/above threshold: update the board post in place.
        try:
            board_msg = await starboard.fetch_message(board_id)
        except Exception:
            # Board post vanished but the message still qualifies:
            # clear the row and re-post fresh.
            try:
                with db._conn() as conn:
                    conn.execute(
                        "DELETE FROM starboard_posts WHERE guild_id=? AND message_id=?",
                        (guild.id, payload.message_id))
            except Exception:
                pass
            # Re-posting here is correct even on a remove event: the
            # message still qualifies and its board copy is gone.
            try:
                posted = await starboard.send(
                    embed=build_starboard_embed(message, star_count, channel))
                with db._conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO starboard_posts "
                        "(guild_id, message_id, starboard_message_id, "
                        "author_id, channel_id, star_count) VALUES (?, ?, ?, ?, ?, ?)",
                        (guild.id, message.id, posted.id, message.author.id,
                         channel.id, star_count))
            except Exception:
                pass
            return
        try:
            await board_msg.edit(embed=build_starboard_embed(message, star_count, channel))
        except Exception:
            return
        try:
            with db._conn() as conn:
                conn.execute(
                    "UPDATE starboard_posts SET star_count=?, author_id=?, "
                    "channel_id=? WHERE guild_id=? AND message_id=?",
                    (star_count, message.author.id, channel.id,
                     guild.id, payload.message_id))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        try:
            if payload.guild_id is None:
                return
            guild = self.bot.get_guild(payload.guild_id)
            if guild is None:
                return
            await self._sync_board_post(guild, payload, is_add=True)
        except Exception:
            print("[starboard] reaction handling failed")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        try:
            if payload.guild_id is None:
                return
            guild = self.bot.get_guild(payload.guild_id)
            if guild is None:
                return
            await self._sync_board_post(guild, payload, is_add=False)
        except Exception:
            print("[starboard] reaction-remove handling failed")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Starboard(bot))
