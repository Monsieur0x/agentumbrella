"""
CRUD-операции с багами.
"""
from database import get_db


async def create_bug(tester_id: int, message_id: int,
                     script_name: str = "", steps: str = "",
                     youtube_link: str = "", file_id: str = "",
                     file_type: str = "",
                     bug_type: str = "bug",
                     points: int = 0, status: str = "pending") -> tuple[int, int]:
    """Создаёт баг, возвращает (ID, display_number)."""
    db = await get_db()

    # Атомарный INSERT с вычислением display_number в одном запросе
    cursor = await db.execute(
        """INSERT INTO bugs
           (tester_id, message_id, title, description,
            type, points_awarded, status,
            script_name, steps, youtube_link, file_id, file_type,
            display_number)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   (SELECT COALESCE(MAX(display_number), 0) + 1 FROM bugs))""",
        (tester_id, message_id, script_name, steps,
         bug_type, points, status,
         script_name, steps, youtube_link, file_id, file_type)
    )
    await db.commit()
    bug_id = cursor.lastrowid

    # Читаем присвоенный display_number
    cursor = await db.execute(
        "SELECT display_number FROM bugs WHERE id = ?", (bug_id,)
    )
    row = await cursor.fetchone()
    return bug_id, row[0]


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
        "SELECT id, display_number, title, description, type FROM bugs WHERE status = 'accepted' ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_bug(bug_id: int) -> bool:
    """Удаляет баг из БД и декрементирует счётчик тестера (если баг был accepted). Возвращает True если удалён."""
    db = await get_db()
    # Сначала проверяем, был ли баг accepted — только такие учитываются в total_bugs
    cursor = await db.execute(
        "SELECT tester_id, status FROM bugs WHERE id = ?", (bug_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return False

    tester_id = row["tester_id"]
    was_accepted = row["status"] == "accepted"

    cursor = await db.execute("DELETE FROM bugs WHERE id = ?", (bug_id,))
    await db.commit()

    if was_accepted and cursor.rowcount > 0:
        await db.execute(
            "UPDATE testers SET total_bugs = CASE WHEN total_bugs - 1 < 0 THEN 0 ELSE total_bugs - 1 END WHERE telegram_id = ?",
            (tester_id,)
        )
        await db.commit()

    return cursor.rowcount > 0


async def delete_all_bugs() -> int:
    """Удаляет все баги из БД и обнуляет счётчики total_bugs у всех тестеров. Возвращает количество удалённых."""
    db = await get_db()
    cursor = await db.execute("DELETE FROM bugs")
    count = cursor.rowcount
    # Обнуляем кешированные счётчики багов у всех тестеров
    await db.execute("UPDATE testers SET total_bugs = 0")
    await db.commit()
    return count


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
            SUM(CASE WHEN type = 'bug' THEN 1 ELSE 0 END) as bugs
        FROM bugs {where_sql}
    """, params)
    row = await cursor.fetchone()
    return dict(row) if row else {}
