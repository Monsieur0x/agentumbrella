"""
Общий Claude API клиент с throttle и retry для защиты от rate limit.
Используется из brain.py и tool_executor.py.
"""
import time
import asyncio
import anthropic
import httpx
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


async def call_claude(max_retries: int = 3, **kwargs):
    """Обёртка над client.messages.create с throttle и retry."""
    for attempt in range(max_retries):
        await _throttle()
        try:
            return await client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 500, 529) and attempt < max_retries - 1:
                wait = 2 ** attempt * 2  # 2s, 4s, 8s
                print(f"[CLAUDE-CLIENT] {e.status_code}, retry {attempt + 1} через {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 2
                print(f"[CLAUDE-CLIENT] Network error ({type(e).__name__}), retry {attempt + 1} через {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise
