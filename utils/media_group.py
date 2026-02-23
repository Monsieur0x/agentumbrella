"""
Сборщик багрепортов из нескольких сообщений.

Telegram может отправить текст, скриншот и файл как отдельные сообщения
(иногда как медиагруппу, иногда полностью раздельно).
Этот модуль буферизует ВСЕ сообщения от одного тестера в топике багов
в течение короткого окна, а затем передаёт их на обработку.
"""
import asyncio
from aiogram.types import Message

# user_id → список сообщений
_buffers: dict[int, list[Message]] = {}
_COLLECT_DELAY = 1.5  # секунд — ждём пока Telegram пришлёт все части


async def collect_bug_messages(message: Message) -> list[Message] | None:
    """Буферизует сообщение от тестера в топике багов.

    Возвращает список всех собранных сообщений для ПЕРВОГО вызова (после задержки),
    None — для остальных (они должны быть проигнорированы вызывающим кодом).
    """
    user_id = message.from_user.id
    is_first = user_id not in _buffers or len(_buffers[user_id]) == 0

    if is_first:
        _buffers[user_id] = []

    _buffers[user_id].append(message)

    if not is_first:
        return None

    # Первое сообщение — ждём остальные, потом возвращаем всё
    await asyncio.sleep(_COLLECT_DELAY)

    messages = _buffers.pop(user_id, [])
    return messages
