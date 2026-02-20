"""
CRUD-операции с тестерами.
"""
from database import get_db


async def get_or_create_tester(telegram_id: int, username: str = None, full_name: str = None) -> dict:
    """Получает тестера из базы или создаёт нового. Возвращает dict."""
    db = await get_db()
    # INSERT OR IGNORE безопасен при конкурентных запросах —
    # не упадёт с UNIQUE constraint если тестер уже есть
    await db.execute(
        "INSERT OR IGNORE INTO testers (telegram_id, username, full_name) VALUES (?, ?, ?)",
        (telegram_id, username, full_name)
    )
    # Обновляем username/full_name если переданы
    if username or full_name:
        await db.execute(
            "UPDATE testers SET username = COALESCE(?, username), full_name = COALESCE(?, full_name) WHERE telegram_id = ?",
            (username, full_name, telegram_id)
        )
    await db.commit()
    cursor = await db.execute(
        "SELECT * FROM testers WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return dict(row)


async def get_tester_by_username(username: str) -> dict | None:
    """Ищет тестера по @username (без @)."""
    clean = username.lstrip("@")
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM testers WHERE LOWER(username) = LOWER(?)", (clean,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_tester_by_id(telegram_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM testers WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_all_testers(active_only: bool = True) -> list[dict]:
    db = await get_db()
    q = "SELECT * FROM testers"
    if active_only:
        q += " WHERE is_active = 1"
    q += " ORDER BY total_points DESC"
    cursor = await db.execute(q)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_tester_points(telegram_id: int, delta: int) -> int:
    """Изменяет total_points на delta (+/-). Возвращает новое значение."""
    db = await get_db()
    await db.execute(
        "UPDATE testers SET total_points = CASE WHEN total_points + ? < 0 THEN 0 ELSE total_points + ? END WHERE telegram_id = ?",
        (delta, delta, telegram_id)
    )
    await db.commit()
    cursor = await db.execute(
        "SELECT total_points FROM testers WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return row["total_points"] if row else 0


async def update_tester_stats(telegram_id: int, bugs: int = 0, crashes: int = 0, games: int = 0):
    """Увеличивает счётчики багов/крашей/игр."""
    db = await get_db()
    await db.execute(
        """UPDATE testers SET
            total_bugs = total_bugs + ?,
            total_crashes = total_crashes + ?,
            total_games = total_games + ?
        WHERE telegram_id = ?""",
        (bugs, crashes, games, telegram_id)
    )
    await db.commit()


async def increment_warnings(telegram_id: int) -> int:
    """Увеличивает счётчик предупреждений, возвращает новое значение."""
    db = await get_db()
    await db.execute(
        "UPDATE testers SET warnings_count = warnings_count + 1 WHERE telegram_id = ?",
        (telegram_id,)
    )
    await db.commit()
    cursor = await db.execute(
        "SELECT warnings_count FROM testers WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return row["warnings_count"] if row else 0


async def decrement_warnings(telegram_id: int, amount: int = 1) -> int:
    """Уменьшает счётчик предупреждений (не ниже 0), возвращает новое значение."""
    db = await get_db()
    await db.execute(
        "UPDATE testers SET warnings_count = CASE WHEN warnings_count - ? < 0 THEN 0 ELSE warnings_count - ? END WHERE telegram_id = ?",
        (amount, amount, telegram_id)
    )
    await db.commit()
    cursor = await db.execute(
        "SELECT warnings_count FROM testers WHERE telegram_id = ?", (telegram_id,)
    )
    row = await cursor.fetchone()
    return row["warnings_count"] if row else 0


async def reset_warnings(telegram_id: int) -> int:
    """Сбрасывает все предупреждения тестера до 0."""
    db = await get_db()
    await db.execute(
        "UPDATE testers SET warnings_count = 0 WHERE telegram_id = ?",
        (telegram_id,)
    )
    await db.commit()
    return 0


async def reset_all_warnings() -> int:
    """Сбрасывает предупреждения у всех тестеров. Возвращает количество затронутых."""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE testers SET warnings_count = 0 WHERE warnings_count > 0"
    )
    await db.commit()
    return cursor.rowcount
