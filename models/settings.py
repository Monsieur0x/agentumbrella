"""
CRUD для таблицы settings — хранение настроек бота (награды и т.д.).
"""
from database import get_db
import config


async def get_setting(key: str, default: str = "") -> str:
    """Получить значение настройки по ключу."""
    db = await get_db()
    cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str):
    """Записать/обновить настройку."""
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    await db.commit()


async def get_points_config() -> dict[str, int]:
    """Возвращает текущие награды. Читает из БД, фоллбэк на config.POINTS."""
    defaults = config.POINTS
    result = {}
    for key, default_val in defaults.items():
        raw = await get_setting(f"points_{key}", "")
        if raw and raw.isdigit():
            result[key] = int(raw)
        else:
            result[key] = default_val
    return result


async def set_points_value(reward_type: str, value: int):
    """Установить количество баллов за награду.
    reward_type: bug_accepted / game_played
    """
    await set_setting(f"points_{reward_type}", str(value))
