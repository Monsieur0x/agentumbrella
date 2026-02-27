"""
Сервис для работы с баллами.
"""
from datetime import datetime
from json_store import async_update, POINTS_LOG_FILE
from models.tester import update_tester_points, get_tester_by_username, get_all_testers


async def award_points(username: str, amount: int, reason: str, admin_id: int = None, source: str = "manual") -> dict:
    """
    Начислить/списать баллы тестеру.
    Возвращает {"success": True, "tester": ..., "new_total": ...} или {"success": False, "error": ...}
    """
    tester = await get_tester_by_username(username)
    if not tester:
        return {"success": False, "error": f"Тестер @{username.lstrip('@')} не найден в базе"}

    new_total = await update_tester_points(tester["telegram_id"], amount)

    # Записываем в лог баллов
    def add_log(data):
        entry_id = data.get("next_id", 1)
        data["next_id"] = entry_id + 1
        if "items" not in data:
            data["items"] = []
        data["items"].append({
            "id": entry_id,
            "tester_id": tester["telegram_id"],
            "amount": amount,
            "reason": reason,
            "source": source,
            "admin_id": admin_id,
            "created_at": datetime.now().isoformat(),
        })
        return data

    await async_update(POINTS_LOG_FILE, add_log)

    return {
        "success": True,
        "username": tester["username"] if tester["username"] else tester["full_name"],
        "full_name": tester["full_name"],
        "amount": amount,
        "old_total": tester["total_points"],
        "new_total": new_total,
        "reason": reason,
    }


async def award_points_bulk(usernames: list | str, amount: int, reason: str, admin_id: int = None) -> dict:
    """
    Начислить баллы нескольким тестерам.
    usernames: список юзернеймов или "all" для всех.
    """
    if usernames == "all" or usernames == ["all"]:
        from models.admin import get_admin_ids
        testers = await get_all_testers()
        admin_ids = await get_admin_ids()
        targets = [t["username"] for t in testers if t["username"] and t["telegram_id"] not in admin_ids]
    else:
        if isinstance(usernames, str):
            targets = [u.strip().lstrip("@") for u in usernames.split(",")]
        else:
            targets = [u.lstrip("@") for u in usernames]

    results = []
    for uname in targets:
        res = await award_points(uname, amount, reason, admin_id, "manual")
        results.append(res)

    success = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    return {
        "success_count": len(success),
        "failed_count": len(failed),
        "results": success,
        "errors": failed,
    }
