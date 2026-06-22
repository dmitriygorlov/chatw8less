from aiogram import types
from aiogram.filters import BaseFilter
from aiogram.dispatcher.middlewares.base import BaseMiddleware
import logging
from datetime import datetime
import os
from bot.config import LOG_DIR
from bot.db import get_or_create_user_for_telegram


class AllowedUserFilter(BaseFilter):
    def __init__(self, allowed_ids: set[int]):
        self.allowed_ids = allowed_ids

    async def __call__(self, message: types.Message) -> bool:
        return message.from_user.id in self.allowed_ids


class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data):
        user_id = event.from_user.id if event.from_user else "unknown"
        if event.from_user:
            display_name = (
                event.from_user.full_name
                or event.from_user.username
                or str(event.from_user.id)
            )
            get_or_create_user_for_telegram(
                event.from_user.id,
                display_name=display_name,
                language_code=getattr(event.from_user, "language_code", None) or "en",
            )
        text = event.text or ""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line_in = f"{timestamp} - IN: {text}\n"
        logging.info(f"Incoming from {user_id}: {text}")

        # Логирование в отдельный файл для пользователя
        user_log_dir = os.path.join(LOG_DIR, "users")
        os.makedirs(user_log_dir, exist_ok=True)
        user_log_file = os.path.join(user_log_dir, f"{user_id}.log")
        with open(user_log_file, "a", encoding="utf-8") as f:
            f.write(log_line_in)

        result = await handler(event, data)

        if result and hasattr(result, "text"):
            out_text = result.text
            log_line_out = f"{timestamp} - OUT: {out_text}\n"
            with open(user_log_file, "a", encoding="utf-8") as f:
                f.write(log_line_out)
            logging.info(f"Outgoing to {user_id}: {out_text}")

        return result
