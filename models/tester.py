"""
CRUD-операции с тестерами (JSON-хранилище).
"""
from datetime import datetime
from json_store import async_load, async_save, async_update, TESTERS_FILE


async def get_or_create_tester(telegram_id: int, username: str = None, full_name: str = None) -> dict:
    """Получает тестера из базы или создаёт нового. Возвращает dict."""
    key = str(telegram_id)

    def updater(data):
        if key not in data:
            data[key] = {
                "telegram_id": telegram_id,
                "username": username,
                "full_name": full_name,
                "total_points": 0,
                "total_bugs": 0,
                "total_games": 0,
                "warnings_count": 0,
                "is_active": True,
                "created_at": datetime.now().isoformat(),
            }
        else:
            if username:
                data[key]["username"] = username
            if full_name:
                data[key]["full_name"] = full_name
        return data

    data = await async_update(TESTERS_FILE, updater)
    return dict(data[key])


async def get_tester_by_username(username: str) -> dict | None:
    """Ищет тестера по @username (без @)."""
    clean = username.lstrip("@")
    data = await async_load(TESTERS_FILE)
    for t in data.values():
        if t.get("username") and t["username"].lower() == clean.lower():
            return dict(t)
    return None


async def get_tester_by_id(telegram_id: int) -> dict | None:
    data = await async_load(TESTERS_FILE)
    t = data.get(str(telegram_id))
    return dict(t) if t else None


async def get_all_testers(active_only: bool = True) -> list[dict]:
    data = await async_load(TESTERS_FILE)
    testers = list(data.values())
    if active_only:
        testers = [t for t in testers if t.get("is_active", True)]
    testers.sort(key=lambda t: t.get("total_points", 0), reverse=True)
    return [dict(t) for t in testers]


async def update_tester_points(telegram_id: int, delta: int) -> int:
    """Изменяет total_points на delta (+/-). Возвращает новое значение."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            new_val = data[key].get("total_points", 0) + delta
            data[key]["total_points"] = max(0, new_val)
        return data

    data = await async_update(TESTERS_FILE, updater)
    return data.get(key, {}).get("total_points", 0)


async def update_tester_stats(telegram_id: int, bugs: int = 0, games: int = 0):
    """Увеличивает счётчики багов/игр."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            data[key]["total_bugs"] = data[key].get("total_bugs", 0) + bugs
            data[key]["total_games"] = data[key].get("total_games", 0) + games
        return data

    await async_update(TESTERS_FILE, updater)


async def increment_warnings(telegram_id: int) -> int:
    """Увеличивает счётчик предупреждений, возвращает новое значение."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            data[key]["warnings_count"] = data[key].get("warnings_count", 0) + 1
        return data

    data = await async_update(TESTERS_FILE, updater)
    return data.get(key, {}).get("warnings_count", 0)


async def decrement_warnings(telegram_id: int, amount: int = 1) -> int:
    """Уменьшает счётчик предупреждений (не ниже 0), возвращает новое значение."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            new_val = data[key].get("warnings_count", 0) - amount
            data[key]["warnings_count"] = max(0, new_val)
        return data

    data = await async_update(TESTERS_FILE, updater)
    return data.get(key, {}).get("warnings_count", 0)


async def reset_warnings(telegram_id: int) -> int:
    """Сбрасывает все предупреждения тестера до 0."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            data[key]["warnings_count"] = 0
        return data

    await async_update(TESTERS_FILE, updater)
    return 0


async def reset_all_warnings() -> int:
    """Сбрасывает предупреждения у всех тестеров. Возвращает количество затронутых."""
    result = {"count": 0}

    def updater(data):
        count = 0
        for key in data:
            if data[key].get("warnings_count", 0) > 0:
                data[key]["warnings_count"] = 0
                count += 1
        result["count"] = count
        return data

    await async_update(TESTERS_FILE, updater)
    return result["count"]


async def set_tester_active(telegram_id: int, is_active: bool):
    """Устанавливает статус активности тестера."""
    key = str(telegram_id)

    def updater(data):
        if key in data:
            data[key]["is_active"] = is_active
        return data

    await async_update(TESTERS_FILE, updater)
