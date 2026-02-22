"""
CRUD для настроек бота (JSON-хранилище).
"""
from json_store import async_load, async_update, SETTINGS_FILE
import config


async def get_setting(key: str, default: str = "") -> str:
    """Получить значение настройки по ключу."""
    data = await async_load(SETTINGS_FILE)
    return data.get(key, default)


async def set_setting(key: str, value: str):
    """Записать/обновить настройку."""
    def updater(data):
        data[key] = value
        return data

    await async_update(SETTINGS_FILE, updater)


async def get_points_config() -> dict[str, int]:
    """Возвращает текущие награды. Читает из JSON, фоллбэк на config.POINTS."""
    defaults = config.POINTS
    data = await async_load(SETTINGS_FILE)
    result = {}
    for key, default_val in defaults.items():
        raw = data.get(f"points_{key}", "")
        if raw and str(raw).isdigit():
            result[key] = int(raw)
        else:
            result[key] = default_val
    return result


async def set_points_value(reward_type: str, value: int):
    """Установить количество баллов за награду."""
    await set_setting(f"points_{reward_type}", str(value))
