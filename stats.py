"""Statistics helpers for /thongke and the daily summary.

Pure data + file writers: period maths (Vietnam time), aggregation
over the store, a matplotlib chart PNG, and CSV / Excel exports.
Handlers own the Telegram formatting; this module never talks to
Telegram. Period bounds are computed in ICT (UTC+7, no DST — the
same fixed offset the rest of the bot uses) and converted to UTC
ISO strings, which is how timestamps are stored.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone

from storage import (
    CHANNELS,
    CHANNEL_ORDER,
    STATUS_APPROVED_WAITING,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_REJECTED,
    STATUS_RETRACTED,
)

ICT = timezone(timedelta(hours=7), name="ICT")
UTC = timezone.utc

PERIODS = ("today", "week", "month", "all")
PERIOD_BUTTON_LABELS = {
    "today": "📅 Hôm nay",
    "week": "🗓 Tuần này",
    "month": "📆 Tháng này",
    "all": "♾ Tất cả",
}

#: Short status names for chart axes (the full "Nháp (DRAFT)" labels
#: are too long to sit under bars).
STATUS_SHORT = {
    STATUS_DRAFT: "Nháp",
    STATUS_PENDING: "Chờ duyệt",
    STATUS_APPROVED_WAITING: "Chờ đăng",
    STATUS_PUBLISHED: "Đã đăng",
    STATUS_REJECTED: "Trả lại",
    STATUS_CANCELLED: "Đã hủy",
    STATUS_RETRACTED: "Thu hồi",
}

VIDEO_CHANNELS = ("video_vertical", "video_horizontal")


def period_range(period: str, now=None):
    """Return (label, start_utc_iso, end_utc_iso) for a period key.

    ``end`` is exclusive; both bounds are None for "all". Weeks run
    Monday–Sunday, Vietnam time.
    """
    now = now or datetime.now(ICT)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        label = f"Hôm nay {now.strftime('%d/%m/%Y')}"
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        label = (f"Tuần này ({start.strftime('%d/%m')} – "
                 f"{(end - timedelta(days=1)).strftime('%d/%m/%Y')})")
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0,
                            microsecond=0)
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1)
        else:
            end = start.replace(month=now.month + 1)
        label = f"Tháng {now.month}/{now.year}"
    else:
        return "Tất cả", None, None
    return (
        label,
        start.astimezone(UTC).isoformat(timespec="seconds"),
        end.astimezone(UTC).isoformat(timespec="seconds"),
    )


def _fmt_time(iso_utc) -> str:
    """Stored UTC ISO → 'HH:MM dd/mm/yyyy' Vietnam time (mirrors the
    handlers' card formatting)."""
    if not iso_utc:
        return ""
    try:
        dt = datetime.fromisoformat(iso_utc)
    except (ValueError, TypeError):
        return iso_utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ICT).strftime("%H:%M %d/%m/%Y")


def collect_stats(store, period: str, now=None) -> dict:
    """Aggregate one period: task/output counts by status and
    channel, per-creator numbers, video numbers, and the flat
    export rows (one per output of a task created in the period)."""
    label, start, end = period_range(period, now)
    if start is None:
        # "all": an unbounded range — anchor at the epoch so the
        # store's >= comparison still works.
        start = "1970-01-01T00:00:00+00:00"
    tasks = store.tasks_created_between(start, end)
    outputs = store.outputs_created_between(start, end)
    published = store.outputs_published_between(start, end)

    task_status_counts = {}
    for task in tasks:
        task_status_counts[task["status"]] = \
            task_status_counts.get(task["status"], 0) + 1

    output_channel_status = {}
    for output in outputs:
        per_channel = output_channel_status.setdefault(
            output["channel"], {})
        per_channel[output["status"]] = \
            per_channel.get(output["status"], 0) + 1

    # Per-creator: tasks created in the period, and outputs of
    # those creators published in the period.
    creator_names = {}

    def creator_name(creator_id):
        if creator_id not in creator_names:
            user = store.get_user(creator_id)
            creator_names[creator_id] = (
                user["display_name"] if user else str(creator_id))
        return creator_names[creator_id]

    per_creator = {}
    for task in tasks:
        entry = per_creator.setdefault(
            task["creator_id"], {"tasks": 0, "published": 0})
        entry["tasks"] += 1
    task_creator = {t["code"]: t["creator_id"] for t in
                    store.list_tasks(limit=100000)}
    for output in published:
        creator_id = task_creator.get(output["task_code"])
        if creator_id is None:
            continue
        entry = per_creator.setdefault(
            creator_id, {"tasks": 0, "published": 0})
        entry["published"] += 1
    per_creator_rows = [
        {
            "creator_id": creator_id,
            "name": creator_name(creator_id),
            "tasks": entry["tasks"],
            "published": entry["published"],
        }
        for creator_id, entry in sorted(
            per_creator.items(), key=lambda kv: -kv[1]["tasks"])
    ]

    video = {
        "drafted": sum(
            1 for o in outputs
            if o["channel"] in VIDEO_CHANNELS and o["draft_ref"]),
        "published": sum(
            1 for o in published if o["channel"] in VIDEO_CHANNELS),
        "published_vertical": sum(
            1 for o in published if o["channel"] == "video_vertical"),
        "published_horizontal": sum(
            1 for o in published
            if o["channel"] == "video_horizontal"),
    }

    tasks_by_code = {t["code"]: t for t in tasks}
    export_rows = []
    for task in tasks:
        task_outputs = store.list_outputs(task["code"])
        if not task_outputs:
            export_rows.append(_export_row(task, None, creator_name))
        for output in task_outputs:
            export_rows.append(_export_row(task, output, creator_name))

    return {
        "period": period,
        "label": label,
        "tasks": tasks,
        "task_status_counts": task_status_counts,
        "outputs": outputs,
        "output_channel_status": output_channel_status,
        "published": published,
        "per_creator": per_creator_rows,
        "video": video,
        "export_rows": export_rows,
        "tasks_by_code": tasks_by_code,
    }


def _export_row(task, output, creator_name) -> dict:
    return {
        "task_code": task["code"],
        "hint": (task["hint"] or "").replace("\n", " "),
        "creator": creator_name(task["creator_id"]),
        "creator_role": task["creator_role"] or "",
        "channel": (
            CHANNELS.get(output["channel"], {}).get(
                "label", output["channel"])
            if output else ""),
        "status": output["status"] if output else task["status"],
        "draft_ref": (output["draft_ref"] or "") if output else "",
        "public_url": (output["public_url"] or "") if output else "",
        "created_at": _fmt_time(
            output["created_at"] if output else task["created_at"]),
        "updated_at": _fmt_time(
            output["updated_at"] if output else task["updated_at"]),
    }


EXPORT_COLUMNS = [
    ("task_code", "Mã việc"),
    ("hint", "Gợi ý"),
    ("creator", "Người tạo"),
    ("creator_role", "Vai trò"),
    ("channel", "Kênh"),
    ("status", "Trạng thái"),
    ("draft_ref", "Tham chiếu nháp"),
    ("public_url", "Link đã đăng"),
    ("created_at", "Tạo lúc"),
    ("updated_at", "Cập nhật lúc"),
]


def write_csv(rows, path) -> None:
    """CSV with a UTF-8 BOM so Excel opens Vietnamese correctly."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([title for _key, title in EXPORT_COLUMNS])
        for row in rows:
            writer.writerow([row[key] for key, _title in EXPORT_COLUMNS])


def write_xlsx(rows, path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Thống kê"
    ws.append([title for _key, title in EXPORT_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row[key] for key, _title in EXPORT_COLUMNS])
    widths = {"A": 10, "B": 42, "C": 18, "D": 14, "E": 14, "F": 18,
              "G": 34, "H": 34, "I": 17, "J": 17}
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    wb.save(path)


def write_chart_png(data: dict, path) -> None:
    """Two-panel bar chart: outputs by status, outputs by channel
    (both for outputs created in the period)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    status_order = [
        STATUS_DRAFT, STATUS_PENDING, STATUS_APPROVED_WAITING,
        STATUS_PUBLISHED, STATUS_REJECTED, STATUS_CANCELLED,
        STATUS_RETRACTED,
    ]
    status_counts = {}
    channel_counts = {}
    for channel, per_status in data["output_channel_status"].items():
        label = CHANNELS.get(channel, {}).get("label", channel)
        channel_counts[label] = sum(per_status.values())
        for status, n in per_status.items():
            status_counts[status] = status_counts.get(status, 0) + n

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    xs = [STATUS_SHORT[s] for s in status_order]
    ys = [status_counts.get(s, 0) for s in status_order]
    axes[0].bar(xs, ys, color="#4C78A8")
    axes[0].set_title("Đầu ra theo trạng thái")
    axes[0].tick_params(axis="x", rotation=30, labelsize=8)
    for tick in axes[0].get_xticklabels():
        tick.set_ha("right")
    for i, value in enumerate(ys):
        axes[0].text(i, value, str(value), ha="center", va="bottom",
                     fontsize=9)

    channels = [CHANNELS.get(c, {}).get("label", c)
                for c in CHANNEL_ORDER
                if c in data["output_channel_status"]]
    counts = [channel_counts[label] for label in channels]
    axes[1].bar(channels, counts, color="#F58518")
    axes[1].set_title("Đầu ra theo kênh")
    axes[1].tick_params(axis="x", rotation=20, labelsize=8)
    for tick in axes[1].get_xticklabels():
        tick.set_ha("right")
    for i, value in enumerate(counts):
        axes[1].text(i, value, str(value), ha="center", va="bottom",
                     fontsize=9)

    fig.suptitle(f"Thống kê đầu ra — {data['label']}", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def today_bounds_utc(now=None):
    """(start, end) UTC ISO bounds of today in Vietnam time — the
    daily summary's window."""
    _label, start, end = period_range("today", now)
    return start, end
