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
             Telegram card, reviewed/published independently. A Facebook
             row additionally carries a multi-place ledger (TD-0013):
             publish_targets / fb_target_set / fb_place_urls /
             fb_place_failed / fb_place_retracted / fb_pending_targets
             (JSON) — one published link per place, not one link total.
facebook_pages  one row per Fanpage that can be picked as a Facebook
             publish place (seed: Minhy). The personal profile is a
             fixed place, not a row. A second Fanpage is one INSERT
             away — no code change, no admin screen yet.
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
from datetime import datetime, timedelta, timezone
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


def channel_kind(channel: str) -> str:
    """The channel's content kind: article / post / carousel / video."""
    meta = CHANNELS.get(channel)
    return meta["kind"] if meta else ""


#: Facebook publish places (TD-0013). A place key is "profile" for the
#: owner's personal profile, or "page_<fb_page_id>" for a Fanpage.
#: The profile entry is fixed; Fanpages live in the facebook_pages
#: table so a second page is one INSERT away — no code change, no
#: admin screen yet (adding a page = one row; see THAY-DOI TD-0013).
FB_PROFILE_KEY = "profile"
FB_PROFILE_LABEL = "👤 Trang cá nhân Nguyễn Trọng Minh Huy"
FB_PROFILE_SHORT = "Trang cá nhân"
FB_PAGE_SEED_ID = "61595515900524"
FB_PAGE_SEED_NAME = "Minhy"


def _fb_json_list(raw):
    """Parse a JSON list of non-empty strings, deduped in order."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
    return out


def _fb_json_map(raw):
    """Parse a JSON object of string -> string (place -> url)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items()
            if isinstance(k, str) and isinstance(v, str)}


def fb_legacy_to_key(target):
    """Old single publish_target values -> new place keys."""
    if target == "profile":
        return "profile"
    if target == "page":
        return f"page_{FB_PAGE_SEED_ID}"
    return None


def fb_key_to_legacy(key):
    """New place keys -> old single publish_target values (fallback
    for old readers; only the first place of a set is kept)."""
    if key == "profile":
        return "profile"
    if isinstance(key, str) and key.startswith("page_"):
        return "page"
    return None


def _fb_col(row, name, default=None):
    """Read a possibly-missing column from a sqlite Row."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Vietnam time (Asia/Saigon, UTC+7, no DST) — the ONE definition.
#: The webapp re-exports it (webapp/app.py: ICT); archive.py formats
#: log/audit times with it. DB columns stay UTC; convert to ICT only
#: at display/log boundaries (TD-0024 correction, 25/10/2026: every
#: time a user reads is Vietnam time).
ICT = timezone(timedelta(hours=7), name="ICT")

#: Total material storage (TD-0024 (f) / TD-0025) — the ONE
#: definition. All material files on the server (the whole
#: data/files/ tree, tmp chunks included) may not exceed 9GB, no
#: matter whether they arrive via the web app or via Telegram.
#: The webapp re-exports it (webapp/app.py: STORAGE_QUOTA_BYTES);
#: the bot's Telegram intake checks it in archive.py. Files kept
#: on Telegram's own servers never count towards it.
STORAGE_QUOTA_BYTES = 9 * 1024 * 1024 * 1024


def ict_now() -> datetime:
    return datetime.now(ICT)


def _as_ict(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ICT)


def format_ict(moment: datetime | None) -> str:
    """'HH:MM DD/MM/YYYY' in ICT for a datetime (naive = UTC)."""
    ict = _as_ict(moment)
    return "—" if ict is None else ict.strftime("%H:%M %d/%m/%Y")


def format_ict_short(moment: datetime | None) -> str:
    """'DD/MM HH:MM' in ICT — log-line prefix."""
    ict = _as_ict(moment)
    return "—" if ict is None else ict.strftime("%d/%m %H:%M")


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
    draft_video_path TEXT,
    draft_file_id TEXT,
    public_url    TEXT,
    reject_reason TEXT,
    scheduled_at  TEXT,
    publish_target TEXT,
    pending_choice INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS output_cards (
    id INTEGER PRIMARY KEY,
    output_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE(output_id, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS user_state (
    telegram_id INTEGER PRIMARY KEY,
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archived_files (
    task_code    TEXT NOT NULL,
    filename     TEXT NOT NULL,
    drive_file_id TEXT NOT NULL,
    web_view_link TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    archived_at  TEXT NOT NULL,
    PRIMARY KEY (task_code, filename)
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
        out_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(outputs)")
        }
        if "scheduled_at" not in out_columns:
            self._conn.execute(
                "ALTER TABLE outputs ADD COLUMN scheduled_at TEXT")
        if "publish_target" not in out_columns:
            self._conn.execute(
                "ALTER TABLE outputs ADD COLUMN publish_target TEXT")
        if "pending_choice" not in out_columns:
            self._conn.execute(
                "ALTER TABLE outputs ADD COLUMN pending_choice "
                "INTEGER NOT NULL DEFAULT 0")
        if "draft_video_path" not in out_columns:
            self._conn.execute(
                "ALTER TABLE outputs ADD COLUMN draft_video_path TEXT")
        if "draft_file_id" not in out_columns:
            self._conn.execute(
                "ALTER TABLE outputs ADD COLUMN draft_file_id TEXT")
        # TD-0013: Facebook multi-place ledger (all JSON, all
        # additive — old rows keep working, see fb_state()).
        for col in ("publish_targets", "fb_target_set", "fb_place_urls",
                    "fb_place_failed", "fb_place_retracted",
                    "fb_pending_targets"):
            if col not in out_columns:
                self._conn.execute(
                    f"ALTER TABLE outputs ADD COLUMN {col} TEXT")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS facebook_pages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " fb_page_id TEXT NOT NULL UNIQUE,"
            " name TEXT NOT NULL,"
            " created_at TEXT NOT NULL)")
        self._conn.execute(
            "INSERT OR IGNORE INTO facebook_pages"
            " (fb_page_id, name, created_at) VALUES (?, ?, ?)",
            (FB_PAGE_SEED_ID, FB_PAGE_SEED_NAME, utcnow_iso()))

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

    def count_tasks_by_status(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def count_outputs_by_status(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT channel, status, COUNT(*) AS n FROM outputs "
                "GROUP BY channel, status ORDER BY channel, status"
            ).fetchall()
        return [(r["channel"], r["status"], r["n"]) for r in rows]

    # ------------------------------------------------------- statistics
    # Period queries for /thongke and the daily summary. Bounds are
    # UTC ISO strings; ``end`` is exclusive, None means unbounded.
    def tasks_created_between(self, start_iso, end_iso=None):
        query = "SELECT * FROM tasks WHERE created_at >= ?"
        args = [start_iso]
        if end_iso is not None:
            query += " AND created_at < ?"
            args.append(end_iso)
        query += " ORDER BY id"
        with self._lock:
            return self._conn.execute(query, args).fetchall()

    def outputs_created_between(self, start_iso, end_iso=None):
        query = "SELECT * FROM outputs WHERE created_at >= ?"
        args = [start_iso]
        if end_iso is not None:
            query += " AND created_at < ?"
            args.append(end_iso)
        query += " ORDER BY id"
        with self._lock:
            return self._conn.execute(query, args).fetchall()

    def outputs_published_between(self, start_iso, end_iso=None):
        """Outputs whose publish moment (updated_at of a PUBLISHED
        row, as elsewhere in this codebase) falls in the period."""
        query = ("SELECT * FROM outputs WHERE status = 'PUBLISHED'"
                 " AND updated_at >= ?")
        args = [start_iso]
        if end_iso is not None:
            query += " AND updated_at < ?"
            args.append(end_iso)
        query += " ORDER BY updated_at"
        with self._lock:
            return self._conn.execute(query, args).fetchall()

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

    def sync_task_status_from_outputs(self, code: str):
        """Derive the parent task's status from its channel outputs.

        Option A (2026-10-23): review and publishing happen on the
        per-channel cards, so the task itself no longer has its own
        lifecycle — its status is a summary of the live outputs.
        Terminal manual states (CANCELLED, or RETRACTED reached while
        any output is not retracted) are left untouched.
        """
        task = self.get_task(code)
        if task is None:
            return None
        if task["status"] == STATUS_CANCELLED:
            return task
        live = [
            o for o in self.list_outputs(code)
            if o["status"] != STATUS_CANCELLED
        ]
        if not live:
            new = STATUS_DRAFT
        elif all(o["status"] == STATUS_PUBLISHED for o in live):
            new = STATUS_PUBLISHED
        elif all(o["status"] == STATUS_RETRACTED for o in live):
            new = STATUS_RETRACTED
        elif any(o["status"] == STATUS_PENDING for o in live):
            new = STATUS_PENDING
        elif any(o["status"] == STATUS_APPROVED_WAITING for o in live):
            new = STATUS_APPROVED_WAITING
        elif any(o["status"] == STATUS_REJECTED for o in live):
            new = STATUS_REJECTED
        else:
            new = STATUS_DRAFT
        if new != task["status"]:
            self.set_task_status(code, new)
        return self.get_task(code)

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

    def set_output_video(self, output_id: int, video_path: str) -> None:
        """Attach a draft video file to a video-channel output.

        ``draft_ref`` mirrors the local path so every existing
        "has a draft" check (submit / approve / publish keyboards,
        Muse's work queue) treats the video exactly like a text
        draft. A previously cached Telegram file_id belongs to the
        old file, so it is cleared — the next card send uploads the
        new file and caches its fresh file_id."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET draft_video_path = ?,"
                " draft_file_id = NULL, draft_ref = ?, updated_at = ?"
                " WHERE id = ?",
                (video_path, video_path, utcnow_iso(), output_id),
            )

    def set_output_file_id(self, output_id: int, file_id: str) -> None:
        """Cache the Telegram file_id of an uploaded draft video so
        later cards re-send it without uploading the file again."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET draft_file_id = ? WHERE id = ?",
                (file_id, output_id),
            )

    def set_output_pending_choice(self, output_id: int,
                                  pending: bool) -> None:
        """Flag an output whose publish/schedule choice is in
        flight — its cards show no buttons until the choice is
        resolved or cancelled."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET pending_choice = ? WHERE id = ?",
                (1 if pending else 0, output_id),
            )

    def latest_audit_id(self, output_id: int, action: str):
        """Newest audit id for one action on one output, or None —
        used to tell whether an order (e.g. retract) is pending."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(id) AS m FROM audit_log WHERE "
                "target_type = 'output' AND target_id = ? AND action = ?",
                (output_id, action),
            ).fetchone()
        return row["m"] if row else None

    def latest_audit(self, output_id: int, action: str):
        """Newest audit row for one action on one output, or None.
        TD-0024: the 48h-wait scan reads the created_at of the audit
        that moved an output into its current waiting state."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM audit_log WHERE target_type = 'output'"
                " AND target_id = ? AND action = ?"
                " ORDER BY id DESC LIMIT 1",
                (str(output_id), action),
            ).fetchone()

    def set_output_schedule(self, output_id: int, scheduled_at,
                            publish_target=None, targets=None) -> None:
        """Set (or clear, with None) an output's scheduled publish
        time — a UTC ISO timestamp — and its chosen publish target.

        TD-0013: pass targets=[place keys] for a Facebook schedule
        and the multi-place request set is stored alongside the
        legacy single publish_target (first place). Clearing a
        schedule (scheduled_at=None) also clears the request set,
        but keeps the frozen target set and any published links.
        """
        with self._lock, self._conn:
            if targets is None:
                if scheduled_at is None:
                    self._conn.execute(
                        "UPDATE outputs SET scheduled_at = ?,"
                        " publish_target = ?, publish_targets = NULL,"
                        " updated_at = ? WHERE id = ?",
                        (None, None, utcnow_iso(), output_id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE outputs SET scheduled_at = ?,"
                        " publish_target = ?, updated_at = ? WHERE id = ?",
                        (scheduled_at, publish_target, utcnow_iso(),
                         output_id),
                    )
                return
            legacy = fb_key_to_legacy(targets[0]) if targets else None
            row = self._conn.execute(
                "SELECT fb_place_urls FROM outputs WHERE id = ?",
                (output_id,)).fetchone()
            urls = _fb_json_map(row["fb_place_urls"]) if row else {}
            if urls:
                self._conn.execute(
                    "UPDATE outputs SET scheduled_at = ?,"
                    " publish_target = ?, publish_targets = ?,"
                    " updated_at = ? WHERE id = ?",
                    (scheduled_at, legacy,
                     json.dumps(targets, ensure_ascii=False),
                     utcnow_iso(), output_id),
                )
            else:
                self._conn.execute(
                    "UPDATE outputs SET scheduled_at = ?,"
                    " publish_target = ?, publish_targets = ?,"
                    " fb_target_set = ?, updated_at = ? WHERE id = ?",
                    (scheduled_at, legacy,
                     json.dumps(targets, ensure_ascii=False),
                     json.dumps(targets, ensure_ascii=False),
                     utcnow_iso(), output_id),
                )

    # ------------------------------------------------ Facebook places
    # (TD-0013: multi-place publish)

    def list_facebook_pages(self):
        with self._lock:
            return self._conn.execute(
                "SELECT fb_page_id, name FROM facebook_pages ORDER BY id"
            ).fetchall()

    def fb_places(self):
        """Ordered selectable Facebook places: profile first, then
        Fanpages in insertion order. The source of truth for every
        picker (bot + webapp) — never hardcoded elsewhere."""
        places = [{"key": FB_PROFILE_KEY, "label": FB_PROFILE_LABEL,
                   "short": FB_PROFILE_SHORT}]
        for row in self.list_facebook_pages():
            key = f"page_{row['fb_page_id']}"
            places.append({"key": key,
                           "label": f"📄 Fanpage {row['name']}",
                           "short": f"Fanpage {row['name']}"})
        return places

    def fb_place_keys(self):
        return [p["key"] for p in self.fb_places()]

    def fb_place_label(self, key):
        for place in self.fb_places():
            if place["key"] == key:
                return place["label"]
        if key == "legacy":
            # TD-0015: display-only key for old posts whose place
            # is unknown (see fb_state); never a pickable place.
            return "Facebook"
        if key == FB_PROFILE_KEY:
            return FB_PROFILE_LABEL
        if isinstance(key, str) and key.startswith("page_"):
            return f"📄 Fanpage {key[5:]}"
        return key

    def fb_place_short(self, key):
        for place in self.fb_places():
            if place["key"] == key:
                return place["short"]
        if key == "legacy":
            return "Facebook"
        if key == FB_PROFILE_KEY:
            return FB_PROFILE_SHORT
        if isinstance(key, str) and key.startswith("page_"):
            return f"Fanpage {key[5:]}"
        return key

    def fb_state(self, output):
        """Normalized Facebook place state for one output row.

        Backward compatible: a legacy single publish_target reads as
        a one-place request, and a legacy PUBLISHED public_url reads
        as that one place done. Unknown keys are preserved (a page
        row deleted later must not silently drop published links).
        """
        request = _fb_json_list(_fb_col(output, "publish_targets"))
        target_set = _fb_json_list(_fb_col(output, "fb_target_set"))
        urls = _fb_json_map(_fb_col(output, "fb_place_urls"))
        failed = _fb_json_list(_fb_col(output, "fb_place_failed"))
        retracted = _fb_json_list(_fb_col(output, "fb_place_retracted"))
        pending = _fb_json_list(_fb_col(output, "fb_pending_targets"))
        legacy = _fb_col(output, "publish_target")
        if not request and legacy:
            key = fb_legacy_to_key(legacy)
            if key:
                request = [key]
        if not target_set:
            target_set = list(request)
        if not urls and output["status"] in (STATUS_PUBLISHED,
                                             STATUS_RETRACTED) \
                and output["public_url"]:
            if target_set:
                urls = {target_set[0]: output["public_url"]}
            elif legacy:
                key = fb_legacy_to_key(legacy)
                if key:
                    urls = {key: output["public_url"]}
        if not urls and output["status"] in (STATUS_PUBLISHED,
                                             STATUS_RETRACTED) \
                and output["public_url"]:
            # TD-0015: old posts published before the place columns
            # existed (publish_target NULL, e.g. CV-0012/0013/0014)
            # still carry their place in the publish_requested
            # audit detail. Only PUBLISHED/RETRACTED rows derive
            # from audit here, so pickers (APPROVED_WAITING only)
            # are unaffected.
            key = self._latest_publish_request_place(output["id"])
            if key:
                urls = {key: output["public_url"]}
                if not target_set:
                    target_set = [key]
            else:
                urls = {"legacy": output["public_url"]}
                if not target_set:
                    target_set = ["legacy"]
        done = [k for k in target_set if k in urls]
        for k in urls:
            if k not in done:
                done.append(k)
        remaining = [k for k in target_set if k not in urls]
        return {
            "request": request,
            "target_set": target_set,
            "urls": urls,
            "failed": failed,
            "retracted": retracted,
            "pending": pending,
            "done": done,
            "remaining": remaining,
            "all_set_done": bool(target_set) and not remaining,
        }

    def _latest_publish_request_place(self, output_id):
        """Newest publish_requested audit's place for one output.

        Parses the audit detail the publish flows write: the
        TD-0013 `targets=a,b` list (first known key wins — a legacy
        single-link output has exactly one link) or the older
        `target=profile` / `target=page` value. Returns None when
        the audit trail has no usable place."""
        with self._lock:
            row = self._conn.execute(
                "SELECT detail FROM audit_log WHERE target_type = 'output'"
                " AND target_id = ? AND action = 'publish_requested'"
                " ORDER BY id DESC LIMIT 1",
                (str(output_id),)).fetchone()
        if not row or not row["detail"]:
            return None
        tokens = row["detail"].split()
        valid = set(self.fb_place_keys())
        for token in tokens:
            if token.startswith("targets="):
                for key in token[len("targets="):].split(","):
                    if key in valid:
                        return key
        for token in tokens:
            if token.startswith("target="):
                return fb_legacy_to_key(token[len("target="):])
        return None

    def fb_locked_places(self, output):
        """Places the pickers must show locked for one output.

        locked = done ∪ (live request − done − failed). A schedule
        still waiting for its time is NOT a live publish request,
        so its places stay editable (rescheduling must keep
        working); only a request with no schedule left (publish
        confirmed, or a fired schedule) locks its not-yet-done,
        not-failed places. Failed places are never locked, so a
        retry stays possible. Returns (locked, pending) where
        pending = locked − done."""
        state = self.fb_state(output)
        done = list(state["done"])
        if _fb_col(output, "scheduled_at"):
            return done, []
        done_set = set(done)
        failed_set = set(state["failed"])
        pending = [k for k in state["request"]
                   if k not in done_set and k not in failed_set]
        locked = list(dict.fromkeys(done + pending))
        return locked, pending

    def clear_fb_request(self, output_id):
        """Drop a recorded publish request set (abort): the frozen
        target set and any published links are kept, so a later
        publish starts from a clean request and an aborted request
        can never lock places forever."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET publish_targets = NULL,"
                " updated_at = ? WHERE id = ?",
                (utcnow_iso(), output_id),
            )

    def add_web_notification_item(self, key, kind, title, body):
        """Insert a global web inbox row (the GĐ3 table, same
        bot.db): shown to every web user, like the daily summary.
        Returns True when a new row was inserted (False on a
        duplicate key). The table is created if missing so bot-side
        writers never depend on the webapp migration having run."""
        with self._lock, self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS web_notification_items (
                    key        TEXT PRIMARY KEY,
                    kind       TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    body       TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""")
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO web_notification_items"
                " (key, kind, title, body, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, kind, title, body, utcnow_iso()))
        return cursor.rowcount > 0

    def validate_fb_targets(self, targets):
        """Keep only known place keys, deduped in order. Returns []
        for anything that is not a non-empty list of known keys."""
        if not isinstance(targets, list):
            return []
        valid = set(self.fb_place_keys())
        out = []
        for item in targets:
            if isinstance(item, str) and item in valid \
                    and item not in out:
                out.append(item)
        return out

    # --------------------------------------- TD-0024: Drive archive
    def record_archived_file(self, task_code, filename, drive_file_id,
                             web_view_link, size) -> None:
        """Remember one material file archived to Google Drive (its
        local copy is deleted afterwards); the row is what the web
        task detail renders as a Drive link instead of a broken
        thumbnail. INSERT OR REPLACE keeps a re-run idempotent."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO archived_files"
                " (task_code, filename, drive_file_id, web_view_link,"
                "  size, archived_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_code, filename, drive_file_id, web_view_link,
                 int(size), utcnow_iso()),
            )

    def archived_files_for_task(self, task_code):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM archived_files WHERE task_code = ?"
                " ORDER BY filename",
                (task_code,),
            ).fetchall()

    def archived_file(self, task_code, filename):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM archived_files WHERE task_code = ?"
                " AND filename = ?",
                (task_code, filename),
            ).fetchone()

    def delete_archived_file(self, task_code, filename) -> None:
        """Drop one archived row (TD-0024 restore): the file is
        local again, so the task detail renders it normally."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM archived_files WHERE task_code = ?"
                " AND filename = ?",
                (task_code, filename),
            )

    def set_output_fb_pending(self, output_id, targets):
        """In-progress toggle selection (bot choice UI); None clears."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET fb_pending_targets = ?,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(targets, ensure_ascii=False)
                 if targets is not None else None,
                 utcnow_iso(), output_id),
            )

    def fb_pending_selection(self, output, available):
        """Effective toggle selection: the stored pending list, or
        all available places when the choice was never stored (a
        choice opened before this column existed)."""
        raw = _fb_col(output, "fb_pending_targets")
        if raw is None:
            return list(available)
        return _fb_json_list(raw)

    def set_output_fb_request(self, output_id, targets):
        """Confirm a publish/schedule request set. While no place is
        done yet the full set is (re)written; once a place is done
        the set freezes so a retry cannot drop a published place."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT fb_place_urls FROM outputs WHERE id = ?",
                (output_id,)).fetchone()
            urls = _fb_json_map(row["fb_place_urls"]) if row else {}
            if urls:
                self._conn.execute(
                    "UPDATE outputs SET publish_targets = ?,"
                    " updated_at = ? WHERE id = ?",
                    (json.dumps(targets, ensure_ascii=False),
                     utcnow_iso(), output_id),
                )
            else:
                self._conn.execute(
                    "UPDATE outputs SET publish_targets = ?,"
                    " fb_target_set = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(targets, ensure_ascii=False),
                     json.dumps(targets, ensure_ascii=False),
                     utcnow_iso(), output_id),
                )

    def mark_fb_place_done(self, output_id, place, url):
        """Record one place published. Returns (all_set_done, state)
        with the fresh state — the caller flips PUBLISHED only when
        all_set_done is true."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM outputs WHERE id = ?", (output_id,)).fetchone()
            state = self.fb_state(row)
            urls = dict(state["urls"])
            urls[place] = url
            failed = [f for f in state["failed"] if f != place]
            target_set = state["target_set"] or [place]
            self._conn.execute(
                "UPDATE outputs SET fb_place_urls = ?,"
                " fb_place_failed = ?, fb_target_set = ?,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(urls, ensure_ascii=False),
                 json.dumps(failed, ensure_ascii=False),
                 json.dumps(target_set, ensure_ascii=False),
                 utcnow_iso(), output_id),
            )
            first = next((k for k in target_set if k in urls), None)
            if first:
                self._conn.execute(
                    "UPDATE outputs SET public_url = ? WHERE id = ?",
                    (urls[first], output_id),
                )
            fresh = self._conn.execute(
                "SELECT * FROM outputs WHERE id = ?", (output_id,)).fetchone()
            state = self.fb_state(fresh)
            return state["all_set_done"], state

    def mark_fb_place_failed(self, output_id, place):
        """Record one place failed; the output stays APPROVED_WAITING
        and a retry covers only the places not done."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM outputs WHERE id = ?", (output_id,)).fetchone()
            state = self.fb_state(row)
            failed = list(state["failed"])
            if place not in failed:
                failed.append(place)
            self._conn.execute(
                "UPDATE outputs SET fb_place_failed = ?,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(failed, ensure_ascii=False),
                 utcnow_iso(), output_id),
            )

    def mark_fb_place_retracted(self, output_id, place):
        """Record one place really deleted. Returns (all_retracted,
        remaining) — the caller flips RETRACTED only when remaining
        is empty."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM outputs WHERE id = ?", (output_id,)).fetchone()
            state = self.fb_state(row)
            retracted = list(state["retracted"])
            if place not in retracted:
                retracted.append(place)
            self._conn.execute(
                "UPDATE outputs SET fb_place_retracted = ?,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(retracted, ensure_ascii=False),
                 utcnow_iso(), output_id),
            )
            remaining = [k for k in state["done"] if k not in retracted]
            return (not remaining), remaining

    def list_due_outputs(self, now_iso: str):
        """Scheduled outputs whose time has come and that still wait
        to be published."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE scheduled_at IS NOT NULL"
                " AND scheduled_at <= ? AND status = 'APPROVED_WAITING'"
                " ORDER BY scheduled_at, id",
                (now_iso,),
            ).fetchall()

    def list_all_scheduled_outputs(self):
        """Every output currently holding a schedule, soonest
        first — the scheduled-posts management view."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE scheduled_at IS NOT NULL"
                " AND status = 'APPROVED_WAITING'"
                " ORDER BY scheduled_at, id"
            ).fetchall()

    def latest_published_output(self, channel: str):
        """Most recently published output on a channel (its
        updated_at is the publish moment) — for the too-close
        warning when a new post is scheduled right after one."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE channel = ? AND "
                "status = 'PUBLISHED' ORDER BY updated_at DESC LIMIT 1",
                (channel,),
            ).fetchone()

    def list_scheduled_outputs(self, channel: str):
        """All outputs currently holding a schedule on one channel —
        used for the too-close-together warning."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outputs WHERE scheduled_at IS NOT NULL"
                " AND status = 'APPROVED_WAITING' AND channel = ?"
                " ORDER BY scheduled_at",
                (channel,),
            ).fetchall()

    def set_output_public_url(self, output_id: int, url: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outputs SET public_url = ?, status = 'PUBLISHED',"
                " updated_at = ? WHERE id = ?",
                (url, utcnow_iso(), output_id),
            )

    # ------------------------------------------------ output cards
    def record_output_card(self, output_id: int, chat_id: int,
                           message_id: int, user_id=None) -> None:
        """Remember a channel card message so a newer card for the
        same output can freeze (de-button) it later."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO output_cards"
                " (output_id, chat_id, message_id, user_id, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (output_id, chat_id, message_id, user_id, utcnow_iso()),
            )

    def list_output_cards(self, output_id: int, chat_id: int | None = None):
        query = "SELECT * FROM output_cards WHERE output_id = ?"
        args: list = [output_id]
        if chat_id is not None:
            query += " AND chat_id = ?"
            args.append(chat_id)
        with self._lock:
            return self._conn.execute(
                query + " ORDER BY id", args).fetchall()

    def delete_output_card(self, row_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM output_cards WHERE id = ?", (row_id,))

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
