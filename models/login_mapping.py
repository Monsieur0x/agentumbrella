"""
CRUD для привязки игровых логинов к Telegram ID (JSON-хранилище).
"""
from datetime import datetime
from json_store import async_load, async_save, async_update, LOGIN_MAPPING_FILE, PROCESSED_MATCHES_FILE


async def link_login(login: str, telegram_id: int):
    """Привязать игровой логин к тестеру (перезаписывает если уже есть)."""
    def updater(data):
        data[login] = telegram_id
        return data

    await async_update(LOGIN_MAPPING_FILE, updater)


async def unlink_login(login: str):
    """Отвязать игровой логин."""
    data = await async_load(LOGIN_MAPPING_FILE)
    if login in data:
        del data[login]
        await async_save(LOGIN_MAPPING_FILE, data)


async def get_telegram_id_by_login(login: str) -> int | None:
    """Найти telegram_id по игровому логину."""
    data = await async_load(LOGIN_MAPPING_FILE)
    tid = data.get(login)
    return int(tid) if tid is not None else None


async def get_login_by_telegram_id(telegram_id: int) -> str | None:
    """Найти игровой логин по telegram_id."""
    data = await async_load(LOGIN_MAPPING_FILE)
    for login, tid in data.items():
        if int(tid) == telegram_id:
            return login
    return None


async def get_all_logins() -> list[dict]:
    """Все привязки логинов: [{login, telegram_id}, ...]."""
    data = await async_load(LOGIN_MAPPING_FILE)
    result = [{"login": k, "telegram_id": int(v)} for k, v in data.items()]
    result.sort(key=lambda x: x["login"])
    return result


async def is_match_processed(match_id: int) -> bool:
    """Проверить, был ли матч уже обработан."""
    data = await async_load(PROCESSED_MATCHES_FILE)
    return str(match_id) in data


async def mark_match_processed(match_id: int):
    """Пометить матч как обработанный."""
    def updater(data):
        data[str(match_id)] = datetime.now().isoformat()
        return data

    await async_update(PROCESSED_MATCHES_FILE, updater)
