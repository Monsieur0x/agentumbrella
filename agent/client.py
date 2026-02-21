"""
Общий Claude API клиент с throttle для защиты от rate limit.
Используется из brain.py и services/duplicate_checker.py.
"""
import time
import asyncio
import anthropic
from config import ANTHROPIC_API_KEY

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# === Защита от перерасхода лимита ===
MIN_INTERVAL = 1.0
_last_request_time = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle():
    """Ждём если запросы слишком частые."""
    global _last_request_time
    async with _throttle_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_INTERVAL:
            await asyncio.sleep(MIN_INTERVAL - elapsed)
        _last_request_time = time.time()


async def call_claude(**kwargs):
    """Обёртка над client.messages.create с throttle."""
    await _throttle()
    return await client.messages.create(**kwargs)
