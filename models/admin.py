"""
CRUD-операции с администраторами.
"""
from database import get_db
from config import OWNER_TELEGRAM_ID


async def init_owner():
    """Добавляет владельца в таблицу админов при старте."""
    if not OWNER_TELEGRAM_ID:
        return
    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO admins (telegram_id, is_owner)
           VALUES (?, 1)""",
        (OWNER_TELEGRAM_ID,)
    )
    await db.commit()


async def is_admin(telegram_id: int) -> bool:
    """Проверяет наличие в таблице admins. Возвращает True и для владельца (он тоже в admins)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM admins WHERE telegram_id = ?", (telegram_id,)
    )
    return await cursor.fetchone() is not None


async def is_owner(telegram_id: int) -> bool:
    return telegram_id == OWNER_TELEGRAM_ID


async def add_admin(telegram_id: int, username: str = None, full_name: str = None) -> bool:
    """Добавляет админа. Возвращает True если успешно."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO admins (telegram_id, username, full_name) VALUES (?, ?, ?)",
            (telegram_id, username, full_name)
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_admin(telegram_id: int) -> bool:
    if telegram_id == OWNER_TELEGRAM_ID:
        return False  # Нельзя удалить владельца
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM admins WHERE telegram_id = ? AND is_owner = 0", (telegram_id,)
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_all_admins() -> list[dict]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM admins ORDER BY is_owner DESC, added_at")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_admin_ids() -> set[int]:
    """Возвращает set telegram_id всех админов и владельца."""
    admins = await get_all_admins()
    return {a["telegram_id"] for a in admins}
