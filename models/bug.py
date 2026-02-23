"""
CRUD-операции с багами (JSON-хранилище).
"""
from datetime import datetime, timedelta
from json_store import async_load, async_save, async_update, BUGS_FILE, TESTERS_FILE


async def create_bug(tester_id: int, message_id: int,
                     script_name: str = "", steps: str = "",
                     youtube_link: str = "", file_id: str = "",
                     file_type: str = "",
                     files: list[dict] | None = None,
                     bug_type: str = "bug",
                     points: int = 0, status: str = "pending") -> tuple[int, int]:
    """Создаёт баг, возвращает (ID, display_number).

    files — список {"file_id": ..., "file_type": ...}.
    Если передан files, он имеет приоритет над file_id/file_type.
    """
    result = {}

    # Собираем список файлов
    if files is None and file_id:
        files = [{"file_id": file_id, "file_type": file_type}]
    elif files is None:
        files = []

    def updater(data):
        bug_id = data.get("next_id", 1)
        dn = data.get("next_display_number", 1)
        data["next_id"] = bug_id + 1
        data["next_display_number"] = dn + 1

        bug = {
            "id": bug_id,
            "tester_id": tester_id,
            "message_id": message_id,
            "title": script_name,
            "description": steps,
            "type": bug_type,
            "status": status,
            "weeek_task_id": None,
            "points_awarded": points,
            "created_at": datetime.now().isoformat(),
            "script_name": script_name,
            "steps": steps,
            "youtube_link": youtube_link,
            "file_id": files[0]["file_id"] if files else "",
            "file_type": files[0]["file_type"] if files else "",
            "files": files,
            "weeek_board_name": None,
            "weeek_column_name": None,
            "display_number": dn,
            "bot_message_id": None,
        }
        if "items" not in data:
            data["items"] = {}
        data["items"][str(bug_id)] = bug
        result["bug_id"] = bug_id
        result["dn"] = dn
        return data

    await async_update(BUGS_FILE, updater)
    return result["bug_id"], result["dn"]


async def get_bug(bug_id: int) -> dict | None:
    data = await async_load(BUGS_FILE)
    items = data.get("items", {})
    bug = items.get(str(bug_id))
    return dict(bug) if bug else None


async def update_bug(bug_id: int, **fields):
    """Обновляет произвольные поля бага."""
    key = str(bug_id)

    def updater(data):
        items = data.get("items", {})
        if key in items:
            for k, v in fields.items():
                items[key][k] = v
        return data

    await async_update(BUGS_FILE, updater)


async def mark_duplicate(bug_id: int):
    await update_bug(bug_id, status="duplicate")


async def get_recent_bugs(limit: int = 50) -> list[dict]:
    """Последние N багов для проверки дублей."""
    data = await async_load(BUGS_FILE)
    items = data.get("items", {})
    bugs = [b for b in items.values() if b.get("status") == "accepted"]
    bugs.sort(key=lambda b: b.get("id", 0), reverse=True)
    return [{"id": b["id"], "display_number": b.get("display_number"),
             "title": b.get("title"), "description": b.get("description"),
             "type": b.get("type")} for b in bugs[:limit]]


async def delete_bug(bug_id: int) -> bool:
    """Удаляет баг и декрементирует счётчик тестера (если баг был accepted)."""
    data = await async_load(BUGS_FILE)
    items = data.get("items", {})
    key = str(bug_id)
    if key not in items:
        return False

    bug = items[key]
    tester_id = bug.get("tester_id")
    was_accepted = bug.get("status") == "accepted"

    del items[key]
    await async_save(BUGS_FILE, data)

    if was_accepted and tester_id:
        from json_store import async_update as _au
        tid_key = str(tester_id)

        def dec_bugs(tdata):
            if tid_key in tdata:
                tdata[tid_key]["total_bugs"] = max(0, tdata[tid_key].get("total_bugs", 0) - 1)
            return tdata

        await _au(TESTERS_FILE, dec_bugs)

    return True


async def delete_all_bugs() -> int:
    """Удаляет все баги и обнуляет счётчики total_bugs."""
    data = await async_load(BUGS_FILE)
    count = len(data.get("items", {}))
    data["items"] = {}
    await async_save(BUGS_FILE, data)

    from json_store import async_update as _au

    def reset_bugs(tdata):
        for key in tdata:
            tdata[key]["total_bugs"] = 0
        return tdata

    await _au(TESTERS_FILE, reset_bugs)
    return count


async def clear_weeek_task_id(bug_id: int):
    """Очищает weeek_task_id у бага."""
    await update_bug(bug_id, weeek_task_id=None, weeek_board_name=None, weeek_column_name=None)


async def get_bug_stats(period: str = "all", bug_type: str = "all") -> dict:
    """Статистика по багам за период."""
    data = await async_load(BUGS_FILE)
    items = list(data.get("items", {}).values())

    now = datetime.now()
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        items = [b for b in items if _parse_dt(b.get("created_at")) >= cutoff]
    elif period == "week":
        cutoff = now - timedelta(days=7)
        items = [b for b in items if _parse_dt(b.get("created_at")) >= cutoff]
    elif period == "month":
        cutoff = now - timedelta(days=30)
        items = [b for b in items if _parse_dt(b.get("created_at")) >= cutoff]

    if bug_type != "all":
        items = [b for b in items if b.get("type") == bug_type]

    total = len(items)
    duplicates = sum(1 for b in items if b.get("status") == "duplicate")
    accepted = sum(1 for b in items if b.get("status") == "accepted")
    bugs_count = sum(1 for b in items if b.get("type") == "bug")

    return {
        "total": total,
        "duplicates": duplicates,
        "accepted": accepted,
        "bugs": bugs_count,
    }


def _parse_dt(s: str | None) -> datetime:
    """Парсит ISO datetime строку."""
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return datetime.min
