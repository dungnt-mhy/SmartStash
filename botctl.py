#!/usr/bin/env python3
"""Local control CLI for Muse to drive the article workflow.

Usage:
    python3 botctl.py list-needs-article
    python3 botctl.py attach-draft CV-0001 https://example.com/draft/1
    python3 botctl.py set-published CV-0001 https://example.com/bai-viet-1
    python3 botctl.py list-outputs CV-0001
    python3 botctl.py attach-output-draft CV-0001 facebook https://example.com/draft/fb
    python3 botctl.py set-output-published CV-0001 facebook https://facebook.com/post/1

- list-needs-article: print tasks in DRAFT/PENDING as JSON (stdout).
- attach-draft: store the website draft URL on the task and notify the
  creator + reviewers in Telegram.
- set-published: mark the task PUBLISHED with its public URL and notify
  the creator + admins.  Local-only command (run on this machine).
- list-outputs: print the per-channel outputs of a task as JSON.
- attach-output-draft: store the draft reference on one channel output
  (status stays DRAFT) and send that channel's card to the creator +
  reviewers in Telegram. With --file <draft.md>, also deliver the
  draft itself: the full text as Telegram messages, plus (website
  channel only) an HTML preview document that opens like the real
  article — nobody has to open a raw .md file to review.
- set-output-published: mark one channel output PUBLISHED with its
  public URL and send its card to the creator + admins.

Notifications go through the Telegram API client (secure-connection
surrogate or TELEGRAM_BOT_TOKEN).  With --no-notify, or when no token is
available, the database update still succeeds and a warning is printed
to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from handlers import BotApp
from storage import Store

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


def task_to_dict(task) -> dict:
    return {
        "code": task["code"],
        "status": task["status"],
        "creator_id": task["creator_id"],
        "creator_role": task["creator_role"],
        "hint": task["hint"],
        "file_ids": json.loads(task["file_ids"]),
        "draft_url": task["draft_url"],
        "public_url": task["public_url"],
        "reject_reason": task["reject_reason"],
        "destinations": (
            json.loads(task["destinations"])
            if "destinations" in task.keys() and task["destinations"]
            else ["website", "facebook", "instagram"]
        ),
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }


def output_to_dict(output) -> dict:
    return {
        "id": output["id"],
        "task_code": output["task_code"],
        "channel": output["channel"],
        "status": output["status"],
        "draft_ref": output["draft_ref"],
        "public_url": output["public_url"],
        "reject_reason": output["reject_reason"],
        "created_at": output["created_at"],
        "updated_at": output["updated_at"],
    }


def _notify_output(store, client, task, output, user_rows):
    """Send the channel card to each recipient; warns instead of failing."""
    if client is None:
        return
    app = BotApp(store, client)
    for row in user_rows:
        if row["chat_id"] is None:
            continue
        try:
            app.send_output_card(row["chat_id"], output, task, row)
        except Exception as exc:
            print(f"Cảnh báo: không gửi được thẻ kênh tới "
                  f"{row['telegram_id']}: {exc}", file=sys.stderr)


def _strip_draft_scaffolding(md: str) -> str:
    """Drop the leading meta block ("# Bản nháp ..." header + the rule
    under it) that Muse's draft files carry, leaving the content the
    reader should see."""
    text = md.replace("\r\n", "\n")
    if text.startswith("# Bản nháp") and "\n---\n" in text[:800]:
        text = text.split("\n---\n", 1)[1]
    return text.strip()


def md_to_telegram_text(md: str) -> str:
    """Flatten markdown into clean plain text for Telegram messages —
    no raw ** or ## for the reader to trip over."""
    import re

    out = []
    for line in _strip_draft_scaffolding(md).split("\n"):
        s = line.rstrip()
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = re.sub(r"^\s*---+\s*$", "", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", s)
        s = s.replace("`", "")
        if s.startswith("- "):
            s = "• " + s[2:]
        out.append(s)
    return "\n".join(out).strip()


def _chunk_text(text: str, limit: int = 3900):
    chunks, current = [], ""
    for para in text.split("\n"):
        candidate = (current + "\n" + para) if current else para
        if len(candidate) > limit and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def md_to_preview_html(md: str, title: str, photo_paths) -> str:
    """Render a website draft as a standalone, phone-friendly HTML page
    that looks like the real article (title, sapo, body, the task's
    photos embedded) — the reader taps the file in Telegram and reads
    it in the browser instead of fighting a raw .md file."""
    import base64
    import html as html_mod
    import re

    def inline(s: str) -> str:
        s = html_mod.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
        return s

    photos_html = ""
    if photo_paths:
        imgs = []
        for p in photo_paths:
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
            except OSError:
                continue
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            imgs.append(
                f'<img src="data:{mime};base64,{b64}" alt="Ảnh tư liệu">')
        if imgs:
            photos_html = '<div class="photos">' + "".join(imgs) + "</div>"

    blocks, list_open, inserted = [], False, False

    def close_list():
        nonlocal list_open
        if list_open:
            blocks.append("</ul>")
            list_open = False

    for raw in _strip_draft_scaffolding(md).split("\n"):
        line = raw.strip()
        if not line:
            close_list()
            continue
        if line.startswith("*Ghi chú") or line.startswith("**Tiêu đề SEO") \
                or line.startswith("**Mô tả SEO"):
            close_list()
            continue
        if line.startswith("### "):
            close_list(); blocks.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list(); blocks.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list(); blocks.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("- "):
            if not list_open:
                blocks.append("<ul>"); list_open = True
            blocks.append(f"<li>{inline(line[2:])}</li>")
        else:
            close_list(); blocks.append(f"<p>{inline(line)}</p>")
        if not inserted and photos_html and len(blocks) >= 2:
            blocks.append(photos_html)
            inserted = True
    close_list()
    if photos_html and not inserted:
        blocks.insert(0, photos_html)
    return (
        "<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\">"
        f"<title>{html_mod.escape(title)}</title><style>"
        "body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;"
        "max-width:720px;margin:0 auto;padding:20px 16px 48px;"
        "line-height:1.65;color:#1a1a1a;background:#fff}"
        "h1{font-size:26px;line-height:1.3;margin:8px 0 12px}"
        "h2{font-size:20px;margin:24px 0 8px}h3{font-size:17px}"
        "p{margin:10px 0}img{max-width:100%;border-radius:10px;"
        "margin:8px 0}.photos{margin:12px 0}"
        ".tag{display:inline-block;background:#eef4ff;color:#2952a3;"
        "border-radius:999px;padding:2px 10px;font-size:12px}"
        "</style></head><body>"
        "<span class=\"tag\">BẢN NHÁP — XEM TRƯỚC NHƯ BÀI THẬT</span>"
        + "".join(blocks) +
        "</body></html>"
    )


def _pil_font(size, bold=False):
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _strip_emoji(text: str) -> str:
    """Preview images are drawn with regular fonts (no colour emoji) —
    drop emoji so the mock text stays clean; the Telegram text message
    keeps the original emoji."""
    import re

    return re.sub(
        "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF"
        "\uFE0F\u200d]+", "", text).strip()


def _wrap_lines(draw, text, font, max_width):
    lines = []
    for para in text.split("\n"):
        words, current = para.split(), ""
        if not words:
            lines.append("")
            continue
        for word in words:
            candidate = (current + " " + word).strip()
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _paste_cover(canvas, photo_path, box):
    """Paste a photo centre-cropped to fill box (x0, y0, x1, y1)."""
    from PIL import Image

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    try:
        img = Image.open(photo_path).convert("RGB")
    except OSError:
        return
    scale = max(width / img.width, height / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    canvas.paste(img.crop((left, top, left + width, top + height)), (x0, y0))


def _render_facebook_preview(task, md, photos, out_path):
    """Render the Facebook draft as ONE glanceable image: a mock post
    (tag, post text, the task's photos) the reader sees inline in
    Telegram without opening anything."""
    from PIL import Image, ImageDraw

    width, margin = 1080, 48
    body_font = _pil_font(40)
    small_font = _pil_font(30)
    tag_font = _pil_font(30, bold=True)
    probe = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    text = _strip_emoji(md_to_telegram_text(md))
    kept = []
    for line in text.split("\n"):
        low = line.strip().lower()
        if low.startswith(("ảnh nổi bật", "hoặc đăng", "ảnh bìa đề xuất",
                           "kênh:", "bản nháp")):
            continue
        kept.append(line)
    wrapped = _wrap_lines(probe, "\n".join(kept).strip(), body_font,
                          width - 2 * margin)
    if len(wrapped) > 16:
        wrapped = wrapped[:16] + ["…"]
    line_h = 56
    text_h = len(wrapped) * line_h
    photo_h = 620 if photos else 0
    height = 120 + text_h + (photo_h + 24 if photo_h else 0) + 90
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([24, 24, width - 24, height - 24], radius=28,
                           outline=(210, 215, 225), width=3)
    draw.ellipse([margin, 44, margin + 56, 100], fill=(24, 119, 242))
    draw.text((margin + 76, 52), "FACEBOOK · BẢN NHÁP", font=tag_font,
              fill=(24, 119, 242))
    y = 124
    for line in wrapped:
        draw.text((margin, y), line, font=body_font, fill=(20, 20, 20))
        y += line_h
    if photo_h:
        y += 24
        if len(photos) >= 2:
            half = (width - 2 * margin - 12) // 2
            _paste_cover(canvas, photos[0], (margin, y, margin + half, y + photo_h))
            _paste_cover(canvas, photos[1],
                         (margin + half + 12, y, width - margin, y + photo_h))
        else:
            _paste_cover(canvas, photos[0], (margin, y, width - margin, y + photo_h))
        y += photo_h
    draw.text((margin, height - 64),
              f"Việc {task['code']} — ảnh mô phỏng bài đăng, chưa đăng thật.",
              font=small_font, fill=(110, 115, 125))
    canvas.save(out_path, "JPEG", quality=88)
    return out_path


def _parse_ig_slides(md: str):
    """Extract carousel slides (overlay text lines) from an Instagram
    draft .md, tolerating the layout variants Muse writes. Marker
    lines (Dòng 1:/Dòng 2:/Chữ trên ảnh:/Phụ đề:) win; quoted strings
    are the fallback. A block ends at the caption/hashtag section so
    quotes from the caption never leak into the last slide."""
    import re

    text = _strip_draft_scaffolding(md)
    blocks, current = [], None
    marker = re.compile(r"(?i)^[#*\s>\-]*(slide|ảnh)\s*(\d+)")
    stopper = re.compile(
        r"(?i)^[#*\s]*(caption|hashtag|ghi chú|ảnh bìa đề xuất|lưu ý)")
    value_re = re.compile(
        r"(?i)(dòng\s*\d+|chữ trên\s*(ảnh|slide)|phụ đề nhỏ)"
        r"\s*[:：]\s*(.+)")
    for line in text.split("\n"):
        if marker.match(line):
            current = []
            blocks.append(current)
            current.append(line)
            continue
        if stopper.match(line):
            current = None
            continue
        if current is not None:
            current.append(line)
    slides = []
    for block in blocks:
        lines = []
        for line in block:
            m = value_re.search(line)
            if m:
                value = m.group(3).strip().strip("*").strip()
                if len(value) >= 2 and value[0] in "\"“" \
                        and value[-1] in "\"”":
                    value = value[1:-1].strip()
                if value:
                    lines.append(value)
        if not lines:
            for quoted in re.findall(r"[\"“]([^\"”]{3,120})[\"”]",
                                     "\n".join(block)):
                if ".jpg" in quoted or "file_" in quoted:
                    continue
                lines.append(quoted.strip())
        lines = [ln for ln in dict.fromkeys(lines) if ln]
        if lines:
            slides.append(lines[:3])
    return slides


def _render_instagram_slide(task, index, total, lines, photo, out_path):
    """Render one carousel slide as a square image: the task's photo
    full-bleed, the slide's text overlaid at the bottom, a 1/N badge —
    exactly what the reader glances at in Telegram."""
    from PIL import Image, ImageDraw

    size = 1080
    canvas = Image.new("RGB", (size, size), (38, 40, 48))
    if photo is not None:
        _paste_cover(canvas, photo, (0, 0, size, size))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for i in range(340):
        alpha = int(190 * (i / 340))
        odraw.line([(0, size - 340 + i), (size, size - 340 + i)],
                   fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    tag_font = _pil_font(30, bold=True)
    draw.rounded_rectangle([36, 36, 330, 92], radius=16, fill=(0, 0, 0, 140))
    draw.text((56, 48), "INSTAGRAM · NHÁP", font=tag_font, fill="white")
    badge = f"{index}/{total}"
    bw = draw.textlength(badge, font=tag_font) + 40
    draw.rounded_rectangle([size - 36 - bw, 36, size - 36, 92], radius=16,
                           fill=(0, 0, 0, 140))
    draw.text((size - 16 - bw, 48), badge, font=tag_font, fill="white")
    font = _pil_font(52, bold=True)
    wrapped = []
    for line in lines:
        wrapped += _wrap_lines(draw, _strip_emoji(line), font, size - 120)
    y = size - 80 - len(wrapped) * 68
    for line in wrapped:
        tw = draw.textlength(line, font=font)
        draw.text(((size - tw) / 2 + 2, y + 2), line, font=font,
                  fill=(0, 0, 0, 160))
        draw.text(((size - tw) / 2, y), line, font=font, fill="white")
        y += 68
    canvas.convert("RGB").save(out_path, "JPEG", quality=88)
    return out_path


def _deliver_draft_content(store, client, task, output, md_path):
    """Send the draft's full content to the creator + reviewers in
    Telegram: readable text messages for every channel, plus — for the
    website channel — an HTML preview document. Best effort: a failed
    send warns on stderr and never fails the attach itself."""
    if client is None:
        return
    from storage import CHANNELS

    try:
        md = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Cảnh báo: không đọc được file nháp {md_path}: {exc}",
              file=sys.stderr)
        return
    label = CHANNELS.get(output["channel"], {}).get(
        "label", output["channel"])
    recipients = _recipients_for_draft(store, task)
    plain = md_to_telegram_text(md)
    header = f"📄 NỘI DUNG BẢN NHÁP — {label} · Việc {task['code']}"
    chunks = _chunk_text(plain)
    files_dir = DEFAULT_DATA_DIR / "files" / task["code"]
    photos = sorted(files_dir.glob("*")) if files_dir.is_dir() else []
    preview_path = None
    if output["channel"] == "website":
        title = next(
            (ln.lstrip("# ").strip() for ln in
             _strip_draft_scaffolding(md).split("\n")
             if ln.startswith("#")), task["code"])
        preview_path = md_path.with_name(md_path.stem + "-preview.html")
        try:
            preview_path.write_text(
                md_to_preview_html(md, title, photos), encoding="utf-8")
        except OSError as exc:
            print(f"Cảnh báo: không tạo được bản xem trước HTML: {exc}",
                  file=sys.stderr)
            preview_path = None
    # Facebook / Instagram: the lightest possible preview in Telegram is
    # an inline image — a rendered mock of the post / carousel slides,
    # visible at a glance without opening any link or file.
    image_paths, image_caption = [], None
    try:
        if output["channel"] == "facebook":
            path = md_path.with_name(md_path.stem + "-fb-preview.jpg")
            _render_facebook_preview(task, md, photos, path)
            image_paths = [path]
            image_caption = (
                f"👀 Xem trước bài Facebook của việc {task['code']} "
                "— ảnh mô phỏng để duyệt nhanh, chưa đăng thật.")
        elif output["channel"] == "instagram":
            slides = _parse_ig_slides(md)
            for i, lines in enumerate(slides, start=1):
                photo = photos[i - 1] if i - 1 < len(photos) \
                    else (photos[-1] if photos else None)
                path = md_path.with_name(
                    md_path.stem + f"-ig-slide{i}.jpg")
                _render_instagram_slide(
                    task, i, len(slides), lines, photo, path)
                image_paths.append(path)
            if image_paths:
                image_caption = (
                    f"👀 Xem trước carousel Instagram của việc "
                    f"{task['code']} — {len(image_paths)} slide, "
                    "ảnh mô phỏng để duyệt nhanh, chưa đăng thật.")
    except Exception as exc:
        print(f"Cảnh báo: không tạo được ảnh xem trước: {exc}",
              file=sys.stderr)
        image_paths = []
    send_document = getattr(client, "send_document", None)
    send_photo = getattr(client, "send_photo", None)
    for row in recipients:
        if row["chat_id"] is None:
            continue
        try:
            for i, chunk in enumerate(chunks):
                text = (header + "\n\n" + chunk) if i == 0 else chunk
                client.send_message(row["chat_id"], text)
            if preview_path is not None and send_document is not None:
                send_document(
                    row["chat_id"], preview_path,
                    caption=f"👀 Xem trước bản nháp Website của việc "
                            f"{task['code']} như bài thật — chạm để mở "
                            "bằng trình duyệt.")
            if send_photo is not None:
                for i, path in enumerate(image_paths):
                    send_photo(
                        row["chat_id"], path,
                        caption=image_caption if i == 0 else None)
        except Exception as exc:
            print(f"Cảnh báo: không gửi được nội dung nháp tới "
                  f"{row['telegram_id']}: {exc}", file=sys.stderr)


def _make_client():
    from telegram_client import TelegramClient

    return TelegramClient()


def _notify(store, client, user_rows, text):
    """Best-effort Telegram notification; warns on stderr instead of failing."""
    if client is None:
        return
    for row in user_rows:
        if row["chat_id"] is None:
            continue
        try:
            client.send_message(row["chat_id"], text)
        except Exception as exc:
            print(f"Cảnh báo: không gửi được thông báo tới "
                  f"{row['telegram_id']}: {exc}", file=sys.stderr)


def _recipients_for_draft(store, task):
    seen, rows = set(), []
    creator = store.get_user(task["creator_id"])
    if creator is not None:
        rows.append(creator)
        seen.add(creator["telegram_id"])
    for reviewer in store.reviewers():
        if reviewer["telegram_id"] not in seen:
            rows.append(reviewer)
            seen.add(reviewer["telegram_id"])
    return rows


def _recipients_for_published(store, task):
    seen, rows = set(), []
    creator = store.get_user(task["creator_id"])
    if creator is not None:
        rows.append(creator)
        seen.add(creator["telegram_id"])
    for admin in store.admins():
        if admin["telegram_id"] not in seen:
            rows.append(admin)
            seen.add(admin["telegram_id"])
    return rows


def build_client_or_warn(no_notify: bool):
    if no_notify:
        return None
    try:
        client = _make_client()
        # Force credential resolution now so a missing token warns here,
        # not per-recipient.
        client.method_url("sendMessage")
        return client
    except Exception as exc:
        print(f"Cảnh báo: không có token Telegram ({exc}); "
              "chỉ cập nhật dữ liệu, không gửi thông báo.", file=sys.stderr)
        return None


def cmd_list_needs_article(store, args) -> int:
    tasks = [task_to_dict(t) for t in store.needs_article()]
    print(json.dumps(tasks, ensure_ascii=False, indent=2))
    return 0


def cmd_attach_draft(store, args) -> int:
    task = store.get_task(args.task_code)
    if task is None:
        print(f"Không tìm thấy việc {args.task_code}", file=sys.stderr)
        return 1
    store.set_draft_url(args.task_code, args.draft_url)
    store.audit(None, "attach_draft", "task", args.task_code,
                detail=f"draft_url={args.draft_url}")
    client = build_client_or_warn(args.no_notify)
    task = store.get_task(args.task_code)
    _notify(
        store, client, _recipients_for_draft(store, task),
        f"✏️ Việc {task['code']} đã có bản nháp để kiểm tra:\n{args.draft_url}\n"
        f"Nội dung/gợi ý: {task['hint']}",
    )
    print(f"Đã gắn link nháp cho {args.task_code}: {args.draft_url}")
    return 0


def cmd_set_published(store, args) -> int:
    task = store.get_task(args.task_code)
    if task is None:
        print(f"Không tìm thấy việc {args.task_code}", file=sys.stderr)
        return 1
    store.set_public_url(args.task_code, args.public_url)
    store.audit(None, "set_published", "task", args.task_code,
                detail=f"public_url={args.public_url}")
    client = build_client_or_warn(args.no_notify)
    task = store.get_task(args.task_code)
    _notify(
        store, client, _recipients_for_published(store, task),
        f"🚀 Việc {task['code']} đã được đăng:\n{args.public_url}",
    )
    print(f"Đã đánh dấu {args.task_code} là PUBLISHED: {args.public_url}")
    return 0


def _get_task_and_output(store, task_code, channel):
    """Return (task, output) or print an error and return (None, None)."""
    task = store.get_task(task_code)
    if task is None:
        print(f"Không tìm thấy việc {task_code}", file=sys.stderr)
        return None, None
    output = store.get_output_by_channel(task_code, channel)
    if output is None:
        print(f"Việc {task_code} chưa có đầu ra kênh {channel}",
              file=sys.stderr)
        return None, None
    return task, output


def cmd_list_outputs(store, args) -> int:
    task = store.get_task(args.task_code)
    if task is None:
        print(f"Không tìm thấy việc {args.task_code}", file=sys.stderr)
        return 1
    outputs = [output_to_dict(o) for o in store.list_outputs(args.task_code)]
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


def cmd_attach_output_draft(store, args) -> int:
    task, output = _get_task_and_output(store, args.task_code, args.channel)
    if output is None:
        return 1
    store.set_output_draft(output["id"], args.ref)
    store.audit(None, "attach_output_draft", "output", output["id"],
                detail=f"task={args.task_code} channel={args.channel} "
                       f"draft_ref={args.ref}")
    client = build_client_or_warn(args.no_notify)
    output = store.get_output(output["id"])
    _notify_output(store, client, task, output,
                   _recipients_for_draft(store, task))
    # With --file, the reader also receives the draft itself: full text
    # in Telegram (+ an HTML preview document for the website channel),
    # so nobody has to open a raw .md file to review it.
    draft_file = getattr(args, "file", None)
    if draft_file:
        _deliver_draft_content(
            store, client, task, output, Path(draft_file))
    print(f"Đã gắn bản nháp cho đầu ra {args.channel} của "
          f"{args.task_code}: {args.ref}")
    return 0


def cmd_set_output_published(store, args) -> int:
    task, output = _get_task_and_output(store, args.task_code, args.channel)
    if output is None:
        return 1
    store.set_output_public_url(output["id"], args.public_url)
    store.audit(None, "set_output_published", "output", output["id"],
                detail=f"task={args.task_code} channel={args.channel} "
                       f"public_url={args.public_url}")
    store.sync_task_status_from_outputs(args.task_code)
    client = build_client_or_warn(args.no_notify)
    output = store.get_output(output["id"])
    _notify_output(store, client, task, output,
                   _recipients_for_published(store, task))
    print(f"Đã đánh dấu đầu ra {args.channel} của {args.task_code} "
          f"là PUBLISHED: {args.public_url}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Điều khiển bot giao việc (dùng nội bộ bởi Muse)")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="Thư mục dữ liệu (mặc định: ./data cạnh botctl.py)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-needs-article",
                   help="In JSON các việc ở trạng thái DRAFT/PENDING")

    p_draft = sub.add_parser("attach-draft", help="Gắn link bài nháp cho một việc")
    p_draft.add_argument("task_code")
    p_draft.add_argument("draft_url")
    p_draft.add_argument("--no-notify", action="store_true")

    p_pub = sub.add_parser("set-published",
                           help="Đánh dấu một việc đã đăng và gắn link public")
    p_pub.add_argument("task_code")
    p_pub.add_argument("public_url")
    p_pub.add_argument("--no-notify", action="store_true")

    p_outs = sub.add_parser("list-outputs",
                            help="In JSON các đầu ra theo kênh của một việc")
    p_outs.add_argument("task_code")

    p_odraft = sub.add_parser(
        "attach-output-draft",
        help="Gắn bản nháp cho một đầu ra kênh và gửi thẻ kênh đi")
    p_odraft.add_argument("task_code")
    p_odraft.add_argument("channel")
    p_odraft.add_argument("ref")
    p_odraft.add_argument("--no-notify", action="store_true")
    p_odraft.add_argument(
        "--file", default=None,
        help="File .md chứa nội dung nháp: gửi kèm toàn văn vào Telegram "
             "(và bản xem trước HTML cho kênh website)")

    p_opub = sub.add_parser(
        "set-output-published",
        help="Đánh dấu một đầu ra kênh đã đăng và gắn link public")
    p_opub.add_argument("task_code")
    p_opub.add_argument("channel")
    p_opub.add_argument("public_url")
    p_opub.add_argument("--no-notify", action="store_true")

    args = parser.parse_args(argv)
    store = Store(Path(args.data_dir) / "bot.db")
    try:
        if args.command == "list-needs-article":
            return cmd_list_needs_article(store, args)
        if args.command == "attach-draft":
            return cmd_attach_draft(store, args)
        if args.command == "set-published":
            return cmd_set_published(store, args)
        if args.command == "list-outputs":
            return cmd_list_outputs(store, args)
        if args.command == "attach-output-draft":
            return cmd_attach_output_draft(store, args)
        if args.command == "set-output-published":
            return cmd_set_output_published(store, args)
    finally:
        store.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
