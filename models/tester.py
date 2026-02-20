"""
CRUD-операции с тестерами.
"""
from database import get_db


async def get_or_create_tester(telegram_id: int, username: str = None, full_name: str = None) -> dict:
    """Получает тестера из базы или создаёт нового. Возвращает dict."""
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def get_tester_by_username(username: str) -> dict | None:
    """Ищет тестера по @username (без @)."""
    clean = username.lstrip("@")
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM testers WHERE LOWER(username) = LOWER(?)", (clean,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_tester_by_id(telegram_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM testers WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_testers(active_only: bool = True) -> list[dict]:
    db = await get_db()
    try:
        q = "SELECT * FROM testers"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY total_points DESC"
        cursor = await db.execute(q)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def update_tester_points(telegram_id: int, delta: int):
    """Изменяет total_points на delta (+/-)."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE testers SET total_points = CASE WHEN total_points + ? < 0 THEN 0 ELSE total_points + ? END WHERE telegram_id = ?",
            (delta, delta, telegram_id)
        )
        await db.commit()
    finally:
        await db.close()


async def update_tester_stats(telegram_id: int, bugs: int = 0, crashes: int = 0, games: int = 0):
    """Увеличивает счётчики багов/крашей/игр."""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE testers SET
                total_bugs = total_bugs + ?,
                total_crashes = total_crashes + ?,
                total_games = total_games + ?
            WHERE telegram_id = ?""",
            (bugs, crashes, games, telegram_id)
        )
        await db.commit()
    finally:
        await db.close()


async def increment_warnings(telegram_id: int) -> int:
    """Увеличивает счётчик предупреждений, возвращает новое значение."""
    db = await get_db()
    try:
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
    finally:
        await db.close()
