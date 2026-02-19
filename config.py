"""
Конфигурация бота QA Manager.
Все секреты берутся из .env файла.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === Telegram ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0"))

# === Anthropic ===
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if ANTHROPIC_API_KEY:
    print(f"🔑 Anthropic ключ: {ANTHROPIC_API_KEY[:8]}...{ANTHROPIC_API_KEY[-4:]}")
else:
    print("⚠️ ANTHROPIC_API_KEY не задан!")

# === Weeek ===
WEEEK_API_KEY = os.getenv("WEEEK_API_KEY", "")

# === ID группы ===
GROUP_ID = int(os.getenv("GROUP_ID", "0"))

# === ID топиков (thread_id) ===
# Запустите с DEBUG_TOPICS=1 чтобы узнать ID каждого топика
TOPIC_IDS = {
    "general": int(os.getenv("TOPIC_GENERAL", "1")),
    "tasks": int(os.getenv("TOPIC_TASKS", "0")),
    "bugs": int(os.getenv("TOPIC_BUGS", "0")),
    "crashes": int(os.getenv("TOPIC_CRASHES", "0")),
    "reports": int(os.getenv("TOPIC_REPORTS", "0")),
    "top": int(os.getenv("TOPIC_TOP", "0")),
    "logs": int(os.getenv("TOPIC_LOGS", "0")),
}

# Обратный маппинг: thread_id → название топика
TOPIC_NAMES = {v: k for k, v in TOPIC_IDS.items() if v != 0}

# === Баллы ===
POINTS = {
    "bug_accepted": 3,      # Принятый баг
    "crash_accepted": 4,    # Принятый краш
    "game_played": 1,       # За каждую игру (из скриншота)
}

# === Модели Anthropic ===
MODEL_AGENT = "claude-opus-4-6"      # Мозг агента (function calling)
MODEL_CHEAP = "claude-haiku-4-5"     # Дешёвые задачи (формат багов, дубли)
MODEL_VISION = "claude-haiku-4-5"    # Анализ скриншотов (Claude поддерживает vision)

# === Режим отладки ===
DEBUG_TOPICS = os.getenv("DEBUG_TOPICS", "0") == "1"
