#!/usr/bin/env python3
"""Post to a Facebook Page through the Meta Graph API (TD-0026).

The bot uses this module instead of the browser for Facebook places
that are Fanpages (`page_*`): the personal profile keeps the browser
path, because the Graph API cannot post to profiles.

Flow for one Page place, all inside this process:
  1. Fetch the Page access token with the stored System User token
     (the `custom.meta-graph` secure connection, via the authd
     surrogate exchange). The Page token lives only in memory here:
     it is never printed, logged or stored anywhere.
  2. Text-only output: one `POST /{page-id}/feed`.
     Photo output: multipart-upload each photo to
     `POST /{page-id}/photos` with `published=false`, then one
     `POST /{page-id}/feed` with `attached_media` holding every
     photo — a single published post carrying all photos.
  3. Read-after-write: the post is fetched back before the caller
     marks anything published.

The draft text comes from the output's telegra.ph draft page
(`fetch_draft_text`); the photos are the task's image files
(`task_photos`). Nothing in this module touches the database or
sends Telegram messages — the worker in handlers.py owns state and
notifications, and calls `publish_facebook_page` / `delete_page_post`.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

GRAPH_BASE = "https://graph.facebook.com"
API_VERSION = "v25.0"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

#: A feed post holds at most this many attached photos. More than
#: that fails the place (the Admin is told, Muse posts via the
#: browser instead) rather than silently dropping photos.
MAX_ATTACHED_PHOTOS = 10

#: The Minhy Page id for the Graph API (Business Settings asset id).
#: The number recorded on 23/10 (61595515900524) is rejected by the
#: API, so it must never be used here.
PAGE_ID_MINHY = "1319112221296020"


class GraphApiError(RuntimeError):
    """A Graph API call failed (or its answer was unusable)."""

    def __init__(self, message, http_status=None, code=None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GraphApiError("redirect refused, verify the endpoint")


def page_id_for_place(place_key):
    """Fanpage place key -> Page id, or None for the profile place."""
    if isinstance(place_key, str) and place_key.startswith("page_"):
        return place_key[len("page_"):]
    return None


def public_post_url(page_id, graph_post_id):
    """Public link for a Graph post id (`<page_id>_<post_id>`)."""
    post_id = graph_post_id.split("_", 1)[-1]
    return f"https://www.facebook.com/{page_id}/posts/{post_id}"


def _call_json(method, url, data=None, headers=None, timeout=60):
    """One JSON Graph call. Returns the parsed body; raises
    GraphApiError on any HTTP or transport failure. Never logs
    headers (they may carry a token)."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(
        url, data=body, headers=req_headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(payload).get("error", {})
        except ValueError:
            error = {}
        raise GraphApiError(
            error.get("message") or f"Graph API HTTP {exc.code}",
            http_status=exc.code, code=error.get("code")) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GraphApiError(f"Graph API unreachable: {exc}") from exc


def _multipart_body(fields, files):
    """Build a multipart/form-data body. `fields` is a list of
    (name, value); `files` a list of (field, filename, data bytes).
    Returns (body, content_type)."""
    boundary = "----giao-viec-" + uuid.uuid4().hex
    chunks = []
    for name, value in fields:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8"))
    for field, filename, data in files:
        mime, _ = mimetypes.guess_type(filename)
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
            .encode("utf-8"))
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _call_multipart(url, fields, files, headers, timeout=120):
    """One multipart Graph call (photo upload). Returns the parsed
    body; raises GraphApiError on failure."""
    body, content_type = _multipart_body(fields, files)
    req_headers = {"Content-Type": content_type}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(
        url, data=body, headers=req_headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(payload).get("error", {})
        except ValueError:
            error = {}
        raise GraphApiError(
            error.get("message") or f"Graph API HTTP {exc.code}",
            http_status=exc.code, code=error.get("code")) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GraphApiError(f"Graph API unreachable: {exc}") from exc


def get_page_token(page_id):
    """Fetch the Page access token for one Page with the stored
    System User token (surrogate exchange, in this process only).
    The returned token is never printed or stored by this module.
    Raises GraphApiError when the token cannot be fetched."""
    import sys
    sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
    from dynamic_credentials import add_surrogate_to_request

    url = (f"{GRAPH_BASE}/{API_VERSION}/{page_id}"
           f"?fields=access_token")
    request = urllib.request.Request(url, method="GET")
    add_surrogate_to_request(
        request, "custom.meta-graph", allowed_hosts=["graph.facebook.com"])
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(payload).get("error", {})
        except ValueError:
            error = {}
        raise GraphApiError(
            error.get("message") or f"Graph API HTTP {exc.code}",
            http_status=exc.code, code=error.get("code")) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GraphApiError(f"Graph API unreachable: {exc}") from exc
    token = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not token:
        raise GraphApiError("Graph API did not return a Page token")
    return token


def publish_page_post(page_token, page_id, message, photo_paths,
                      published=True):
    """Publish one post to a Page as the Page itself. Text-only goes
    straight to /feed; with photos each photo is uploaded unpublished
    first and the feed post carries them all via attached_media.
    Returns the Graph post id (`<page_id>_<post_id>`). Raises
    GraphApiError on any failure — the caller must not mark the
    place published then."""
    headers = {"Authorization": f"Bearer {page_token}"}
    photo_paths = [Path(p) for p in (photo_paths or [])]
    if len(photo_paths) > MAX_ATTACHED_PHOTOS:
        raise GraphApiError(
            f"quá {MAX_ATTACHED_PHOTOS} ảnh cho một bài đăng "
            f"({len(photo_paths)} ảnh)")
    if not photo_paths:
        payload = {"message": message}
        if not published:
            payload["published"] = False
        result = _call_json(
            "POST", f"{GRAPH_BASE}/{API_VERSION}/{page_id}/feed",
            data=payload, headers=headers)
        post_id = result.get("id") if isinstance(result, dict) else None
        if not post_id:
            raise GraphApiError("Graph API did not return a post id")
        return post_id
    media_fbids = []
    for path in photo_paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise GraphApiError(
                f"không đọc được ảnh {path.name}: {exc}") from exc
        result = _call_multipart(
            f"{GRAPH_BASE}/{API_VERSION}/{page_id}/photos",
            [("published", "false")],
            [("source", path.name, data)],
            headers=headers)
        fbid = result.get("id") if isinstance(result, dict) else None
        if not fbid:
            raise GraphApiError(
                "Graph API did not return a photo id for "
                f"{path.name}")
        media_fbids.append({"media_fbid": fbid})
    payload = {"message": message, "attached_media": media_fbids}
    if not published:
        payload["published"] = False
    result = _call_json(
        "POST", f"{GRAPH_BASE}/{API_VERSION}/{page_id}/feed",
        data=payload, headers=headers)
    post_id = result.get("id") if isinstance(result, dict) else None
    if not post_id:
        raise GraphApiError("Graph API did not return a post id")
    return post_id


def verify_page_post(page_token, post_id):
    """True when the post exists and is readable; False when Meta
    says it does not exist. Other failures raise, so the caller
    never mistakes a transport error for a missing post. Code 10
    counts as "gone" too: Meta answers a GET on a just-deleted
    post with (#10) "Object does not exist..." (probe 25/10), and
    a Page token that just created the post can always read it
    while it exists."""
    headers = {"Authorization": f"Bearer {page_token}"}
    try:
        result = _call_json(
            "GET", f"{GRAPH_BASE}/{API_VERSION}/{post_id}?fields=id",
            headers=headers)
    except GraphApiError as exc:
        if exc.http_status in (400, 404) and exc.code in (10, 100, 803):
            return False
        raise
    return isinstance(result, dict) and result.get("id") == post_id


def delete_page_post(page_token, post_id):
    """Delete a Page post and verify it is gone. Returns True only
    when the post no longer reads back. A delete that answers "does
    not exist" counts as deleted too (idempotent: a crash between a
    successful delete and the caller's state write must not leave
    the place stuck). Raises GraphApiError when the delete itself
    fails for another reason."""
    headers = {"Authorization": f"Bearer {page_token}"}
    try:
        result = _call_json(
            "DELETE", f"{GRAPH_BASE}/{API_VERSION}/{post_id}",
            headers=headers)
    except GraphApiError as exc:
        if exc.http_status in (400, 404) and exc.code in (10, 100, 803):
            return not verify_page_post(page_token, post_id)
        raise
    if not (isinstance(result, dict) and result.get("success")):
        return False
    return not verify_page_post(page_token, post_id)


def publish_facebook_page(page_id, message, photo_paths,
                          published=True, _page_token=None):
    """Full Page publish for one place: fetch the Page token (unless
    the caller injects one — tests only), publish, read back to
    verify. Returns (graph_post_id, public_url). The Page token is
    never printed or stored. Raises GraphApiError on any failure."""
    page_token = _page_token or get_page_token(page_id)
    post_id = publish_page_post(
        page_token, page_id, message, photo_paths, published=published)
    if not verify_page_post(page_token, post_id):
        raise GraphApiError(
            "bài đã đăng nhưng không đọc lại được để xác minh")
    return post_id, public_post_url(page_id, post_id)


class _DraftTextParser(HTMLParser):
    """Plain text out of a telegra.ph draft page: paragraphs and
    headings inside <article>, skipping the header (title/author),
    figures (the task photos) and side notes."""

    _BLOCKS = {"p", "h3", "h4", "h5", "blockquote", "li", "pre"}
    _SKIP = {"figure", "header", "address", "aside", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_article = False
        self._skip_depth = 0
        self._current = None
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self._in_article = True
            return
        if not self._in_article:
            return
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCKS:
            self._flush()
            self._current = []
        elif tag == "br" and self._current is not None:
            self._current.append("\n")

    def handle_endtag(self, tag):
        if tag == "article":
            self._flush()
            self._in_article = False
            return
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self._in_article or self._skip_depth:
            return
        if self._current is None:
            return
        self._current.append(data)

    def _flush(self):
        if self._current is None:
            return
        text = "".join(self._current).strip()
        if text:
            self.blocks.append(text)
        self._current = None


def fetch_draft_text(draft_ref):
    """Post text out of a draft page URL (telegra.ph). Returns the
    paragraphs joined with blank lines. Raises GraphApiError when
    the page cannot be read or holds no text. The path is
    percent-encoded first: a draft titled in Vietnamese gets a
    telegra.ph URL with raw diacritics, which urllib cannot send."""
    if not isinstance(draft_ref, str) \
            or not draft_ref.startswith(("http://", "https://")):
        raise GraphApiError("đầu ra chưa có link nháp để lấy nội dung")
    parts = urllib.parse.urlsplit(draft_ref)
    safe_url = urllib.parse.urlunsplit((
        parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%"),
        urllib.parse.quote(parts.query, safe="=&%"),
        parts.fragment))
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; IAICTBot/1.0)"},
        method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GraphApiError(
            f"không tải được trang nháp: {exc}") from exc
    parser = _DraftTextParser()
    parser.feed(page_html)
    parser.close()
    text = "\n\n".join(html.unescape(block) for block in parser.blocks)
    text = text.strip()
    if not text:
        raise GraphApiError("trang nháp không có nội dung chữ")
    return text


def task_photos(files_dir, task_code):
    """Image files of a task, in name order — the photos of the
    Facebook post. An empty list means a text-only post."""
    import archive

    photos = []
    for name, path, _size in archive.task_material_files(
            files_dir, task_code):
        if Path(name).suffix.lower() in IMAGE_EXTENSIONS:
            photos.append(path)
    return photos
