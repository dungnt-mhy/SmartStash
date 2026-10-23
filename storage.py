"""SQLite storage for the article-workflow bot.

Tables
------
users        telegram_id, username, display_name, role (NULL = pending),
             can_publish, categories (JSON list), chat_id, created_at
tasks        id, code (CV-0001...), creator_id, creator_role, hint,
             file_ids (JSON list), draft_url, public_url, status,
             reject_reason, destinations (JSON list of publish targets),
             created_at, updated_at -- the parent task is only the
             source-materials record (Option A)
outputs      id, task_code, channel, status, draft_ref, public_url,
             reject_reason, created_at, updated_at -- one row per publish
             channel of a task, each with its own lifecycle and its own
             Telegram card, reviewed/published independently
audit_log    actor_id, action, target_type, target_id, detail, created_at
bot_meta     key/value -- long-polling offset, task sequence, ...
intake_buffer  per (chat_id, telegram_id) photos/documents waiting for the
             finalising text message
user_state   per-user ephemeral interaction state (e.g. "waiting for the
             reject reason for task CV-0001")
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

STATUS_DRAFT = "DRAFT"
STATUS_PENDING = "PENDING"
STATUS_APPROVED_WAITING = "APPROVED_WAITING"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
STATUS_RETRACTED = "RETRACTED"

ALL_STATUSES = (
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_APPROVED_WAITING,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_RETRACTED,
)

#: Publish destinations a new task is posted to by default.
DEFAULT_DESTINATIONS = ["website", "facebook", "instagram"]

#: All publish channels the system knows about, in display order.
#: ``destinations`` on a task selects which of these get an output row.
#: The two video channels are predefined for the future video phase
#: (user-approved Option A keeps the channel list data-driven) but are
#: NOT part of DEFAULT_DESTINATIONS and are NOT auto-created yet.
CHANNELS = {
    "website": {"label": "Website", "kind": "article"},
    "facebook": {"label": "Facebook", "kind": "post"},
    "instagram": {"label": "Instagram", "kind": "carousel"},
    "video_vertical": {"label": "Video dọc", "kind": "video"},
    "video_horizontal": {"label": "Video ngang", "kind": "video"},
}
CHANNEL_ORDER = tuple(CHANNELS.keys())


def channel_label(channel: str) -> str:
    meta = CHANNELS.get(channel)
    return meta["label"] if meta else channel


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT,
    display_name TEXT,
    role        TEXT,
    can_publish INTEGER NOT NULL DEFAULT 0,
    categories  TEXT NOT NULL DEFAULT '[]',
    chat_id     INTEGER,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL UNIQUE,
    creator_id    INTEGER NOT NULL,
    creator_role  TEXT,
    hint          TEXT NOT NULL DEFAULT '',
    file_ids      TEXT NOT NULL DEFAULT '[]',
    draft_url     TEXT,
    public_url    TEXT,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    reject_reason TEXT,
    destinations  TEXT NOT NULL DEFAULT '["website", "facebook", "instagram"]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outputs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_code     TEXT NOT NULL,
    channel       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    draft_ref     TEXT,
    public_url    TEXT,
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(task_code, channel)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    INTEGER,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS intake_buffer (
    chat_id     INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    items_json  TEXT NOT NULL DEFAULT '[]',
    hints_json  TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, telegram_id)
);
CREATE TABLE IF NOT EXISTS user_state (
    telegram_id INTEGER PRIMARY KEY,
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);
"""


class Store:
    """Thread-safe (single-process) SQLite store."""

    def __init__(self, db_path: str | Path = "data/bot.db"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Upgrade older databases in place, without losing data.

        ``CREATE TABLE IF NOT EXISTS`` never adds columns to a table that
        already exists, so new columns are added explicitly when missing.
        """
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(tasks)")
        }
        if "destinations" not in columns:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN destinations TEXT NOT NULL"
                " DEFAULT '[\"website\", \"facebook\", \"instagram\"]'"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- users
    def register_user(self, telegram_id: int, username, display_name, chat_id):
        """Insert a pending user, or refresh profile/chat of a known user.

        Never touches an existing user's role.
        """
        now = utcnow_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO users (telegram_id, username, display_name, role,
                                   can_publish, categories, chat_id, created_at)
                VALUES (?, ?, ?, NULL, 0, '[]', ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    chat_id=excluded.chat_id
                """,
                (telegram_id, username, display_name, chat_id, now),
            )
        return self.get_user(telegram_id)

    def get_user(self, telegram_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()

    def list_users(self):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM users ORDER BY created_at, telegram_id"
            ).fetchall()

    def has_admin(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
            ).fetchone()
        return row["n"] > 0

    def reviewers(self):
        """Users (admin / deputy / editor) with a known chat_id."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM users WHERE role IN ('admin','deputy_admin','editor')"
                " AND chat_id IS NOT NULL ORDER BY telegram_id"
            ).fetchall()

    def admins(self):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM users WHERE role = 'admin' AND chat_id IS NOT NULL"
            ).fetchall()

    def set_role(self, telegram_id: int, role, can_publish: bool = False,
                 categories=None) -> None:
        cats = json.dumps(categories or [], ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET role = ?, can_publish = ?, categories = ?"
                " WHERE telegram_id = ?",
                (role, 1 if can_publish else 0, cats, telegram_id),
            )

    def revoke_role(self, telegram_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET role = NULL, can_publish = 0, categories = '[]'"
                " WHERE telegram_id = ?",
                (telegram_id,),
            )

    # ---------------------------------------------------------------- tasks
    def _next_task_code(self) -> str:
        row = self._conn.execute(
            "SELECT value FROM bot_meta WHERE key = 'task_seq'"
        ).fetchone()
        seq = int(row["value"]) + 1 if row else 1
        self._conn.execute(
            "INSERT INTO bot_meta (key, value) VALUES ('task_seq', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(seq),),
        )
        return f"CV-{seq:04d}"

    def create_task(self, creator_id: int, creator_role, hint: str, file_ids,
                    destinations=None):
        now = utcnow_iso()
        dests = list(destinations) if destinations else list(DEFAULT_DESTINATIONS)
        with self._lock, self._conn:
            code = self._next_task_code()
            self._conn.execute(
                """
                INSERT INTO tasks (code, creator_id, creator_role, hint,
                                   file_ids, destinations, status,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
                """,
                (code, creator_id, creator_role, hint,
                 json.dumps(list(file_ids), ensure_ascii=False),
                 json.dumps(dests, ensure_ascii=False), now, now),
            )
        return self.get_task(code)

    def get_task(self, code: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks WHERE code = ?", (code,)
            ).fetchone()

    def list_tasks(self, limit: int = 50):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def list_tasks_by_creator(self, creator_id: int, limit: int = 50):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks WHERE creator_id = ? ORDER BY id DESC LIMIT ?",
                (creator_id, limit),
            ).fetchall()

    def needs_article(self):
        """Tasks Muse still has to write / process: DRAFT and PENDING."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks WHERE status IN ('DRAFT','PENDING') ORDER BY id"
            ).fetchall()

    def set_task_status(self, code: str, status: str, reject_reason=None) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown task status: {status}")
        with self._lock, self._conn:
            if reject_reason is not None:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, reject_reason = ?, updated_at = ?"
                    " WHERE code = ?",
                    (status, reject_reason, utcnow_iso(), code),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE code = ?",
                    (status, utcnow_iso(), code),
                )

    def set_task_hint(self, code: str, hint: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET hint = ?, updated_at = ? WHERE code = ?",
                (hint, utcnow_iso(), code),
            )

    def set_task_destinations(self, code: str, destinations) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET destinations = ?, updated_at = ?"
                " WHERE code = ?",
                (json.dumps(list(destinations), ensure_ascii=False),
                 utcnow_iso(), code),
            )

    def set_draft_url(self, code: str, url: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET draft_url = ?, updated_at = ? WHERE code = ?",
                (url, utcnow_iso(), code),
            )

    def set_public_url(self, code: str, url: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET public_url = ?, status = 'PUBLISHED',"
                " updated_at = ? WHERE code = ?",
                (url, utcnow_iso(), code),
            )

    # -------------------------------------------------------------- outputs
    # Per-channel outputs (Option A): every destination of a task gets its
    # own output row with its own lifecycle, reviewed and published
    # independently of the other channels and of the parent task.
    def create_output(self, task_code: str, channel: str):
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel: {channel}")
        now = utcnow_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO outputs (task_code, channel, status,"
                " created_at, updated_at) VALUES (?, ?, 'DRAFT', ?, ?)",
                (task_code, channel, now, now),
            )
        return self.get_output_by_channel(task_code, channel)

    def get_output(self, output_id: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE id = ?", (output_id,)
            ).fetchone()

    def get_output_by_channel(self, task_code: str, channel: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE task_code = ? AND channel = ?",
                (task_code, channel),
            ).fetchone()

    def list_outputs(self, task_code: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE task_code = ? ORDER BY id",
                (task_code,),
            ).fetchall()

    def set_output_status(self, output_id: int, status: str,
                          reject_reason=None) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown output status: {status}")
        with self._lock, self._conn:
            if reject_reason is not None:
                self._conn.execute(
                    "UPDATE outputs SET status = ?, reject_reason = ?,"
                    " updated_at = ? WHERE id = ?",
                    (status, reject_reason, utcnow_iso(), output_id),
                )
            else:
                self._conn.execute(
                    "UPDATE outputs SET status = ?, updated_at = ?"
                    " WHERE id = ?",
                    (status, utcnow_iso(), output_id),
                )

    def set_output_draft(self, output_id: int, draft_ref: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET draft_ref = ?, updated_at = ?"
                " WHERE id = ?",
                (draft_ref, utcnow_iso(), output_id),
            )

    def set_output_public_url(self, output_id: int, url: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET public_url = ?, status = 'PUBLISHED',"
                " updated_at = ? WHERE id = ?",
                (url, utcnow_iso(), output_id),
            )

    def cancel_open_outputs(self, task_code: str) -> int:
        """Cancel every DRAFT/PENDING output of a task. Returns the count."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE outputs SET status = 'CANCELLED', updated_at = ?"
                " WHERE task_code = ? AND status IN ('DRAFT', 'PENDING')",
                (utcnow_iso(), task_code),
            )
        return cursor.rowcount

    def sync_outputs_for_task(self, task):
        """Make the task's outputs match its current destinations.

        - Creates a DRAFT output for every selected channel that has no
          live output (a previously CANCELLED output is revived to DRAFT).
        - Cancels outputs in DRAFT or PENDING whose channel is no longer
          selected. Outputs in APPROVED_WAITING / PUBLISHED / RETRACTED
          (and REJECTED ones still selected) are left untouched.
        Returns the task's outputs afterwards.
        """
        code = task["code"]
        raw = task["destinations"] if "destinations" in task.keys() else None
        try:
            dests = json.loads(raw) if raw else list(DEFAULT_DESTINATIONS)
        except (ValueError, TypeError):
            dests = list(DEFAULT_DESTINATIONS)
        if not isinstance(dests, list):
            dests = list(DEFAULT_DESTINATIONS)
        dests = [d for d in dests if d in CHANNELS]
        now = utcnow_iso()
        with self._lock, self._conn:
            existing = {
                row["channel"]: row
                for row in self._conn.execute(
                    "SELECT * FROM outputs WHERE task_code = ?", (code,)
                )
            }
            for channel in dests:
                row = existing.get(channel)
                if row is None:
                    self._conn.execute(
                        "INSERT INTO outputs (task_code, channel, status,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, 'DRAFT', ?, ?)",
                        (code, channel, now, now),
                    )
                elif row["status"] == STATUS_CANCELLED:
                    self._conn.execute(
                        "UPDATE outputs SET status = 'DRAFT',"
                        " reject_reason = NULL, updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
            for channel, row in existing.items():
                if channel not in dests and row["status"] in (
                    STATUS_DRAFT, STATUS_PENDING,
                ):
                    self._conn.execute(
                        "UPDATE outputs SET status = 'CANCELLED',"
                        " updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
        return self.list_outputs(code)

    # ---------------------------------------------------------------- audit
    def audit(self, actor_id, action: str, target_type=None, target_id=None,
              detail=None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (actor_id, action, target_type,"
                " target_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (actor_id, action, target_type,
                 None if target_id is None else str(target_id), detail,
                 utcnow_iso()),
            )

    def audit_entries(self, action: str | None = None):
        with self._lock:
            if action is None:
                return self._conn.execute(
                    "SELECT * FROM audit_log ORDER BY id"
                ).fetchall()
            return self._conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
            ).fetchall()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO bot_meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------- intake buffering
    def buffer_add(self, chat_id: int, telegram_id: int, item: dict,
                   caption: str | None = None) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT items_json, hints_json FROM intake_buffer"
                " WHERE chat_id = ? AND telegram_id = ?",
                (chat_id, telegram_id),
            ).fetchone()
            items = json.loads(row["items_json"]) if row else []
            hints = json.loads(row["hints_json"]) if row else []
            items.append(item)
            if caption:
                hints.append(caption)
            self._conn.execute(
                "INSERT INTO intake_buffer (chat_id, telegram_id, items_json,"
                " hints_json, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(chat_id, telegram_id) DO UPDATE SET"
                " items_json = excluded.items_json,"
                " hints_json = excluded.hints_json,"
                " updated_at = excluded.updated_at",
                (chat_id, telegram_id, json.dumps(items, ensure_ascii=False),
                 json.dumps(hints, ensure_ascii=False), utcnow_iso()),
            )

    def buffer_get(self, chat_id: int, telegram_id: int):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM intake_buffer WHERE chat_id = ? AND telegram_id = ?",
                (chat_id, telegram_id),
            ).fetchone()
        if row is None:
            return {"items": [], "hints": []}
        return {
            "items": json.loads(row["items_json"]),
            "hints": json.loads(row["hints_json"]),
        }

    def buffer_clear(self, chat_id: int, telegram_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM intake_buffer WHERE chat_id = ? AND telegram_id = ?",
                (chat_id, telegram_id),
            )

    # ------------------------------------------------------------ user state
    def set_user_state(self, telegram_id: int, state: str, payload: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO user_state (telegram_id, state, payload, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(telegram_id) DO UPDATE SET state = excluded.state,"
                " payload = excluded.payload, updated_at = excluded.updated_at",
                (telegram_id, state, json.dumps(payload, ensure_ascii=False),
                 utcnow_iso()),
            )

    def get_user_state(self, telegram_id: int):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_state WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
        if row is None:
            return None
        return {"state": row["state"], "payload": json.loads(row["payload"])}

    def clear_user_state(self, telegram_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM user_state WHERE telegram_id = ?", (telegram_id,)
            )
