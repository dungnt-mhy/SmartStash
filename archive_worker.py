#!/usr/bin/env python3
"""Background worker for TD-0024 Drive archive/restore.

Detached by archive.spawn_worker() so a big Drive transfer never
blocks the publish request, the 48h scan or the restore button that
triggered it. Prints a one-line progress log (data/archive.log)
and a JSON result on stdout.

Usage:
    python3 archive_worker.py archive <task_code> [reason]
    python3 archive_worker.py restore <task_code>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive  # noqa: E402
from storage import Store  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"


def _log(message: str) -> None:
    print(message, flush=True)


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in ("archive", "restore"):
        print("Dùng: archive_worker.py archive|restore <task_code> "
              "[reason]", file=sys.stderr)
        return 2
    mode, code = argv[1], argv[2]
    reason = argv[3] if len(argv) > 3 else "manual"
    store = Store(DATA_DIR / "bot.db")
    files_dir = DATA_DIR / "files"
    try:
        if mode == "archive":
            result = archive.archive_task_files(
                store, files_dir, code, reason, log=_log)
        else:
            result = archive.restore_task_files(
                store, files_dir, code, log=_log)
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] in (
        "done", "nothing_to_archive", "nothing_to_restore") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
