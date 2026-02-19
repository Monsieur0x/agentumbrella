"""
CRUD-операции с багами.
"""
from database import get_db


async def create_bug(tester_id: int, message_id: int, title: str, description: str,
                     expected: str, actual: str, bug_type: str = "bug",
                     points: int = 0, status: str = "accepted") -> int:
    """Создаёт баг, возвращает ID."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO bugs (tester_id, message_id, title, description, expected, actual, type, points_awarded, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tester_id, message_id, title, description, expected, actual, bug_type, points, status)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_bug(bug_id: int) -> dict | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM bugs WHERE id = ?", (bug_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def mark_duplicate(bug_id: int):
    db = await get_db()
    try:
        await db.execute("UPDATE bugs SET status = 'duplicate' WHERE id = ?", (bug_id,))
        await db.commit()
    finally:
        await db.close()


async def get_recent_bugs(limit: int = 50) -> list[dict]:
    """Последние N багов для проверки дублей."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, title, description, type FROM bugs WHERE status = 'accepted' ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_bug_stats(period: str = "all", bug_type: str = "all") -> dict:
    """Статистика по багам за период."""
    db = await get_db()
    try:
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
    finally:
        await db.close()
