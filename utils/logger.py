"""
Логирование в топик Logs в Telegram.
"""
from datetime import datetime
from aiogram import Bot
from config import GROUP_ID, TOPIC_IDS

# Ссылка на бота устанавливается при старте
_bot: Bot | None = None


def set_bot(bot: Bot):
    global _bot
    _bot = bot


async def log(level: str, text: str):
    """
    Отправляет лог в топик Logs.
    level: INFO, WARN, ERROR, ADMIN
    """
    if not _bot or not TOPIC_IDS.get("logs"):
        print(f"[{level}] {text}")
        return

    icons = {
        "INFO": "🔵",
        "WARN": "🟡",
        "ERROR": "🔴",
        "ADMIN": "🟣",
    }
    icon = icons.get(level, "⚪")
    now = datetime.now().strftime("%H:%M")
    msg = f"{icon} [{level}] {now} — {text}"

    try:
        await _bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=TOPIC_IDS["logs"],
            text=msg,
        )
    except Exception as e:
        print(f"[LOG ERROR] {e}: {msg}")


async def log_info(text: str):
    await log("INFO", text)

async def log_warn(text: str):
    await log("WARN", text)

async def log_error(text: str):
    await log("ERROR", text)

async def log_admin(text: str):
    await log("ADMIN", text)
