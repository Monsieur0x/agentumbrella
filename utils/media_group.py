"""
Сборщик медиагрупп Telegram.

Telegram отправляет файлы из одного сообщения (скриншот + документ)
как несколько отдельных Message с одним media_group_id.
Этот модуль буферизует их и вызывает callback один раз со всеми сообщениями.
"""
import asyncio
from collections import defaultdict
from aiogram.types import Message

# media_group_id → список сообщений
_buffers: dict[str, list[Message]] = defaultdict(list)
_COLLECT_DELAY = 0.5  # секунд — ждём пока Telegram пришлёт все части


async def collect_media_group(message: Message) -> list[Message] | None:
    """Буферизует сообщение из медиагруппы.

    Возвращает список всех сообщений группы для ПЕРВОГО вызова (после задержки),
    None — для остальных (они должны быть проигнорированы).
    """
    mg_id = message.media_group_id
    if not mg_id:
        return [message]

    is_first = mg_id not in _buffers or len(_buffers[mg_id]) == 0
    _buffers[mg_id].append(message)

    if not is_first:
        return None

    # Первое сообщение — ждём остальные, потом возвращаем всё
    await asyncio.sleep(_COLLECT_DELAY)

    messages = _buffers.pop(mg_id, [])
    return messages
