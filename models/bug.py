"""
CRUD-операции с багами.
"""
from database import get_db


async def create_bug(tester_id: int, message_id: int,
                     script_name: str = "", steps: str = "",
                     youtube_link: str = "", file_id: str = "",
                     file_type: str = "",
                     # Обратная совместимость со старыми полями
                     title: str = "", description: str = "",
                     expected: str = "", actual: str = "",
                     bug_type: str = "bug",
                     points: int = 0, status: str = "pending") -> int:
    """Создаёт баг, возвращает ID."""
    # Маппинг: script_name → title, steps → description для совместимости
    effective_title = script_name or title
    effective_desc = steps or description
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO bugs
           (tester_id, message_id, title, description, expected, actual,
            type, points_awarded, status,
            script_name, steps, youtube_link, file_id, file_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tester_id, message_id, effective_title, effective_desc,
         expected, actual, bug_type, points, status,
         script_name, steps, youtube_link, file_id, file_type)
    )
    await db.commit()
    return cursor.lastrowid


async def get_bug(bug_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def mark_duplicate(bug_id: int):
    db = await get_db()
    await db.execute("UPDATE bugs SET status = 'duplicate' WHERE id = ?", (bug_id,))
    await db.commit()


async def get_recent_bugs(limit: int = 50) -> list[dict]:
    """Последние N багов для проверки дублей."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, title, description, type FROM bugs WHERE status = 'accepted' ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_bug(bug_id: int) -> bool:
    """Удаляет баг из БД. Возвращает True если удалён."""
    db = await get_db()
    cursor = await db.execute("DELETE FROM bugs WHERE id = ?", (bug_id,))
    await db.commit()
    return cursor.rowcount > 0


async def delete_all_bugs() -> int:
    """Удаляет все баги из БД. Возвращает количество удалённых."""
    db = await get_db()
    cursor = await db.execute("DELETE FROM bugs")
    await db.commit()
    return cursor.rowcount


async def clear_weeek_task_id(bug_id: int):
    """Очищает weeek_task_id у бага (после удаления из Weeek)."""
    db = await get_db()
    await db.execute(
        "UPDATE bugs SET weeek_task_id = NULL, weeek_board_name = NULL, weeek_column_name = NULL WHERE id = ?",
        (bug_id,)
    )
    await db.commit()


async def get_bug_stats(period: str = "all", bug_type: str = "all") -> dict:
    """Статистика по багам за период."""
    db = await get_db()
    where = []
    params = []
    if bug_type != "all":
        where.append("type = ?")
        params.append(bug_type)

    if period == "today":
        where.append("date(created_at) = date('now')")
    elif period == "week":
        where.append("created_at >= datetime('now', '-7 days')")
    elif period == "month":
        where.append("created_at >= datetime('now', '-30 days')")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cursor = await db.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) as duplicates,
            SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) as accepted,
            SUM(CASE WHEN type = 'bug' THEN 1 ELSE 0 END) as bugs,
            SUM(CASE WHEN type = 'crash' THEN 1 ELSE 0 END) as crashes
        FROM bugs {where_sql}
    """, params)
    row = await cursor.fetchone()
    return dict(row) if row else {}
