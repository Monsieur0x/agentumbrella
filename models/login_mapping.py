"""
CRUD для таблицы login_mapping — связь игровых логинов с Telegram ID.
"""
from database import get_db


async def link_login(login: str, telegram_id: int):
    """Привязать игровой логин к тестеру (перезаписывает если уже есть)."""
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO login_mapping (login, telegram_id) VALUES (?, ?)",
        (login, telegram_id)
    )
    await db.commit()


async def unlink_login(login: str):
    """Отвязать игровой логин."""
    db = await get_db()
    await db.execute("DELETE FROM login_mapping WHERE login = ?", (login,))
    await db.commit()


async def get_telegram_id_by_login(login: str) -> int | None:
    """Найти telegram_id по игровому логину."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT telegram_id FROM login_mapping WHERE login = ?", (login,)
    )
    row = await cursor.fetchone()
    return row["telegram_id"] if row else None


async def get_login_by_telegram_id(telegram_id: int) -> str | None:
    """Найти игровой логин по telegram_id."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT login FROM login_mapping WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return row["login"] if row else None


async def is_match_processed(match_id: int) -> bool:
    """Проверить, был ли матч уже обработан."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM processed_matches WHERE match_id = ?", (match_id,)
    )
    return await cursor.fetchone() is not None


async def mark_match_processed(match_id: int):
    """Пометить матч как обработанный."""
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO processed_matches (match_id) VALUES (?)",
        (match_id,)
    )
    await db.commit()
