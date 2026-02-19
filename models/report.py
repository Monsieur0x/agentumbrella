"""
CRUD-операции с отчётами (скриншоты игр).
"""
from database import get_db


async def create_report(tester_id: int, message_id: int, games_count: int,
                        claimed_count: int, file_id: str, points: int = 0,
                        status: str = "accepted") -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO reports (tester_id, message_id, games_count, claimed_count,
                                   screenshot_file_id, points_awarded, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tester_id, message_id, games_count, claimed_count, file_id, points, status)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()
