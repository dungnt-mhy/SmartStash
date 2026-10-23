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
    "/vaitro — xem danh sách vai trò\n"
    "/viec — xem danh sách việc\n"
    "/capquyen — cấp quyền (Admin / Phó Admin)\n"
    "/thuhoi — thu hồi quyền (Admin / Phó Admin)\n"
    "/danhsach — danh sách người dùng + vai trò (Admin / Phó Admin)\n\n"
    "Gửi ảnh/tài liệu (kèm chú thích làm gợi ý), rồi nhắn một tin nhắn "
    "văn bản để chốt tạo việc.\n"
    "Trên thẻ việc: bấm ✏️ Chỉnh sửa để sửa gợi ý, bấm các nút ✅/⬜ để "
    "chọn nơi đăng, bấm 🗑 Thu hồi việc để hủy việc chưa đăng (bài đã "
    "đăng thì dùng nút ↩️ Thu hồi bài đã đăng)."
)


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

        # A reviewer who tapped "Trả lại" is expected to type the reason.
        state = self.store.get_user_state(user_id)
        if state and state["state"] == "await_reject_reason" and text:
            self._apply_reject_reason(user, chat_id, text, state["payload"])
            return

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

    def _create_task(self, user, chat_id, hint, file_ids):
        task = self.store.create_task(
            creator_id=user["telegram_id"], creator_role=user["role"],
            hint=hint, file_ids=file_ids,
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
        self._notify_reviewers(task, exclude_id=user["telegram_id"])
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
        """Buttons on a task card, tailored to the viewer's permissions.

        The publish button is shown on a waiting card so the workflow is
        discoverable; authorisation is enforced again in the callback.
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
            rows.append([_btn("📤 Gửi duyệt", f"task_submit:{code}")])
            rows.append([_btn("✏️ Chỉnh sửa", f"task_edit:{code}")])
        if roles.can_review(viewer["role"]) and status == STATUS_PENDING:
            rows.append([
                _btn("✅ Duyệt", f"task_approve:{code}"),
                _btn("↩️ Trả lại", f"task_reject:{code}"),
            ])
        if status == STATUS_APPROVED_WAITING:
            rows.append([_btn("🚀 Đăng bài", f"task_publish:{code}")])
        live_outputs = [
            o for o in self.store.list_outputs(code)
            if o["status"] != STATUS_CANCELLED
        ]
        if live_outputs:
            rows.append([_btn("📋 Thẻ các kênh", f"task_outputs:{code}")])
        if _can_toggle_destinations(viewer, task):
            chosen = task_destinations(task)
            rows.append([
                _btn(
                    f"{'✅' if key in chosen else '⬜'} {label}",
                    f"task_dest:{code}:{key}",
                )
                for key, label in DESTINATIONS
            ])
        if status in (STATUS_DRAFT, STATUS_PENDING, STATUS_REJECTED) and (
            is_creator or is_admin
        ):
            rows.append([_btn("🗑 Thu hồi việc", f"task_cancel:{code}")])
        if status == STATUS_PUBLISHED:
            rows.append([_btn("↩️ Thu hồi bài đã đăng", f"task_retract:{code}")])
        return _keyboard(rows)

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
            "/vaitro": self._cmd_vaitro,
            "/danhsach": self._cmd_danhsach,
            "/capquyen": self._cmd_capquyen,
            "/thuhoi": self._cmd_thuhoi,
            "/viec": self._cmd_viec,
            "/help": self._cmd_help,
            "/trogiup": self._cmd_help,
        }.get(command)
        if handler is None:
            self._send(chat_id, "Lệnh chưa được hỗ trợ.\n\n" + HELP_TEXT)
            return
        handler(user, tg_user, chat_id)

    def _cmd_help(self, user, tg_user, chat_id):
        self._send(chat_id, HELP_TEXT)

    def _cmd_start(self, user, tg_user, chat_id):
        if user is None:
            user = self.store.register_user(
                tg_user["id"], tg_user.get("username"),
                _display_name(tg_user), chat_id,
            )
        lines = [f"Xin chào {user['display_name']}!"]
        if user["role"]:
            lines.append(f"Vai trò của bạn: {roles.role_title(user['role'])}.")
            lines.append("Gửi ảnh/tài liệu kèm gợi ý để tạo việc, hoặc /viec để xem việc.")
            self._send(chat_id, "\n".join(lines))
            return
        lines.append(
            "Bạn đã đăng ký và đang chờ cấp quyền. "
            "Admin sẽ cấp quyền cho bạn ngay trong Telegram."
        )
        markup = None
        if not self.store.has_admin():
            lines.append(
                "Hiện chưa có Admin. Nếu bạn là chủ website, "
                "bấm nút dưới đây để nhận quyền Admin."
            )
            markup = _keyboard([[_btn("👑 Nhận quyền Admin", "claim_admin")]])
        self._send(chat_id, "\n".join(lines), reply_markup=markup)

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

    # ------------------------------------------------------------ callbacks
    def handle_callback(self, callback: dict) -> None:
        callback_id = callback.get("id")
        data = callback.get("data") or ""
        tg_user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user = self._touch_user(tg_user, chat_id) if tg_user.get("id") else None

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
        elif data.startswith("task_dest:"):
            _prefix, code, dest = data.split(":", 2)
            self._task_dest(callback_id, user, chat_id, code, dest)
        elif data.startswith("task_"):
            action, code = data.split(":", 1)
            self._cb_task(callback_id, user, chat_id, action, code)
        else:
            self._answer(callback_id, "Thao tác không còn hiệu lực.")

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
        self._send(
            chat_id,
            "👑 Bạn đã trở thành Admin gốc. Dùng /capquyen để cấp quyền "
            "cho nhân viên, /danhsach để xem danh sách người dùng.",
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
        if target["chat_id"]:
            self._send(
                target["chat_id"],
                f"Bạn đã được cấp vai trò: {roles.role_title(target_role)}. "
                "Gửi ảnh/tài liệu kèm gợi ý để tạo việc, hoặc /vaitro để xem vai trò.",
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
        if target["chat_id"]:
            self._send(target["chat_id"],
                       "Quyền của bạn trên bot giao việc đã bị thu hồi.")

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

        if action == "task_submit":
            self._task_submit(callback_id, user, chat_id, task)
        elif action == "task_approve":
            self._task_approve(callback_id, user, chat_id, task)
        elif action == "task_reject":
            self._task_reject_start(callback_id, user, chat_id, task)
        elif action == "task_publish":
            self._task_publish(callback_id, user, chat_id, task)
        elif action == "task_edit":
            self._task_edit_start(callback_id, user, chat_id, task)
        elif action == "task_cancel":
            self._task_cancel_start(callback_id, user, chat_id, task)
        elif action == "task_cancel_confirm":
            self._task_cancel_confirm(callback_id, user, chat_id, task)
        elif action == "task_retract":
            self._task_retract_start(callback_id, user, chat_id, task)
        elif action == "task_retract_confirm":
            self._task_retract_confirm(callback_id, user, chat_id, task)
        else:
            self._answer(callback_id, "Thao tác không hợp lệ.")

    def _task_submit(self, callback_id, user, chat_id, task):
        if task["creator_id"] != user["telegram_id"]:
            self._answer(callback_id, "⛔ Chỉ người tạo việc mới gửi duyệt được.",
                         show_alert=True)
            return
        if task["status"] not in (STATUS_DRAFT, STATUS_REJECTED):
            self._answer(callback_id, "Việc này không ở trạng thái gửi duyệt được.",
                         show_alert=True)
            return
        self.store.set_task_status(task["code"], STATUS_PENDING)
        self.store.audit(user["telegram_id"], "submit_task", "task", task["code"])
        self._answer(callback_id, "Đã gửi duyệt.")
        updated = self.store.get_task(task["code"])
        self._send(chat_id, f"📤 Việc {task['code']} đã được gửi duyệt.")
        self._notify_reviewers(updated, exclude_id=user["telegram_id"])

    def _task_approve(self, callback_id, user, chat_id, task):
        if not roles.can_review(user["role"]):
            self.store.audit(user["telegram_id"], "denied_approve_attempt",
                             "task", task["code"], detail=f"role={user['role']}")
            self._answer(callback_id, "⛔ Bạn không có quyền duyệt bài.",
                         show_alert=True)
            return
        if task["status"] != STATUS_PENDING:
            self._answer(callback_id, "Việc này chưa được gửi duyệt.",
                         show_alert=True)
            return
        self.store.set_task_status(task["code"], STATUS_APPROVED_WAITING)
        self.store.audit(user["telegram_id"], "approve_task", "task", task["code"])
        self._answer(callback_id, "Đã duyệt, bài chuyển sang chờ đăng.")
        updated = self.store.get_task(task["code"])
        self._notify_creator(updated, prefix="✅ Bài của bạn đã được duyệt, đang chờ đăng:")

    def _task_reject_start(self, callback_id, user, chat_id, task):
        if not roles.can_review(user["role"]):
            self.store.audit(user["telegram_id"], "denied_reject_attempt",
                             "task", task["code"], detail=f"role={user['role']}")
            self._answer(callback_id, "⛔ Bạn không có quyền trả lại bài.",
                         show_alert=True)
            return
        if task["status"] != STATUS_PENDING:
            self._answer(callback_id, "Việc này chưa được gửi duyệt.",
                         show_alert=True)
            return
        self.store.set_user_state(
            user["telegram_id"], "await_reject_reason", {"code": task["code"]}
        )
        self._answer(callback_id, "Hãy nhắn lý do trả lại.")
        self._send(
            chat_id,
            f"↩️ Trả lại việc {task['code']}: hãy nhắn lý do trả lại "
            "trong tin nhắn tiếp theo.",
        )

    def _apply_reject_reason(self, user, chat_id, reason, payload):
        code = (payload or {}).get("code")
        self.store.clear_user_state(user["telegram_id"])
        task = self.store.get_task(code) if code else None
        if task is None:
            self._send(chat_id, "Không tìm thấy việc cần trả lại.")
            return
        if user is None or not roles.can_review(user["role"]):
            self._send(chat_id, "⛔ Bạn không còn quyền trả lại bài.")
            return
        if task["status"] != STATUS_PENDING:
            self._send(chat_id, "Việc này không còn ở trạng thái chờ duyệt.")
            return
        self.store.set_task_status(code, STATUS_REJECTED, reject_reason=reason)
        self.store.audit(user["telegram_id"], "reject_task", "task", code,
                         detail=f"reason={reason}")
        self._send(chat_id, f"↩️ Đã trả lại việc {code} với lý do: {reason}")
        updated = self.store.get_task(code)
        self._notify_creator(
            updated, prefix=f"↩️ Bài của bạn bị trả lại. Lý do: {reason}"
        )

    def _task_publish(self, callback_id, user, chat_id, task):
        if not roles.can_publish(user):
            # Especially important: contributors must never publish.
            self.store.audit(
                user["telegram_id"], "denied_publish_attempt", "task",
                task["code"],
                detail=f"role={user['role']} can_publish={bool(user['can_publish'])}",
            )
            self._answer(callback_id, "⛔ Bạn không có quyền đăng bài.",
                         show_alert=True)
            return
        if task["status"] != STATUS_APPROVED_WAITING:
            self._answer(callback_id, "Bài chưa ở trạng thái chờ đăng.",
                         show_alert=True)
            return
        self.store.set_task_status(task["code"], STATUS_PUBLISHED)
        self.store.audit(user["telegram_id"], "publish_task", "task", task["code"])
        self._answer(callback_id, "Đã đăng bài.")
        updated = self.store.get_task(task["code"])
        self._notify_creator(updated, prefix="🚀 Bài của bạn đã được đăng:")
        for admin in self.store.admins():
            if admin["telegram_id"] != user["telegram_id"]:
                self._send(admin["chat_id"],
                           f"🚀 Việc {task['code']} vừa được đăng bởi "
                           f"{user['display_name']}.")

    # ------------------------------------------- publish destination toggles
    def _task_dest(self, callback_id, user, chat_id, code, dest):
        task = self.store.get_task(code)
        if task is None:
            self._answer(callback_id, "Không tìm thấy việc này.", show_alert=True)
            return
        if user is None or user["role"] is None:
            self._answer(callback_id, "⛔ Bạn chưa được cấp quyền.",
                         show_alert=True)
            return
        if dest not in DESTINATION_KEYS:
            self._answer(callback_id, "Nơi đăng không hợp lệ.", show_alert=True)
            return
        if task["status"] in (STATUS_PUBLISHED, STATUS_RETRACTED, STATUS_CANCELLED):
            self._answer(callback_id,
                         "Việc đã đóng, không đổi được nơi đăng nữa.",
                         show_alert=True)
            return
        if not _can_toggle_destinations(user, task):
            self._answer(callback_id,
                         "⛔ Bạn không có quyền đổi nơi đăng của việc này.",
                         show_alert=True)
            return
        chosen = task_destinations(task)
        if dest in chosen:
            if len(chosen) <= 1:
                self._answer(callback_id,
                             "Phải chọn ít nhất một nơi đăng.",
                             show_alert=True)
                return
            chosen = [k for k in chosen if k != dest]
        else:
            chosen = [k for k in DESTINATION_KEYS if k in chosen or k == dest]
        self.store.set_task_destinations(code, chosen)
        self.store.audit(user["telegram_id"], "set_destinations", "task",
                         code, detail=json.dumps(chosen, ensure_ascii=False))
        updated = self.store.get_task(code)
        # Keep the per-channel outputs in step with the new selection:
        # deselected channels lose their DRAFT/PENDING output, newly
        # selected channels gain one.
        self.store.sync_outputs_for_task(updated)
        self._answer(callback_id, "Đã cập nhật nơi đăng.")
        self._send(chat_id, self.task_card_text(updated),
                   reply_markup=self.task_keyboard(updated, user))

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

    # --------------------------------------------------------- retract flow
    def _task_retract_start(self, callback_id, user, chat_id, task):
        if task["status"] != STATUS_PUBLISHED:
            self._answer(callback_id, "Bài này chưa được đăng.",
                         show_alert=True)
            return
        self._answer(callback_id)
        markup = _keyboard([[
            _btn("↩️ Xác nhận thu hồi bài đã đăng",
                 f"task_retract_confirm:{task['code']}"),
        ]])
        self._send(
            chat_id,
            f"Thu hồi bài đã đăng của việc {task['code']}? "
            "Bấm Xác nhận để thu hồi.",
            reply_markup=markup,
        )

    def _task_retract_confirm(self, callback_id, user, chat_id, task):
        if not roles.can_publish(user):
            self.store.audit(
                user["telegram_id"], "denied_retract_attempt", "task",
                task["code"], detail=f"role={user['role']}",
            )
            self._answer(callback_id,
                         "⛔ Bạn không có quyền thu hồi bài đã đăng.",
                         show_alert=True)
            return
        if task["status"] != STATUS_PUBLISHED:
            self._answer(callback_id, "Bài này không còn ở trạng thái đã đăng.",
                         show_alert=True)
            return
        self.store.set_task_status(task["code"], STATUS_RETRACTED)
        self.store.audit(user["telegram_id"], "retract_task", "task",
                         task["code"])
        self._answer(callback_id, "Đã thu hồi bài.")
        updated = self.store.get_task(task["code"])
        self._notify_creator(updated, prefix="↩️ Bài của bạn đã bị thu hồi:")
        for admin in self.store.admins():
            if admin["telegram_id"] != user["telegram_id"]:
                self._send(
                    admin["chat_id"],
                    f"↩️ Việc {task['code']} (bài đã đăng) vừa bị thu hồi bởi "
                    f"{user['display_name']}.\n" + self.task_card_text(updated),
                )

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
            lines.append(f"Lý do trả lại: {output['reject_reason']}")
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
                _btn("↩️ Trả lại", f"out_reject:{oid}"),
            ])
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
        self.store.set_output_status(output["id"], STATUS_PENDING)
        self.store.audit(user["telegram_id"], "submit_output", "output",
                         output["id"],
                         detail=f"task={task['code']} channel={output['channel']}")
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
        if output["status"] != STATUS_PENDING:
            self._answer(callback_id, "Thẻ kênh này chưa được gửi duyệt.",
                         show_alert=True)
            return
        self.store.set_user_state(
            user["telegram_id"], "await_output_reject_reason",
            {"output_id": output["id"]},
        )
        self._answer(callback_id, "Hãy nhắn lý do trả lại.")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        self._send(
            chat_id,
            f"↩️ Trả lại thẻ kênh {label} của việc {task['code']}: "
            "hãy nhắn lý do trả lại trong tin nhắn tiếp theo.",
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
        if output["status"] != STATUS_PENDING:
            self._send(chat_id, "Thẻ kênh này không còn ở trạng thái chờ duyệt.")
            return
        self.store.set_output_status(
            output["id"], STATUS_REJECTED, reject_reason=reason)
        self.store.audit(user["telegram_id"], "reject_output", "output",
                         output["id"],
                         detail=f"task={task['code']} "
                                f"channel={output['channel']} reason={reason}")
        label = CHANNELS.get(output["channel"], {}).get(
            "label", output["channel"])
        self._send(
            chat_id,
            f"↩️ Đã trả lại thẻ kênh {label} của việc {task['code']} "
            f"với lý do: {reason}",
        )
        updated = self.store.get_output(output["id"])
        self._notify_output_creator(
            updated, task,
            prefix=f"↩️ Thẻ kênh {label} của bạn bị trả lại. "
                   f"Lý do: {reason}")

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
