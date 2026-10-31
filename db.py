"""SQLite persistence for NEWR Social. No secrets are stored here.

Tables:
  settings        (guild_id, key, value) — channel/threshold config.
  starboard_posts (guild_id, message_id, starboard_message_id)
                  — messages already copied to starboard, to post once only.
  counting_state  (guild_id, expected, streak)
                  — next expected number + current streak per guild.
  confessions     (id, guild_id, author_hash, text, channel_id,
                   message_id, created_at)
                  — PRIVACY: author_hash is a truncated SHA-256 hash used
                  only for the 10-minute rate limit / abuse checks. There
                  is NO raw user_id column in this table, so a confession
                  can never be traced back to a user from the stored row.
  confess_rate    (author_hash, guild_id, last_at)
                  — last confession time per hashed author, per guild.
  birthdays       (guild_id, user_id, month, day)
                  — birthdays are NOT anonymous: they are only ever shown
                  to staff via /birthday_list or posted as a public wish
                  in the configured birthday channel.

DB_PATH env var overrides the default data.db next to this file.
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


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(
            guild_id INTEGER, key TEXT, value TEXT,
            PRIMARY KEY (guild_id, key));
        CREATE TABLE IF NOT EXISTS starboard_posts(
            guild_id INTEGER, message_id INTEGER,
            starboard_message_id INTEGER,
            PRIMARY KEY (guild_id, message_id));
        CREATE TABLE IF NOT EXISTS counting_state(
            guild_id INTEGER PRIMARY KEY, expected INTEGER NOT NULL DEFAULT 1,
            streak INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS confessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
            author_hash TEXT, text TEXT, channel_id INTEGER,
            message_id INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS confess_rate(
            author_hash TEXT, guild_id INTEGER, last_at TEXT,
            PRIMARY KEY (author_hash, guild_id));
        CREATE TABLE IF NOT EXISTS birthdays(
            guild_id INTEGER, user_id INTEGER, month INTEGER, day INTEGER,
            PRIMARY KEY (guild_id, user_id));
        """)


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
