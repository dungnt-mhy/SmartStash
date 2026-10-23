"""Update handlers: commands, inline-button callbacks and media intake.

All permission decisions are re-checked here, server-side, on every
command and every callback -- the buttons a user happens to see never
grant them anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import roles
from storage import (
    CHANNELS,
    CHANNEL_ORDER,
    DEFAULT_DESTINATIONS,
    STATUS_APPROVED_WAITING,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
    STATUS_RETRACTED,
)

STATUS_LABELS = {
    STATUS_DRAFT: "Nháp (DRAFT)",
    STATUS_PENDING: "Chờ duyệt (PENDING)",
    STATUS_APPROVED_WAITING: "Đã duyệt, chờ đăng (APPROVED_WAITING)",
    STATUS_PUBLISHED: "Đã đăng (PUBLISHED)",
    STATUS_REJECTED: "Bị trả lại (REJECTED)",
    STATUS_CANCELLED: "Đã hủy (CANCELLED)",
    STATUS_RETRACTED: "Đã thu hồi (RETRACTED)",
}

#: Publish destinations, in display order: (key, label).
DESTINATIONS = (
    ("website", "Website"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
)
DESTINATION_KEYS = tuple(key for key, _label in DESTINATIONS)
DESTINATION_LABELS = dict(DESTINATIONS)


def task_destinations(task):
    """The task's destination keys, canonical order, defaults applied."""
    raw = None
    if task is not None and "destinations" in task.keys():
        raw = task["destinations"]
    if raw:
        try:
            chosen = json.loads(raw)
        except (ValueError, TypeError):
            chosen = None
        if isinstance(chosen, list):
            return [k for k in DESTINATION_KEYS if k in chosen]
    return list(DEFAULT_DESTINATIONS)


def destinations_label(task) -> str:
    chosen = task_destinations(task)
    if not chosen:
        return "(chưa chọn)"
    return " • ".join(DESTINATION_LABELS[k] for k in chosen)


def _can_toggle_destinations(user, task) -> bool:
    """Who may change publish destinations, per task status."""
    if user is None or user["role"] is None or task is None:
        return False
    status = task["status"]
    if status in (STATUS_DRAFT, STATUS_REJECTED):
        return user["telegram_id"] == task["creator_id"]
    if status in (STATUS_PENDING, STATUS_APPROVED_WAITING):
        return roles.can_review(user["role"])
    return False

CREATOR_ROLES = (roles.ROLE_WRITER, roles.ROLE_CONTRIBUTOR,
                 roles.ROLE_EDITOR, roles.ROLE_DEPUTY, roles.ROLE_ADMIN)

HELP_TEXT = (
    "🤖 Bot giao việc viết bài\n\n"
    "/start — đăng ký / bắt đầu\n"
    "/giaoviec — giao việc mới (hướng dẫn từng bước)\n"
    "/viec — xem danh sách việc\n"
    "/choduyet — việc đang chờ duyệt (người duyệt)\n"
    "/thongke — thống kê việc (người duyệt)\n"
    "/vaitro — xem danh sách vai trò\n"
    "/capquyen — cấp quyền (Admin / Phó Admin)\n"
    "/thuhoi — thu hồi quyền (Admin / Phó Admin)\n"
    "/danhsach — danh sách người dùng + vai trò (Admin / Phó Admin)\n\n"
    "Cách nhanh: dùng các nút ở bàn phím dưới khung chat. Cũng có thể "
    "gửi thẳng ảnh/tài liệu (kèm chú thích làm gợi ý), rồi nhắn một "
    "tin nhắn văn bản để chốt tạo việc.\n"
    "Trên thẻ việc: bấm ✏️ Chỉnh sửa gợi ý để sửa gợi ý; ở mục 🎯 "
    "Chọn kênh đăng bài, chạm từng kênh để chọn (✅) hoặc bỏ chọn (❌) "
    "rồi bấm OK để chốt và gửi lệnh cho Muse viết bài; bấm 🗑 Thu "
    "hồi việc để hủy việc chưa đăng."
)

#: Main reply-keyboard button labels. A tap on a reply-keyboard button
#: arrives as an ordinary text message whose text is exactly the label,
#: so these strings are protocol constants, matched verbatim in
#: handle_message.
BTN_NEW_TASK = "📝 Giao việc mới"
BTN_MY_TASKS = "📋 Việc của tôi"
BTN_ALL_TASKS = "📋 Tất cả việc"
BTN_PENDING = "⏳ Chờ duyệt"
BTN_STATS = "📊 Thống kê"
BTN_USERS = "👥 Người dùng"
BTN_HELP = "❓ Trợ giúp"
BTN_CLAUDE = "🤖 Muse đang làm"
BTN_HIDE_KB = "🙈 Ẩn bàn phím"


def main_keyboard(user):
    """The persistent reply keyboard for a user's role (the bot's home
    screen). Reviewers get the review queue and statistics; Admin and
    Phó Admin also get user management. Pending users only get help."""
    help_btn = {"text": BTN_CLAUDE}
    hide_row = [{"text": BTN_HIDE_KB}]
    if user is None or user["role"] is None:
        rows = [[help_btn], hide_row]
    elif roles.can_review(user["role"]):
        rows = [
            [{"text": BTN_NEW_TASK}, {"text": BTN_PENDING}],
            [{"text": BTN_ALL_TASKS}, {"text": BTN_STATS}],
        ]
        if user["role"] in (roles.ROLE_ADMIN, roles.ROLE_DEPUTY):
            rows.append([{"text": BTN_USERS}, help_btn])
        else:
            rows.append([help_btn])
        rows.append(hide_row)
    else:
        rows = [
            [{"text": BTN_NEW_TASK}, {"text": BTN_MY_TASKS}],
            [help_btn],
            hide_row,
        ]
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Gửi ảnh + gợi ý, hoặc bấm nút bên dưới…",
    }


#: Wizard reply-keyboard labels. While the creation wizard runs, the
#: reply keyboard is swapped to these pinned buttons so the user never
#: has to scroll up to the step prompt to advance or cancel.
BTN_WIZ_DONE = "✅ Xong, sang bước gợi ý"
BTN_WIZ_CANCEL = "❌ Huỷ giao việc"


def wizard_files_keyboard():
    return {
        "keyboard": [[{"text": BTN_WIZ_DONE}], [{"text": BTN_WIZ_CANCEL}]],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Đang giao việc — gửi ảnh/slide vào đây…",
    }


def wizard_hint_keyboard():
    return {
        "keyboard": [[{"text": BTN_WIZ_CANCEL}]],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Đang giao việc — nhập gợi ý…",
    }


#: Command lists for the bottom-left Menu button. Telegram has no
#: per-role command scope, so the base list is registered for everyone
#: and a longer list is registered per private chat (BotCommandScopeChat)
#: for reviewers and admins. The menu is presentation only; every
#: command is still authorised server-side.
BASE_COMMANDS = [
    {"command": "start", "description": "Bắt đầu / đăng ký"},
    {"command": "menu", "description": "Hiện bàn phím chính"},
    {"command": "giaoviec", "description": "Giao việc mới (từng bước)"},
    {"command": "viec", "description": "Danh sách việc"},
    {"command": "vaitro", "description": "Danh sách vai trò"},
    {"command": "help", "description": "Trợ giúp"},
]
REVIEWER_COMMANDS = BASE_COMMANDS + [
    {"command": "choduyet", "description": "Việc đang chờ duyệt"},
    {"command": "thongke", "description": "Thống kê việc"},
]
ADMIN_COMMANDS = REVIEWER_COMMANDS + [
    {"command": "danhsach", "description": "Danh sách người dùng"},
    {"command": "capquyen", "description": "Cấp quyền cho người dùng"},
    {"command": "thuhoi", "description": "Thu hồi quyền người dùng"},
]


def commands_for_role(role):
    if role in (roles.ROLE_ADMIN, roles.ROLE_DEPUTY):
        return ADMIN_COMMANDS
    if role == roles.ROLE_EDITOR:
        return REVIEWER_COMMANDS
    return BASE_COMMANDS


def _btn(text, callback_data):
    return {"text": text, "callback_data": callback_data}


def _keyboard(rows):
    return {"inline_keyboard": rows} if rows else None


_ICT = None  # lazy Asia/Saigon timezone (UTC+7, no DST)


def _format_created(iso_utc: str) -> str:
    """Format a stored ISO UTC timestamp as 'HH:MM dd/mm/yyyy' in Vietnam time."""
    global _ICT
    if _ICT is None:
        from datetime import timedelta, timezone

        _ICT = timezone(timedelta(hours=7), name="ICT")
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            from datetime import timezone as _tz

            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_ICT).strftime("%H:%M %d/%m/%Y")
    except (ValueError, TypeError):
        return iso_utc or ""


def _display_name(tg_user: dict) -> str:
    parts = [tg_user.get("first_name") or "", tg_user.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or tg_user.get("username") or str(tg_user.get("id"))


class BotApp:
    def __init__(self, store, api, files_dir=None, download_files: bool = False):
        self.store = store
        self.api = api
        self.files_dir = Path(files_dir) if files_dir else None
        self.download_files = download_files

    # ------------------------------------------------------------ utilities
    def _send(self, chat_id, text, reply_markup=None):
        if chat_id is None:
            return None
        return self.api.send_message(chat_id, text, reply_markup=reply_markup)

    def _answer(self, callback_id, text=None, show_alert=False):
        try:
            return self.api.answer_callback_query(
                callback_id, text=text, show_alert=show_alert
            )
        except Exception:
            return None

    def _edit_card(self, chat_id, message_id, text, reply_markup=None):
        """Edit a card message in place. A missing keyboard means the
        card is closed, so an empty inline keyboard is sent to strip
        any buttons the old card still shows."""
        if chat_id is None or message_id is None:
            return None
        fn = getattr(self.api, "edit_message_text", None)
        if fn is None:
            return None
        try:
            return fn(
                chat_id, message_id, text,
                reply_markup=reply_markup if reply_markup is not None
                else {"inline_keyboard": []},
            )
        except Exception:
            return None

    def _edit_markup(self, chat_id, message_id, reply_markup):
        if chat_id is None or message_id is None:
            return None
        fn = getattr(self.api, "edit_message_reply_markup", None)
        if fn is None:
            return None
        try:
            return fn(chat_id, message_id, reply_markup=reply_markup)
        except Exception:
            return None

    def _send_main(self, chat_id, text, user):
        """Send a plain message carrying the user's main reply keyboard."""
        return self._send(chat_id, text, reply_markup=main_keyboard(user))

    # ------------------------------------------------- menu command sync
    def sync_user_commands(self, user):
        """Register the Menu-button command list for one user's private
        chat, matching their current role. Never raises: the menu is a
        convenience, not a permission."""
        if user is None:
            return
        fn = getattr(self.api, "set_my_commands", None)
        if fn is None:
            return
        chat_id = user["chat_id"] or user["telegram_id"]
        try:
            fn(commands_for_role(user["role"]),
               scope={"type": "chat", "chat_id": chat_id})
        except Exception:
            pass

    def sync_all_commands(self):
        """Register the base command list globally and refresh every
        known user's per-chat list. Called once at startup."""
        fn = getattr(self.api, "set_my_commands", None)
        if fn is None:
            return
        for scope in (None, {"type": "all_private_chats"}):
            try:
                fn(list(BASE_COMMANDS), scope=scope)
            except Exception:
                pass
        for u in self.store.list_users():
            self.sync_user_commands(u)

    def _touch_user(self, tg_user, chat_id):
        """Refresh profile/chat_id of a known user (role is preserved)."""
        existing = self.store.get_user(tg_user["id"])
        if existing is not None:
            self.store.register_user(
                tg_user["id"], tg_user.get("username"),
                _display_name(tg_user), chat_id,
            )
            return self.store.get_user(tg_user["id"])
        return None

    # ------------------------------------------------------------- dispatch
    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
        elif "message" in update:
            self.handle_message(update["message"])
        # edited_message / channel posts are ignored on purpose.

    # ------------------------------------------------------------- messages
    def handle_message(self, message: dict) -> None:
        tg_user = message.get("from") or {}
        user_id = tg_user.get("id")
        chat_id = (message.get("chat") or {}).get("id")
        if user_id is None or chat_id is None:
            return
        user = self._touch_user(tg_user, chat_id)

        text = message.get("text") or ""

        if text.startswith("/"):
            command = text.split()[0].split("@")[0].lower()
            self.handle_command(command, user, tg_user, chat_id)
            return

        state = self.store.get_user_state(user_id)

        # A creator who tapped "Chỉnh sửa" is expected to type the new hint.
        if state and state["state"] == "await_edit_hint" and text:
            self._apply_edit_hint(user, chat_id, text, state["payload"])
            return

        # A reviewer who tapped "Trả lại" on a channel card is expected
        # to type the reject reason for that output.
        if state and state["state"] == "await_output_reject_reason" and text:
            self._apply_output_reject_reason(
                user, chat_id, text, state["payload"])
            return

        # Main reply-keyboard buttons arrive as ordinary text messages
        # whose text is exactly the button label.
        if text and self._handle_keyboard_label(user, tg_user, chat_id, text):
            return

        # The step-by-step creation wizard owns messages while active.
        if state and state["state"] in ("wizard_files", "wizard_hint"):
            self._wizard_message(
                user, chat_id, state, text,
                self._extract_media(message), message.get("caption"),
            )
            return

        media_item = self._extract_media(message)
        caption = message.get("caption")

        if media_item is not None:
            self._handle_media(user, chat_id, media_item, caption)
            return

        if text:
            self._handle_plain_text(user, tg_user, chat_id, text)

    @staticmethod
    def _extract_media(message: dict):
        photos = message.get("photo")
        if photos:
            biggest = photos[-1]  # Telegram sends ascending sizes
            return {"type": "photo", "file_id": biggest["file_id"]}
        document = message.get("document")
        if document:
            return {
                "type": "document",
                "file_id": document["file_id"],
                "file_name": document.get("file_name"),
            }
        return None

    def _handle_media(self, user, chat_id, item, caption):
        if user is None or user["role"] is None:
            self._send(
                chat_id,
                "Bạn chưa được cấp quyền. Hãy gửi /start để đăng ký, "
                "Admin sẽ cấp quyền cho bạn.",
            )
            return
        if user["role"] not in CREATOR_ROLES:
            self._send(chat_id, "Vai trò của bạn không được gửi tư liệu tạo việc.")
            return
        self.store.buffer_add(chat_id, user["telegram_id"], item, caption)
        buffered = self.store.buffer_get(chat_id, user["telegram_id"])
        self._send(
            chat_id,
            f"Đã nhận {item['type'] == 'photo' and 'ảnh' or 'tài liệu'} "
            f"(tổng {len(buffered['items'])} file). "
            "Gửi thêm nếu cần, hoặc nhắn một tin nhắn văn bản "
            "(gợi ý / nội dung) để chốt tạo việc.",
        )

    def _handle_plain_text(self, user, tg_user, chat_id, text):
        if user is None or user["role"] is None:
            self._send(
                chat_id,
                "Bạn chưa được cấp quyền tạo việc. Hãy gửi /start để đăng ký, "
                "Admin sẽ cấp quyền cho bạn.",
            )
            return

        buffered = self.store.buffer_get(chat_id, user["telegram_id"])
        if buffered["items"]:
            hints = list(buffered["hints"]) + [text]
            file_ids = [i["file_id"] for i in buffered["items"]]
            self.store.buffer_clear(chat_id, user["telegram_id"])
            self._create_task(user, chat_id, "\n".join(hints), file_ids)
            return

        if user["role"] == roles.ROLE_WRITER:
            # A writer may send their own draft text with no media at all.
            self._create_task(user, chat_id, text, [])
            return

        if user["role"] == roles.ROLE_CONTRIBUTOR:
            self._send(
                chat_id,
                "Cộng tác viên hãy gửi ảnh/tài liệu trước, "
                "sau đó nhắn gợi ý để chốt tạo việc.",
            )
            return

        self._send(
            chat_id,
            "Hãy gửi ảnh/tài liệu kèm gợi ý để tạo việc, "
            "hoặc dùng /viec để xem danh sách việc.",
        )

    def _create_task(self, user, chat_id, hint, file_ids, destinations=None):
        task = self.store.create_task(
            creator_id=user["telegram_id"], creator_role=user["role"],
            hint=hint, file_ids=file_ids, destinations=destinations,
        )
        self.store.audit(
            user["telegram_id"], "create_task", "task", task["code"],
            detail=f"role={user['role']} files={len(file_ids)}",
        )
        # Option A: one output record (own lifecycle, own card) per
        # selected publish channel.
        self.store.sync_outputs_for_task(task)
        task = self.store.get_task(task["code"])
        self._maybe_download_files(task)
        self._send(chat_id, self.task_card_text(task),
                   reply_markup=self.task_keyboard(task, user))
        # Option A: reviewers are NOT sent the (still empty) task card
        # at creation — there is nothing to review yet. They receive
        # each channel card when Muse attaches its draft (botctl
        # attach-output-draft) and again when it is submitted.
        return task

    def _maybe_download_files(self, task):
        if not self.download_files or self.files_dir is None:
            return
        file_ids = json.loads(task["file_ids"])
        if not file_ids:
            return
        dest_dir = self.files_dir / task["code"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        for idx, file_id in enumerate(file_ids, start=1):
            try:
                info = self.api.get_file(file_id)
                file_path = (info or {}).get("file_path")
                if not file_path:
                    continue
                name = Path(file_path).name or f"file-{idx}"
                self.api.download_file(file_path, dest_dir / name)
            except Exception:
                # A failed download must never block task creation; the
                # file_id stays stored on the task for a later retry.
                continue

    # ------------------------------------------------------- task card bits
    def task_card_text(self, task) -> str:
        creator = self.store.get_user(task["creator_id"])
        creator_name = (
            creator["display_name"] if creator else str(task["creator_id"])
        )
        file_ids = json.loads(task["file_ids"])
        lines = [
            f"📝 Việc {task['code']} — {STATUS_LABELS.get(task['status'], task['status'])}",
            f"Người tạo: {creator_name} ({roles.role_title(task['creator_role'])})",
            f"Thời gian tạo: {_format_created(task['created_at'])}",
            f"Nội dung/gợi ý: {task['hint'] or '(không có)'}",
            f"Số file đính kèm: {len(file_ids)}",
            f"Nơi đăng: {destinations_label(task)}",
        ]
        if task["draft_url"]:
            lines.append(f"Link nháp: {task['draft_url']}")
        if task["public_url"]:
            lines.append(f"Link bài đã đăng: {task['public_url']}")
        if task["status"] == STATUS_REJECTED and task["reject_reason"]:
            lines.append(f"Lý do trả lại: {task['reject_reason']}")
        live_outputs = [
            o for o in self.store.list_outputs(task["code"])
            if o["status"] != STATUS_CANCELLED
        ]
        if live_outputs:
            have_drafts = sum(1 for o in live_outputs if o["draft_ref"])
            if have_drafts == 0:
                lines.append(
                    "✍️ Bản nháp: chưa có — đang chờ Muse viết bài.")
            else:
                lines.append(
                    f"✍️ Bản nháp: đã có {have_drafts}/{len(live_outputs)}"
                    " kênh.")
            lines.append("Đầu ra theo kênh:")
            for output in live_outputs:
                meta = CHANNELS.get(output["channel"], {})
                label = meta.get("label", output["channel"])
                lines.append(
                    f"• {label}: "
                    f"{STATUS_LABELS.get(output['status'], output['status'])}"
                )
        return "\n".join(lines)

    def task_keyboard(self, task, viewer):
        """Buttons on a task card: dossier management only.

        Option A (2026-10-23): submitting, reviewing and publishing the
        actual content happens on the per-channel cards
        (``output_keyboard``), because that is where the draft lives.
        The task card only edits the hint, picks the publish channels
        and cancels the task.
        Closed tasks (CANCELLED / RETRACTED) show no buttons at all.
        """
        if viewer is None:
            return None
        status = task["status"]
        if status in (STATUS_CANCELLED, STATUS_RETRACTED):
            return None
        rows = []
        code = task["code"]
        is_creator = viewer["telegram_id"] == task["creator_id"]
        is_admin = viewer["role"] == roles.ROLE_ADMIN
        if is_creator and status in (STATUS_DRAFT, STATUS_REJECTED):
            rows.append([_btn("✏️ Chỉnh sửa gợi ý", f"task_edit:{code}")])
        live_outputs = [
            o for o in self.store.list_outputs(code)
            if o["status"] != STATUS_CANCELLED
        ]
        can_toggle = _can_toggle_destinations(viewer, task)
        if can_toggle:
            # The channel picker is a staged selection (owner's model):
            # taps flip ✅/❌ on the card only; the OK button beside
            # Thu hồi việc commits the choice and sends it to Muse.
            rows.append([_btn("🎯 Chọn kênh đăng bài",
                              f"task_dest_head:{code}")])
            pending = self._dest_pending(viewer, task)
            chosen = pending if pending is not None \
                else task_destinations(task)
            rows.append([
                _btn(
                    f"{'✅' if key in chosen else '❌'} {label}",
                    f"task_dest:{code}:{key}",
                )
                for key, label in DESTINATIONS
            ])
            if len(chosen) < len(DESTINATIONS):
                rows.append([_btn("✅ Chọn tất cả", f"task_dest_all:{code}")])
        elif live_outputs:
            rows.append([_btn("📋 Thẻ các kênh", f"task_outputs:{code}")])
        bottom = []
        if status in (STATUS_DRAFT, STATUS_PENDING, STATUS_REJECTED) and (
            is_creator or is_admin
        ):
            bottom.append(_btn("🗑 Thu hồi việc", f"task_cancel:{code}"))
        if can_toggle:
            bottom.append(_btn("✅ OK", f"task_dest_ok:{code}"))
        if bottom:
            rows.append(bottom)
        return _keyboard(rows)

    def _dest_pending(self, user, task):
        """The viewer's staged (not yet OK-confirmed) channel selection
        for this task, or None. Toggles only stage; OK commits."""
        if user is None or task is None:
            return None
        state = self.store.get_user_state(user["telegram_id"])
        if state and state["state"] == "task_dest_pending":
            payload = state["payload"] or {}
            if payload.get("code") == task["code"]:
                chosen = payload.get("chosen")
                if isinstance(chosen, list):
                    return [k for k in DESTINATION_KEYS if k in chosen]
        return None

    def _notify_reviewers(self, task, exclude_id=None):
        for reviewer in self.store.reviewers():
            if reviewer["telegram_id"] == exclude_id:
                continue
            self._send(
                reviewer["chat_id"], self.task_card_text(task),
                reply_markup=self.task_keyboard(task, reviewer),
            )

    def _notify_creator(self, task, prefix=""):
        creator = self.store.get_user(task["creator_id"])
        if creator is None or creator["chat_id"] is None:
            return
        text = (prefix + "\n" if prefix else "") + self.task_card_text(task)
        self._send(creator["chat_id"], text,
                   reply_markup=self.task_keyboard(task, creator))

    # ------------------------------------------------------------- commands
    def handle_command(self, command, user, tg_user, chat_id):
        handler = {
            "/start": self._cmd_start,
            "/menu": self._cmd_menu,
            "/vaitro": self._cmd_vaitro,
            "/danhsach": self._cmd_danhsach,
            "/capquyen": self._cmd_capquyen,
            "/thuhoi": self._cmd_thuhoi,
            "/viec": self._cmd_viec,
            "/giaoviec": self._cmd_giaoviec,
            "/choduyet": self._cmd_choduyet,
            "/thongke": self._cmd_thongke,
            "/help": self._cmd_help,
            "/trogiup": self._cmd_help,
        }.get(command)
        if handler is None:
            self._send(chat_id, "Lệnh chưa được hỗ trợ.\n\n" + HELP_TEXT)
            return
        handler(user, tg_user, chat_id)

    def _cmd_help(self, user, tg_user, chat_id):
        self._send(chat_id, HELP_TEXT)

    def _cmd_claude_status(self, user, tg_user, chat_id):
        """What Muse is currently working on, from live bot data:
        channels still waiting for a draft, drafts waiting to be
        submitted, and cards waiting for review."""
        writing, drafted, reviewing = [], [], []
        for task in self.store.list_tasks():
            if task["status"] in (STATUS_CANCELLED, STATUS_RETRACTED,
                                  STATUS_PUBLISHED):
                continue
            for output in self.store.list_outputs(task["code"]):
                if output["status"] == STATUS_CANCELLED:
                    continue
                label = CHANNELS.get(output["channel"], {}).get(
                    "label", output["channel"])
                item = f"{task['code']} · {label}"
                if output["status"] == STATUS_PENDING:
                    reviewing.append(item)
                elif not output["draft_ref"]:
                    writing.append(item)
                elif output["status"] == STATUS_DRAFT:
                    drafted.append(item)
        lines = ["🤖 Muse đang làm:"]
        if not (writing or drafted or reviewing):
            lines.append(
                "Hiện không có đầu việc nào đang chờ — mọi thứ đã xong. 🎉")
        if writing:
            lines.append("✍️ Đang chờ Muse viết nháp: "
                         + ", ".join(writing[:10]))
        if drafted:
            lines.append("📄 Đã có nháp, chờ gửi duyệt: "
                         + ", ".join(drafted[:10]))
        if reviewing:
            lines.append("⏳ Đang chờ duyệt: " + ", ".join(reviewing[:10]))
        self._send(chat_id, "\n".join(lines))

    def _cmd_menu(self, user, tg_user, chat_id):
        """Show the main reply keyboard again after it was hidden."""
        self._send_main(
            chat_id,
            "⌨️ Đây là bàn phím chính của bạn. Khi muốn ẩn, bấm "
            "🙈 Ẩn bàn phím ở hàng dưới cùng.",
            user)

    def _hide_keyboard(self, chat_id):
        self._send(
            chat_id,
            "Đã ẩn bàn phím. Khi cần hiện lại, gõ /menu.",
            reply_markup={"remove_keyboard": True})

    def _cmd_start(self, user, tg_user, chat_id):
        if user is None:
            user = self.store.register_user(
                tg_user["id"], tg_user.get("username"),
                _display_name(tg_user), chat_id,
            )
        self.sync_user_commands(user)
        lines = [f"Xin chào {user['display_name']}!"]
        if user["role"]:
            lines.append(f"Vai trò của bạn: {roles.role_title(user['role'])}.")
            lines.append(
                "Bấm 📝 Giao việc mới để tạo việc từng bước, "
                "hoặc gửi thẳng ảnh kèm gợi ý như trước."
            )
            self._send(chat_id, "\n".join(lines),
                       reply_markup=main_keyboard(user))
            return
        lines.append(
            "Bạn đã đăng ký và đang chờ cấp quyền. "
            "Admin sẽ cấp quyền cho bạn ngay trong Telegram."
        )
        self._send(chat_id, "\n".join(lines),
                   reply_markup=main_keyboard(user))
        if not self.store.has_admin():
            self._send(
                chat_id,
                "Hiện chưa có Admin. Nếu bạn là chủ website, "
                "bấm nút dưới đây để nhận quyền Admin.",
                reply_markup=_keyboard(
                    [[_btn("👑 Nhận quyền Admin", "claim_admin")]]),
            )

    def _cmd_vaitro(self, user, tg_user, chat_id):
        self._send(chat_id, roles.roles_overview_text())

    def _require_admin_or_deputy(self, user, chat_id) -> bool:
        if user is None or user["role"] not in (roles.ROLE_ADMIN, roles.ROLE_DEPUTY):
            self._send(chat_id, "⛔ Bạn không có quyền dùng lệnh này.")
            return False
        return True

    def _cmd_danhsach(self, user, tg_user, chat_id):
        if not self._require_admin_or_deputy(user, chat_id):
            return
        users = self.store.list_users()
        if not users:
            self._send(chat_id, "Chưa có người dùng nào.")
            return
        lines = ["👥 Danh sách người dùng:"]
        for u in users:
            username = f"@{u['username']}" if u["username"] else "(không có username)"
            publish = " • được đăng bài" if roles.can_publish(u) else ""
            lines.append(
                f"- {u['display_name']} {username} — "
                f"{roles.role_title(u['role'])}{publish}"
            )
        self._send(chat_id, "\n".join(lines))

    def _cmd_capquyen(self, user, tg_user, chat_id):
        if not self._require_admin_or_deputy(user, chat_id):
            return
        rows = []
        for target in self.store.list_users():
            if target["telegram_id"] == user["telegram_id"]:
                continue
            # A deputy may only manage writer/contributor (or pending) users.
            if user["role"] == roles.ROLE_DEPUTY and target["role"] not in (
                None, roles.ROLE_WRITER, roles.ROLE_CONTRIBUTOR,
            ):
                continue
            label = (
                f"{target['display_name']} — {roles.role_title(target['role'])}"
            )
            rows.append([_btn(label, f"grant_pick:{target['telegram_id']}")])
        if not rows:
            self._send(chat_id, "Chưa có người dùng nào để cấp quyền.")
            return
        self._send(chat_id, "Chọn người cần cấp quyền:",
                   reply_markup=_keyboard(rows))

    def _cmd_thuhoi(self, user, tg_user, chat_id):
        if not self._require_admin_or_deputy(user, chat_id):
            return
        rows = []
        for target in self.store.list_users():
            if target["role"] is None:
                continue
            if not roles.can_revoke(user["role"], target["role"]):
                continue
            if target["telegram_id"] == user["telegram_id"]:
                continue
            label = f"{target['display_name']} — {roles.role_title(target['role'])}"
            rows.append([_btn(label, f"revoke_pick:{target['telegram_id']}")])
        if not rows:
            self._send(chat_id, "Không có người dùng nào bạn có thể thu hồi quyền.")
            return
        self._send(chat_id, "Chọn người cần thu hồi quyền:",
                   reply_markup=_keyboard(rows))

    def _cmd_viec(self, user, tg_user, chat_id):
        if user is None or user["role"] is None:
            self._send(
                chat_id,
                "Bạn chưa được cấp quyền. Hãy gửi /start để đăng ký trước.",
            )
            return
        if roles.can_review(user["role"]):
            tasks = self.store.list_tasks(limit=20)
            heading = "📋 Danh sách việc gần đây:"
        else:
            tasks = self.store.list_tasks_by_creator(user["telegram_id"], limit=20)
            heading = "📋 Việc của bạn:"
        if not tasks:
            self._send(chat_id, "Chưa có việc nào.")
            return
        lines = [heading]
        for t in tasks:
            hint = (t["hint"] or "").replace("\n", " ")[:60]
            creator = self.store.get_user(t["creator_id"])
            creator_name = creator["display_name"] if creator else str(t["creator_id"])
            lines.append(
                f"- {t['code']} • {STATUS_LABELS.get(t['status'], t['status'])}"
                f" • {creator_name} • {_format_created(t['created_at'])} • {hint}"
            )
        self._send(chat_id, "\n".join(lines))

    def _cmd_choduyet(self, user, tg_user, chat_id):
        if user is None or not roles.can_review(user["role"]):
            self._send(chat_id, "⛔ Bạn không có quyền xem hàng chờ duyệt.")
            return
        tasks = [
            t for t in self.store.list_tasks(limit=50)
            if t["status"] == STATUS_PENDING
        ]
        if not tasks:
            self._send(chat_id, "⏳ Không có việc nào đang chờ duyệt.")
            return
        lines = ["⏳ Việc đang chờ duyệt:"]
        for t in tasks:
            hint = (t["hint"] or "").replace("\n", " ")[:60]
            creator = self.store.get_user(t["creator_id"])
            creator_name = creator["display_name"] if creator else str(t["creator_id"])
            lines.append(
                f"- {t['code']} • {creator_name} • "
                f"{_format_created(t['created_at'])} • {hint}"
            )
        lines.append("Mở thẻ việc trong cuộc trò chuyện để duyệt hoặc trả lại.")
        self._send(chat_id, "\n".join(lines))

    def _cmd_thongke(self, user, tg_user, chat_id):
        if user is None or not roles.can_review(user["role"]):
            self._send(chat_id, "⛔ Bạn không có quyền xem thống kê.")
            return
        task_counts = self.store.count_tasks_by_status()
        total = sum(task_counts.values())
        lines = [f"📊 Thống kê việc (tổng {total} việc):"]
        for status in (STATUS_DRAFT, STATUS_PENDING, STATUS_APPROVED_WAITING,
                       STATUS_PUBLISHED, STATUS_REJECTED, STATUS_CANCELLED,
                       STATUS_RETRACTED):
            if task_counts.get(status):
                lines.append(
                    f"• {STATUS_LABELS.get(status, status)}: {task_counts[status]}"
                )
        out_counts = self.store.count_outputs_by_status()
        if out_counts:
            by_channel = {}
            for channel, status, n in out_counts:
                by_channel.setdefault(channel, []).append((status, n))
            lines.append("")
            lines.append("Đầu ra theo kênh:")
            for channel in CHANNEL_ORDER:
                if channel not in by_channel:
                    continue
                label = CHANNELS.get(channel, {}).get("label", channel)
                parts = ", ".join(
                    f"{STATUS_LABELS.get(s, s)}: {n}"
                    for s, n in by_channel[channel]
                )
                lines.append(f"• {label}: {parts}")
        self._send(chat_id, "\n".join(lines))

    # ------------------------------------------- main keyboard label taps
    def _handle_keyboard_label(self, user, tg_user, chat_id, text) -> bool:
        """Dispatch a main reply-keyboard tap. Returns True when the
        text was a button label (and has been handled)."""
        if text == BTN_NEW_TASK:
            self._wizard_start(user, chat_id)
        elif text == BTN_WIZ_DONE:
            self._wizard_advance_from_files(user, chat_id)
        elif text == BTN_WIZ_CANCEL:
            self._wizard_cancel(user, chat_id)
        elif text in (BTN_MY_TASKS, BTN_ALL_TASKS):
            self._cmd_viec(user, tg_user, chat_id)
        elif text == BTN_PENDING:
            self._cmd_choduyet(user, tg_user, chat_id)
        elif text == BTN_STATS:
            self._cmd_thongke(user, tg_user, chat_id)
        elif text == BTN_USERS:
            self._cmd_danhsach(user, tg_user, chat_id)
        elif text == BTN_HELP:
            self._cmd_help(user, tg_user, chat_id)
        elif text == BTN_CLAUDE:
            self._cmd_claude_status(user, tg_user, chat_id)
        elif text == BTN_HIDE_KB:
            self._hide_keyboard(chat_id)
        else:
            return False
        return True

    # ------------------------------------------------ creation wizard
    def _cmd_giaoviec(self, user, tg_user, chat_id):
        self._wizard_start(user, chat_id)

    def _wizard_start(self, user, chat_id):
        if user is None or user["role"] is None:
            self._send(
                chat_id,
                "Bạn chưa được cấp quyền. Hãy gửi /start để đăng ký, "
                "Admin sẽ cấp quyền cho bạn.",
            )
            return
        if user["role"] not in CREATOR_ROLES:
            self._send(chat_id, "Vai trò của bạn không được tạo việc.")
            return
        # Start clean: drop any half-finished legacy intake buffer so its
        # files cannot leak into this wizard task (or a later one).
        self.store.buffer_clear(chat_id, user["telegram_id"])
        payload = {
            "files": [], "captions": [],
            "destinations": list(DEFAULT_DESTINATIONS), "hint": "",
        }
        self.store.set_user_state(user["telegram_id"], "wizard_files", payload)
        self._send(
            chat_id,
            "📝 Giao việc mới — Bước 1/3: Gửi ảnh/slide cho việc này "
            "(gửi bao nhiêu cũng được). Gửi xong, bấm nút ✅ Xong ở bàn "
            "phím bên dưới — nút luôn ở đó, không cần kéo lên tìm.",
            reply_markup=wizard_files_keyboard(),
        )

    def _wizard_message(self, user, chat_id, state, text, media_item, caption):
        if user is None or user["role"] is None:
            return
        payload = state["payload"]
        step = state["state"]
        if media_item is not None:
            payload.setdefault("files", []).append(media_item["file_id"])
            if caption:
                payload.setdefault("captions", []).append(caption)
            if step == "wizard_files":
                # One single counter message, edited in place as files
                # arrive — no fresh ack (and no fresh button block) per
                # photo. The finish/cancel actions live on the pinned
                # reply keyboard under the chat box.
                self._wizard_file_counter(user, chat_id, payload)
            else:
                self.store.set_user_state(user["telegram_id"], step, payload)
                self._send(chat_id, "Đã thêm file vào việc đang tạo.")
            return
        if not text:
            return
        if step == "wizard_files":
            if text.strip().lower() in ("xong", "xong rồi", "đủ rồi", "done", "ok"):
                self._wizard_advance_from_files(user, chat_id)
                return
            self._send(
                chat_id,
                "Đang ở Bước 1: cứ gửi ảnh/slide vào đây. Xong thì bấm "
                "nút ✅ Xong ở bàn phím bên dưới, hoặc gõ chữ \"xong\".",
            )
            return
        # wizard_hint: this text is the hint (or the writer's draft).
        hints = list(payload.get("captions") or []) + [text]
        payload["hint"] = "\n".join(h for h in hints if h)
        self.store.set_user_state(
            user["telegram_id"], "wizard_channels", payload)
        self._send(
            chat_id,
            "Bước 3/3: Chọn nơi đăng cho việc này, rồi bấm ✅ Tạo việc.",
            reply_markup=self._wizard_channels_keyboard(payload),
        )

    def _wizard_file_counter(self, user, chat_id, payload):
        """Maintain the single 'files received' message of the wizard's
        first step: send it once, then edit its count in place."""
        count = len(payload.get("files") or [])
        text = (
            f"📎 Đã nhận {count} file cho việc này. Gửi thêm nếu cần; "
            "xong thì bấm nút ✅ Xong ở bàn phím bên dưới."
        )
        message_id = payload.get("status_msg_id")
        if message_id:
            if self._edit_card(chat_id, message_id, text) is not None:
                self.store.set_user_state(
                    user["telegram_id"], "wizard_files", payload)
                return
            payload["status_msg_id"] = None
        result = self._send(chat_id, text)
        if isinstance(result, dict):
            payload["status_msg_id"] = result.get("message_id")
        self.store.set_user_state(
            user["telegram_id"], "wizard_files", payload)

    def _wizard_channels_keyboard(self, payload):
        chosen = payload.get("destinations") or []
        rows = [[
            _btn(
                f"{'✅' if key in chosen else '⬜'} {label}",
                f"wiz_dest:{key}",
            )
            for key, label in DESTINATIONS
        ]]
        if len(chosen) < len(DESTINATIONS):
            rows.append([_btn("✅ Chọn tất cả", "wiz_dest_all")])
        rows.append([
            _btn("✅ Tạo việc", "wiz_create"),
            _btn("❌ Huỷ", "wiz_cancel"),
        ])
        return _keyboard(rows)

    def _cb_wizard_dest_all(self, callback_id, user, chat_id, message):
        state = self._wizard_state(user)
        if state is None or state["state"] != "wizard_channels":
            self._answer(callback_id, "Phiên giao việc đã kết thúc.",
                         show_alert=True)
            return
        payload = state["payload"]
        payload["destinations"] = list(DESTINATION_KEYS)
        self.store.set_user_state(
            user["telegram_id"], "wizard_channels", payload)
        self._answer(callback_id, "Đã chọn tất cả nơi đăng.")
        self._edit_markup(
            chat_id, (message or {}).get("message_id"),
            self._wizard_channels_keyboard(payload),
        )

    def _wizard_state(self, user):
        if user is None:
            return None
        state = self.store.get_user_state(user["telegram_id"])
        if state and state["state"].startswith("wizard_"):
            return state
        return None

    def _wizard_advance_from_files(self, user, chat_id, callback_id=None):
        """Shared by the inline button, the pinned reply-keyboard button
        and typing "xong": move the wizard from the files step to the
        hint step."""
        state = self._wizard_state(user)
        if state is None or state["state"] != "wizard_files":
            if callback_id:
                self._answer(callback_id, "Phiên giao việc đã kết thúc.",
                             show_alert=True)
            else:
                self._send_main(
                    chat_id,
                    "Bạn không ở bước gửi file nào. "
                    "Bấm 📝 Giao việc mới để bắt đầu.",
                    user,
                )
            return
        payload = state["payload"]
        if user["role"] == roles.ROLE_CONTRIBUTOR and not payload.get("files"):
            note = "Cộng tác viên cần gửi ít nhất một ảnh/tài liệu trước."
            if callback_id:
                self._answer(callback_id, note, show_alert=True)
            else:
                self._send(chat_id, "⛔ " + note)
            return
        self.store.set_user_state(user["telegram_id"], "wizard_hint", payload)
        if callback_id:
            self._answer(callback_id)
        self._send(
            chat_id,
            "Bước 2/3: Nhắn gợi ý ngắn cho việc này "
            "(Nhân viên viết bài có thể dán luôn bản thảo).",
            reply_markup=wizard_hint_keyboard(),
        )

    def _cb_wizard_done_files(self, callback_id, user, chat_id):
        self._wizard_advance_from_files(user, chat_id, callback_id)

    def _cb_wizard_dest(self, callback_id, user, chat_id, dest, message):
        state = self._wizard_state(user)
        if state is None or state["state"] != "wizard_channels":
            self._answer(callback_id, "Phiên giao việc đã kết thúc.",
                         show_alert=True)
            return
        if dest not in DESTINATION_KEYS:
            self._answer(callback_id, "Nơi đăng không hợp lệ.",
                         show_alert=True)
            return
        payload = state["payload"]
        chosen = list(payload.get("destinations") or [])
        if dest in chosen:
            chosen.remove(dest)
        else:
            chosen.append(dest)
        payload["destinations"] = [k for k in DESTINATION_KEYS if k in chosen]
        self.store.set_user_state(
            user["telegram_id"], "wizard_channels", payload)
        self._answer(callback_id)
        self._edit_markup(
            chat_id, (message or {}).get("message_id"),
            self._wizard_channels_keyboard(payload),
        )

    def _cb_wizard_create(self, callback_id, user, chat_id):
        state = self._wizard_state(user)
        if state is None or state["state"] != "wizard_channels":
            self._answer(callback_id, "Phiên giao việc đã kết thúc.",
                         show_alert=True)
            return
        if user["role"] not in CREATOR_ROLES:
            self._answer(callback_id, "⛔ Vai trò của bạn không được tạo việc.",
                         show_alert=True)
            return
        payload = state["payload"]
        destinations = payload.get("destinations") or []
        if not destinations:
            self._answer(callback_id, "Hãy chọn ít nhất một nơi đăng.",
                         show_alert=True)
            return
        if user["role"] == roles.ROLE_CONTRIBUTOR and not payload.get("files"):
            self._answer(
                callback_id,
                "Cộng tác viên cần ít nhất một ảnh/tài liệu.",
                show_alert=True,
            )
            return
        hint = (payload.get("hint") or "").strip()
        self.store.clear_user_state(user["telegram_id"])
        self._answer(callback_id, "Đã tạo việc.")
        task = self._create_task(user, chat_id, hint,
                                 payload.get("files") or [],
                                 destinations=destinations)
        self._send_main(
            chat_id,
            f"✅ Đã tạo việc {task['code']} (thẻ việc ở phía trên). "
            "Bàn phím chính đã quay lại.",
            user,
        )

    def _wizard_cancel(self, user, chat_id, callback_id=None):
        if user is not None:
            state = self.store.get_user_state(user["telegram_id"])
            if state and state["state"].startswith("wizard_"):
                self.store.clear_user_state(user["telegram_id"])
        if callback_id:
            self._answer(callback_id, "Đã huỷ giao việc.")
        self._send_main(
            chat_id,
            "Đã huỷ giao việc. Bấm 📝 Giao việc mới khi cần tạo việc khác.",
            user,
        )

    def _cb_wizard_cancel(self, callback_id, user, chat_id):
        self._wizard_cancel(user, chat_id, callback_id)

    # ------------------------------------------------------------ callbacks
    def handle_callback(self, callback: dict) -> None:
        callback_id = callback.get("id")
        data = callback.get("data") or ""
        tg_user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        user = self._touch_user(tg_user, chat_id) if tg_user.get("id") else None
        card_snapshot = self._card_snapshot(data, user)
        dest_confirmed = False

        if data == "claim_admin":
            self._cb_claim_admin(callback_id, user, chat_id)
        elif data == "claim_admin_confirm":
            self._cb_claim_admin_confirm(callback_id, user, chat_id)
        elif data.startswith("grant_pick:"):
            self._cb_grant_pick(callback_id, user, chat_id,
                                int(data.split(":")[1]))
        elif data.startswith("grant_role:"):
            _, target_id, target_role = data.split(":")
            self._cb_grant_role(callback_id, user, chat_id,
                                int(target_id), target_role)
        elif data.startswith("grant_confirm:"):
            _, target_id, target_role = data.split(":")
            self._cb_grant_confirm(callback_id, user, chat_id,
                                   int(target_id), target_role)
        elif data.startswith("grant_publish_toggle:"):
            self._cb_grant_publish_toggle(callback_id, user, chat_id,
                                          int(data.split(":")[1]))
        elif data.startswith("revoke_pick:"):
            self._cb_revoke_pick(callback_id, user, chat_id,
                                 int(data.split(":")[1]))
        elif data.startswith("revoke_confirm:"):
            self._cb_revoke_confirm(callback_id, user, chat_id,
                                    int(data.split(":")[1]))
        elif data == "wiz_done_files":
            self._cb_wizard_done_files(callback_id, user, chat_id)
        elif data.startswith("wiz_dest:"):
            self._cb_wizard_dest(callback_id, user, chat_id,
                                 data.split(":", 1)[1], message)
        elif data == "wiz_dest_all":
            self._cb_wizard_dest_all(callback_id, user, chat_id, message)
        elif data == "wiz_create":
            self._cb_wizard_create(callback_id, user, chat_id)
        elif data == "wiz_cancel":
            self._cb_wizard_cancel(callback_id, user, chat_id)
        elif data.startswith("task_outputs:"):
            self._cb_task_outputs(
                callback_id, user, chat_id, data.split(":", 1)[1])
        elif data.startswith("out_"):
            action, _sep, raw_id = data.partition(":")
            try:
                output_id = int(raw_id)
            except ValueError:
                output_id = None
            self._cb_output(callback_id, user, chat_id, action, output_id)
        elif data.startswith("task_dest_all:"):
            self._task_dest_all(callback_id, user, chat_id,
                                data.split(":", 1)[1])
        elif data.startswith("task_dest_ok:"):
            dest_confirmed = self._task_dest_ok(
                callback_id, user, chat_id, data.split(":", 1)[1])
        elif data.startswith("task_dest_head:"):
            self._answer(
                callback_id,
                "Chạm vào từng kênh để chọn (✅) hoặc bỏ chọn (❌), "
                "xong bấm OK ở dưới để chốt và gửi lệnh cho Muse.")
        elif data.startswith("task_dest:"):
            _prefix, code, dest = data.split(":", 2)
            self._task_dest(callback_id, user, chat_id, code, dest)
        elif data.startswith("task_"):
            action, code = data.split(":", 1)
            self._cb_task(callback_id, user, chat_id, action, code)
        else:
            self._answer(callback_id, "Thao tác không còn hiệu lực.")

        if dest_confirmed and message_id is not None:
            # Freeze the card the OK button sat on (owner's request,
            # like Facebook action bars): strip its buttons and stamp
            # the locked state. The flow continues on the fresh
            # channel cards sent below it; a newly sent task card
            # still works normally.
            frozen_task = self.store.get_task(data.split(":", 1)[1])
            if frozen_task is not None:
                self._edit_card(
                    chat_id, message_id,
                    self.task_card_text(frozen_task)
                    + "\n🔒 Đã chốt kênh đăng — Muse đang làm bài cho "
                      "các kênh đã chọn. (Nút trên thẻ này đã đóng "
                      "băng; duyệt và đăng ở thẻ kênh phía dưới.)")
        else:
            self._refresh_tapped_card(
                card_snapshot, user, chat_id, message_id)

    # ------------------------------------------- edit-in-place card refresh
    def _card_snapshot(self, data, user=None):
        """Remember a task/output card's state before a callback runs,
        so the tapped card can be refreshed in place afterwards."""
        try:
            if data.startswith("task_"):
                code = data.split(":", 2)[1] if data.startswith("task_dest") \
                    else data.split(":", 1)[1]
                task = self.store.get_task(code)
                if task is not None:
                    pending = self._dest_pending(user, task)
                    return ("task", code, (
                        task["status"], task["destinations"],
                        tuple(pending) if pending is not None else None))
            elif data.startswith("out_"):
                output_id = int(data.partition(":")[2])
                output = self.store.get_output(output_id)
                if output is not None:
                    return ("output", output_id, output["status"])
        except (ValueError, IndexError):
            return None
        return None

    def _refresh_tapped_card(self, snapshot, user, chat_id, message_id):
        """When a callback changed a task/output, rewrite the exact
        message the button sat on instead of sending a fresh card, so
        chats stay free of stale duplicate cards. Notifications to
        other people are still sent as new messages by the handlers."""
        if snapshot is None or user is None or message_id is None:
            return
        kind, key, before = snapshot
        if kind == "task":
            task = self.store.get_task(key)
            if task is None:
                return
            pending = self._dest_pending(user, task)
            now = (task["status"], task["destinations"],
                   tuple(pending) if pending is not None else None)
            if now == before:
                return
            self._edit_card(chat_id, message_id, self.task_card_text(task),
                            self.task_keyboard(task, user))
        else:
            output = self.store.get_output(key)
            if output is None or output["status"] == before:
                return
            task = self.store.get_task(output["task_code"])
            if task is None:
                return
            self._edit_card(chat_id, message_id,
                            self.output_card_text(output, task),
                            self.output_keyboard(output, user))

    # ------------------------------------------------------- claim admin flow
    def _cb_claim_admin(self, callback_id, user, chat_id):
        if user is None:
            self._answer(callback_id, "Hãy gửi /start trước.", show_alert=True)
            return
        if self.store.has_admin():
            self._answer(callback_id, "Đã có Admin, không thể nhận thêm.",
                         show_alert=True)
            return
        self._answer(callback_id)
        markup = _keyboard([[
            _btn("✅ Xác nhận nhận quyền Admin", "claim_admin_confirm"),
        ]])
        self._send(
            chat_id,
            "Bạn sắp trở thành Admin gốc của bot (toàn quyền phân quyền, "
            "duyệt và đăng bài). Bấm Xác nhận để hoàn tất.",
            reply_markup=markup,
        )

    def _cb_claim_admin_confirm(self, callback_id, user, chat_id):
        if user is None:
            self._answer(callback_id, "Hãy gửi /start trước.", show_alert=True)
            return
        if self.store.has_admin():
            self.store.audit(user["telegram_id"], "denied_claim_admin",
                             "user", user["telegram_id"],
                             detail="admin already exists")
            self._answer(callback_id, "Đã có Admin, không thể nhận thêm.",
                         show_alert=True)
            return
        self.store.set_role(user["telegram_id"], roles.ROLE_ADMIN,
                            can_publish=True)
        self.store.audit(user["telegram_id"], "claim_admin",
                         "user", user["telegram_id"],
                         detail="first user claimed admin_root")
        self.store.audit(user["telegram_id"], "grant_role",
                         "user", user["telegram_id"], detail="role=admin (claim)")
        self._answer(callback_id, "Bạn đã là Admin.")
        fresh = self.store.get_user(user["telegram_id"])
        self.sync_user_commands(fresh)
        self._send_main(
            chat_id,
            "👑 Bạn đã trở thành Admin gốc. Dùng /capquyen để cấp quyền "
            "cho nhân viên, /danhsach để xem danh sách người dùng. "
            "Bàn phím chính và menu lệnh của bạn đã được cập nhật.",
            fresh,
        )

    # ------------------------------------------------------------ grant flow
    def _cb_grant_pick(self, callback_id, user, chat_id, target_id):
        if user is None or user["role"] not in (roles.ROLE_ADMIN, roles.ROLE_DEPUTY):
            self._answer(callback_id, "⛔ Bạn không có quyền cấp quyền.",
                         show_alert=True)
            return
        target = self.store.get_user(target_id)
        if target is None:
            self._answer(callback_id, "Không tìm thấy người dùng.", show_alert=True)
            return
        allowed = roles.GRANTABLE.get(user["role"], ())
        rows = [
            [_btn(f"Cấp vai trò: {roles.role_title(r)}", f"grant_role:{target_id}:{r}")]
            for r in allowed
        ]
        self._answer(callback_id)
        self._send(
            chat_id,
            f"Chọn vai trò cho {target['display_name']}:",
            reply_markup=_keyboard(rows),
        )

    def _cb_grant_role(self, callback_id, user, chat_id, target_id, target_role):
        if user is None or not roles.can_grant(user["role"], target_role):
            self._answer(callback_id, "⛔ Bạn không được cấp vai trò này.",
                         show_alert=True)
            return
        target = self.store.get_user(target_id)
        if target is None:
            self._answer(callback_id, "Không tìm thấy người dùng.", show_alert=True)
            return
        self._answer(callback_id)
        markup = _keyboard([[
            _btn("✅ Xác nhận cấp quyền", f"grant_confirm:{target_id}:{target_role}"),
        ]])
        self._send(
            chat_id,
            f"Cấp vai trò {roles.role_title(target_role)} cho "
            f"{target['display_name']}? Bấm Xác nhận để áp dụng.",
            reply_markup=markup,
        )

    def _cb_grant_confirm(self, callback_id, user, chat_id, target_id, target_role):
        # Re-check everything at apply time.
        if user is None or not roles.can_grant(user["role"], target_role):
            self._answer(callback_id, "⛔ Bạn không được cấp vai trò này.",
                         show_alert=True)
            return
        target = self.store.get_user(target_id)
        if target is None:
            self._answer(callback_id, "Không tìm thấy người dùng.", show_alert=True)
            return
        if user["role"] == roles.ROLE_DEPUTY and target["role"] not in (
            None, roles.ROLE_WRITER, roles.ROLE_CONTRIBUTOR,
        ):
            self._answer(callback_id,
                         "⛔ Phó Admin chỉ quản lý Nhân viên viết bài / Cộng tác viên.",
                         show_alert=True)
            return
        self.store.set_role(target_id, target_role,
                            can_publish=(target_role == roles.ROLE_ADMIN))
        self.store.audit(user["telegram_id"], "grant_role", "user", target_id,
                         detail=f"role={target_role}")
        self._answer(callback_id, "Đã cấp quyền.")
        lines = [
            f"✅ Đã cấp vai trò {roles.role_title(target_role)} "
            f"cho {target['display_name']}."
        ]
        markup = None
        if target_role in (roles.ROLE_DEPUTY, roles.ROLE_EDITOR) \
                and user["role"] == roles.ROLE_ADMIN:
            markup = _keyboard([[
                _btn("🔀 Bật/tắt quyền đăng bài cho người này",
                     f"grant_publish_toggle:{target_id}"),
            ]])
            lines.append(
                "Mặc định vai trò này chưa được đăng bài; "
                "bấm nút dưới đây nếu muốn bật quyền đăng."
            )
        self._send(chat_id, "\n".join(lines), reply_markup=markup)
        fresh_target = self.store.get_user(target_id)
        self.sync_user_commands(fresh_target)
        if target["chat_id"]:
            self._send(
                target["chat_id"],
                f"Bạn đã được cấp vai trò: {roles.role_title(target_role)}. "
                "Bấm 📝 Giao việc mới để tạo việc, hoặc /vaitro để xem vai trò. "
                "Bàn phím chính và menu lệnh của bạn đã được cập nhật.",
                reply_markup=main_keyboard(fresh_target),
            )

    def _cb_grant_publish_toggle(self, callback_id, user, chat_id, target_id):
        if user is None or user["role"] != roles.ROLE_ADMIN:
            self._answer(callback_id, "⛔ Chỉ Admin mới bật/tắt quyền đăng.",
                         show_alert=True)
            return
        target = self.store.get_user(target_id)
        if target is None or target["role"] not in (roles.ROLE_DEPUTY, roles.ROLE_EDITOR):
            self._answer(callback_id, "Người này không thuộc vai trò được bật quyền đăng.",
                         show_alert=True)
            return
        new_value = not bool(target["can_publish"])
        self.store.set_role(target_id, target["role"], can_publish=new_value,
                            categories=json.loads(target["categories"]))
        self.store.audit(user["telegram_id"], "set_can_publish", "user",
                         target_id, detail=f"can_publish={new_value}")
        self._answer(callback_id, "Đã cập nhật quyền đăng.")
        self._send(
            chat_id,
            f"Quyền đăng bài của {target['display_name']}: "
            f"{'ĐÃ BẬT' if new_value else 'ĐÃ TẮT'}.",
        )

    # ----------------------------------------------------------- revoke flow
    def _cb_revoke_pick(self, callback_id, user, chat_id, target_id):
        target = self.store.get_user(target_id)
        if user is None or target is None or not roles.can_revoke(
            user["role"], target["role"]
        ):
            self._answer(callback_id, "⛔ Bạn không được thu hồi quyền của người này.",
                         show_alert=True)
            return
        self._answer(callback_id)
        markup = _keyboard([[
            _btn("🗑 Xác nhận thu hồi quyền", f"revoke_confirm:{target_id}"),
        ]])
        self._send(
            chat_id,
            f"Thu hồi vai trò {roles.role_title(target['role'])} của "
            f"{target['display_name']}? Người này sẽ không giao việc được nữa.",
            reply_markup=markup,
        )

    def _cb_revoke_confirm(self, callback_id, user, chat_id, target_id):
        target = self.store.get_user(target_id)
        if user is None or target is None or not roles.can_revoke(
            user["role"], target["role"]
        ):
            self._answer(callback_id, "⛔ Bạn không được thu hồi quyền của người này.",
                         show_alert=True)
            return
        old_role = target["role"]
        self.store.revoke_role(target_id)
        self.store.audit(user["telegram_id"], "revoke_role", "user", target_id,
                         detail=f"was_role={old_role}")
        self._answer(callback_id, "Đã thu hồi quyền.")
        self._send(chat_id, f"✅ Đã thu hồi quyền của {target['display_name']}.")
        fresh_target = self.store.get_user(target_id)
        self.sync_user_commands(fresh_target)
        if target["chat_id"]:
            self._send(target["chat_id"],
                       "Quyền của bạn trên bot giao việc đã bị thu hồi.",
                       reply_markup=main_keyboard(fresh_target))

    # ------------------------------------------------------------- task flow
    def _cb_task(self, callback_id, user, chat_id, action, code):
        task = self.store.get_task(code)
        if task is None:
            self._answer(callback_id, "Không tìm thấy việc này.", show_alert=True)
            return
        if user is None or user["role"] is None:
            self._answer(callback_id, "⛔ Bạn chưa được cấp quyền.",
                         show_alert=True)
            return

        if action in (
            "task_submit", "task_approve", "task_reject", "task_publish",
            "task_retract", "task_retract_confirm",
        ):
            # Option A: these buttons only exist on stale cards sent
            # before the flow moved to the channel cards.
            self._answer(
                callback_id,
                "Luồng mới: duyệt và đăng bài ngay trên thẻ kênh, nơi "
                "có bản nháp. Mở thẻ việc và bấm OK ở mục 🎯 Chọn kênh "
                "đăng bài để bot gửi lại các thẻ kênh.",
                show_alert=True,
            )
            return
        if action == "task_edit":
            self._task_edit_start(callback_id, user, chat_id, task)
        elif action == "task_cancel":
            self._task_cancel_start(callback_id, user, chat_id, task)
        elif action == "task_cancel_confirm":
            self._task_cancel_confirm(callback_id, user, chat_id, task)
        else:
            self._answer(callback_id, "Thao tác không hợp lệ.")

    # ------------------------------------------- publish destination toggles
    def _check_dest_change(self, callback_id, user, code):
        """Shared guards for the staged channel picker. Returns the
        task when the tap may proceed, else None (already answered)."""
        task = self.store.get_task(code)
        if task is None:
            self._answer(callback_id, "Không tìm thấy việc này.",
                         show_alert=True)
            return None
        if user is None or user["role"] is None:
            self._answer(callback_id, "⛔ Bạn chưa được cấp quyền.",
                         show_alert=True)
            return None
        if task["status"] in (STATUS_PUBLISHED, STATUS_RETRACTED,
                              STATUS_CANCELLED):
            self._answer(callback_id,
                         "Việc đã đóng, không đổi được nơi đăng nữa.",
                         show_alert=True)
            return None
        if not _can_toggle_destinations(user, task):
            self._answer(callback_id,
                         "⛔ Bạn không có quyền đổi nơi đăng của việc này.",
                         show_alert=True)
            return None
        return task

    def _task_dest(self, callback_id, user, chat_id, code, dest):
        """Stage one channel flip. Nothing is written to the task yet:
        the selection only takes effect when the user taps OK, which
        is also what sends the work order to Muse."""
        task = self._check_dest_change(callback_id, user, code)
        if task is None:
            return
        if dest not in DESTINATION_KEYS:
            self._answer(callback_id, "Nơi đăng không hợp lệ.",
                         show_alert=True)
            return
        pending = self._dest_pending(user, task)
        chosen = pending if pending is not None \
            else task_destinations(task)
        label = DESTINATION_LABELS[dest]
        if dest in chosen:
            if len(chosen) <= 1:
                self._answer(callback_id,
                             "Phải chọn ít nhất một nơi đăng.",
                             show_alert=True)
                return
            chosen = [k for k in chosen if k != dest]
            note = f"Đã bỏ chọn {label} (chưa chốt)"
        else:
            chosen = [k for k in DESTINATION_KEYS if k in chosen or k == dest]
            note = f"Đã chọn {label} (chưa chốt)"
        self.store.set_user_state(
            user["telegram_id"], "task_dest_pending",
            {"code": code, "chosen": chosen})
        self._answer(callback_id, note + " — bấm OK để chốt và gửi "
                                   "lệnh cho Muse.")

    def _task_dest_all(self, callback_id, user, chat_id, code):
        task = self._check_dest_change(callback_id, user, code)
        if task is None:
            return
        pending = self._dest_pending(user, task)
        chosen = pending if pending is not None \
            else task_destinations(task)
        if len(chosen) >= len(DESTINATION_KEYS):
            self._answer(callback_id, "Đã chọn tất cả nơi đăng rồi.")
            return
        self.store.set_user_state(
            user["telegram_id"], "task_dest_pending",
            {"code": code, "chosen": list(DESTINATION_KEYS)})
        self._answer(callback_id, "Đã chọn tất cả (chưa chốt) — bấm OK "
                                  "để chốt và gửi lệnh cho Muse.")

    def _task_dest_ok(self, callback_id, user, chat_id, code):
        """Commit the staged channel selection and send the work order
        to Muse: destinations + per-channel outputs are synced, the
        channel cards are (re)sent, and the task lands in the queue
        Muse watches for drafts to write."""
        task = self._check_dest_change(callback_id, user, code)
        if task is None:
            return False
        pending = self._dest_pending(user, task)
        chosen = pending if pending is not None \
            else task_destinations(task)
        if not chosen:
            self._answer(callback_id,
                         "Hãy chọn ít nhất một kênh đăng trước.",
                         show_alert=True)
            return False
        self.store.set_task_destinations(code, chosen)
        self.store.audit(user["telegram_id"], "set_destinations", "task",
                         code,
                         detail=json.dumps(chosen, ensure_ascii=False))
        self.store.audit(user["telegram_id"], "destinations_confirmed",
                         "task", code,
                         detail=json.dumps(chosen, ensure_ascii=False))
        updated = self.store.get_task(code)
        self.store.sync_outputs_for_task(updated)
        self.store.sync_task_status_from_outputs(code)
        if pending is not None:
            self.store.clear_user_state(user["telegram_id"])
        labels = " • ".join(DESTINATION_LABELS[k] for k in chosen)
        self._answer(callback_id, f"Đã chốt kênh đăng: {labels}.")
        self._send(
            chat_id,
            f"🎯 Đã chốt kênh đăng bài cho việc {code}: {labels}.\n"
            "✅ Đã gửi đến Muse. Đợi Muse làm xong — khi xong, "
            "Muse sẽ thông báo và gửi kết quả công việc (thẻ kênh "
            "kèm bản nháp) ngay tại đây.")
        updated = self.store.get_task(code)
        for output in self.store.list_outputs(code):
            if output["status"] != STATUS_CANCELLED:
                self.send_output_card(chat_id, output, updated, user)
        return True

    # ------------------------------------------------------------ edit flow
    def _task_edit_start(self, callback_id, user, chat_id, task):
        if task["creator_id"] != user["telegram_id"]:
            self.store.audit(user["telegram_id"], "denied_edit_attempt",
                             "task", task["code"],
                             detail=f"role={user['role']}")
            self._answer(callback_id,
                         "⛔ Chỉ người tạo việc mới chỉnh sửa được.",
                         show_alert=True)
            return
        if task["status"] not in (STATUS_DRAFT, STATUS_REJECTED):
            self._answer(callback_id,
                         "Chỉ sửa được việc đang là Nháp hoặc Bị trả lại.",
                         show_alert=True)
            return
        self.store.set_user_state(
            user["telegram_id"], "await_edit_hint", {"code": task["code"]}
        )
        self._answer(callback_id, "Hãy nhắn gợi ý mới.")
        self._send(
            chat_id,
            f"✏️ Chỉnh sửa việc {task['code']}: hãy nhắn nội dung/gợi ý mới "
            "trong tin nhắn tiếp theo. Gợi ý mới sẽ thay toàn bộ gợi ý hiện "
            "tại của việc.",
        )

    def _apply_edit_hint(self, user, chat_id, new_hint, payload):
        code = (payload or {}).get("code")
        self.store.clear_user_state(user["telegram_id"])
        task = self.store.get_task(code) if code else None
        if task is None:
            self._send(chat_id, "Không tìm thấy việc cần chỉnh sửa.")
            return
        if user is None or task["creator_id"] != user["telegram_id"]:
            self._send(chat_id, "⛔ Bạn không còn quyền chỉnh sửa việc này.")
            return
        if task["status"] not in (STATUS_DRAFT, STATUS_REJECTED):
            self._send(chat_id, "Việc này không còn ở trạng thái sửa được.")
            return
        self.store.set_task_hint(code, new_hint)
        self.store.audit(user["telegram_id"], "edit_task", "task", code,
                         detail=f"hint={new_hint[:100]}")
        updated = self.store.get_task(code)
        self._send(
            chat_id, "✅ Đã cập nhật việc:\n" + self.task_card_text(updated),
            reply_markup=self.task_keyboard(updated, user),
        )

    # ---------------------------------------------------------- cancel flow
    def _can_cancel(self, user, task) -> bool:
        if user is None or user["role"] is None:
            return False
        if task["status"] not in (STATUS_DRAFT, STATUS_PENDING, STATUS_REJECTED):
            return False
        return (
            user["telegram_id"] == task["creator_id"]
            or user["role"] == roles.ROLE_ADMIN
        )

    def _task_cancel_start(self, callback_id, user, chat_id, task):
        if not self._can_cancel(user, task):
            self._answer(callback_id,
                         "⛔ Bạn không thu hồi được việc này, hoặc việc "
                         "không còn ở trạng thái thu hồi được.",
                         show_alert=True)
            return
        self._answer(callback_id)
        markup = _keyboard([[
            _btn("🗑 Xác nhận thu hồi việc", f"task_cancel_confirm:{task['code']}"),
        ]])
        self._send(
            chat_id,
            f"Thu hồi việc {task['code']}? Việc sẽ bị hủy và không xử lý "
            "nữa. Bấm Xác nhận để thu hồi.",
            reply_markup=markup,
        )

    def _task_cancel_confirm(self, callback_id, user, chat_id, task):
        if not self._can_cancel(user, task):
            self._answer(callback_id,
                         "⛔ Bạn không thu hồi được việc này.",
                         show_alert=True)
            return
        self.store.set_task_status(task["code"], STATUS_CANCELLED)
        self.store.audit(user["telegram_id"], "cancel_task", "task",
                         task["code"])
        # Cancelling the parent also cancels its still-open outputs.
        self.store.cancel_open_outputs(task["code"])
        self._answer(callback_id, "Đã thu hồi việc.")
        self._send(chat_id, f"🗑 Việc {task['code']} đã được thu hồi (đã hủy).")
        updated = self.store.get_task(task["code"])
        for reviewer in self.store.reviewers():
            if reviewer["telegram_id"] == user["telegram_id"]:
                continue
            self._send(
                reviewer["chat_id"],
                f"🗑 Việc {task['code']} đã bị thu hồi bởi "
                f"{user['display_name']}.\n" + self.task_card_text(updated),
            )
        if task["creator_id"] != user["telegram_id"]:
            self._notify_creator(
                updated, prefix="🗑 Việc của bạn đã bị Admin thu hồi:")

    # ------------------------------------------------- per-channel outputs
    # Option A: each publish channel of a task has its own output record,
    # its own card and its own lifecycle, independent of the other
    # channels. The rules below mirror the parent-task rules.
    def output_card_text(self, output, task) -> str:
        meta = CHANNELS.get(output["channel"], {})
        label = meta.get("label", output["channel"])
        kind = meta.get("kind", "")
        creator = self.store.get_user(task["creator_id"])
        creator_name = (
            creator["display_name"] if creator else str(task["creator_id"])
        )
        hint = (task["hint"] or "").replace("\n", " ")[:200]
        lines = [
            f"🧾 Thẻ kênh {label} — "
            f"{STATUS_LABELS.get(output['status'], output['status'])}",
            f"Loại đầu ra: {kind}" if kind else "Loại đầu ra: (không rõ)",
            f"Việc gốc: {task['code']}",
            f"Người tạo: {creator_name} ({roles.role_title(task['creator_role'])})",
            f"Thời gian tạo: {_format_created(output['created_at'])}",
            f"Gợi ý: {hint or '(không có)'}",
        ]
        if output["draft_ref"]:
            lines.append(f"Bản nháp: {output['draft_ref']}")
        if output["public_url"]:
            lines.append(f"Link đã đăng: {output['public_url']}")
        if output["status"] == STATUS_REJECTED and output["reject_reason"]:
            lines.append(f"✏️ Yêu cầu chỉnh sửa: {output['reject_reason']}")
        return "\n".join(lines)

    def output_keyboard(self, output, viewer):
        """Buttons on a channel card, tailored to the viewer's permissions.

        Mirrors the task card: the publish/retract buttons are shown so
        the flow is discoverable; authorisation is enforced again in the
        callback. Closed outputs (CANCELLED / RETRACTED) show no buttons.
        """
        if viewer is None:
            return None
        status = output["status"]
        if status in (STATUS_CANCELLED, STATUS_RETRACTED):
            return None
        rows = []
        oid = output["id"]
        task = self.store.get_task(output["task_code"])
        is_creator = task is not None and \
            viewer["telegram_id"] == task["creator_id"]
        if is_creator and status in (STATUS_DRAFT, STATUS_REJECTED):
            rows.append([_btn("📤 Gửi duyệt", f"out_submit:{oid}")])
        if roles.can_review(viewer["role"]) and status == STATUS_PENDING:
            rows.append([
                _btn("✅ Duyệt", f"out_approve:{oid}"),
                _btn("↩️ Trả lại kèm yêu cầu sửa", f"out_reject:{oid}"),
            ])
        if roles.can_review(viewer["role"]) and status == STATUS_DRAFT \
                and output["draft_ref"]:
            rows.append([
                _btn("↩️ Trả lại kèm yêu cầu sửa", f"out_reject:{oid}")])
        if status == STATUS_APPROVED_WAITING:
            rows.append([_btn("🚀 Đăng", f"out_publish:{oid}")])
        if status == STATUS_PUBLISHED:
            rows.append([_btn("↩️ Thu hồi", f"out_retract:{oid}")])
        return _keyboard(rows)

    def send_output_card(self, chat_id, output, task, viewer, prefix=""):
        text = (prefix + "\n" if prefix else "") + \
            self.output_card_text(output, task)
        return self._send(chat_id, text,
                          reply_markup=self.output_keyboard(output, viewer))

    def _notify_output_creator(self, output, task, prefix=""):
        creator = self.store.get_user(task["creator_id"])
        if creator is None or creator["chat_id"] is None:
            return
        self.send_output_card(
            creator["chat_id"], output, task, creator, prefix=prefix)

    def _notify_output_reviewers(self, output, task, prefix="",
                                 exclude_id=None):
        for reviewer in self.store.reviewers():
            if reviewer["telegram_id"] == exclude_id:
                continue
            self.send_output_card(
                reviewer["chat_id"], output, task, reviewer, prefix=prefix)

    def _cb_task_outputs(self, callback_id, user, chat_id, code):
        task = self.store.get_task(code)
        if task is None:
            self._answer(callback_id, "Không tìm thấy việc này.",
                         show_alert=True)
            return
        if user is None or user["role"] is None:
            self._answer(callback_id, "⛔ Bạn chưa được cấp quyền.",
                         show_alert=True)
            return
        live = [
            o for o in self.store.list_outputs(code)
            if o["status"] != STATUS_CANCELLED
        ]
        if not live:
            self._answer(
                callback_id,
                "Việc này chưa có thẻ kênh nào. Hãy chọn ít nhất một "
                "nơi đăng trên thẻ việc.",
                show_alert=True,
            )
            return
        self._answer(callback_id)
        for output in live:
            self.send_output_card(chat_id, output, task, user)

    # ---------------------------------------------------- output callbacks
    def _cb_output(self, callback_id, user, chat_id, action, output_id):
        output = self.store.get_output(output_id) \
            if output_id is not None else None
        if output is None:
            self._answer(callback_id, "Không tìm thấy thẻ kênh này.",
                         show_alert=True)
            return
        task = self.store.get_task(output["task_code"])
        if task is None:
            self._answer(callback_id, "Không tìm thấy việc gốc.",
                         show_alert=True)
            return
        if user is None or user["role"] is None:
            self._answer(callback_id, "⛔ Bạn chưa được cấp quyền.",
                         show_alert=True)
            return

        if action == "out_submit":
            self._output_submit(callback_id, user, chat_id, output, task)
        elif action == "out_approve":
            self._output_approve(callback_id, user, chat_id, output, task)
        elif action == "out_reject":
            self._output_reject_start(callback_id, user, chat_id, output, task)
        elif action == "out_publish":
            self._output_publish(callback_id, user, chat_id, output, task)
        elif action == "out_retract":
            self._output_retract_start(
                callback_id, user, chat_id, output, task)
        elif action == "out_retract_confirm":
            self._output_retract_confirm(
                callback_id, user, chat_id, output, task)
        else:
            self._answer(callback_id, "Thao tác không hợp lệ.")

    def _output_submit(self, callback_id, user, chat_id, output, task):
        if task["creator_id"] != user["telegram_id"]:
            self._answer(callback_id,
                         "⛔ Chỉ người tạo việc mới gửi duyệt được.",
                         show_alert=True)
            return
        if output["status"] not in (STATUS_DRAFT, STATUS_REJECTED):
            self._answer(callback_id,
                         "Thẻ kênh này không ở trạng thái gửi duyệt được.",
                         show_alert=True)
            return
        if not output["draft_ref"]:
            # Option A: submitting means submitting the actual draft —
            # never an empty shell. Drafts arrive when Muse writes
            # them and attaches them to this channel card.
            self._answer(callback_id,
                         "Kênh này chưa có bản nháp — Muse đang viết. "
                         "Khi nháp được gắn vào, thẻ kênh sẽ được gửi "
                         "lại để bạn gửi duyệt.",
                         show_alert=True)
            return
        self.store.set_output_status(output["id"], STATUS_PENDING)
        self.store.audit(user["telegram_id"], "submit_output", "output",
                         output["id"],
                         detail=f"task={task['code']} channel={output['channel']}")
        self.store.sync_task_status_from_outputs(task["code"])
        self._answer(callback_id, "Đã gửi duyệt.")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        updated = self.store.get_output(output["id"])
        self._send(chat_id,
                   f"📤 Thẻ kênh {label} của việc {task['code']} "
                   "đã được gửi duyệt.")
        self._notify_output_reviewers(
            updated, task,
            prefix=f"📤 Thẻ kênh {label} của việc {task['code']} "
                   "đang chờ duyệt:",
            exclude_id=user["telegram_id"])

    def _output_approve(self, callback_id, user, chat_id, output, task):
        if not roles.can_review(user["role"]):
            self.store.audit(user["telegram_id"],
                             "denied_output_approve_attempt", "output",
                             output["id"], detail=f"role={user['role']}")
            self._answer(callback_id, "⛔ Bạn không có quyền duyệt.",
                         show_alert=True)
            return
        if output["status"] != STATUS_PENDING:
            self._answer(callback_id, "Thẻ kênh này chưa được gửi duyệt.",
                         show_alert=True)
            return
        self.store.set_output_status(output["id"], STATUS_APPROVED_WAITING)
        self.store.audit(user["telegram_id"], "approve_output", "output",
                         output["id"],
                         detail=f"task={task['code']} channel={output['channel']}")
        self.store.sync_task_status_from_outputs(task["code"])
        self._answer(callback_id, "Đã duyệt, thẻ kênh chuyển sang chờ đăng.")
        updated = self.store.get_output(output["id"])
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        self._notify_output_creator(
            updated, task,
            prefix=f"✅ Thẻ kênh {label} của bạn đã được duyệt, "
                   "đang chờ đăng:")

    def _output_reject_start(self, callback_id, user, chat_id, output, task):
        if not roles.can_review(user["role"]):
            self.store.audit(user["telegram_id"],
                             "denied_output_reject_attempt", "output",
                             output["id"], detail=f"role={user['role']}")
            self._answer(callback_id, "⛔ Bạn không có quyền trả lại.",
                         show_alert=True)
            return
        if output["status"] not in (STATUS_PENDING, STATUS_DRAFT):
            self._answer(callback_id,
                         "Thẻ kênh này không ở trạng thái trả lại được.",
                         show_alert=True)
            return
        if output["status"] == STATUS_DRAFT and not output["draft_ref"]:
            self._answer(callback_id,
                         "Kênh này chưa có bản nháp để trả lại.",
                         show_alert=True)
            return
        self.store.set_user_state(
            user["telegram_id"], "await_output_reject_reason",
            {"output_id": output["id"]},
        )
        self._answer(callback_id, "Hãy nhắn nội dung cần chỉnh sửa.")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        self._send(
            chat_id,
            f"✏️ Trả lại thẻ kênh {label} của việc {task['code']} kèm "
            "yêu cầu chỉnh sửa: hãy nhắn NỘI DUNG CẦN SỬA trong tin "
            "nhắn tiếp theo — người tạo sẽ nhận được nguyên văn "
            "yêu cầu này trên thẻ kênh.",
        )

    def _apply_output_reject_reason(self, user, chat_id, reason, payload):
        output_id = (payload or {}).get("output_id")
        self.store.clear_user_state(user["telegram_id"])
        output = self.store.get_output(output_id) \
            if output_id is not None else None
        if output is None:
            self._send(chat_id, "Không tìm thấy thẻ kênh cần trả lại.")
            return
        task = self.store.get_task(output["task_code"])
        if user is None or not roles.can_review(user["role"]):
            self._send(chat_id, "⛔ Bạn không còn quyền trả lại.")
            return
        if output["status"] not in (STATUS_PENDING, STATUS_DRAFT):
            self._send(chat_id,
                       "Thẻ kênh này không còn ở trạng thái trả lại được.")
            return
        self.store.set_output_status(
            output["id"], STATUS_REJECTED, reject_reason=reason)
        self.store.audit(user["telegram_id"], "reject_output", "output",
                         output["id"],
                         detail=f"task={task['code']} "
                                f"channel={output['channel']} reason={reason}")
        self.store.sync_task_status_from_outputs(task["code"])
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        self._send(
            chat_id,
            f"↩️ Đã trả lại thẻ kênh {label} của việc {task['code']}. "
            f"✏️ Yêu cầu chỉnh sửa: {reason}",
        )
        updated = self.store.get_output(output["id"])
        self._notify_output_creator(
            updated, task,
            prefix=f"↩️ Thẻ kênh {label} của bạn bị trả lại. "
                   f"✏️ Yêu cầu chỉnh sửa: {reason}")

    def _output_publish(self, callback_id, user, chat_id, output, task):
        if not roles.can_publish(user):
            # Especially important: contributors must never publish.
            self.store.audit(
                user["telegram_id"], "denied_output_publish_attempt",
                "output", output["id"],
                detail=f"role={user['role']} "
                       f"can_publish={bool(user['can_publish'])}",
            )
            self._answer(callback_id, "⛔ Bạn không có quyền đăng.",
                         show_alert=True)
            return
        if output["status"] != STATUS_APPROVED_WAITING:
            self._answer(callback_id,
                         "Thẻ kênh này chưa ở trạng thái chờ đăng.",
                         show_alert=True)
            return
        self.store.set_output_status(output["id"], STATUS_PUBLISHED)
        self.store.audit(user["telegram_id"], "publish_output", "output",
                         output["id"],
                         detail=f"task={task['code']} channel={output['channel']}")
        self.store.sync_task_status_from_outputs(task["code"])
        self._answer(callback_id, "Đã đăng.")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        updated = self.store.get_output(output["id"])
        self._notify_output_creator(
            updated, task, prefix=f"🚀 Thẻ kênh {label} của bạn đã được đăng:")
        for admin in self.store.admins():
            if admin["telegram_id"] != user["telegram_id"]:
                self._send(
                    admin["chat_id"],
                    f"🚀 Thẻ kênh {label} của việc {task['code']} "
                    f"vừa được đăng bởi {user['display_name']}.")

    def _output_retract_start(self, callback_id, user, chat_id, output, task):
        if output["status"] != STATUS_PUBLISHED:
            self._answer(callback_id, "Thẻ kênh này chưa được đăng.",
                         show_alert=True)
            return
        self._answer(callback_id)
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        markup = _keyboard([[
            _btn("↩️ Xác nhận thu hồi",
                 f"out_retract_confirm:{output['id']}"),
        ]])
        self._send(
            chat_id,
            f"Thu hồi thẻ kênh {label} đã đăng của việc {task['code']}? "
            "Bấm Xác nhận để thu hồi.",
            reply_markup=markup,
        )

    def _output_retract_confirm(self, callback_id, user, chat_id, output, task):
        if not roles.can_publish(user):
            self.store.audit(
                user["telegram_id"], "denied_output_retract_attempt",
                "output", output["id"], detail=f"role={user['role']}",
            )
            self._answer(callback_id,
                         "⛔ Bạn không có quyền thu hồi thẻ kênh đã đăng.",
                         show_alert=True)
            return
        if output["status"] != STATUS_PUBLISHED:
            self._answer(callback_id,
                         "Thẻ kênh này không còn ở trạng thái đã đăng.",
                         show_alert=True)
            return
        self.store.set_output_status(output["id"], STATUS_RETRACTED)
        self.store.audit(user["telegram_id"], "retract_output", "output",
                         output["id"],
                         detail=f"task={task['code']} channel={output['channel']}")
        self.store.sync_task_status_from_outputs(task["code"])
        self._answer(callback_id, "Đã thu hồi.")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        updated = self.store.get_output(output["id"])
        self._notify_output_creator(
            updated, task, prefix=f"↩️ Thẻ kênh {label} của bạn đã bị thu hồi:")
        for admin in self.store.admins():
            if admin["telegram_id"] != user["telegram_id"]:
                self._send(
                    admin["chat_id"],
                    f"↩️ Thẻ kênh {label} của việc {task['code']} "
                    f"vừa bị thu hồi bởi {user['display_name']}.")
