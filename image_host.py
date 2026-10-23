"""Host draft photos so telegra.ph pages can embed them.

telegra.ph article pages accept <img> nodes pointing at any public
URL, but telegra.ph's own upload endpoint rejects every upload from
this network ("Unknown error") and the usual anonymous image hosts
are unreachable from here. The one public host that works is the
project's own GitHub repository (public): photos are committed under
draft-media/<task-code>/ through the Contents API, using the same
secure credential exchange as the github-cli helper, and the raw URL
becomes the image source.

Uploads are cached by content hash in the bot's data directory so
re-publishing an edited draft never re-uploads unchanged photos.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, \
    read_json_response  # noqa: E402

CREDENTIAL_NAME = "custom.github-cli"
API = "https://api.github.com"
REPO = "dungnt-mhy/SmartStash"


class ImageHostError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


class ImageHost:
    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._cache_lock = threading.Lock()
        self._cache = {}
        if self._cache_file.is_file():
            try:
                loaded = json.loads(
                    self._cache_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._cache = loaded
            except (OSError, ValueError):
                pass

    def _api(self, method: str, path: str, payload=None):
        body = None
        headers = {"Accept": "application/vnd.github+json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            API + path, data=body, headers=headers, method=method)
        add_surrogate_to_request(
            request, CREDENTIAL_NAME, allowed_hosts=["api.github.com"])
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                    request, timeout=60) as response:
                return response.status, read_json_response(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                detail = {}
            return exc.code, detail

    def _save(self) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def upload(self, file_path, dest_path: str) -> str:
        """Upload one image to the repo at dest_path; return its raw
        public URL. Identical bytes are uploaded only once."""
        data = Path(file_path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with self._cache_lock:
            if digest in self._cache:
                return self._cache[digest]["url"]
        api_path = f"/repos/{REPO}/contents/{dest_path}"
        status, existing = self._api("GET", api_path)
        payload = {
            "message": f"Ảnh bản nháp: {dest_path}",
            "content": base64.b64encode(data).decode("ascii"),
        }
        if status == 200 and isinstance(existing, dict) \
                and existing.get("sha"):
            payload["sha"] = existing["sha"]
        status, result = self._api("PUT", api_path, payload)
        if status not in (200, 201) or not isinstance(result, dict):
            raise ImageHostError(
                f"GitHub upload failed ({status}): "
                f"{result.get('message', '') if isinstance(result, dict) else ''}")
        url = result["content"]["download_url"]
        with self._cache_lock:
            self._cache[digest] = {"url": url, "dest": dest_path}
            self._save()
        return url
