"""Unit tests for the article-workflow bot.

Everything runs offline: FakeApi stands in for the Telegram transport,
the store is an in-memory SQLite database, and no token is used.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import botctl
import roles
from handlers import BotApp
from storage import Store

ADMIN = 111
EDITOR = 222
CONTRIBUTOR = 333
NEWBIE = 444
FIRST = 555


class FakeApi:
    """Records every sendMessage / answerCallbackQuery instead of calling
    the real Telegram API."""

    def __init__(self):
        self.sent = []       # [{chat_id, text, reply_markup}]
        self.answered = []   # [{callback_query_id, text, show_alert}]
        self.edits = []      # [{chat_id, message_id, text?, reply_markup}]
        self.commands_set = []  # [{commands, scope}]
        self.documents = []  # [{chat_id, path, caption}]
        self._next_id = 1

    def send_document(self, chat_id, file_path, caption=None):
        from pathlib import Path as _P
        self.documents.append(
            {"chat_id": chat_id, "path": _P(file_path), "caption": caption})
        return {"message_id": 999}

    def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent.append(
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        )
        self._next_id += 1
        return {"message_id": self._next_id}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None,
                          **kwargs):
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text,
             "reply_markup": reply_markup}
        )
        return True

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": None,
             "reply_markup": reply_markup}
        )
        return True

    def set_my_commands(self, commands, scope=None, language_code=None):
        self.commands_set.append({"commands": commands, "scope": scope})
        return True

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered.append(
            {"callback_query_id": callback_query_id, "text": text,
             "show_alert": show_alert}
        )
        return True

    # get_file / download_file exist on the real client; downloads are
    # disabled in tests (BotApp(download_files=False)) so these must not
    # be called.
    def get_file(self, file_id):  # pragma: no cover
        raise AssertionError("get_file must not be called in tests")

    def download_file(self, file_path, destination):  # pragma: no cover
        raise AssertionError("download_file must not be called in tests")

    # ------------------------------------------------------------ helpers
    def texts_to(self, chat_id):
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]

    def all_callback_data(self):
        data = []
        for message in self.sent:
            markup = message.get("reply_markup") or {}
            for row in markup.get("inline_keyboard", []):
                for button in row:
                    data.append(button["callback_data"])
        return data

    def find_callback(self, prefix, suffix=None):
        for data in self.all_callback_data():
            if data.startswith(prefix) and (suffix is None or data.endswith(suffix)):
                return data
        return None


def tg_user(user_id, username=None, name=None):
    return {
        "id": user_id,
        "username": username or f"user{user_id}",
        "first_name": name or f"User{user_id}",
    }


def message_update(user_id, text=None, photo=None, document=None,
                   caption=None, update_id=1):
    message = {
        "message_id": update_id,
        "from": tg_user(user_id),
        "chat": {"id": user_id, "type": "private"},
    }
    if text is not None:
        message["text"] = text
    if photo is not None:
        message["photo"] = photo
    if document is not None:
        message["document"] = document
    if caption is not None:
        message["caption"] = caption
    return {"update_id": update_id, "message": message}


def callback_update(user_id, data, update_id=1):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}-{data}",
            "from": tg_user(user_id),
            "message": {"message_id": 1, "chat": {"id": user_id}},
            "data": data,
        },
    }


PHOTO = [{"file_id": "photo-small"}, {"file_id": "photo-large"}]


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.api = FakeApi()
        self.app = BotApp(self.store, self.api)
        # A standing cast: admin + editor + contributor, all registered.
        for uid, role, can_publish in (
            (ADMIN, roles.ROLE_ADMIN, True),
            (EDITOR, roles.ROLE_EDITOR, False),
            (CONTRIBUTOR, roles.ROLE_CONTRIBUTOR, False),
        ):
            self.store.register_user(uid, f"user{uid}", f"User{uid}", uid)
            self.store.set_role(uid, role, can_publish=can_publish)
        self.api.sent.clear()

    def tearDown(self):
        self.store.close()

    # ------------------------------------------------------------- helpers
    def create_contributor_task(self):
        """Contributor sends a photo, then a text hint to finalise."""
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        self.app.handle_update(message_update(
            CONTRIBUTOR,
            text="Hội thảo nông nghiệp thông minh xã Chân Mộng, Phú Thọ",
        ))
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 1)
        return tasks[0]

    def attach_draft_and_submit(self, code, channel="website"):
        """Option A flow: Muse's draft lands on the channel card,
        then the creator submits that channel for review."""
        output = self.store.get_output_by_channel(code, channel)
        self.store.set_output_draft(
            output["id"], f"https://example.com/draft/{channel}")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{output['id']}"))
        return self.store.get_output(output["id"])

    # --------------------------------------------------------------- tests
    def test_unknown_user_cannot_create_task(self):
        self.app.handle_update(message_update(999, photo=PHOTO))
        self.app.handle_update(message_update(999, text="làm bài này giúp"))
        self.assertEqual(self.store.list_tasks(), [])
        texts = self.api.texts_to(999)
        self.assertTrue(any("/start" in t for t in texts))

    def test_pending_user_cannot_create_task(self):
        self.app.handle_update(message_update(NEWBIE, text="/start"))
        self.app.handle_update(message_update(NEWBIE, photo=PHOTO))
        self.app.handle_update(message_update(NEWBIE, text="gợi ý"))
        self.assertEqual(self.store.list_tasks(), [])

    def test_contributor_photo_and_hint_creates_task(self):
        task = self.create_contributor_task()
        self.assertEqual(task["code"], "CV-0001")
        self.assertEqual(task["status"], "DRAFT")
        self.assertEqual(task["creator_id"], CONTRIBUTOR)
        self.assertEqual(task["creator_role"], roles.ROLE_CONTRIBUTOR)
        self.assertIn("Chân Mộng", task["hint"])
        self.assertEqual(json.loads(task["file_ids"]), ["photo-large"])
        # Caption counts as a hint too.
        self.app.handle_update(message_update(
            CONTRIBUTOR, photo=PHOTO, caption="Ảnh hội thảo"))
        self.app.handle_update(message_update(CONTRIBUTOR, text="chốt bài"))
        task2 = self.store.get_task("CV-0002")
        self.assertIsNotNone(task2)
        self.assertIn("Ảnh hội thảo", task2["hint"])
        self.assertIn("chốt bài", task2["hint"])
        # Option A: reviewers are NOT sent the still-empty task card at
        # creation — there is no draft to review yet. The creator got
        # the card; reviewers hear about the task when a channel draft
        # is attached or submitted.
        self.assertFalse(any(
            "CV-0001" in t for t in self.api.texts_to(ADMIN)))
        self.assertTrue(any(
            "CV-0001" in t for t in self.api.texts_to(CONTRIBUTOR)))

    def test_task_level_actions_on_stale_card_are_redirected(self):
        # Option A: cards sent before the redesign still carry task-level
        # review buttons. Tapping one must not move anything — the bot
        # points the user at the channel cards instead.
        task = self.create_contributor_task()
        code = task["code"]
        for action in ("task_submit", "task_approve", "task_publish",
                       "task_retract"):
            self.api.answered.clear()
            self.app.handle_update(callback_update(ADMIN, f"{action}:{code}"))
            self.assertTrue(self.api.answered[-1]["show_alert"])
            self.assertIn("thẻ kênh", self.api.answered[-1]["text"])
            self.assertEqual(self.store.get_task(code)["status"], "DRAFT")

    def test_output_approve_moves_channel_and_derives_task_status(self):
        task = self.create_contributor_task()
        code = task["code"]
        output = self.attach_draft_and_submit(code)
        self.assertEqual(output["status"], "PENDING")
        # The parent task's status is derived from its channels.
        self.assertEqual(self.store.get_task(code)["status"], "PENDING")
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{output['id']}"))
        self.assertEqual(
            self.store.get_output(output["id"])["status"], "APPROVED_WAITING")
        self.assertEqual(
            self.store.get_task(code)["status"], "APPROVED_WAITING")
        self.assertEqual(len(self.store.audit_entries("approve_output")), 1)

    def test_output_publish_requires_permission_admin_works(self):
        task = self.create_contributor_task()
        code = task["code"]
        output = self.attach_draft_and_submit(code)
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{output['id']}"))
        # Editor has can_publish = False.
        self.app.handle_update(
            callback_update(EDITOR, f"out_publish:{output['id']}"))
        self.assertEqual(
            self.store.get_output(output["id"])["status"], "APPROVED_WAITING")
        self.assertEqual(
            len(self.store.audit_entries("denied_output_publish_attempt")), 1)
        # Admin publishes.
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{output['id']}"))
        self.assertEqual(
            self.store.get_output(output["id"])["status"], "PUBLISHED")
        self.assertEqual(len(self.store.audit_entries("publish_output")), 1)

    def test_output_submit_blocked_until_draft_attached(self):
        # The exact flaw the owner reported: submitting for review while
        # no article exists yet. The channel card refuses until Muse's
        # draft is attached.
        task = self.create_contributor_task()
        code = task["code"]
        web = self.store.get_output_by_channel(code, "website")
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{web['id']}"))
        self.assertEqual(self.store.get_output(web["id"])["status"], "DRAFT")
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertIn("chưa có bản nháp", self.api.answered[-1]["text"])
        # Once the draft lands, submitting works.
        self.store.set_output_draft(web["id"], "https://example.com/draft/web")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PENDING")

    def test_non_admin_capquyen_denied(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/capquyen"))
        texts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any("không có quyền" in t for t in texts))
        # And via a forged callback, too.
        self.app.handle_update(callback_update(CONTRIBUTOR, "grant_pick:444"))
        self.assertTrue(self.api.answered[-1]["show_alert"])

    def test_admin_grant_flow(self):
        # New user registers as pending.
        self.app.handle_update(message_update(NEWBIE, text="/start"))
        user = self.store.get_user(NEWBIE)
        self.assertIsNone(user["role"])
        # Admin opens /capquyen and picks the new user.
        self.app.handle_update(message_update(ADMIN, text="/capquyen"))
        pick = self.api.find_callback("grant_pick:", suffix=str(NEWBIE))
        self.assertIsNotNone(pick)
        self.app.handle_update(callback_update(ADMIN, pick))
        role_cb = self.api.find_callback(
            "grant_role:", suffix=f"{NEWBIE}:{roles.ROLE_WRITER}")
        self.assertIsNotNone(role_cb)
        self.app.handle_update(callback_update(ADMIN, role_cb))
        confirm = self.api.find_callback(
            "grant_confirm:", suffix=f"{NEWBIE}:{roles.ROLE_WRITER}")
        self.assertIsNotNone(confirm)
        self.app.handle_update(callback_update(ADMIN, confirm))
        user = self.store.get_user(NEWBIE)
        self.assertEqual(user["role"], roles.ROLE_WRITER)
        grants = [a for a in self.store.audit_entries("grant_role")
                  if a["actor_id"] == ADMIN]
        self.assertEqual(len(grants), 1)

    def test_deputy_cannot_grant_editor(self):
        deputy = 777
        self.store.register_user(deputy, "deputy", "Deputy", deputy)
        self.store.set_role(deputy, roles.ROLE_DEPUTY)
        self.app.handle_update(message_update(NEWBIE, text="/start"))
        self.app.handle_update(
            callback_update(deputy, f"grant_confirm:{NEWBIE}:{roles.ROLE_EDITOR}"))
        self.assertIsNone(self.store.get_user(NEWBIE)["role"])
        self.assertTrue(self.api.answered[-1]["show_alert"])

    def test_revoke_works(self):
        writer = NEWBIE
        self.store.register_user(writer, "writer", "Writer", writer)
        self.store.set_role(writer, roles.ROLE_WRITER)
        self.app.handle_update(message_update(ADMIN, text="/thuhoi"))
        pick = self.api.find_callback("revoke_pick:", suffix=str(writer))
        self.assertIsNotNone(pick)
        self.app.handle_update(callback_update(ADMIN, pick))
        confirm = self.api.find_callback("revoke_confirm:", suffix=str(writer))
        self.assertIsNotNone(confirm)
        self.app.handle_update(callback_update(ADMIN, confirm))
        self.assertIsNone(self.store.get_user(writer)["role"])
        self.assertEqual(len(self.store.audit_entries("revoke_role")), 1)
        # The revoked user can no longer create tasks.
        self.app.handle_update(message_update(writer, photo=PHOTO))
        self.app.handle_update(message_update(writer, text="gợi ý"))
        self.assertEqual(self.store.list_tasks(), [])

    def test_first_user_can_claim_admin(self):
        store = Store(":memory:")
        api = FakeApi()
        app = BotApp(store, api)
        try:
            app.handle_update(message_update(FIRST, text="/start"))
            claim = api.find_callback("claim_admin")
            self.assertEqual(claim, "claim_admin")
            app.handle_update(callback_update(FIRST, "claim_admin"))
            confirm = api.find_callback("claim_admin_confirm")
            self.assertEqual(confirm, "claim_admin_confirm")
            app.handle_update(callback_update(FIRST, "claim_admin_confirm"))
            user = store.get_user(FIRST)
            self.assertEqual(user["role"], roles.ROLE_ADMIN)
            self.assertEqual(len(store.audit_entries("claim_admin")), 1)
            # A second user cannot claim once an admin exists.
            app.handle_update(message_update(666, text="/start"))
            app.handle_update(callback_update(666, "claim_admin_confirm"))
            self.assertIsNone(store.get_user(666)["role"])
        finally:
            store.close()

    def test_vaitro_lists_roles(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/vaitro"))
        texts = self.api.texts_to(CONTRIBUTOR)
        joined = "\n".join(texts)
        for title in ("Admin", "Phó Admin", "Biên tập viên",
                      "Nhân viên viết bài", "Cộng tác viên"):
            self.assertIn(title, joined)

    # ------------------------------------------- new: card / destinations
    def test_card_shows_creator_time_and_destinations(self):
        task = self.create_contributor_task()
        cards = [t for t in self.api.texts_to(CONTRIBUTOR) if task["code"] in t]
        self.assertTrue(cards)
        card = cards[0]
        self.assertIn(f"Người tạo: User{CONTRIBUTOR}", card)
        self.assertRegex(
            card, r"Thời gian tạo: \d{2}:\d{2} \d{2}/\d{2}/\d{4}")
        self.assertIn("Nơi đăng: Website • Facebook • Instagram", card)
        # Default destinations stored on the task itself.
        self.assertEqual(
            json.loads(task["destinations"]),
            ["website", "facebook", "instagram"],
        )

    def test_destination_toggle_remove_readd_and_last_refusal(self):
        task = self.create_contributor_task()
        code = task["code"]
        # Remove Facebook.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        task = self.store.get_task(code)
        self.assertEqual(json.loads(task["destinations"]),
                         ["website", "instagram"])
        # A refreshed card was sent showing the new selection + toggle state.
        cards = [t for t in self.api.texts_to(CONTRIBUTOR) if code in t]
        self.assertIn("Nơi đăng: Website • Instagram", cards[-1])
        self.assertIsNotNone(
            self.api.find_callback("task_dest:", suffix=f"{code}:facebook"))
        buttons = [
            b["text"]
            for m in self.api.sent if m["chat_id"] == CONTRIBUTOR
            for row in (m.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row
        ]
        self.assertIn("⬜ Facebook", buttons)
        self.assertIn("✅ Website", buttons)
        # Re-add Facebook.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "facebook", "instagram"],
        )
        # Remove two, leaving only Facebook.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:website"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:instagram"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]), ["facebook"])
        # Removing the last one is refused.
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]), ["facebook"])
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertIn("ít nhất một nơi đăng", self.api.answered[-1]["text"])
        # Successful toggles were audited (remove + re-add + 2 removes;
        # the refused removal of the last destination is not audited).
        self.assertEqual(len(self.store.audit_entries("set_destinations")), 4)

    def test_destination_toggle_permission_by_status(self):
        task = self.create_contributor_task()
        code = task["code"]
        # While the task is PENDING (a channel is under review), the
        # contributor creator may no longer toggle; a reviewer may.
        self.attach_draft_and_submit(code)
        self.assertEqual(self.store.get_task(code)["status"], "PENDING")
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "facebook", "instagram"],
        )
        self.app.handle_update(
            callback_update(EDITOR, f"task_dest:{code}:facebook"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "instagram"],
        )

    # ----------------------------------------------------- new: edit flow
    def test_non_creator_cannot_edit(self):
        task = self.create_contributor_task()
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(EDITOR, f"task_edit:{task['code']}"))
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertEqual(
            len(self.store.audit_entries("denied_edit_attempt")), 1)
        self.assertIn("Chân Mộng", self.store.get_task(task["code"])["hint"])

    def test_creator_edit_flow_replaces_hint(self):
        task = self.create_contributor_task()
        code = task["code"]
        self.app.handle_update(callback_update(CONTRIBUTOR, f"task_edit:{code}"))
        prompts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any("Chỉnh sửa việc" in t for t in prompts))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Gợi ý mới: họp chợ nông sản"))
        task = self.store.get_task(code)
        self.assertEqual(task["hint"], "Gợi ý mới: họp chợ nông sản")
        self.assertEqual(len(self.store.audit_entries("edit_task")), 1)
        # Updated card was re-sent with the new hint.
        cards = [t for t in self.api.texts_to(CONTRIBUTOR) if code in t]
        self.assertIn("Gợi ý mới: họp chợ nông sản", cards[-1])

    # --------------------------------------------------- new: cancel flow
    def test_creator_recall_cancels_draft_after_confirmation(self):
        task = self.create_contributor_task()
        code = task["code"]
        self.app.handle_update(callback_update(CONTRIBUTOR, f"task_cancel:{code}"))
        confirm = self.api.find_callback("task_cancel_confirm:", suffix=code)
        self.assertIsNotNone(confirm)
        # Not cancelled yet — confirmation is a separate step.
        self.assertEqual(self.store.get_task(code)["status"], "DRAFT")
        self.app.handle_update(callback_update(CONTRIBUTOR, confirm))
        self.assertEqual(self.store.get_task(code)["status"], "CANCELLED")
        self.assertEqual(len(self.store.audit_entries("cancel_task")), 1)
        # Reviewers were notified.
        self.assertTrue(any(
            "đã bị thu hồi" in t for t in self.api.texts_to(ADMIN)))
        # A cancelled task shows no action buttons.
        cancelled = self.store.get_task(code)
        creator_row = self.store.get_user(CONTRIBUTOR)
        self.assertIsNone(self.app.task_keyboard(cancelled, creator_row))

    def test_cancelled_task_rejects_submit(self):
        task = self.create_contributor_task()
        code = task["code"]
        self.app.handle_update(callback_update(CONTRIBUTOR, f"task_cancel:{code}"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_cancel_confirm:{code}"))
        self.assertEqual(self.store.get_task(code)["status"], "CANCELLED")
        self.api.answered.clear()
        self.app.handle_update(callback_update(CONTRIBUTOR, f"task_submit:{code}"))
        self.assertEqual(self.store.get_task(code)["status"], "CANCELLED")
        self.assertTrue(self.api.answered[-1]["show_alert"])

    # ------------------------------------- retract flow (channel cards)
    def test_output_retract_requires_publish_permission_admin_works(self):
        task = self.create_contributor_task()
        code = task["code"]
        web = self.attach_draft_and_submit(code)
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{web['id']}"))
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PUBLISHED")
        # Editor without can_publish: the retract confirm is denied and
        # audited.
        self.app.handle_update(
            callback_update(EDITOR, f"out_retract:{web['id']}"))
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(EDITOR, f"out_retract_confirm:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PUBLISHED")
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertEqual(
            len(self.store.audit_entries("denied_output_retract_attempt")), 1)
        # Contributor is denied too.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_retract_confirm:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PUBLISHED")
        self.assertEqual(
            len(self.store.audit_entries("denied_output_retract_attempt")), 2)
        # Admin retracts successfully.
        self.app.handle_update(
            callback_update(ADMIN, f"out_retract:{web['id']}"))
        confirm = self.api.find_callback("out_retract_confirm:")
        self.assertIsNotNone(confirm)
        self.app.handle_update(callback_update(ADMIN, confirm))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "RETRACTED")
        self.assertEqual(len(self.store.audit_entries("retract_output")), 1)
        # Creator was notified.
        self.assertTrue(any(
            "đã bị thu hồi" in t for t in self.api.texts_to(CONTRIBUTOR)))

    # --------------------------------- new: per-channel outputs (Option A)
    def outputs_by_channel(self, code):
        return {o["channel"]: o for o in self.store.list_outputs(code)}

    def test_task_creation_auto_creates_three_outputs(self):
        task = self.create_contributor_task()
        outputs = self.outputs_by_channel(task["code"])
        self.assertEqual(set(outputs), {"website", "facebook", "instagram"})
        for output in outputs.values():
            self.assertEqual(output["status"], "DRAFT")
            self.assertEqual(output["task_code"], task["code"])
            self.assertIsNone(output["draft_ref"])
            self.assertIsNone(output["public_url"])

    def test_destination_toggle_syncs_outputs(self):
        task = self.create_contributor_task()
        code = task["code"]
        # Toggle Facebook off: its DRAFT output is cancelled, others stay.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        outputs = self.outputs_by_channel(code)
        self.assertEqual(outputs["facebook"]["status"], "CANCELLED")
        self.assertEqual(outputs["website"]["status"], "DRAFT")
        self.assertEqual(outputs["instagram"]["status"], "DRAFT")
        # Toggle Facebook back on: the output is recreated as DRAFT
        # (same row revived).
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        revived = self.store.get_output_by_channel(code, "facebook")
        self.assertEqual(revived["status"], "DRAFT")
        self.assertEqual(revived["id"], outputs["facebook"]["id"])

    def test_task_outputs_button_sends_channel_cards(self):
        task = self.create_contributor_task()
        code = task["code"]
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_outputs:{code}"))
        cards = [t for t in self.api.texts_to(CONTRIBUTOR)
                 if "Thẻ kênh" in t]
        self.assertEqual(len(cards), 3)
        joined = "\n".join(cards)
        for label in ("Website", "Facebook", "Instagram"):
            self.assertIn(f"Thẻ kênh {label}", joined)
        self.assertIn(code, joined)
        # Each card carries its own action buttons.
        self.assertIsNotNone(self.api.find_callback("out_submit:"))

    def test_parent_card_contains_channel_summary(self):
        task = self.create_contributor_task()
        card = self.app.task_card_text(self.store.get_task(task["code"]))
        self.assertIn("Đầu ra theo kênh:", card)
        self.assertIn("• Website: Nháp (DRAFT)", card)
        self.assertIn("• Facebook: Nháp (DRAFT)", card)
        self.assertIn("• Instagram: Nháp (DRAFT)", card)

    def test_output_lifecycle_is_independent_per_channel(self):
        task = self.create_contributor_task()
        code = task["code"]
        web = self.outputs_by_channel(code)["website"]
        self.store.set_output_draft(
            web["id"], "https://example.com/draft/web")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PENDING")
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "APPROVED_WAITING")
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PUBLISHED")
        self.assertEqual(len(self.store.audit_entries("approve_output")), 1)
        self.assertEqual(len(self.store.audit_entries("publish_output")), 1)
        # The other channels and the parent task are untouched.
        outputs = self.outputs_by_channel(code)
        self.assertEqual(outputs["facebook"]["status"], "DRAFT")
        self.assertEqual(outputs["instagram"]["status"], "DRAFT")
        self.assertEqual(self.store.get_task(code)["status"], "DRAFT")

    def test_contributor_output_publish_denied_and_audited(self):
        task = self.create_contributor_task()
        code = task["code"]
        web = self.outputs_by_channel(code)["website"]
        self.store.set_output_draft(
            web["id"], "https://example.com/draft/web")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{web['id']}"))
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "APPROVED_WAITING")
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_publish:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "APPROVED_WAITING")
        denied = self.store.audit_entries("denied_output_publish_attempt")
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["actor_id"], CONTRIBUTOR)
        self.assertTrue(self.api.answered[-1]["show_alert"])

    def test_output_reject_with_reason_returns_to_creator(self):
        task = self.create_contributor_task()
        code = task["code"]
        fb = self.outputs_by_channel(code)["facebook"]
        self.store.set_output_draft(
            fb["id"], "https://example.com/draft/fb")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{fb['id']}"))
        self.app.handle_update(
            callback_update(EDITOR, f"out_reject:{fb['id']}"))
        prompts = self.api.texts_to(EDITOR)
        self.assertTrue(any("Thẻ kênh Facebook" in t for t in prompts))
        self.app.handle_update(
            message_update(EDITOR, text="Caption chưa hay, viết lại"))
        output = self.store.get_output(fb["id"])
        self.assertEqual(output["status"], "REJECTED")
        self.assertEqual(output["reject_reason"], "Caption chưa hay, viết lại")
        self.assertEqual(len(self.store.audit_entries("reject_output")), 1)
        # Creator got the channel card back with the reason.
        creator_texts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any(
            "Thẻ kênh Facebook" in t and "Caption chưa hay" in t
            for t in creator_texts))

    def test_attach_output_draft_stores_ref_and_sends_card(self):
        task = self.create_contributor_task()
        code = task["code"]
        original = botctl.build_client_or_warn
        botctl.build_client_or_warn = lambda no_notify: self.api
        self.api.sent.clear()
        try:
            args = argparse.Namespace(
                task_code=code, channel="facebook",
                ref="https://example.com/draft/fb", no_notify=False)
            rc = botctl.cmd_attach_output_draft(self.store, args)
        finally:
            botctl.build_client_or_warn = original
        self.assertEqual(rc, 0)
        fb = self.store.get_output_by_channel(code, "facebook")
        self.assertEqual(fb["draft_ref"], "https://example.com/draft/fb")
        # Attaching a draft does not move the output's status.
        self.assertEqual(fb["status"], "DRAFT")
        self.assertEqual(
            len(self.store.audit_entries("attach_output_draft")), 1)
        # The channel card was sent to the creator and to a reviewer.
        for chat_id in (CONTRIBUTOR, ADMIN):
            texts = self.api.texts_to(chat_id)
            self.assertTrue(any(
                "Thẻ kênh Facebook" in t
                and "https://example.com/draft/fb" in t for t in texts),
                f"no channel card sent to {chat_id}")

    def test_attach_output_draft_with_file_delivers_readable_content(self):
        # The owner could not open the bare .md references: with --file
        # the draft itself must arrive in Telegram as readable text,
        # and the website channel also gets an HTML preview document.
        task = self.create_contributor_task()
        code = task["code"]
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / f"{code}-website.md"
            md_path.write_text(
                "# Bản nháp Website — " + code + "\n"
                "Kênh: Website\n\n---\n\n"
                "## Tiêu đề\n**Bài viết hay về hội thảo**\n\n"
                "Nội dung chính của bài viết nằm ở đây.",
                encoding="utf-8")
            original = botctl.build_client_or_warn
            botctl.build_client_or_warn = lambda no_notify: self.api
            self.api.sent.clear()
            try:
                args = argparse.Namespace(
                    task_code=code, channel="website",
                    ref=md_path.name, no_notify=False, file=str(md_path))
                rc = botctl.cmd_attach_output_draft(self.store, args)
            finally:
                botctl.build_client_or_warn = original
            self.assertEqual(rc, 0)
            texts = self.api.texts_to(CONTRIBUTOR)
            self.assertTrue(any(
                "NỘI DUNG BẢN NHÁP" in t and "Bài viết hay về hội thảo" in t
                and "Nội dung chính của bài viết" in t for t in texts))
            # Markdown markers were flattened for the reader.
            self.assertFalse(any("**Bài viết" in t for t in texts))
            docs = [d for d in self.api.documents
                    if d["chat_id"] == CONTRIBUTOR]
            self.assertEqual(len(docs), 1)
            self.assertTrue(docs[0]["path"].name.endswith("-preview.html"))
            preview = docs[0]["path"].read_text(encoding="utf-8")
            self.assertIn("Bài viết hay về hội thảo", preview)
            self.assertIn("<strong>", preview)

    # --------------------------------------- new: botctl + migration cover
    def test_botctl_task_dict_includes_destinations(self):
        task = self.create_contributor_task()
        as_dict = botctl.task_to_dict(self.store.get_task(task["code"]))
        self.assertEqual(
            as_dict["destinations"], ["website", "facebook", "instagram"])

    def test_store_migration_adds_destinations_to_old_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE tasks ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " code TEXT NOT NULL UNIQUE,"
                " creator_id INTEGER NOT NULL,"
                " creator_role TEXT,"
                " hint TEXT NOT NULL DEFAULT '',"
                " file_ids TEXT NOT NULL DEFAULT '[]',"
                " draft_url TEXT, public_url TEXT,"
                " status TEXT NOT NULL DEFAULT 'DRAFT',"
                " reject_reason TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO tasks (code, creator_id, creator_role, hint,"
                " created_at, updated_at) VALUES"
                " ('CV-0009', 1, 'admin', 'việc cũ',"
                " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
            conn.close()
            store = Store(path)
            try:
                cols = {
                    r["name"]
                    for r in store._conn.execute("PRAGMA table_info(tasks)")
                }
                self.assertIn("destinations", cols)
                old = store.get_task("CV-0009")
                self.assertEqual(old["hint"], "việc cũ")
                self.assertEqual(
                    json.loads(old["destinations"]),
                    ["website", "facebook", "instagram"],
                )
            finally:
                store.close()


class Phase1TestCase(BotTestCase):
    """Phase 1: role menus, main keyboard, creation wizard, edit-in-place."""

    def keyboard_labels_of(self, chat_id):
        labels = []
        for m in self.api.sent:
            if m["chat_id"] != chat_id:
                continue
            markup = m.get("reply_markup") or {}
            for row in markup.get("keyboard", []):
                labels.extend(b["text"] for b in row)
        return labels

    def test_start_sends_role_main_keyboard(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/start"))
        labels = self.keyboard_labels_of(CONTRIBUTOR)
        self.assertIn("📝 Giao việc mới", labels)
        self.assertIn("📋 Việc của tôi", labels)
        self.assertNotIn("📊 Thống kê", labels)
        self.app.handle_update(message_update(ADMIN, text="/start"))
        labels = self.keyboard_labels_of(ADMIN)
        self.assertIn("⏳ Chờ duyệt", labels)
        self.assertIn("📊 Thống kê", labels)
        self.assertIn("👥 Người dùng", labels)

    def test_menu_commands_follow_role(self):
        self.app.sync_all_commands()
        by_chat = {}
        for entry in self.api.commands_set:
            scope = entry["scope"] or {}
            if scope.get("type") == "chat":
                by_chat[scope["chat_id"]] = [
                    c["command"] for c in entry["commands"]]
        self.assertIn("thongke", by_chat[ADMIN])
        self.assertIn("capquyen", by_chat[ADMIN])
        self.assertIn("thongke", by_chat[EDITOR])
        self.assertNotIn("capquyen", by_chat[EDITOR])
        self.assertNotIn("thongke", by_chat[CONTRIBUTOR])
        self.assertIn("giaoviec", by_chat[CONTRIBUTOR])

    def test_keyboard_label_opens_my_tasks(self):
        self.create_contributor_task()
        self.api.sent.clear()
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="📋 Việc của tôi"))
        texts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any("CV-0001" in t for t in texts))

    def test_wizard_full_flow_with_channel_toggle(self):
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="📝 Giao việc mới"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_files")
        self.app.handle_update(message_update(
            CONTRIBUTOR, photo=PHOTO, caption="Ảnh cổng trường"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_hint")
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Khai giảng năm học mới"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_channels")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, "wiz_dest:instagram"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(
            state["payload"]["destinations"], ["website", "facebook"])
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        self.assertIsNone(self.store.get_user_state(CONTRIBUTOR))
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertIn("Khai giảng", task["hint"])
        self.assertIn("Ảnh cổng trường", task["hint"])
        self.assertEqual(
            json.loads(task["destinations"]), ["website", "facebook"])
        channels = [o["channel"] for o in self.store.list_outputs(task["code"])]
        self.assertEqual(sorted(channels), ["facebook", "website"])

    def test_wizard_contributor_needs_a_file(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/giaoviec"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_files")
        self.assertTrue(self.api.answered[-1]["show_alert"])

    def test_wizard_cancel_clears_state(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/giaoviec"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_cancel"))
        self.assertIsNone(self.store.get_user_state(CONTRIBUTOR))
        self.assertEqual(self.store.list_tasks(), [])

    def test_card_refreshes_in_place_on_submit(self):
        task = self.create_contributor_task()
        web = self.store.get_output_by_channel(task["code"], "website")
        self.store.set_output_draft(
            web["id"], "https://example.com/draft/web")
        self.api.edits.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{web['id']}"))
        self.assertTrue(self.api.edits)
        edit = self.api.edits[-1]
        self.assertEqual(edit["chat_id"], CONTRIBUTOR)
        self.assertIn("Thẻ kênh Website", edit["text"])
        self.assertIn("Chờ duyệt (PENDING)", edit["text"])

    def test_wizard_pinned_keyboard_and_label_advance(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/giaoviec"))
        # The step-1 prompt swaps in the pinned wizard reply keyboard.
        prompt = self.api.sent[-1]
        kb = (prompt["reply_markup"] or {}).get("keyboard", [])
        labels = [b["text"] for row in kb for b in row]
        self.assertIn("✅ Xong, sang bước gợi ý", labels)
        # Files are acknowledged by ONE counter message that is edited
        # in place — no new message and no button block per photo.
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        counters = [m for m in self.api.sent if "Đã nhận" in m["text"]]
        self.assertEqual(len(counters), 1)
        self.assertIn("Đã nhận 1 file", counters[0]["text"])
        self.assertNotIn("wiz_done_files", self.api.all_callback_data())
        sent_before = len(self.api.sent)
        photo2 = [{"file_id": "photo2-small"}, {"file_id": "photo2-large"}]
        self.app.handle_update(message_update(CONTRIBUTOR, photo=photo2))
        self.assertEqual(len(self.api.sent), sent_before)
        edited = [e for e in self.api.edits if "Đã nhận 2 file" in (e["text"] or "")]
        self.assertTrue(edited)
        # Tapping the pinned reply-keyboard Done button advances too.
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="✅ Xong, sang bước gợi ý"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_hint")

    def test_wizard_typing_xong_advances_for_writer(self):
        writer = 999
        self.store.register_user(writer, "writer999", "Writer999", writer)
        self.store.set_role(writer, roles.ROLE_WRITER)
        self.app.handle_update(message_update(writer, text="/giaoviec"))
        self.app.handle_update(message_update(writer, text="xong"))
        state = self.store.get_user_state(writer)
        self.assertEqual(state["state"], "wizard_hint")

    def test_wizard_create_restores_main_keyboard(self):
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="📝 Giao việc mới"))
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Gợi ý cho việc mới"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        labels = self.keyboard_labels_of(CONTRIBUTOR)
        self.assertIn("📋 Việc của tôi", labels)

    def test_select_all_destinations_wizard_and_card(self):
        # Wizard: turn one channel off, then "Chọn tất cả" restores it.
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="📝 Giao việc mới"))
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Gợi ý chọn kênh"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, "wiz_dest:instagram"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(
            state["payload"]["destinations"], ["website", "facebook"])
        # The "Chọn tất cả" button appears on the in-place edited
        # channel-picker keyboard once a channel is off.
        edited = self.api.edits[-1]["reply_markup"] or {}
        edited_data = [
            b["callback_data"]
            for row in edited.get("inline_keyboard", []) for b in row
        ]
        self.assertIn("wiz_dest_all", edited_data)
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_dest_all"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(
            state["payload"]["destinations"],
            ["website", "facebook", "instagram"])
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        task = self.store.list_tasks()[0]
        # Task card: remove a channel via toggle, then select all again.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{task['code']}:facebook"))
        task = self.store.get_task(task["code"])
        self.assertEqual(
            json.loads(task["destinations"]), ["website", "instagram"])
        self.assertIn(
            f"task_dest_all:{task['code']}", self.api.all_callback_data())
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_all:{task['code']}"))
        task = self.store.get_task(task["code"])
        self.assertEqual(
            json.loads(task["destinations"]),
            ["website", "facebook", "instagram"])
        channels = [o["channel"] for o in self.store.list_outputs(task["code"])]
        self.assertEqual(sorted(channels), ["facebook", "instagram", "website"])

    def test_choduyet_and_thongke_for_reviewers_only(self):
        task = self.create_contributor_task()
        self.attach_draft_and_submit(task["code"])
        self.api.sent.clear()
        self.app.handle_update(message_update(EDITOR, text="/choduyet"))
        self.assertTrue(any("CV-0001" in t for t in self.api.texts_to(EDITOR)))
        self.app.handle_update(message_update(CONTRIBUTOR, text="/choduyet"))
        self.assertTrue(any(
            "không có quyền" in t for t in self.api.texts_to(CONTRIBUTOR)))
        self.app.handle_update(message_update(ADMIN, text="/thongke"))
        stats = [t for t in self.api.texts_to(ADMIN) if "Thống kê" in t]
        self.assertTrue(stats)
        self.assertIn("Chờ duyệt (PENDING): 1", stats[-1])


if __name__ == "__main__":
    unittest.main()
