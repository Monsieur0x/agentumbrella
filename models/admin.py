"""
CRUD-операции с администраторами (JSON-хранилище).
"""
from datetime import datetime
from json_store import async_load, async_save, async_update, ADMINS_FILE
from config import OWNER_TELEGRAM_ID


async def init_owner():
    """Добавляет владельца в JSON при старте."""
    if not OWNER_TELEGRAM_ID:
        return
    key = str(OWNER_TELEGRAM_ID)

    def updater(data):
        if key not in data:
            data[key] = {
                "telegram_id": OWNER_TELEGRAM_ID,
                "username": None,
                "full_name": None,
                "is_owner": True,
                "added_at": datetime.now().isoformat(),
            }
        else:
            data[key]["is_owner"] = True
        return data

    await async_update(ADMINS_FILE, updater)


async def is_admin(telegram_id: int) -> bool:
    """Проверяет наличие в admins. Возвращает True и для владельца."""
    data = await async_load(ADMINS_FILE)
    return str(telegram_id) in data


async def is_owner(telegram_id: int) -> bool:
    return telegram_id == OWNER_TELEGRAM_ID


async def add_admin(telegram_id: int, username: str = None, full_name: str = None) -> bool:
    """Добавляет админа. Возвращает True если успешно."""
    key = str(telegram_id)

    def updater(data):
        if key not in data:
            data[key] = {
                "telegram_id": telegram_id,
                "username": username,
                "full_name": full_name,
                "is_owner": False,
                "added_at": datetime.now().isoformat(),
            }
        return data

    await async_update(ADMINS_FILE, updater)
    return True


async def remove_admin(telegram_id: int) -> bool:
    if telegram_id == OWNER_TELEGRAM_ID:
        return False
    key = str(telegram_id)
    data = await async_load(ADMINS_FILE)
    if key in data and not data[key].get("is_owner", False):
        del data[key]
        await async_save(ADMINS_FILE, data)
        return True
    return False


async def get_all_admins() -> list[dict]:
    data = await async_load(ADMINS_FILE)
    admins = list(data.values())
    admins.sort(key=lambda a: (not a.get("is_owner", False), a.get("added_at", "")))
    return [dict(a) for a in admins]


async def get_admin_ids() -> set[int]:
    """Возвращает set telegram_id всех админов и владельца."""
    data = await async_load(ADMINS_FILE)
    return {v["telegram_id"] for v in data.values()}
