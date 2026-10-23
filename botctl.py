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
  reviewers in Telegram. With --file <draft.md>, the draft is
  published as a telegra.ph page and the page URL becomes the stored
  reference, so the card carries one tidy link that opens the whole
  formatted draft inside Telegram (Instant View) — nothing else is
  sent into the chat. Re-attaching edits the same page.
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
from telegraph import Telegraph

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


def _telegraph_inline(text: str):
    """Split a line into telegra.ph children, honouring **bold** and
    *italic* spans."""
    import re

    children = []
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            children.append({"tag": "strong", "children": [part[2:-2]]})
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            children.append({"tag": "em", "children": [part[1:-1]]})
        else:
            children.append(part)
    return children or [""]


def _md_nodes_generic(lines, drop_prefixes=()):
    """Convert draft markdown lines to telegra.ph nodes: headings,
    paragraphs, bullet lists, rules. An editorial-notes / SEO section
    ends the page — its content is internal and never published."""
    import re

    nodes, bullets = [], []

    def flush():
        nonlocal bullets
        if bullets:
            nodes.append({
                "tag": "ul",
                "children": [
                    {"tag": "li", "children": _telegraph_inline(item)}
                    for item in bullets],
            })
            bullets = []

    for raw in lines:
        stripped = raw.strip()
        if re.match(r"(?i)^(#{1,6}\s*)?\**\s*(ghi chú biên tập|seo)\b",
                    stripped):
            break
        if any(stripped.startswith(prefix) for prefix in drop_prefixes):
            continue
        if stripped.startswith("### "):
            flush()
            nodes.append({"tag": "h4",
                          "children": [stripped[4:].strip("* ")]})
        elif stripped.startswith("## "):
            flush()
            nodes.append({"tag": "h3",
                          "children": [stripped[3:].strip("* ")]})
        elif stripped.startswith("# "):
            flush()
            nodes.append({"tag": "h3",
                          "children": [stripped[2:].strip("* ")]})
        elif stripped in ("---", "***"):
            flush()
            nodes.append({"tag": "hr"})
        elif stripped.startswith(("- ", "• ")):
            bullets.append(stripped[2:].strip())
        elif not stripped:
            flush()
        else:
            flush()
            nodes.append({"tag": "p",
                          "children": _telegraph_inline(stripped)})
    flush()
    return nodes


def _ig_caption_paragraphs(md: str):
    import re

    match = re.search(r"(?im)^[#*\s]*caption.*$", md)
    if not match:
        return []
    paragraphs = []
    for block in re.split(r"\n\s*\n", md[match.end():]):
        lines = []
        for line in block.split("\n"):
            stripped = line.strip()
            if not stripped or re.match(r"^#{1,4}\s", stripped):
                continue
            lines.append(stripped)
        if lines:
            paragraphs.append(" ".join(lines))
    return paragraphs


_GENERIC_HEADINGS = {
    "tiêu đề", "sapo", "nội dung", "caption", "caption đăng kèm",
    "cấu trúc carousel", "kịch bản carousel", "hashtag",
    "hashtag kèm theo",
}


def _draft_page_title(task, output, md: str) -> str:
    from storage import CHANNELS

    label = CHANNELS.get(output["channel"], {}).get(
        "label", output["channel"])
    lines = _strip_draft_scaffolding(md).split("\n")
    title = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(("# ", "## ", "### ")):
            continue
        heading = stripped.lstrip("# ").strip().strip("*").strip()
        if heading.lower() in _GENERIC_HEADINGS:
            # A section label ("## Tiêu đề") — the real title is the
            # next content line, usually bolded; slide scaffolding
            # lines (Ảnh 1…, Chữ trên ảnh:…) are not titles.
            heading = ""
            for following in lines[index + 1:]:
                candidate = following.strip().strip("*").strip()
                if not candidate or "file_" in candidate:
                    continue
                if candidate.startswith(
                        ("Chữ trên", "Ảnh ", "Dòng ", "Slide ", "#")):
                    continue
                heading = candidate
                break
        title = heading
        break
    if output["channel"] == "instagram":
        slides = _parse_ig_slides(md)
        if slides and (not title or title.lower() in _GENERIC_HEADINGS
                       or task["code"] in title):
            title = slides[0][0]
    hint = task["hint"] if "hint" in task.keys() else None
    base = title or hint or task["code"]
    return f"{task['code']} · {label} — {base}"[:250]


def _draft_telegraph_nodes(task, output, md: str):
    """Build the telegra.ph page body for one channel draft. The owner
    reviews drafts through ONE tidy link per channel card, so the page
    carries the whole formatted draft: article text for website /
    facebook, slide texts + caption for instagram."""
    channel = output["channel"]
    if channel == "instagram":
        nodes = []
        for index, lines in enumerate(_parse_ig_slides(md), start=1):
            nodes.append({"tag": "h4", "children": [f"Slide {index}"]})
            for line in lines:
                nodes.append({"tag": "p",
                              "children": _telegraph_inline(line)})
        paragraphs = _ig_caption_paragraphs(md)
        if paragraphs:
            nodes.append({"tag": "h4", "children": ["Caption đăng kèm"]})
            for paragraph in paragraphs:
                nodes.append({"tag": "p",
                              "children": _telegraph_inline(paragraph)})
        if nodes:
            return nodes
    text = _strip_draft_scaffolding(md)
    drops = ()
    if channel == "website":
        drops = ("**Tiêu đề SEO", "**Mô tả SEO", "*Ghi chú biên tập",
                 "Kênh:")
    elif channel == "facebook":
        drops = ("Ảnh nổi bật", "Hoặc đăng", "Ảnh bìa đề xuất",
                 "**Ảnh nổi bật", "**Hoặc đăng", "**Ảnh bìa đề xuất")
    return _md_nodes_generic(text.split("\n"), drops)



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
    ref = args.ref
    # With --file, the draft itself is published as a telegra.ph page
    # and the page URL becomes the draft reference: the channel card in
    # Telegram then carries ONE tidy link that opens the whole
    # formatted draft (Instant View) — no content dumped into the chat,
    # and no raw .md link that browsers cannot render. Re-attaching a
    # revised draft edits the same page, so earlier links keep working.
    draft_file = getattr(args, "file", None)
    if draft_file:
        try:
            md = Path(draft_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Lỗi: không đọc được file nháp: {exc}", file=sys.stderr)
            return 1
        publisher = Telegraph(DEFAULT_DATA_DIR / "telegraph.json")
        try:
            ref = publisher.publish(
                f"{task['code']}:{output['channel']}",
                _draft_page_title(task, output, md),
                _draft_telegraph_nodes(task, output, md))
        except Exception as exc:
            print(f"Lỗi: không đăng được trang nháp lên telegra.ph: "
                  f"{exc}", file=sys.stderr)
            return 1
        print(f"Trang nháp: {ref}")
    store.set_output_draft(output["id"], ref)
    store.audit(None, "attach_output_draft", "output", output["id"],
                detail=f"task={args.task_code} channel={args.channel} "
                       f"draft_ref={ref}")
    client = build_client_or_warn(args.no_notify)
    output = store.get_output(output["id"])
    _notify_output(store, client, task, output,
                   _recipients_for_draft(store, task))
    print(f"Đã gắn bản nháp cho đầu ra {args.channel} của "
          f"{args.task_code}: {ref}")
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
        help="File .md chứa nội dung nháp: đăng thành trang telegra.ph "
             "và dùng link trang làm tham chiếu nháp trên thẻ kênh")

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
