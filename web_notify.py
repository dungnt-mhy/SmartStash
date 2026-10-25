#!/usr/bin/env python3
"""Web inbox + Web Push mirror for bot publish/retract events.

Moved here from botctl.py in TD-0026 so the in-bot API worker
(handlers.py) can send the same notifications without importing
botctl (botctl imports handlers — the reverse import would cycle).

The inbox row is global (every web user sees it, like the daily
summary). After the row, the same title/body also go out as a Web
Push to every subscriber (deep-link /?task=<code>).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# TD-0017: injectable web-push sender for tests. None = real path
# (WebDB + PushService from the webapp project). Tests set this to a
# recorder; production always uses the real path below.
_WEB_PUSH_SENDER = None


def _web_push_service(store):
    """Build (webdb, PushService) for this store's database, or None.

    Never creates VAPID keys: if vapid.json is absent, push is
    skipped with a warning (generating fresh keys would invalidate
    every existing subscription). Import problems also degrade to a
    warning — the inbox row is already written by then."""
    try:
        webapp_dir = Path(__file__).resolve().parent.parent / "webapp-giao-viec"
        if str(webapp_dir) not in sys.path:
            sys.path.insert(0, str(webapp_dir))
        from webapp.db import WebDB
        from webapp.push import PushService, default_vapid_path
    except Exception as exc:
        print(f"Cảnh báo: không tải được module web push: {exc}",
              file=sys.stderr)
        return None
    try:
        keys_path = default_vapid_path(store.db_path)
        if not keys_path.exists():
            print("Cảnh báo: không có vapid.json nên bỏ qua web push.",
                  file=sys.stderr)
            return None
        webdb = WebDB(store.db_path)
        return webdb, PushService(webdb, keys_path)
    except Exception as exc:
        print(f"Cảnh báo: không khởi tạo được web push: {exc}",
              file=sys.stderr)
        return None


def _send_web_push(store, title, body, url):
    """Best-effort Web Push to every subscriber (TD-0017).

    Recipients mirror the global inbox row: every subscription in
    web_push_subscriptions. Only counts reach stderr — never
    endpoints, keys or payloads. Any failure degrades to a warning;
    the caller's exit code is never affected."""
    if _WEB_PUSH_SENDER is not None:
        try:
            _WEB_PUSH_SENDER(title, body, url)
        except Exception as exc:
            print(f"Cảnh báo: gửi web push thất bại: {exc}",
                  file=sys.stderr)
        return
    built = _web_push_service(store)
    if built is None:
        return
    webdb, service = built
    try:
        payload = json.dumps({"title": title, "body": body, "url": url},
                             ensure_ascii=False).encode("utf-8")
        sent = failed = gone = 0
        for sub in webdb.push_subscriptions_all():
            try:
                result = service.send_to_subscription(sub, payload)
            except Exception as exc:
                print(f"Cảnh báo: gửi web push thất bại: {exc}",
                      file=sys.stderr)
                failed += 1
                continue
            if result["ok"]:
                sent += 1
            elif result["gone"]:
                gone += 1
            else:
                failed += 1
        if sent or failed or gone:
            print(f"Web push: đã gửi {sent}, lỗi {failed}, "
                  f"hết hạn {gone}.", file=sys.stderr)
    except Exception as exc:
        print(f"Cảnh báo: gửi web push thất bại: {exc}",
              file=sys.stderr)
    finally:
        try:
            webdb.close()
        except Exception:
            pass


def _notify_web(store, no_notify, kind, title, body, key, task_code=None):
    """Mirror a bot publish/retract event into the web inbox.

    TD-0015: the web_notification_items table is global (every web
    user sees the row, like the daily summary), so one row reaches
    the task creator, reviewers and Admins at once. --no-notify
    skips the web row exactly like it skips Telegram; a missing
    Telegram token does NOT skip the web row (the web needs no
    Telegram token).
    TD-0017: after the row, the same title/body also go out as a
    Web Push to every subscriber (deep-link /?task=<code>), because
    an inbox row alone never buzzes the phone."""
    if no_notify:
        return
    try:
        store.add_web_notification_item(key, kind, title, body)
    except Exception as exc:
        print(f"Cảnh báo: không ghi được thông báo web: {exc}",
              file=sys.stderr)
    url = f"/?task={task_code}" if task_code else "/"
    _send_web_push(store, title, body, url)
