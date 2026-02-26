"""
CRUD-операции с трекерами (JSON-хранилище).
Трекер — тестер с правом выдавать баллы, но без полных админских прав.
"""
from datetime import datetime
from json_store import async_load, async_save, async_update, TRACKERS_FILE


async def is_tracker(telegram_id: int) -> bool:
    """Проверяет наличие в trackers."""
    data = await async_load(TRACKERS_FILE)
    return str(telegram_id) in data


async def add_tracker(telegram_id: int, username: str = None, full_name: str = None) -> bool:
    """Добавляет трекера. Возвращает True если успешно."""
    key = str(telegram_id)

    def updater(data):
        if key not in data:
            data[key] = {
                "telegram_id": telegram_id,
                "username": username,
                "full_name": full_name,
                "added_at": datetime.now().isoformat(),
            }
        return data

    await async_update(TRACKERS_FILE, updater)
    return True


async def remove_tracker(telegram_id: int) -> bool:
    """Удаляет трекера. Возвращает True если был удалён."""
    key = str(telegram_id)
    data = await async_load(TRACKERS_FILE)
    if key in data:
        del data[key]
        await async_save(TRACKERS_FILE, data)
        return True
    return False


async def get_all_trackers() -> list[dict]:
    """Возвращает список всех трекеров."""
    data = await async_load(TRACKERS_FILE)
    trackers = list(data.values())
    trackers.sort(key=lambda t: t.get("added_at", ""))
    return [dict(t) for t in trackers]


async def get_tracker_ids() -> set[int]:
    """Возвращает set telegram_id всех трекеров."""
    data = await async_load(TRACKERS_FILE)
    return {v["telegram_id"] for v in data.values()}
