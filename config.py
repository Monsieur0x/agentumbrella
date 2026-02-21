"""
Конфигурация бота Umbrella Bot.
Все секреты берутся из .env файла.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int = 0) -> int:
    """Читает int из .env с понятной ошибкой при невалидном значении."""
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"❌ {name}={raw!r} — должно быть целым числом")
        sys.exit(1)


# === Telegram ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_TELEGRAM_ID = _int_env("OWNER_TELEGRAM_ID")

# === Anthropic ===
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if ANTHROPIC_API_KEY:
    print("✅ ANTHROPIC_API_KEY задан")
else:
    print("⚠️ ANTHROPIC_API_KEY не задан!")

# === Weeek ===
WEEEK_API_KEY = os.getenv("WEEEK_API_KEY", "")

# === ID группы ===
GROUP_ID = _int_env("GROUP_ID")

# === ID топиков (thread_id) ===
# Запустите с DEBUG_TOPICS=1 чтобы узнать ID каждого топика
TOPIC_IDS = {
    "general": _int_env("TOPIC_GENERAL", 1),
    "tasks": _int_env("TOPIC_TASKS"),
    "bugs": _int_env("TOPIC_BUGS"),
    "top": _int_env("TOPIC_TOP"),
    "logs": _int_env("TOPIC_LOGS"),
    "logins": _int_env("TOPIC_LOGINS"),
}

# Обратный маппинг: thread_id → название топика
TOPIC_NAMES = {v: k for k, v in TOPIC_IDS.items() if v != 0}

# === Баллы ===
POINTS = {
    "bug_accepted": 3,      # Принятый баг
    "game_played": 1,       # За каждую игру
}

# === Модели Anthropic ===
# Единая модель для всех задач — Haiku (дешёвая и быстрая)
MODEL = os.getenv("MODEL", "claude-haiku-4-5-20251001")

# === Режим отладки ===
DEBUG_TOPICS = os.getenv("DEBUG_TOPICS", "0") == "1"

# === Лимиты агента ===
MAX_TOKENS = _int_env("MAX_TOKENS", 1024)
MAX_TOOL_ROUNDS = _int_env("MAX_TOOL_ROUNDS", 3)
MAX_HISTORY = {
    "tester": 2,
    "admin": 2,
    "owner": 3,
}
MAX_USERS_CACHE = 200
DUPLICATE_CHECK_LIMIT = 50
SEARCH_BUGS_LIMIT = 20

# === Режим работы бота (выбирается владельцем при запуске) ===
BOT_MODE = "active"  # "active" | "observe" | "chat"

# === Модель для свободного чата (режим chat) ===
CHAT_MODEL = os.getenv("CHAT_MODEL", MODEL)

OBSERVE_REPLY = "Пока что я только наблюдаю за вами."

# === Weeek интеграция (переключается владельцем в рантайме) ===
WEEEK_ENABLED = True
