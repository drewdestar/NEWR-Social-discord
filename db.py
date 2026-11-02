"""SQLite persistence for NEWR Social. No secrets are stored here.

Tables:
  settings        (guild_id, key, value) — channel/threshold config.
                  Keys: confession_channel, confession_vetting ("1"/"0"),
                  starboard_channel, starboard_threshold (default 3, 1-20),
                  starboard_emojis (JSON list, always includes ⭐),
                  counting_channel, birthday_channel, birthday_role.
  starboard_posts (guild_id, message_id, starboard_message_id,
                  author_id, channel_id, star_count)
                  — messages copied to starboard, plus the last known
                  star count and author/channel so the board post can
                  be edited/removed and /starboard_top can rank posts.
                  Rows for posts tracked before the count/author
                  columns existed have NULLs there and are backfilled
                  on the next reaction event for that message.
  counting_state  (guild_id, expected, streak, last_counter)
                  — next expected number + current streak + the user
                  who posted the last accepted count (NULL after a
                  reset), per guild.
  confessions     (id, guild_id, author_hash, text, image_url,
                   channel_id, message_id, created_at)
                  — PRIVACY: author_hash is a truncated SHA-256 hash used
                  only for the 10-minute rate limit / abuse checks. There
                  is NO raw user_id column in this table, so a confession
                  can never be traced back to a user from the stored row.
  confess_pending (id, guild_id, author_hash, text, image_url,
                   created_at)
                  — confessions awaiting staff review when vetting is
                  on. Same privacy rule: hash only, no raw user_id.
                  Approved rows move to confessions; rejected rows are
                  deleted, not kept.
  confess_blockwords (guild_id, word)
                  — staff-set blocked words/phrases (lowercased) for
                  confessions, per guild.
  confess_rate    (author_hash, guild_id, last_at)
                  — last confession time per hashed author, per guild.
  birthdays       (guild_id, user_id, month, day)
                  — birthdays are NOT anonymous: they are only ever shown
                  to staff via /birthday_list or posted as a public wish
                  in the configured birthday channel.

DB_PATH env var overrides the default data.db next to this file.

Schema upgrades are applied in init_db() with ALTER TABLE ... ADD
COLUMN guarded by a PRAGMA check, so an existing data.db keeps working
and a fresh database gets the full schema directly.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str,
                   definition: str) -> None:
    """Add a column to an existing table if it is not there yet."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {r["name"] for r in rows}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(
            guild_id INTEGER, key TEXT, value TEXT,
            PRIMARY KEY (guild_id, key));
        CREATE TABLE IF NOT EXISTS starboard_posts(
            guild_id INTEGER, message_id INTEGER,
            starboard_message_id INTEGER,
            author_id INTEGER, channel_id INTEGER, star_count INTEGER,
            PRIMARY KEY (guild_id, message_id));
        CREATE TABLE IF NOT EXISTS counting_state(
            guild_id INTEGER PRIMARY KEY, expected INTEGER NOT NULL DEFAULT 1,
            streak INTEGER NOT NULL DEFAULT 0, last_counter INTEGER);
        CREATE TABLE IF NOT EXISTS confessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
            author_hash TEXT, text TEXT, image_url TEXT, channel_id INTEGER,
            message_id INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS confess_pending(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
            author_hash TEXT, text TEXT, image_url TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS confess_blockwords(
            guild_id INTEGER, word TEXT,
            PRIMARY KEY (guild_id, word));
        CREATE TABLE IF NOT EXISTS confess_rate(
            author_hash TEXT, guild_id INTEGER, last_at TEXT,
            PRIMARY KEY (author_hash, guild_id));
        CREATE TABLE IF NOT EXISTS birthdays(
            guild_id INTEGER, user_id INTEGER, month INTEGER, day INTEGER,
            PRIMARY KEY (guild_id, user_id));
        """)
        # Migrations for databases created before these columns existed.
        _ensure_column(conn, "starboard_posts", "author_id", "INTEGER")
        _ensure_column(conn, "starboard_posts", "channel_id", "INTEGER")
        _ensure_column(conn, "starboard_posts", "star_count", "INTEGER")
        _ensure_column(conn, "counting_state", "last_counter", "INTEGER")
        _ensure_column(conn, "confessions", "image_url", "TEXT")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_setting(guild_id: int, key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO settings VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
            (guild_id, key, value))


def get_setting(guild_id: int, key: str, default: str | None = None) -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE guild_id=? AND key=?",
            (guild_id, key)).fetchone()
    return row["value"] if row else default


def get_int_setting(guild_id: int, key: str, default: int | None = None) -> int | None:
    raw = get_setting(guild_id, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
