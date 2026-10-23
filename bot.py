#!/usr/bin/env python3
"""Long-polling entry point for the article-workflow Telegram bot.

Run:
    python3 bot.py [--data-dir data]

The bot token is resolved by telegram_client.TelegramClient:
(1) the Muse secure connection ``custom.telegram`` (surrogate), or
(2) the TELEGRAM_BOT_TOKEN environment variable for local simulation.
The token is never printed or stored.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from handlers import BotApp
from storage import Store
from telegram_client import TelegramClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bot Telegram giao việc viết bài")
    parser.add_argument("--data-dir", default=str(Path(__file__).parent / "data"),
                        help="Thư mục dữ liệu (mặc định: ./data cạnh bot.py)")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    store = Store(data_dir / "bot.db")
    client = TelegramClient()
    app = BotApp(store, client, files_dir=data_dir / "files", download_files=True)

    # Register the Menu-button command lists (base list globally, longer
    # lists per user chat for reviewers/admins). Never block startup.
    try:
        app.sync_all_commands()
    except Exception as exc:
        print(f"setMyCommands lỗi (bỏ qua): {exc}", file=sys.stderr)

    offset_raw = store.get_meta("update_offset")
    offset = int(offset_raw) if offset_raw else None
    print("Bot giao việc đang chạy (long polling). Ctrl+C để dừng.", flush=True)

    try:
        while True:
            try:
                updates = client.get_updates(offset=offset, timeout=30)
            except Exception as exc:  # network / API hiccup: back off, retry
                print(f"getUpdates lỗi: {exc}; thử lại sau 5 giây...", file=sys.stderr)
                time.sleep(5)
                continue
            for update in updates or []:
                update_id = update.get("update_id")
                try:
                    app.handle_update(update)
                except Exception as exc:  # one bad update must not kill the bot
                    print(f"Xử lý update {update_id} lỗi: {exc}", file=sys.stderr)
                if update_id is not None:
                    offset = update_id + 1
                    store.set_meta("update_offset", str(offset))
            # Scheduled publishes: fire whatever is due this round.
            try:
                app.fire_due_schedules()
            except Exception as exc:
                print(f"Kiểm tra lịch đăng lỗi: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nĐã dừng bot.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
