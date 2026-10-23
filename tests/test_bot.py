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
import handlers
from handlers import BotApp
from storage import Store

ADMIN = 111
EDITOR = 222
CONTRIBUTOR = 333
NEWBIE = 444
FIRST = 555


class FakeTelegraph:
    """Records publish calls instead of hitting telegra.ph."""

    instances = []

    def __init__(self, state_file):
        self.state_file = state_file
        self.calls = []
        FakeTelegraph.instances.append(self)

    def publish(self, key, title, nodes):
        self.calls.append({"key": key, "title": title, "nodes": nodes})
        return "https://telegra.ph/trang-nhap-mau-10-23"


class FakeImageHost:
    """Returns example URLs instead of uploading anywhere."""

    def __init__(self, cache_file):
        self.cache_file = cache_file

    def upload(self, file_path, dest_path):
        return "https://img.example/" + Path(file_path).name


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

    def send_photo(self, chat_id, file_path, caption=None):
        from pathlib import Path as _P
        self.documents.append(
            {"chat_id": chat_id, "path": _P(file_path), "caption": caption,
             "kind": "photo"})
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
        # Admin presses Đăng: that is the final go-ahead, not the
        # publish itself — the output stays APPROVED_WAITING, one
        # publish_requested audit is written, and Muse completes
        # the publish (botctl set-output-published) with the link.
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{output['id']}"))
        self.assertEqual(
            self.store.get_output(output["id"])["status"],
            "APPROVED_WAITING")
        self.assertEqual(
            len(self.store.audit_entries("publish_requested")), 1)
        self.assertEqual(len(self.store.audit_entries("publish_output")), 0)
        self.assertTrue(any(
            "Đã nhận lệnh đăng" in t for t in self.api.texts_to(ADMIN)))
        self.store.set_output_public_url(
            output["id"], "https://example.com/bai-viet")
        self.assertEqual(
            self.store.get_output(output["id"])["status"], "PUBLISHED")

    def create_admin_task(self):
        """Admin sends a photo, then a text hint to finalise."""
        self.app.handle_update(message_update(ADMIN, photo=PHOTO))
        self.app.handle_update(message_update(
            ADMIN, text="Việc của admin tự làm"))
        tasks = [t for t in self.store.list_tasks()
                 if t["creator_id"] == ADMIN]
        self.assertEqual(len(tasks), 1)
        return tasks[0]

    def test_admin_own_card_skips_review_straight_to_publish(self):
        # Owner's rule: an Admin on their OWN task needs no
        # Gửi duyệt/Duyệt — the draft-ready card shows Đăng, and
        # the Facebook target is picked in Telegram.
        task = self.create_admin_task()
        fb = self.store.get_output_by_channel(task["code"], "facebook")
        self.store.set_output_draft(fb["id"], "https://example.com/d/fb")
        admin = self.store.get_user(ADMIN)
        markup = self.app.output_keyboard(
            self.store.get_output(fb["id"]), admin)
        data = [b["callback_data"] for row in markup["inline_keyboard"]
                for b in row]
        self.assertIn(f"out_publish:{fb['id']}", data)
        self.assertNotIn(f"out_submit:{fb['id']}", data)
        # Click Đăng: auto-advances past review, then asks the target.
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{fb['id']}"))
        self.assertEqual(
            self.store.get_output(fb["id"])["status"], "APPROVED_WAITING")
        auto = self.store.audit_entries("approve_output")
        self.assertTrue(any("auto=admin_own" in (a["detail"] or "")
                            for a in auto))
        choice = [m for m in self.api.sent
                  if "lên đâu?" in (m.get("text") or "")]
        self.assertEqual(len(choice), 1)
        # Pick the Fanpage: the request is recorded WITH its target
        # and the choice message becomes the single acknowledgement.
        self.app.handle_update(
            callback_update(ADMIN, f"out_pubto:{fb['id']}:page"))
        reqs = self.store.audit_entries("publish_requested")
        self.assertEqual(len(reqs), 1)
        self.assertIn("target=page", reqs[0]["detail"])
        self.assertTrue(any(
            "Fanpage Minhy" in (e.get("text") or "")
            for e in self.api.edits))

    def test_admin_own_card_has_no_review_buttons(self):
        # Owner's rule, buttons split by role: on the Admin's OWN
        # card there is no Gửi duyệt, no Duyệt, no Trả lại — only
        # Đăng and Hủy bài, in DRAFT and in a legacy PENDING alike.
        task = self.create_admin_task()
        fb = self.store.get_output_by_channel(task["code"], "facebook")
        self.store.set_output_draft(fb["id"], "https://example.com/d/fb")
        admin = self.store.get_user(ADMIN)
        for status in ("DRAFT", "PENDING"):
            self.store.set_output_status(fb["id"], status)
            markup = self.app.output_keyboard(
                self.store.get_output(fb["id"]), admin)
            data = [b["callback_data"]
                    for row in markup["inline_keyboard"] for b in row]
            self.assertIn(f"out_publish:{fb['id']}", data)
            self.assertIn(f"out_cancel:{fb['id']}", data)
            self.assertNotIn(f"out_submit:{fb['id']}", data)
            self.assertNotIn(f"out_approve:{fb['id']}", data)
            self.assertNotIn(f"out_reject:{fb['id']}", data)
        # Đăng from the legacy PENDING state also fast-tracks.
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{fb['id']}"))
        self.assertEqual(
            self.store.get_output(fb["id"])["status"], "APPROVED_WAITING")
        # A reviewer looking at SOMEONE ELSE's card still gets the
        # full review row — roles stay clearly separated.
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        self.app.handle_update(message_update(
            CONTRIBUTOR, text="Việc của cộng tác viên"))
        task2 = [t for t in self.store.list_tasks()
                 if t["creator_id"] == CONTRIBUTOR][0]
        web = self.attach_draft_and_submit(task2["code"])
        editor = self.store.get_user(EDITOR)
        markup = self.app.output_keyboard(
            self.store.get_output(web["id"]), editor)
        data = [b["callback_data"] for row in markup["inline_keyboard"]
                for b in row]
        self.assertIn(f"out_approve:{web['id']}", data)
        self.assertIn(f"out_reject:{web['id']}", data)

    def test_admin_own_website_publish_records_request_directly(self):
        task = self.create_admin_task()
        web = self.store.get_output_by_channel(task["code"], "website")
        self.store.set_output_draft(web["id"], "https://example.com/d/web")
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(ADMIN, f"out_publish:{web['id']}"))
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "APPROVED_WAITING")
        self.assertEqual(
            len(self.store.audit_entries("publish_requested")), 1)
        self.assertFalse(any(
            "lên đâu?" in t for t in self.api.texts_to(ADMIN)))

    def test_self_approve_sends_no_second_card(self):
        # Owner's report: approving your own output sent a second
        # card, so two Đăng buttons showed. The tapped card already
        # refreshes in place — no new card may be sent. Approving
        # SOMEONE ELSE's output still notifies the creator.
        task = self.create_admin_task()
        fb = self.store.get_output_by_channel(task["code"], "facebook")
        self.store.set_output_draft(fb["id"], "https://example.com/d/fb")
        self.store.set_output_status(fb["id"], "PENDING")
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(ADMIN, f"out_approve:{fb['id']}"))
        self.assertEqual(
            self.store.get_output(fb["id"])["status"], "APPROVED_WAITING")
        self.assertFalse(any(
            "đã được duyệt" in t for t in self.api.texts_to(ADMIN)))
        # Someone else's output: the creator is notified as before.
        self.app.handle_update(message_update(CONTRIBUTOR, photo=PHOTO))
        self.app.handle_update(message_update(
            CONTRIBUTOR, text="Việc của cộng tác viên"))
        task2 = [t for t in self.store.list_tasks()
                 if t["creator_id"] == CONTRIBUTOR][0]
        web = self.attach_draft_and_submit(task2["code"])
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(EDITOR, f"out_approve:{web['id']}"))
        self.assertTrue(any(
            "đã được duyệt" in t for t in self.api.texts_to(CONTRIBUTOR)))

    def test_new_output_card_freezes_the_previous_one(self):
        # Owner's report: after a publish, the OLD card in the chat
        # still showed its Đăng button. Every fresh card must freeze
        # the previous cards of the same output (state updated,
        # buttons stripped); only the newest card stays live.
        task = self.create_contributor_task()
        web = self.store.get_output_by_channel(task["code"], "website")
        self.store.set_output_draft(web["id"], "https://example.com/d/web")
        creator = self.store.get_user(CONTRIBUTOR)
        first = self.app.send_output_card(
            CONTRIBUTOR, self.store.get_output(web["id"]), task, creator)
        first_id = first["message_id"]
        self.store.set_output_public_url(
            web["id"], "https://example.com/bai-viet")
        self.api.edits.clear()
        second = self.app.send_output_card(
            CONTRIBUTOR, self.store.get_output(web["id"]),
            self.store.get_task(task["code"]), creator,
            prefix="✅ Đã đăng thành công:")
        frozen = [e for e in self.api.edits
                  if e["message_id"] == first_id]
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0]["reply_markup"],
                         {"inline_keyboard": []})
        self.assertIn("Đã đăng", frozen[0]["text"])
        tracked = self.store.list_output_cards(web["id"])
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["message_id"], second["message_id"])

    def test_creator_can_request_draft_revision(self):
        # Owner's rule: every draft card needs a Sửa lại button so
        # the creator can tell Muse what to change.
        task = self.create_admin_task()
        fb = self.store.get_output_by_channel(task["code"], "facebook")
        self.store.set_output_draft(fb["id"], "https://example.com/d/fb")
        admin = self.store.get_user(ADMIN)
        markup = self.app.output_keyboard(
            self.store.get_output(fb["id"]), admin)
        data = [b["callback_data"] for row in markup["inline_keyboard"]
                for b in row]
        self.assertIn(f"out_revise:{fb['id']}", data)
        # Someone else's Sửa lại tap is denied.
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(EDITOR, f"out_revise:{fb['id']}"))
        self.assertTrue(self.api.answered[-1]["show_alert"])
        # The creator taps it and types the change: the output goes
        # to REJECTED carrying the note verbatim, audited, and the
        # creator gets exactly one confirmation message.
        self.app.handle_update(
            callback_update(ADMIN, f"out_revise:{fb['id']}"))
        self.api.sent.clear()
        self.app.handle_update(
            message_update(ADMIN, text="Viết ngắn gọn hơn, bỏ hashtag"))
        updated = self.store.get_output(fb["id"])
        self.assertEqual(updated["status"], "REJECTED")
        self.assertEqual(updated["reject_reason"],
                         "Viết ngắn gọn hơn, bỏ hashtag")
        self.assertEqual(len(self.store.audit_entries("revise_output")), 1)
        notes = [t for t in self.api.texts_to(ADMIN)
                 if "Đã ghi nhận yêu cầu sửa" in t]
        self.assertEqual(len(notes), 1)
        self.assertIn("Viết ngắn gọn hơn, bỏ hashtag", notes[0])

    def test_contributor_card_also_has_revise_button(self):
        task = self.create_contributor_task()
        web = self.store.get_output_by_channel(task["code"], "website")
        self.store.set_output_draft(web["id"], "https://example.com/d/web")
        creator = self.store.get_user(CONTRIBUTOR)
        markup = self.app.output_keyboard(
            self.store.get_output(web["id"]), creator)
        data = [b["callback_data"] for row in markup["inline_keyboard"]
                for b in row]
        self.assertIn(f"out_submit:{web['id']}", data)
        self.assertIn(f"out_revise:{web['id']}", data)

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
        self.assertIn(f"👤 User{CONTRIBUTOR}", card)
        self.assertRegex(card, r"\d{2}:\d{2} \d{2}/\d{2}/\d{4}")
        self.assertIn("🎯 🌐 Website • 📘 Facebook • 📸 Instagram", card)
        # Default destinations stored on the task itself.
        self.assertEqual(
            json.loads(task["destinations"]),
            ["website", "facebook", "instagram"],
        )

    def test_destination_toggle_stages_then_ok_commits(self):
        # Owner's model: taps only STAGE the selection on the card
        # (✅/❌ marks); OK commits it and sends the work order to
        # Muse; the tapped card then freezes (buttons stripped).
        task = self.create_contributor_task()
        code = task["code"]
        # Stage Facebook off: stored destinations unchanged yet.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "facebook", "instagram"])
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "task_dest_pending")
        self.assertEqual(state["payload"]["chosen"],
                         ["website", "instagram"])
        # The tapped card was edited in place: ❌ Facebook, ✅ Website;
        # the bottom row is [Chốt! Gửi Muse, Thu hồi việc].
        edit = self.api.edits[-1]
        rows = edit["reply_markup"]["inline_keyboard"]
        labels = [b["text"] for row in rows for b in row]
        self.assertIn("❌ Facebook", labels)
        self.assertIn("🌐 Website", labels)
        self.assertEqual(
            [b["text"] for b in rows[-1]],
            ["🚀 Chốt! Gửi Muse", "🗑 Thu hồi việc"])
        # OK commits and notifies that the order went to Muse.
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{code}"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "instagram"])
        confirmations = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any(
            "Đã chốt kênh đăng bài" in t and "Đã gửi đến Muse" in t
            and "gửi kết quả công việc" in t for t in confirmations))
        # Exactly ONE notification: no channel cards are sent along.
        self.assertFalse(any("🧾 Thẻ kênh" in t for t in confirmations))
        # The OK card froze: an edit stripped its buttons + lock line.
        frozen = [e for e in self.api.edits
                  if e["reply_markup"] == {"inline_keyboard": []}
                  and "đóng băng" in (e["text"] or "")]
        self.assertTrue(frozen)
        # Stage Facebook back on, OK; then stage two off, OK.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{code}"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "facebook", "instagram"])
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:website"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:instagram"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{code}"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["facebook"])
        # Staging off the last remaining channel is refused.
        self.api.answered.clear()
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertIn("ít nhất một nơi đăng", self.api.answered[-1]["text"])
        # set_destinations audited once per successful OK (3 times).
        self.assertEqual(
            len(self.store.audit_entries("set_destinations")), 3)
        self.assertEqual(
            len(self.store.audit_entries("destinations_confirmed")), 3)

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
        # Editor stages the change (nothing stored yet), then OKs it.
        self.app.handle_update(
            callback_update(EDITOR, f"task_dest:{code}:facebook"))
        self.assertEqual(
            json.loads(self.store.get_task(code)["destinations"]),
            ["website", "facebook", "instagram"],
        )
        self.app.handle_update(
            callback_update(EDITOR, f"task_dest_ok:{code}"))
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
        self.store.set_output_public_url(
            web["id"], "https://example.com/bai-viet")
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
        # Toggle Facebook off + OK: its DRAFT output is cancelled.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{code}"))
        outputs = self.outputs_by_channel(code)
        self.assertEqual(outputs["facebook"]["status"], "CANCELLED")
        self.assertEqual(outputs["website"]["status"], "DRAFT")
        self.assertEqual(outputs["instagram"]["status"], "DRAFT")
        # Toggle Facebook back on + OK: the output is recreated as
        # DRAFT (same row revived).
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{code}:facebook"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{code}"))
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
            self.store.get_output(web["id"])["status"], "APPROVED_WAITING")
        self.assertEqual(
            len(self.store.audit_entries("publish_requested")), 1)
        self.store.set_output_public_url(
            web["id"], "https://example.com/bai-viet")
        self.store.sync_task_status_from_outputs(code)
        self.assertEqual(
            self.store.get_output(web["id"])["status"], "PUBLISHED")
        self.assertEqual(len(self.store.audit_entries("approve_output")), 1)
        self.assertEqual(len(self.store.audit_entries("publish_output")), 0)
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
        # Owner's rule (2026-10-23): on reject, NO card and NO notice
        # go back to the creator — only the reviewer's single
        # "Đã trả lại" notice exists. The creator sees the card again
        # when the revised draft arrives.
        creator_texts = self.api.texts_to(CONTRIBUTOR)
        self.assertFalse(any("Caption chưa hay" in t
                             for t in creator_texts))
        editor_texts = self.api.texts_to(EDITOR)
        self.assertTrue(any(
            "Đã trả lại thẻ kênh Facebook" in t
            and "Caption chưa hay, viết lại" in t
            for t in editor_texts))

    def test_reviewer_can_return_draft_channel_with_revision_note(self):
        # The owner asked for a "return with revision request" button
        # that also works on a DRAFT channel card (before formal
        # submit): the reviewer types what must be fixed, and the
        # creator sees that exact note on the returned card.
        task = self.create_contributor_task()
        code = task["code"]
        fb = self.outputs_by_channel(code)["facebook"]
        self.store.set_output_draft(
            fb["id"], "https://telegra.ph/ban-nhap-fb")
        editor = self.store.get_user(EDITOR)
        keyboard = self.app.output_keyboard(
            self.store.get_output(fb["id"]), editor)
        callbacks = [
            btn["callback_data"]
            for row in keyboard["inline_keyboard"] for btn in row]
        self.assertIn(f"out_reject:{fb['id']}", callbacks)
        self.app.handle_update(
            callback_update(EDITOR, f"out_reject:{fb['id']}"))
        prompts = self.api.texts_to(EDITOR)
        self.assertTrue(any("NỘI DUNG CẦN SỬA" in t for t in prompts))
        self.app.handle_update(message_update(
            EDITOR, text="Đổi ảnh bìa, rút ngắn caption"))
        output = self.store.get_output(fb["id"])
        self.assertEqual(output["status"], "REJECTED")
        self.assertEqual(
            output["reject_reason"], "Đổi ảnh bìa, rút ngắn caption")
        # Owner's rule: exactly ONE notice, to the reviewer — the
        # creator gets nothing until the revised draft arrives.
        editor_texts = self.api.texts_to(EDITOR)
        self.assertTrue(any(
            "Đã trả lại thẻ kênh" in t
            and "Yêu cầu chỉnh sửa: Đổi ảnh bìa, rút ngắn caption" in t
            for t in editor_texts))
        creator_texts = self.api.texts_to(CONTRIBUTOR)
        self.assertFalse(any("bị trả lại" in t for t in creator_texts))

    def test_output_cancel_button_row_and_flow(self):
        """The review row is [✅ Duyệt | ↩️ Trả lại | 🗑 Hủy bài] on
        one row; cancelling is final, reviewers only, one notice."""
        task = self.create_contributor_task()
        code = task["code"]
        fb = self.outputs_by_channel(code)["facebook"]
        self.store.set_output_draft(fb["id"], "https://telegra.ph/x")
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_submit:{fb['id']}"))
        editor = self.store.get_user(EDITOR)
        keyboard = self.app.output_keyboard(
            self.store.get_output(fb["id"]), editor)
        row_labels = [b["text"] for row in keyboard["inline_keyboard"]
                      for b in row]
        self.assertEqual(
            row_labels[-3:], ["✅ Duyệt", "↩️ Trả lại", "🗑 Hủy bài"])
        # A contributor cannot cancel.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"out_cancel:{fb['id']}"))
        self.assertTrue(self.api.answered[-1]["show_alert"])
        self.assertEqual(
            self.store.get_output(fb["id"])["status"], "PENDING")
        # The reviewer cancels: status flips, one notice, audit logged.
        self.api.sent.clear()
        self.app.handle_update(
            callback_update(EDITOR, f"out_cancel:{fb['id']}"))
        self.assertEqual(
            self.store.get_output(fb["id"])["status"], "CANCELLED")
        self.assertEqual(len(self.store.audit_entries("cancel_output")), 1)
        self.assertTrue(any(
            "Đã hủy bài" in t for t in self.api.texts_to(EDITOR)))
        self.assertFalse(any(
            "Đã hủy bài" in t for t in self.api.texts_to(CONTRIBUTOR)))
        # A cancelled card has no buttons left.
        self.assertIsNone(self.app.output_keyboard(
            self.store.get_output(fb["id"]), editor))

    def test_hide_and_show_main_keyboard(self):
        self.api.sent.clear()
        self.app.handle_update(
            message_update(EDITOR, text=handlers.BTN_HIDE_OPS))
        markups = [m.get("reply_markup") for m in self.api.sent
                   if m["chat_id"] == EDITOR]
        self.assertIn({"remove_keyboard": True}, markups)
        self.api.sent.clear()
        self.app.handle_update(message_update(EDITOR, text="/menu"))
        markups = [m.get("reply_markup") for m in self.api.sent
                   if m["chat_id"] == EDITOR and m.get("reply_markup")]
        keyboards = [m for m in markups if "keyboard" in m]
        self.assertTrue(keyboards)
        labels = [btn["text"] for row in keyboards[0]["keyboard"]
                  for btn in row]
        self.assertIn(handlers.BTN_NEW_TASK, labels)
        self.assertIn(handlers.BTN_HIDE_OPS, labels)
        commands = [c["command"]
                    for c in handlers.commands_for_role(roles.ROLE_EDITOR)]
        self.assertIn("menu", commands)

    def test_instagram_page_title_is_cover_headline(self):
        """The telegra.ph page title is slide 1's headline, never its
        subtitle line."""
        md = ("# Bản nháp Instagram — CV-0007\nKênh: Instagram\n\n---\n\n"
              "## Cấu trúc carousel\n\n"
              "**Ảnh 1 (toàn cảnh — file_1.jpg):**\n"
              'Chữ trên ảnh: "Mầm non lúc ngủ 😴"\n'
              'Phụ đề nhỏ: "ôm gối ôm chặt như ôm cả thế giới"\n\n'
              "## Caption đăng kèm\nNgủ ngon nhé con.\n")
        title = botctl._draft_page_title(
            {"code": "CV-0007", "hint": "gợi ý"}, {"channel": "instagram"},
            md)
        self.assertIn("Mầm non lúc ngủ", title)
        self.assertNotIn("Phụ đề nhỏ", title)

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

    def _attach_with_fake_telegraph(self, code, channel, md_path):
        original_client = botctl.build_client_or_warn
        original_tg = botctl.Telegraph
        original_host = botctl.ImageHost
        botctl.build_client_or_warn = lambda no_notify: self.api
        botctl.Telegraph = FakeTelegraph
        botctl.ImageHost = FakeImageHost
        FakeTelegraph.instances.clear()
        self.api.sent.clear()
        try:
            args = argparse.Namespace(
                task_code=code, channel=channel, ref=md_path.name,
                no_notify=False, file=str(md_path), no_images=False)
            return botctl.cmd_attach_output_draft(self.store, args)
        finally:
            botctl.build_client_or_warn = original_client
            botctl.Telegraph = original_tg
            botctl.ImageHost = original_host

    def test_attach_output_draft_with_file_publishes_link(self):
        # The owner wants ONE tidy link per draft on the channel card:
        # with --file the draft is published to telegra.ph and the page
        # URL is the stored reference — no content dumped into the chat.
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
            rc = self._attach_with_fake_telegraph(code, "website", md_path)
            self.assertEqual(rc, 0)
            outputs = {o["channel"]: o
                       for o in self.store.list_outputs(task["code"])}
            self.assertEqual(
                outputs["website"]["draft_ref"],
                "https://telegra.ph/trang-nhap-mau-10-23")
            calls = FakeTelegraph.instances[0].calls
            self.assertEqual(len(calls), 1)
            body = json.dumps(calls[0]["nodes"], ensure_ascii=False)
            self.assertIn("Bài viết hay về hội thảo", body)
            self.assertIn("Nội dung chính của bài viết", body)
            # The task's material photos are embedded in the page.
            self.assertIn('"figure"', body)
            self.assertIn("https://img.example/", body)
            texts = self.api.texts_to(CONTRIBUTOR)
            self.assertEqual(len(texts), 1)
            self.assertIn("https://telegra.ph/trang-nhap-mau-10-23",
                          texts[0])

    def test_attach_facebook_draft_publishes_link(self):
        task = self.create_contributor_task()
        code = task["code"]
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / f"{code}-facebook.md"
            md_path.write_text(
                "Bài đăng Facebook vui nhộn về hội thảo.\n\n"
                "Mọi người cùng xem nhé!",
                encoding="utf-8")
            rc = self._attach_with_fake_telegraph(code, "facebook", md_path)
            self.assertEqual(rc, 0)
            outputs = {o["channel"]: o
                       for o in self.store.list_outputs(task["code"])}
            self.assertEqual(
                outputs["facebook"]["draft_ref"],
                "https://telegra.ph/trang-nhap-mau-10-23")
            body = json.dumps(
                FakeTelegraph.instances[0].calls[0]["nodes"],
                ensure_ascii=False)
            self.assertIn("Bài đăng Facebook vui nhộn", body)

    def test_attach_instagram_draft_publishes_slides_and_caption(self):
        task = self.create_contributor_task()
        code = task["code"]
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / f"{code}-instagram.md"
            md_path.write_text(
                "# Kịch bản carousel\n\n"
                "## Slide 1 (ảnh bìa)\n"
                "- Dòng 1: Xin chào slide một\n"
                "- Dòng 2: Dòng phụ của slide một\n\n"
                "## Slide 2\n"
                "- Dòng 1: Nội dung slide hai\n\n"
                "## Caption đăng kèm\n"
                "Caption mẫu cho bài đăng.\n"
                "#hashtagmau #vidu\n",
                encoding="utf-8")
            rc = self._attach_with_fake_telegraph(code, "instagram", md_path)
            self.assertEqual(rc, 0)
            body = json.dumps(
                FakeTelegraph.instances[0].calls[0]["nodes"],
                ensure_ascii=False)
            self.assertIn("Slide 1", body)
            self.assertIn("Xin chào slide một", body)
            self.assertIn("Slide 2", body)
            self.assertIn("Nội dung slide hai", body)
            self.assertIn("Caption đăng kèm", body)
            self.assertIn("Caption mẫu cho bài đăng.", body)
            self.assertIn("#hashtagmau", body)

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
        self.assertNotIn("📊 Thống kê", labels)
        self.assertNotIn("📋 Tất cả việc", labels)
        # Owner's layout: no Users button; the last row pairs Muse's
        # status (left) with the hide-controls button (right).
        self.assertNotIn("👥 Người dùng", labels)
        self.assertEqual(labels[-2:],
                         [handlers.BTN_CLAUDE, handlers.BTN_HIDE_OPS])

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
        # The wizard finish IS the confirm: the card lands frozen and
        # exactly one "sent to Muse" notification goes out.
        texts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any("🔒 Đã chốt kênh đăng" in t for t in texts))
        self.assertTrue(any(
            "Đã tạo việc" in t and "Đã gửi đến Muse" in t
            for t in texts))

    def test_wizard_step3_edit_hint(self):
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="📝 Giao việc mới"))
        self.app.handle_update(message_update(
            CONTRIBUTOR, photo=PHOTO, caption="Ảnh cổng trường"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Gợi ý cũ"))
        step3 = self.api.texts_to(CONTRIBUTOR)[-1]
        self.assertIn("Bước 3/3", step3)
        self.assertIn("Gợi ý cũ", step3)
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_hint"))
        self.assertTrue(any(
            "Gõ lại toàn bộ nội dung gợi ý mới"
            in t for t in self.api.texts_to(CONTRIBUTOR)))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="Gợi ý mới tinh"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["payload"]["hint"], "Gợi ý mới tinh")
        self.assertIn("Gợi ý mới tinh",
                      self.api.texts_to(CONTRIBUTOR)[-1])
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        task = self.store.list_tasks()[0]
        self.assertEqual(task["hint"], "Gợi ý mới tinh")

    def test_wizard_contributor_needs_a_file(self):
        self.app.handle_update(message_update(CONTRIBUTOR, text="/giaoviec"))
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_done_files"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(state["state"], "wizard_files")
        self.assertTrue(self.api.answered[-1]["show_alert"])

    def test_wizard_legacy_cancel_label_still_cancels(self):
        # Keyboards sent before the cancel button was renamed to the
        # trash icon still emit the old "❌ Huỷ giao việc" label —
        # tapping it must cancel (and restore the main keyboard), not
        # linger unresponsive on screen.
        self.app.handle_update(message_update(CONTRIBUTOR, text="/giaoviec"))
        self.assertIsNotNone(self.store.get_user_state(CONTRIBUTOR))
        self.app.handle_update(
            message_update(CONTRIBUTOR, text="❌ Huỷ giao việc"))
        self.assertIsNone(self.store.get_user_state(CONTRIBUTOR))
        self.assertTrue(any(
            "Đã huỷ giao việc" in t
            for t in self.api.texts_to(CONTRIBUTOR)))

    def test_stale_wizard_button_gets_toast_not_alert(self):
        # Tapping a button of an already-finished wizard shows a
        # small toast (no modal alert) with a friendly pointer.
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        answer = self.api.answered[-1]
        self.assertFalse(answer.get("show_alert"))
        self.assertIn("đã kết thúc", answer["text"])

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

    def test_claude_status_button_reports_queue(self):
        task = self.create_contributor_task()
        self.api.sent.clear()
        self.app.handle_update(
            message_update(CONTRIBUTOR, text=handlers.BTN_CLAUDE))
        texts = self.api.texts_to(CONTRIBUTOR)
        self.assertTrue(any(
            "Muse đang làm" in t and task["code"] in t
            and "viết nháp" in t for t in texts))
        # The bottom menu now carries Muse's status instead of Help.
        markup = handlers.main_keyboard(
            self.store.get_user(CONTRIBUTOR))
        labels = [b["text"] for row in markup["keyboard"] for b in row]
        self.assertIn(handlers.BTN_CLAUDE, labels)
        self.assertNotIn(handlers.BTN_HELP, labels)

    def test_no_select_all_button_toggles_flip_icon(self):
        # Owner's model: no "Chọn tất cả" button (only 3 channels);
        # a chosen channel shows its icon, tapping swaps it for ❌,
        # tapping again restores it.
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
        edited = self.api.edits[-1]["reply_markup"] or {}
        edited_rows = edited.get("inline_keyboard", [])
        edited_data = [b["callback_data"] for row in edited_rows
                       for b in row]
        self.assertNotIn("wiz_dest_all", edited_data)
        edited_labels = [b["text"] for row in edited_rows for b in row]
        self.assertIn("❌ Instagram", edited_labels)
        self.assertIn("🌐 Website", edited_labels)
        # Tapping the ❌ channel again restores its icon.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, "wiz_dest:instagram"))
        state = self.store.get_user_state(CONTRIBUTOR)
        self.assertEqual(
            state["payload"]["destinations"],
            ["website", "facebook", "instagram"])
        self.app.handle_update(callback_update(CONTRIBUTOR, "wiz_create"))
        task = self.store.list_tasks()[0]
        # Task card: same behaviour, no select-all row either.
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{task['code']}:facebook"))
        edited = self.api.edits[-1]["reply_markup"] or {}
        edited_data = [
            b["callback_data"]
            for row in edited.get("inline_keyboard", []) for b in row
        ]
        self.assertNotIn(f"task_dest_all:{task['code']}", edited_data)
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest:{task['code']}:facebook"))
        self.app.handle_update(
            callback_update(CONTRIBUTOR, f"task_dest_ok:{task['code']}"))
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
