"""Telegram Bot API client.

Token resolution (never logged, never persisted):

1. Dynamic credential surrogate -- the Muse secure connection named
   ``custom.telegram``, fetched with ``dynamic_credential_entry`` from
   ``/opt/muse/skills/custom-connector-setup/bin/dynamic_credentials.py``
   and placed raw into ``https://api.telegram.org/bot{}/<method>``.
2. Environment variable ``TELEGRAM_BOT_TOKEN`` -- local simulation only.

The HTTP layer is a ``transport`` callable ``(url, payload_dict) -> dict``
so tests can inject a fake and no real API call is ever made in tests.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

API_BASE_TEMPLATE = "https://api.telegram.org/bot{}/{}"
FILE_BASE_TEMPLATE = "https://api.telegram.org/file/bot{}/{}"
ALLOWED_HOSTS = ["api.telegram.org"]
CREDENTIAL_NAME = "custom.telegram"
_DYNAMIC_CREDENTIALS_DIR = "/opt/muse/skills/custom-connector-setup/bin"


class TelegramApiError(RuntimeError):
    pass


class NoTokenError(TelegramApiError):
    """No credential surrogate and no TELEGRAM_BOT_TOKEN available."""


def build_surrogate_url(template: str, surrogate: str, method_or_path: str) -> str:
    """Fill a ``bot{}`` URL template with a credential surrogate.

    The surrogate is placed RAW. Percent-encoding it (as
    ``url_with_surrogate_path_segment`` does) breaks the egress swap:
    the proxy cannot match the encoded surrogate and Telegram receives
    a mangled token, answering 404. Verified against the real API on
    2026-10-22: raw surrogate returns 200, encoded returns 404.
    """
    return template.format(surrogate, method_or_path)


def _surrogate_url(template: str, method_or_path: str) -> str | None:
    """Return the URL with a credential surrogate, or None if unavailable."""
    try:
        if _DYNAMIC_CREDENTIALS_DIR not in sys.path:
            sys.path.insert(0, _DYNAMIC_CREDENTIALS_DIR)
        from dynamic_credentials import dynamic_credential_entry

        entry = dynamic_credential_entry(CREDENTIAL_NAME)
        surrogate = str(entry.get("surrogate", "")).strip()
        if not surrogate.startswith("hsurr:"):
            return None
        host = template.split("/")[2] if "://" in template else ""
        if ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
            return None
        return build_surrogate_url(template, surrogate, method_or_path)
    except Exception:
        # No registered secure connection (yet) -- fall back to the env var.
        return None


def _urllib_transport(url: str, payload: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    # Never follow redirects on an authenticated request.
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102
        raise TelegramApiError("redirect refused on Telegram API request")


class TelegramClient:
    def __init__(self, transport=None, token: str | None = None,
                 timeout: float = 60.0):
        """
        transport: callable(url, payload) -> parsed JSON dict.
                   Defaults to a urllib POST transport.
        token:     explicit token (tests / local simulation only).  When
                   given it wins over the surrogate so tests stay offline.
        """
        self._transport = transport or _urllib_transport
        self._explicit_token = token
        self._timeout = timeout

    # ------------------------------------------------------------ url build
    def method_url(self, method: str) -> str:
        if self._explicit_token is not None:
            return API_BASE_TEMPLATE.format(self._explicit_token, method)
        url = _surrogate_url(API_BASE_TEMPLATE, method)
        if url:
            return url
        env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if env_token:
            return API_BASE_TEMPLATE.format(env_token, method)
        raise NoTokenError(
            "Chưa có token Telegram: hãy nhập token qua biểu mẫu bảo mật "
            "của Muse (kết nối custom.telegram), hoặc đặt biến môi trường "
            "TELEGRAM_BOT_TOKEN khi chạy mô phỏng nội bộ."
        )

    def file_url(self, file_path: str) -> str:
        if self._explicit_token is not None:
            return FILE_BASE_TEMPLATE.format(self._explicit_token, file_path)
        url = _surrogate_url(FILE_BASE_TEMPLATE, file_path)
        if url:
            return url
        env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if env_token:
            return FILE_BASE_TEMPLATE.format(env_token, file_path)
        raise NoTokenError("Chưa có token Telegram để tải file.")

    # ----------------------------------------------------------------- call
    def call(self, method: str, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        url = self.method_url(method)
        if self._transport is _urllib_transport:
            result = self._transport(url, payload, timeout=self._timeout)
        else:
            result = self._transport(url, payload)
        if not isinstance(result, dict) or not result.get("ok", False):
            description = ""
            if isinstance(result, dict):
                description = result.get("description", "")
            raise TelegramApiError(f"Telegram API {method} failed: {description}")
        return result.get("result")

    # ------------------------------------------------------- convenience api
    def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        return self.call(
            "sendMessage", chat_id=chat_id, text=text,
            reply_markup=reply_markup, **kwargs,
        )

    def _post_multipart(self, method, field_name, chat_id, file_path,
                        caption=None):
        """Shared multipart upload for sendDocument / sendPhoto.

        The JSON transport cannot carry uploads, so the multipart body
        is built directly and posted to the method URL — the credential
        surrogate rides raw in the URL path, exactly as for every other
        call in this client.
        """
        import mimetypes
        import uuid
        from pathlib import Path

        path = Path(file_path)
        boundary = "----musebot" + uuid.uuid4().hex
        fields = [("chat_id", str(chat_id))]
        if caption:
            fields.append(("caption", caption))
        body = bytearray()
        for name, value in fields:
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body += value.encode("utf-8") + b"\r\n"
        content_type = mimetypes.guess_type(path.name)[0] \
            or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{path.name}"\r\n'
        ).encode()
        body += f"Content-Type: {content_type}\r\n\r\n".encode()
        body += path.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.method_url(method), data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=max(self._timeout, 120.0)) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, dict) or not result.get("ok", False):
            description = result.get("description", "") \
                if isinstance(result, dict) else ""
            raise TelegramApiError(
                f"Telegram API {method} failed: {description}")
        return result.get("result")

    def send_document(self, chat_id, file_path, caption=None):
        return self._post_multipart(
            "sendDocument", "document", chat_id, file_path, caption)

    def send_photo(self, chat_id, file_path, caption=None):
        return self._post_multipart(
            "sendPhoto", "photo", chat_id, file_path, caption)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None,
                          **kwargs):
        return self.call(
            "editMessageText", chat_id=chat_id, message_id=message_id,
            text=text, reply_markup=reply_markup, **kwargs,
        )

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return self.call(
            "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
            reply_markup=reply_markup,
        )

    def set_my_commands(self, commands, scope=None, language_code=None):
        return self.call(
            "setMyCommands", commands=commands, scope=scope,
            language_code=language_code,
        )

    def answer_callback_query(self, callback_query_id, text=None,
                              show_alert=False):
        return self.call(
            "answerCallbackQuery", callback_query_id=callback_query_id,
            text=text, show_alert=show_alert,
        )

    def get_updates(self, offset=None, timeout=30):
        return self.call("getUpdates", offset=offset, timeout=timeout) or []

    def get_file(self, file_id):
        return self.call("getFile", file_id=file_id)

    def download_file(self, file_path: str, destination) -> None:
        """Download a file (from getFile) to ``destination`` via GET."""
        url = self.file_url(file_path)
        request = urllib.request.Request(url, method="GET")
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=self._timeout) as response:
            data = response.read()
        with open(destination, "wb") as fh:
            fh.write(data)
