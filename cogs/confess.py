"""Anonymous confessions — slash commands only.

  /confess <message> [image]  — post anonymously to the staff-set channel.
  /confess_setup <channel>    — staff-only: set the confession channel.
  /confess_vetting <enabled>  — staff-only: turn staff review on/off
                                (default OFF: direct posting, as before).
  /confess_pending            — staff-only: list confessions awaiting review.
  /confess_review <id> <approve|reject> — staff-only: approve (posts it)
                                or reject (deletes it) a pending confession.
  /confess_blockwords <add|remove|list> [word] — staff-only: manage the
                                blocked-word list for confessions.

PRIVACY (critical, never weaken this):
  * The confession embed carries no author name, avatar, mention, ID,
    or footer that could identify the sender — including in the
    vetting queue: staff see only a pending ID, the text, and the
    date, never who sent it.
  * The ephemeral confirmation never echoes the message back in a way
    that links it publicly; it is a generic "posted"/"submitted" notice.
  * The database stores only a truncated SHA-256 hash of
    (fixed salt + user_id) for the 10-minute rate limit. There is no
    raw user_id column on the confessions or confess_pending tables,
    and no log line ever prints a user ID, username, or hash for a
    confession.
  * Rate limit: 1 confession per 10 minutes per user (per guild),
    enforced on the hash, counted at submission time (direct or queued).
    A hit gets an ephemeral "slow down" reply.
  * Blocked-word rejections are generic: they never name the blocked
    word, so the list cannot be leaked by probing, and they do not
    consume the rate limit.
  * Image attachments: only image attachments are accepted, and the
    image is shown in the posted embed (or in the pending record, for
    vetting) with no author information attached.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from staff import require_staff

# Fixed salt string (not a secret, not a token): it only makes the stored
# hash non-trivially reversible from the hash alone. Changing it resets
# everyone's rate-limit window, which is harmless.
_SALT = "newr-social-confession-v1"
_RATE_LIMIT_SECONDS = 10 * 60
_MAX_LENGTH = 2000


def _author_hash(user_id: int) -> str:
    return hashlib.sha256(f"{_SALT}:{user_id}".encode()).hexdigest()[:32]


def contains_blocked_word(text: str, words: list[str]) -> bool:
    """Case-insensitive match with word-boundary-like guards.

    A blocked word/phrase matches only when it is not glued to other
    word characters on either side, so e.g. blocking "cat" does not
    reject "concatenate". Matching is done on the raw text, never
    echoed back to the sender.
    """
    if not text or not words:
        return False
    for word in words:
        word = (word or "").strip()
        if not word:
            continue
        try:
            pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            if word.lower() in text.lower():
                return True
    return False


def _blocked_words(guild_id: int) -> list[str]:
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT word FROM confess_blockwords WHERE guild_id=?",
                (guild_id,)).fetchall()
        return [r["word"] for r in rows]
    except Exception:
        return []


def _vetting_enabled(guild_id: int) -> bool:
    return db.get_setting(guild_id, "confession_vetting", "0") == "1"


def _confession_embed(text: str, image_url: str | None) -> discord.Embed:
    # Deliberately NO author and NO sender-identifying footer. The
    # footer below is bot branding only, identical on every
    # confession, and reveals nothing about the sender.
    embed = discord.Embed(
        title="💬 Anonymous Confession",
        description=text,
        color=ui.BLURPLE,
        timestamp=discord.utils.utcnow())
    embed.set_footer(text=ui.FOOTER)
    if image_url:
        embed.set_image(url=image_url)
    return embed


async def _resolve_channel(bot: commands.Bot, guild: discord.Guild,
                           channel_id: int) -> discord.TextChannel | None:
    channel = guild.get_channel(channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        try:
            fetched = await bot.fetch_channel(channel_id)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except Exception:
            channel = None
    return channel


class Confess(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="confess", description="Post an anonymous confession.")
    @app_commands.describe(
        message="Your confession (posted anonymously, max 2000 characters).",
        image="Optional image to attach to your confession (image files only).")
    async def confess(self, interaction: discord.Interaction, message: str,
                      image: discord.Attachment | None = None) -> None:
        try:
            # Defer FIRST: Discord requires an initial response within
            # 3 seconds, but posting to the confession channel through
            # the VM proxy can take longer, which used to surface as
            # "The application did not respond" even though the post
            # itself had already succeeded (see #confessions, "hi").
            await interaction.response.defer(ephemeral=True)

            async def reply(*args, **kwargs):
                kwargs.pop("ephemeral", None)
                await interaction.followup.send(*args, ephemeral=True, **kwargs)

            if interaction.guild is None:
                await reply(embed=ui.err("Confessions", "Confessions only work inside a server."), ephemeral=True)
                return
            text = (message or "").strip()
            if not text:
                await reply(embed=ui.err("Empty confession", "Your confession cannot be empty."), ephemeral=True)
                return
            if len(text) > _MAX_LENGTH:
                await reply(
                    embed=ui.err("Confession too long", f"Your confession is too long (max {_MAX_LENGTH} characters)."),
                    ephemeral=True)
                return
            image_url: str | None = None
            if image is not None:
                content_type = image.content_type or ""
                if not content_type.startswith("image/"):
                    await reply(
                        embed=ui.err("Image only", "Only image attachments are accepted for confessions."),
                        ephemeral=True)
                    return
                image_url = image.url
            channel_id = db.get_int_setting(interaction.guild.id, "confession_channel")
            if channel_id is None:
                await reply(
                    embed=ui.err("No confession channel",
                                 "No confession channel has been set yet. Ask staff to run "
                                 "/confess_setup first."), ephemeral=True)
                return
            # Block-word check first: generic rejection, no rate-limit use,
            # and the blocked word is never named back to the sender.
            if contains_blocked_word(text, _blocked_words(interaction.guild.id)):
                await reply(embed=ui.err("Could not post", "Your confession could not be posted."), ephemeral=True)
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
                        await reply(
                            embed=ui.warn("Slow down",
                                          f"You can send another confession in {mins}m {secs}s."),
                            ephemeral=True)
                        return
            except Exception:
                pass  # rate-limit lookup failure must not block a confession

            if _vetting_enabled(interaction.guild.id):
                # Queue instead of posting: staff review via slash commands.
                try:
                    with db._conn() as conn:
                        conn.execute(
                            "INSERT INTO confess_pending "
                            "(guild_id, author_hash, text, image_url, created_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (interaction.guild.id, author_hash, text, image_url, db.now()))
                        conn.execute(
                            "INSERT INTO confess_rate VALUES (?, ?, ?) "
                            "ON CONFLICT(author_hash, guild_id) DO UPDATE SET last_at=excluded.last_at",
                            (author_hash, interaction.guild.id, db.now()))
                except Exception:
                    await reply(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                    return
                await reply(
                    embed=ui.ok("Submitted for review",
                                "Your confession has been submitted anonymously for staff "
                                "review. It will be posted once approved."), ephemeral=True)
                return

            channel = await _resolve_channel(self.bot, interaction.guild, channel_id)
            if channel is None:
                await reply(
                    embed=ui.err("Channel not found",
                                 "The configured confession channel could not be found. "
                                 "Ask staff to run /confess_setup again."), ephemeral=True)
                return
            try:
                posted = await channel.send(embed=_confession_embed(text, image_url))
            except discord.Forbidden:
                await reply(
                    embed=ui.err("No permission",
                                 "I don't have permission to post in the confession channel."),
                    ephemeral=True)
                return
            except Exception:
                await reply(
                    embed=ui.err("Posting failed",
                                 "Something went wrong while posting your confession. "
                                 "Please try again later."), ephemeral=True)
                return
            try:
                with db._conn() as conn:
                    conn.execute(
                        "INSERT INTO confessions "
                        "(guild_id, author_hash, text, image_url, channel_id, message_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (interaction.guild.id, author_hash, text, image_url,
                         channel.id, posted.id, db.now()))
                    conn.execute(
                        "INSERT INTO confess_rate VALUES (?, ?, ?) "
                        "ON CONFLICT(author_hash, guild_id) DO UPDATE SET last_at=excluded.last_at",
                        (author_hash, interaction.guild.id, db.now()))
            except Exception:
                pass  # storage failure must not un-post or leak anything
            # Ephemeral confirmation only — nothing public names the sender.
            await reply(
                embed=ui.ok("Confession posted", "Your confession has been posted anonymously."),
                ephemeral=True)
        except Exception:
            # Never crash the bot on bad input; never leak the sender.
            # Traceback lines are code locations only, no user text/IDs.
            import traceback as _tb
            print("[confess] confession handling failed:")
            print("".join(_tb.format_exc(limit=8)))
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await reply(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="confess_setup", description="Staff only: set the confession channel.")
    @app_commands.describe(channel="Channel where anonymous confessions will be posted.")
    async def confess_setup(self, interaction: discord.Interaction,
                            channel: discord.TextChannel) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "confession_channel", str(channel.id))
            await interaction.response.send_message(
                embed=ui.ok("Confession channel set", f"Confession channel set to {channel.mention}."),
                ephemeral=True)
        except Exception:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="confess_vetting", description="Staff only: turn confession staff review on or off.")
    @app_commands.describe(enabled="True: confessions wait for staff approval. False: post directly (default).")
    async def confess_vetting(self, interaction: discord.Interaction,
                              enabled: bool) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            db.set_setting(interaction.guild.id, "confession_vetting",
                           "1" if enabled else "0")
            state = "enabled" if enabled else "disabled"
            extra = (" Confessions will now wait for staff review "
                     "(/confess_pending, /confess_review)."
                     if enabled else
                     " Confessions will now post directly.")
            await interaction.response.send_message(
                embed=ui.ok("Staff review " + state, f"Confession staff review {state}.{extra}"),
                ephemeral=True)
        except Exception:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="confess_pending", description="Staff only: list confessions awaiting review (no authors shown).")
    async def confess_pending(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            with db._conn() as conn:
                rows = conn.execute(
                    "SELECT id, text, image_url, created_at FROM confess_pending "
                    "WHERE guild_id=? ORDER BY id ASC LIMIT 20",
                    (interaction.guild.id,)).fetchall()
            if not rows:
                await interaction.response.send_message(embed=ui.info("No pending confessions", "No pending confessions."), ephemeral=True)
                return
            lines = ["Pending confessions (review with /confess_review):"]
            for row in rows:
                preview = (row["text"] or "")[:100].replace("\n", " ")
                if len(row["text"] or "") > 100:
                    preview += "…"
                has_image = " [image]" if row["image_url"] else ""
                lines.append(f"#{row['id']}{has_image} — {preview}")
            if len(rows) == 20:
                lines.append("Showing the oldest 20 pending confessions.")
            await interaction.response.send_message(
                embed=ui.info("Pending confessions", "\n".join(lines[1:])), ephemeral=True)
        except Exception:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="confess_review", description="Staff only: approve or reject a pending confession.")
    @app_commands.describe(
        confession_id="Pending confession ID from /confess_pending.",
        decision="Approve posts it anonymously; Reject deletes it.")
    @app_commands.choices(decision=[
        app_commands.Choice(name="Approve", value="approve"),
        app_commands.Choice(name="Reject", value="reject")])
    async def confess_review(self, interaction: discord.Interaction,
                             confession_id: int, decision: str) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            if decision not in ("approve", "reject"):
                await interaction.response.send_message(embed=ui.err("Invalid decision", "Decision must be Approve or Reject."), ephemeral=True)
                return
            with db._conn() as conn:
                row = conn.execute(
                    "SELECT id, author_hash, text, image_url FROM confess_pending "
                    "WHERE guild_id=? AND id=?",
                    (interaction.guild.id, confession_id)).fetchone()
            if row is None:
                await interaction.response.send_message(
                    embed=ui.err("Not found", f"No pending confession with ID {confession_id}."),
                    ephemeral=True)
                return
            if decision == "reject":
                try:
                    with db._conn() as conn:
                        conn.execute(
                            "DELETE FROM confess_pending WHERE guild_id=? AND id=?",
                            (interaction.guild.id, confession_id))
                except Exception:
                    pass
                await interaction.response.send_message(
                    embed=ui.ok("Confession rejected", f"Confession #{confession_id} rejected and removed."),
                    ephemeral=True)
                return
            # Approve: post anonymously, then move the row to confessions.
            channel_id = db.get_int_setting(interaction.guild.id, "confession_channel")
            if channel_id is None:
                await interaction.response.send_message(
                    embed=ui.err("No confession channel",
                                 "No confession channel has been set yet. Ask staff to run "
                                 "/confess_setup first. The pending confession was kept."),
                    ephemeral=True)
                return
            channel = await _resolve_channel(self.bot, interaction.guild, channel_id)
            if channel is None:
                await interaction.response.send_message(
                    embed=ui.err("Channel not found",
                                 "The configured confession channel could not be found. "
                                 "The pending confession was kept."), ephemeral=True)
                return
            try:
                posted = await channel.send(
                    embed=_confession_embed(row["text"], row["image_url"]))
            except discord.Forbidden:
                await interaction.response.send_message(
                    embed=ui.err("No permission",
                                 "I don't have permission to post in the confession channel. "
                                 "The pending confession was kept."), ephemeral=True)
                return
            except Exception:
                await interaction.response.send_message(
                    embed=ui.err("Posting failed",
                                 "Something went wrong while posting. "
                                 "The pending confession was kept."), ephemeral=True)
                return
            try:
                with db._conn() as conn:
                    conn.execute(
                        "INSERT INTO confessions "
                        "(guild_id, author_hash, text, image_url, channel_id, message_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (interaction.guild.id, row["author_hash"], row["text"],
                         row["image_url"], channel.id, posted.id, db.now()))
                    conn.execute(
                        "DELETE FROM confess_pending WHERE guild_id=? AND id=?",
                        (interaction.guild.id, confession_id))
            except Exception:
                pass
            await interaction.response.send_message(
                embed=ui.ok("Confession approved",
                            f"Confession #{confession_id} approved and posted anonymously."),
                ephemeral=True)
        except Exception:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name="confess_blockwords", description="Staff only: add, remove, or list blocked words for confessions.")
    @app_commands.describe(
        action="Add a word, remove a word, or list the current words.",
        word="Word or phrase to add or remove (not needed for List).")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
        app_commands.Choice(name="List", value="list")])
    async def confess_blockwords(self, interaction: discord.Interaction,
                                 action: str, word: str | None = None) -> None:
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=ui.err("Server only", "This command only works inside a server."), ephemeral=True)
                return
            if not await require_staff(interaction):
                return
            if action not in ("add", "remove", "list"):
                await interaction.response.send_message(embed=ui.err("Invalid action", "Action must be Add, Remove, or List."), ephemeral=True)
                return
            if action == "list":
                words = _blocked_words(interaction.guild.id)
                if not words:
                    await interaction.response.send_message(embed=ui.info("Blocked words", "No blocked words are set."), ephemeral=True)
                else:
                    shown = sorted(words)[:100]
                    extra = (f"\n…and {len(words) - 100} more."
                             if len(words) > 100 else "")
                    await interaction.response.send_message(
                        embed=ui.info("Blocked words", "Blocked words:\n" + "\n".join(shown) + extra),
                        ephemeral=True)
                return
            cleaned = (word or "").strip().lower()
            if not cleaned:
                await interaction.response.send_message(embed=ui.err("Word needed", "Provide a word for Add or Remove."), ephemeral=True)
                return
            if len(cleaned) > 100:
                await interaction.response.send_message(embed=ui.err("Word too long", "That word is too long (max 100 characters)."), ephemeral=True)
                return
            with db._conn() as conn:
                if action == "add":
                    conn.execute(
                        "INSERT OR IGNORE INTO confess_blockwords VALUES (?, ?)",
                        (interaction.guild.id, cleaned))
                    await interaction.response.send_message(embed=ui.ok("Blocked word added", "Blocked word added."), ephemeral=True)
                else:
                    cur = conn.execute(
                        "DELETE FROM confess_blockwords WHERE guild_id=? AND word=?",
                        (interaction.guild.id, cleaned))
                    await interaction.response.send_message(
                        embed=ui.ok("Blocked word removed", "Blocked word removed.")
                        if cur.rowcount else
                        ui.info("Not on the list", "That word was not on the blocked list."),
                        ephemeral=True)
        except Exception:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
                else:
                    await interaction.response.send_message(embed=ui.err("Something went wrong", "Something went wrong. Please try again later."), ephemeral=True)
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Confess(bot))
