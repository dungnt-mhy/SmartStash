"""Publish draft pages to telegra.ph (Telegram's own article host).

Why: the project owner reviews drafts inside Telegram and wants ONE
tidy link per draft on the channel card — no full-text dumps in the
chat. A telegra.ph link opens with Instant View right inside
Telegram: light, formatted, readable on any phone.

The API lives at https://api.telegra.ph (JSON POST). One anonymous
account is created for the bot and its access token + the map of
published pages are persisted in a small JSON file in the bot's data
directory (never committed — see .gitignore), so re-publishing a
revised draft EDITs the same page and the link already shared in
Telegram keeps working.

Note: telegra.ph image upload is unreliable from this network, so
pages are text-only; the task's photos stay in the Telegram
conversation where they were submitted.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

API_BASE = "https://api.telegra.ph"
AUTHOR_NAME = "IAICT Media Bot"


class TelegraphError(RuntimeError):
    pass


class Telegraph:
    def __init__(self, state_file):
        self._state_file = Path(state_file)
        self._state = {"access_token": None, "pages": {}}
        if self._state_file.is_file():
            try:
                loaded = json.loads(
                    self._state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._state.update(loaded)
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------- api
    def _post(self, method: str, payload: dict) -> dict:
        import http.client
        import time
        import urllib.error

        request = urllib.request.Request(
            f"{API_BASE}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except (http.client.RemoteDisconnected, ConnectionError,
                    urllib.error.URLError) as exc:
                # The egress route occasionally drops the connection;
                # a short backoff and retry is safe (createPage for an
                # already-known key is never repeated — publish() edits
                # instead once the first success is recorded).
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        else:
            raise TelegraphError(
                f"telegra.ph {method} failed: {last_error}")
        if not isinstance(result, dict) or not result.get("ok"):
            description = ""
            if isinstance(result, dict):
                description = str(result.get("error", ""))
            raise TelegraphError(f"telegra.ph {method} failed: {description}")
        return result["result"]

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _token(self) -> str:
        if not self._state.get("access_token"):
            account = self._post("createAccount", {
                "short_name": "IAICTMediaBot",
                "author_name": AUTHOR_NAME,
            })
            self._state["access_token"] = account["access_token"]
            self._save()
        return self._state["access_token"]

    # --------------------------------------------------------- publish
    def publish(self, key: str, title: str, nodes: list) -> str:
        """Create the page for ``key`` or edit it in place; return URL."""
        token = self._token()
        pages = self._state.setdefault("pages", {})
        existing = pages.get(key)
        if existing:
            page = self._post("editPage", {
                "access_token": token,
                "path": existing["path"],
                "title": title,
                "author_name": AUTHOR_NAME,
                "content": nodes,
            })
        else:
            page = self._post("createPage", {
                "access_token": token,
                "title": title,
                "author_name": AUTHOR_NAME,
                "content": nodes,
            })
            pages[key] = {"path": page["path"], "url": page["url"]}
            self._save()
        return page["url"]
