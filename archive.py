#!/usr/bin/env python3
"""Google Drive archive + restore for task material files (TD-0024).

One shared engine for every trigger, so the Drive code is never
duplicated:

- (b) after ALL outputs of a task are PUBLISHED: ``maybe_archive_task``
  (called from ``botctl set-output-published``) spawns the worker;
- (d) a task waiting in PENDING / APPROVED_WAITING for over 48h:
  ``scan_waiting_tasks`` (called from the webapp's background loop,
  the same thread that sends the 20:00 summary) spawns the worker;
- (e) restore: ``maybe_restore_task`` (publish/schedule entry points
  on both surfaces, plus the manual button / botctl command) spawns
  the worker, and ``restore_task_files`` does the download.

The worker (``archive_worker.py``) is a detached process because a
1GB upload/download to Drive takes minutes and must never block the
request or command that triggered it. Uploads are verified per file
(Drive file id returned) before the local copy is deleted; a Drive
failure keeps every local file, audits ``files_archive_failed`` and
never deletes. Runs are idempotent: a file already recorded in
``archived_files`` is never uploaded twice, and a second trigger
with no local files left is a no-op.

Drive access goes through the connected Google Drive CLI
(``hatch_gws_cli``) — the same credential the agent's Drive
connector uses. The CLI is run from a detached process, so no
interactive confirmation is involved (verified 25/10: a detached
``+upload`` succeeded on its own).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import (STATUS_APPROVED_WAITING, STATUS_CANCELLED,
                     STATUS_PENDING, STORAGE_QUOTA_BYTES, format_ict,
                     format_ict_short, ict_now)

#: Google Drive CLI (the connected Drive account).
DRIVE_CLI = "/opt/hatch/bin/hatch_gws_cli"
#: Upload attempts per file before the run gives up (and keeps locals).
UPLOAD_ATTEMPTS = 3
#: A task waiting this long in PENDING / APPROVED_WAITING gets its
#: files archived (TD-0024 (d)).
WAITING_ARCHIVE_AFTER = timedelta(hours=48)
#: Audit actions the archive/restore runs write (target_type "task").
AUDIT_ARCHIVED = "files_archived"
AUDIT_ARCHIVE_FAILED = "files_archive_failed"
AUDIT_RESTORED = "files_restored"
AUDIT_RESTORE_FAILED = "files_restore_failed"

_ARCHIVE_FOLDER_NAME = "Giao Việc IAICT — Bản gốc"


class ArchiveError(Exception):
    """A Drive (or local) step of an archive/restore run failed."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw) -> datetime:
    """Parse an ISO timestamp from the DB; naive values are UTC."""
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return _utcnow()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _log(message: str, log=None) -> None:
    # TD-0024 correction (25/10): every time a user reads is Vietnam
    # time — log lines carry an ICT timestamp, never a UTC one.
    if log is not None:
        try:
            log(f"[{format_ict_short(ict_now())}] {message}")
        except Exception:
            pass


def default_config_path() -> Path:
    """data/drive_archive.json next to this module (bot data dir)."""
    return Path(__file__).resolve().parent / "data" / "drive_archive.json"


def load_config(config_path=None) -> dict:
    path = Path(config_path) if config_path else default_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("tasks", {})
    return data


def save_config(config: dict, config_path=None) -> None:
    """Atomic write (tmp + rename), mode 600: the folder ids are
    the owner's Drive layout, never committed anywhere."""
    path = Path(config_path) if config_path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def files_dir_for_store(store) -> Path | None:
    """data/files next to the store's bot.db, or None when the store
    is in-memory / has no file path (unit tests)."""
    db_path = getattr(store, "db_path", None)
    if not db_path or db_path == ":memory:":
        return None
    return Path(db_path).parent / "files"


def task_material_files(files_dir, code: str) -> list:
    """Top-level material files of a task: (name, path, size).

    Subdirectories (e.g. video-drafts/, the ffmpeg draft renders)
    are derived artifacts, not materials, and are never archived
    or deleted by this module."""
    directory = Path(files_dir) / code
    if not directory.is_dir():
        return []
    found = []
    for entry in sorted(directory.iterdir(), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith("."):
            found.append((entry.name, entry, entry.stat().st_size))
    return found


def storage_usage_bytes(root) -> int:
    """Total bytes under a directory tree (TD-0024 (f): the 9GB
    quota counts the whole data/files/ tree, tmp chunks included)."""
    root = Path(root)
    if not root.is_dir():
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += Path(dirpath, name).stat().st_size
            except OSError:
                continue
    return total


def would_exceed_quota(files_dir, extra_bytes: int, quota=None,
                       used=None) -> bool:
    """True when adding ``extra_bytes`` under ``files_dir`` would
    push the 9GB total over the quota (TD-0025 test injection
    point: pass a small ``quota`` instead of filling a real 9GB;
    pass ``used`` when the caller already measured usage, e.g.
    before downloading a file whose bytes are not on disk yet)."""
    limit = STORAGE_QUOTA_BYTES if quota is None else quota
    if used is None:
        used = storage_usage_bytes(files_dir)
    return used + int(extra_bytes) > limit


def direct_to_drive(store, files_dir, code: str, name: str, local_path,
                    size: int, uploader=None, config_path=None,
                    log=None) -> dict:
    """TD-0025: one file that arrived while the 9GB quota is full
    goes straight to Drive — no local copy is kept afterwards.

    Reuses the TD-0024 upload + record path (per-task Drive
    subfolder, retries, the same ``archived_files`` row the task
    detail renders as a Drive link), so the TD-0024 restore flow
    picks the file up exactly like an archived one. This function
    never deletes the local file: the caller removes it only after
    this returns "done", and keeps it (local, over quota for now)
    on "failed".

    Returns {"status": "done" | "failed", ...}."""
    if store.archived_file(code, name) is not None:
        # A previous run already put this file on Drive: never
        # upload twice.
        return {"status": "done", "already": True}
    try:
        folder_id = ensure_task_folder(config_path, code)
        file_id, link = _upload_with_retries(
            local_path, folder_id, name, uploader, log)
    except ArchiveError as exc:
        store.audit(None, AUDIT_ARCHIVE_FAILED, "task", code,
                    detail=f"reason=direct_to_drive file={name} "
                           f"error={exc} lúc {format_ict(ict_now())}")
        _log(f"[{code}] gửi thẳng lên Drive lỗi ở {name}: {exc}", log)
        return {"status": "failed", "error": str(exc)}
    store.record_archived_file(code, name, file_id, link, size)
    store.audit(None, AUDIT_ARCHIVED, "task", code,
                detail=f"reason=direct_to_drive file={name} "
                       f"lúc {format_ict(ict_now())}")
    _log(f"[{code}] đã gửi thẳng {name} lên Drive (kho đầy).", log)
    return {"status": "done", "drive_file_id": file_id, "link": link}


# ------------------------------------------------------------ Drive CLI
def _run_cli(argv, runner=None, timeout=None):
    """Run the Drive CLI; returns parsed JSON stdout ({} when the
    command prints nothing). Raises ArchiveError on failure."""
    if runner is not None:
        returncode, stdout, stderr = runner(argv)
    else:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ArchiveError(f"Drive hết thời gian chờ: {' '.join(argv[:3])}")
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    if returncode != 0:
        raise ArchiveError(
            f"Drive lỗi ({' '.join(argv[:3])}): {(stderr or stdout or '').strip()[:300]}")
    try:
        parsed = json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        raise ArchiveError("Drive trả về kết quả không đọc được.")
    return parsed if isinstance(parsed, dict) else {}


def _drive_file_link(file_id: str, runner=None):
    """webViewLink for a Drive file id (None when it cannot be
    read — the archive still counts, the UI just shows no link)."""
    try:
        out = _run_cli([
            DRIVE_CLI, "drive", "files", "get", "--params",
            json.dumps({"fileId": file_id,
                        "fields": "id,name,webViewLink"}),
        ], runner, timeout=120)
    except ArchiveError:
        return None
    return out.get("webViewLink")


def drive_upload(local_path, parent_id: str, name: str,
                 runner=None):
    """Upload one local file to a Drive folder.

    Returns (drive_file_id, web_view_link). The caller deletes the
    local copy only after this returns, so a failure here never
    loses data."""
    out = _run_cli([
        DRIVE_CLI, "drive", "+upload", str(local_path),
        "--parent", parent_id, "--name", name,
    ], runner, timeout=7200)
    file_id = out.get("id")
    if not file_id:
        raise ArchiveError(f"Drive không trả về file id cho {name}.")
    return file_id, _drive_file_link(file_id, runner)


def drive_download(file_id: str, dest_path, runner=None) -> None:
    """Download one Drive file to dest_path (overwrites). Raises
    ArchiveError on failure; the caller keeps the archived row."""
    _run_cli([
        DRIVE_CLI, "drive", "files", "get", "--params",
        json.dumps({"fileId": file_id, "alt": "media"}),
        "--output", str(dest_path),
    ], runner, timeout=7200)
    if not Path(dest_path).is_file():
        raise ArchiveError(f"Drive không tải được file {file_id}.")


def ensure_task_folder(config_path, code: str, runner=None) -> str:
    """Per-task subfolder under the archive root; its id is cached
    in drive_archive.json so later runs never create duplicates."""
    path = Path(config_path) if config_path else default_config_path()
    config = load_config(path)
    root_id = config.get("folder_id")
    if not root_id:
        raise ArchiveError(
            "Chưa có thư mục lưu trữ trên Drive "
            "(thiếu folder_id trong drive_archive.json).")
    tasks = config.setdefault("tasks", {})
    if code in tasks:
        return tasks[code]
    out = _run_cli([
        DRIVE_CLI, "drive", "files", "create", "--params",
        json.dumps({"ignoreDefaultVisibility": True}), "--json",
        json.dumps({"name": code,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [root_id]}),
    ], runner, timeout=120)
    folder_id = out.get("id")
    if not folder_id:
        raise ArchiveError(f"Không tạo được thư mục Drive cho {code}.")
    tasks[code] = folder_id
    save_config(config, path)
    return folder_id


# ------------------------------------------------------------ core runs
def _upload_with_retries(local_path, folder_id, name, uploader,
                         log=None):
    """Upload one file, retrying a few times; returns
    (drive_file_id, web_view_link)."""
    last_error = None
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            if uploader is not None:
                return uploader(local_path, folder_id, name)
            return drive_upload(local_path, folder_id, name)
        except Exception as exc:  # noqa: BLE001 - retry, then report
            last_error = exc
            _log(f"  tải lên {name} lần {attempt} lỗi: {exc}", log)
            if attempt < UPLOAD_ATTEMPTS:
                time.sleep(5 * attempt)
    raise ArchiveError(str(last_error))


def _download_with_retries(file_id, dest_path, downloader, log=None):
    last_error = None
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            if downloader is not None:
                downloader(file_id, dest_path)
            else:
                drive_download(file_id, dest_path)
            return
        except Exception as exc:  # noqa: BLE001 - retry, then report
            last_error = exc
            _log(f"  tải về {file_id} lần {attempt} lỗi: {exc}", log)
            if attempt < UPLOAD_ATTEMPTS:
                time.sleep(5 * attempt)
    raise ArchiveError(str(last_error))


def _remove_quietly(path, log=None) -> bool:
    try:
        Path(path).unlink()
        return True
    except OSError as exc:
        _log(f"  không xóa được {path}: {exc}", log)
        return False


def archive_task_files(store, files_dir, code: str, reason: str,
                       uploader=None, config_path=None, log=None) -> dict:
    """Shared archive run (TD-0024 (b) + (d)).

    Uploads every local material file of the task to its Drive
    subfolder, records each in ``archived_files`` and only then
    deletes the local copy. A file already recorded is never
    re-uploaded — its local copy is just dropped. On the first
    failed file the run stops, audits ``files_archive_failed`` and
    keeps every remaining local file.

    Returns {"status": "done" | "failed" | "nothing_to_archive" |
    "no_task", "files": [names archived-or-dropped this run]}.
    """
    task = store.get_task(code)
    if task is None:
        return {"status": "no_task", "files": []}
    files = task_material_files(files_dir, code)
    if not files:
        return {"status": "nothing_to_archive", "files": []}
    folder_id = None
    done, failed, delete_errors = [], None, []
    for name, path, size in files:
        row = store.archived_file(code, name)
        if row is not None:
            # An earlier partial run already put this file on Drive:
            # never upload twice, just drop the local copy.
            if not _remove_quietly(path, log):
                delete_errors.append(name)
            done.append(name)
            continue
        if folder_id is None:
            folder_id = ensure_task_folder(config_path, code)
        try:
            file_id, link = _upload_with_retries(
                path, folder_id, name, uploader, log)
        except ArchiveError as exc:
            failed = {"file": name, "error": str(exc)}
            break
        store.record_archived_file(code, name, file_id, link, size)
        if not _remove_quietly(path, log):
            delete_errors.append(name)
        done.append(name)
    if failed is not None:
        store.audit(None, AUDIT_ARCHIVE_FAILED, "task", code,
                    detail=f"reason={reason} file={failed['file']} "
                           f"error={failed['error']} "
                           f"lúc {format_ict(ict_now())}")
        _log(f"[{code}] lưu trữ lỗi ở {failed['file']}: "
             f"{failed['error']}", log)
        return {"status": "failed", "files": done, "failed": failed}
    detail = (f"reason={reason} files={len(done)} "
              f"lúc {format_ict(ict_now())}")
    if delete_errors:
        detail += f" delete_errors={','.join(delete_errors)}"
    store.audit(None, AUDIT_ARCHIVED, "task", code, detail=detail)
    _log(f"[{code}] đã lưu trữ {len(done)} file lên Drive "
         f"(lý do: {reason}).", log)
    return {"status": "done", "files": done}


def restore_task_files(store, files_dir, code: str, downloader=None,
                       log=None) -> dict:
    """Shared restore run (TD-0024 (e)): download every archived
    file of the task back to data/files/<code>/, verify its size,
    then drop its archived row — the task detail renders it as a
    normal local file again. A failed download keeps the row (and
    the audit) so the restore can be retried.

    Returns {"status": "done" | "failed" | "nothing_to_restore",
    "files": [names restored this run]}.
    """
    rows = store.archived_files_for_task(code)
    if not rows:
        return {"status": "nothing_to_restore", "files": []}
    directory = Path(files_dir) / code
    directory.mkdir(parents=True, exist_ok=True)
    done, failed = [], None
    for row in rows:
        name = row["filename"]
        dest = directory / name
        if dest.is_file():
            # A fresh local copy already exists (re-uploaded): the
            # archived row is redundant — drop it, keep the file.
            store.delete_archived_file(code, name)
            done.append(name)
            continue
        tmp = dest.with_name(dest.name + ".download")
        try:
            _download_with_retries(row["drive_file_id"], tmp,
                                   downloader, log)
            if row["size"] and tmp.stat().st_size != row["size"]:
                raise ArchiveError(
                    f"kích thước tải về ({tmp.stat().st_size}) khác "
                    f"bản gốc ({row['size']}).")
            os.replace(tmp, dest)
        except (ArchiveError, OSError) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            failed = {"file": name, "error": str(exc)}
            break
        store.delete_archived_file(code, name)
        done.append(name)
    if failed is not None:
        store.audit(None, AUDIT_RESTORE_FAILED, "task", code,
                    detail=f"file={failed['file']} "
                           f"error={failed['error']} "
                           f"lúc {format_ict(ict_now())}")
        _log(f"[{code}] khôi phục lỗi ở {failed['file']}: "
             f"{failed['error']}", log)
        return {"status": "failed", "files": done, "failed": failed}
    store.audit(None, AUDIT_RESTORED, "task", code,
                detail=f"files={len(done)} lúc {format_ict(ict_now())}")
    _log(f"[{code}] đã tải lại {len(done)} file từ Drive.", log)
    return {"status": "done", "files": done}


# ------------------------------------------------------- trigger checks
def has_outstanding_publish_request(store, output) -> bool:
    """True when a publish order for this output is still live.

    Facebook: the picker's own pending places (a live request minus
    done/failed; a merely scheduled post is NOT live). Other
    channels: a publish_requested audit newer than the latest
    completion/abort. An output that is not APPROVED_WAITING can
    never hold a live request."""
    if output["status"] != STATUS_APPROVED_WAITING:
        return False
    if output["channel"] == "facebook":
        _locked, pending = store.fb_locked_places(output)
        return bool(pending)
    requested = store.latest_audit_id(output["id"], "publish_requested")
    if requested is None:
        return False
    done = store.latest_audit_id(output["id"], "set_output_published")
    aborted = store.latest_audit_id(output["id"], "publish_aborted")
    return requested > (done or 0) and requested > (aborted or 0)


def waiting_since(store, output) -> datetime:
    """When the output entered its current waiting state: the audit
    that moved it there (submit → PENDING, approve → APPROVED_
    WAITING), falling back to the output's created_at."""
    entry_actions = {
        STATUS_PENDING: ("submit_output",),
        STATUS_APPROVED_WAITING: ("approve_output",),
    }
    latest = None
    for action in entry_actions.get(output["status"], ()):
        row = store.latest_audit(output["id"], action)
        if row is not None and (latest is None or row["id"] > latest["id"]):
            latest = row
    raw = latest["created_at"] if latest is not None \
        else output["created_at"]
    return _parse_ts(raw)


def task_waiting_too_long(store, code: str, now=None) -> bool:
    """True when any live output of the task has waited in PENDING /
    APPROVED_WAITING for over 48h."""
    now = now or _utcnow()
    for output in store.list_outputs(code):
        if output["status"] in (STATUS_PENDING, STATUS_APPROVED_WAITING):
            if now - waiting_since(store, output) > WAITING_ARCHIVE_AFTER:
                return True
    return False


def scan_waiting_tasks(store, files_dir, now=None, spawn=True,
                       uploader=None, config_path=None, log=None) -> list:
    """TD-0024 (d): archive the files of every task that has waited
    over 48h with no live publish request. Tasks with no local files
    left, or with no waiting output, are skipped. Returns the codes
    acted on. With spawn=True (production) each archive runs in a
    detached worker; with spawn=False (tests/probes) it runs inline.
    """
    now = now or _utcnow()
    acted = []
    for task in store.list_tasks(limit=100000):
        code = task["code"]
        if not task_material_files(files_dir, code):
            continue
        outputs = [o for o in store.list_outputs(code)
                   if o["status"] != STATUS_CANCELLED]
        if not outputs:
            continue
        if any(has_outstanding_publish_request(store, o)
               for o in outputs):
            continue
        if not task_waiting_too_long(store, code, now):
            continue
        if spawn:
            spawn_worker("archive", code, reason="waiting_48h")
        else:
            archive_task_files(store, files_dir, code,
                               reason="waiting_48h", uploader=uploader,
                               config_path=config_path, log=log)
        acted.append(code)
    return acted


# ------------------------------------------------------------- spawning
def spawn_worker(mode: str, code: str, reason: str | None = None) -> bool:
    """Detach archive_worker.py (mode "archive"/"restore") so a big
    Drive transfer never blocks the caller. Stdout/stderr go to
    data/archive.log. Returns False only when the spawn itself
    fails. Tests patch this to run the worker inline."""
    worker = Path(__file__).resolve().parent / "archive_worker.py"
    log_path = Path(__file__).resolve().parent / "data" / "archive.log"
    argv = [sys.executable, str(worker), mode, code]
    if reason:
        argv.append(reason)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_path, "ab")
        try:
            subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        finally:
            fh.close()
    except OSError as exc:
        print(f"Cảnh báo: không chạy được archive worker: {exc}",
              file=sys.stderr)
        return False
    return True


def maybe_archive_task(store, files_dir, code: str) -> bool:
    """TD-0024 (b) trigger: all live outputs PUBLISHED and local
    files still present -> spawn the archive worker. Returns True
    when a worker was spawned."""
    task = store.get_task(code)
    if task is None:
        return False
    outputs = [o for o in store.list_outputs(code)
               if o["status"] != STATUS_CANCELLED]
    if not outputs or not all(o["status"] == "PUBLISHED"
                              for o in outputs):
        return False
    if not task_material_files(files_dir, code):
        return False
    return spawn_worker("archive", code, reason="published")


def maybe_restore_task(store, files_dir, code: str) -> bool:
    """TD-0024 (e) trigger: the task has archived files -> spawn
    the restore worker so the files are back before Muse acts.
    Returns True when a worker was spawned. files_dir is accepted
    for call-site symmetry; the worker derives it from bot.db."""
    if not store.archived_files_for_task(code):
        return False
    return spawn_worker("restore", code)
