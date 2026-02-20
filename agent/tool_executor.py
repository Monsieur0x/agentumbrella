"""
Выполнение функций (tools) — связывает названия из ИИ с реальным кодом.
"""
import json
from models.tester import (
    get_tester_by_username, get_all_testers, increment_warnings
)
from models.bug import get_bug, mark_duplicate, get_bug_stats, get_recent_bugs
from models.admin import add_admin, remove_admin, get_all_admins
from services.points_service import award_points, award_points_bulk
from services.rating_service import get_rating
from database import get_db
from utils.logger import log_info, log_admin, get_bot


def _normalize_username(username: str) -> str:
    """Убирает @ в начале username, если есть."""
    return username.lstrip("@") if username else ""


def _tag(username: str) -> str:
    """Форматирует username с @ для кликабельности в Telegram."""
    if not username:
        return "?"
    clean = username.lstrip("@")
    return f"@{clean}"


async def execute_tool(name: str, arguments: str, caller_id: int = None) -> str:
    """
    Выполняет функцию по имени и возвращает JSON-результат.
    arguments — строка JSON от ИИ.
    """
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return json.dumps({"error": "Не удалось разобрать аргументы"}, ensure_ascii=False)

    try:
        result = await _dispatch(name, args, caller_id)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"Ошибка: {str(e)}"}, ensure_ascii=False)


_ADMIN_TOOLS = {"award_points", "award_points_bulk", "issue_warning", "create_task", "mark_bug_duplicate", "search_bugs", "publish_rating"}
_OWNER_TOOLS = {"manage_admin"}


async def _check_permission(name: str, caller_id: int) -> str | None:
    """Возвращает сообщение об ошибке если нет прав, иначе None."""
    if name not in _ADMIN_TOOLS and name not in _OWNER_TOOLS:
        return None
    from models.admin import is_admin, is_owner
    if name in _OWNER_TOOLS:
        if not await is_owner(caller_id or 0):
            return "Только для владельца"
    if name in _ADMIN_TOOLS:
        if not (await is_admin(caller_id or 0) or await is_owner(caller_id or 0)):
            return "Недостаточно прав"
    return None


async def _dispatch(name: str, args: dict, caller_id: int = None) -> dict:
    """Маршрутизация вызовов функций."""

    # === Проверка прав ===
    perm_error = await _check_permission(name, caller_id)
    if perm_error:
        return {"error": perm_error}

    # === АНАЛИТИКА ===
    if name == "get_tester_stats":
        return await _get_tester_stats(args["username"])

    elif name == "get_team_stats":
        return await _get_team_stats(args.get("period", "all"))

    elif name == "get_inactive_testers":
        return await _get_inactive_testers(args.get("days", 7))

    elif name == "compare_testers":
        return await _compare_testers(args["username1"], args["username2"])

    elif name == "get_testers_list":
        return await _get_testers_list(args.get("include_inactive", False))

    elif name == "get_bug_stats":
        return await _get_bug_stats_handler(args.get("period", "all"), args.get("type", "all"))

    # === БАЛЛЫ ===
    elif name == "award_points":
        result = await award_points(
            args["username"], args["amount"], args["reason"], caller_id
        )
        if result.get("success"):
            await log_admin(
                f"@{result['username']}: {'+' if args['amount'] > 0 else ''}{args['amount']} б. ({args['reason']})"
            )
        return result

    elif name == "award_points_bulk":
        usernames = args.get("usernames", "all")
        result = await award_points_bulk(usernames, args["amount"], args["reason"], caller_id)
        await log_admin(f"Массовое начисление: {args['amount']} б. ({args['reason']})")
        return result

    # === ПРЕДУПРЕЖДЕНИЯ ===
    elif name == "issue_warning":
        return await _issue_warning(args["username"], args["reason"], caller_id)

    # === ЗАДАНИЯ ===
    elif name == "create_task":
        return await _create_task(args["brief"], caller_id)

    # === РЕЙТИНГ ===
    elif name == "get_rating":
        data = await get_rating(args.get("top_count", 0))
        from services.rating_service import format_rating_message
        data["formatted_message"] = format_rating_message(data)
        return data

    elif name == "publish_rating":
        data = await get_rating(args.get("top_count", 0))
        comment = args.get("comment", "")
        from services.rating_service import publish_rating_to_topic, format_rating_message
        bot = get_bot()
        if bot:
            msg_id = await publish_rating_to_topic(bot, data, comment)
            data["published"] = bool(msg_id)
        else:
            data["published"] = False
        data["formatted_message"] = format_rating_message(data)
        if comment:
            data["formatted_message"] += f"\n\n{comment}"
        await log_admin("Рейтинг опубликован в топик «Топ»")
        return data

    # === АДМИНЫ ===
    elif name == "manage_admin":
        return await _manage_admin(args["action"], args.get("username"))

    # === БАГИ ===
    elif name == "mark_bug_duplicate":
        await mark_duplicate(args["bug_id"])
        await log_info(f"Баг #{args['bug_id']} помечен как дубль")
        return {"success": True, "bug_id": args["bug_id"], "status": "duplicate"}

    elif name == "search_bugs":
        return await _search_bugs(args["query"], args.get("tester"))

    else:
        return {"error": f"Неизвестная функция: {name}"}


# === Реализации функций ===

async def _get_testers_list(include_inactive: bool = False) -> dict:
    from models.admin import get_all_admins
    testers = await get_all_testers(active_only=not include_inactive)
    admins = await get_all_admins()
    admin_ids = {a["telegram_id"] for a in admins}
    # Исключаем админов и владельца — показываем только тестеров
    testers = [t for t in testers if t["telegram_id"] not in admin_ids]
    return {
        "total": len(testers),
        "testers": [
            {
                "username": _tag(t["username"]),
                "full_name": t["full_name"],
                "total_points": t["total_points"],
                "warnings_count": t["warnings_count"],
                "is_active": t["is_active"],
            }
            for t in testers
        ]
    }


async def _get_tester_stats(username: str) -> dict:
    tester = await get_tester_by_username(_normalize_username(username))
    if not tester:
        return {"error": f"Тестер @{_normalize_username(username)} не найден"}
    return {
        "username": _tag(tester["username"]),
        "full_name": tester["full_name"],
        "total_points": tester["total_points"],
        "total_bugs": tester["total_bugs"],
        "total_crashes": tester["total_crashes"],
        "total_games": tester["total_games"],
        "warnings_count": tester["warnings_count"],
        "is_active": tester["is_active"],
        "registered": tester["created_at"],
    }


async def _get_team_stats(period: str) -> dict:
    testers = await get_all_testers()
    bugs = await get_bug_stats(period)

    total_points = sum(t["total_points"] for t in testers)
    total_games = sum(t["total_games"] for t in testers)

    # Топ-3
    top3 = testers[:3] if testers else []

    return {
        "period": period,
        "total_testers": len(testers),
        "total_points": total_points,
        "total_games": total_games,
        "bugs_stats": bugs,
        "top_3": [
            {"username": _tag(t["username"]), "points": t["total_points"],
             "bugs": t["total_bugs"], "games": t["total_games"]}
            for t in top3
        ],
        "average_points": round(total_points / len(testers), 1) if testers else 0,
    }


async def _get_inactive_testers(days: int) -> dict:
    db = await get_db()
    try:
        # Тестеры, у которых нет записей в points_log за N дней
        cursor = await db.execute("""
            SELECT t.username, t.full_name, t.total_points,
                   MAX(pl.created_at) as last_activity
            FROM testers t
            LEFT JOIN points_log pl ON t.telegram_id = pl.tester_id
            WHERE t.is_active = 1
            GROUP BY t.telegram_id
            HAVING last_activity IS NULL
                OR last_activity < datetime('now', ? || ' days')
        """, (f"-{days}",))
        rows = await cursor.fetchall()
        return {
            "days": days,
            "inactive_count": len(rows),
            "testers": [
                {"username": _tag(r["username"]), "full_name": r["full_name"],
                 "last_activity": r["last_activity"]}
                for r in rows
            ]
        }
    finally:
        await db.close()


async def _compare_testers(u1: str, u2: str) -> dict:
    t1 = await get_tester_by_username(_normalize_username(u1))
    t2 = await get_tester_by_username(_normalize_username(u2))
    if not t1:
        return {"error": f"Тестер @{_normalize_username(u1)} не найден"}
    if not t2:
        return {"error": f"Тестер @{_normalize_username(u2)} не найден"}

    return {
        "tester_1": {
            "username": _tag(t1["username"]), "points": t1["total_points"],
            "bugs": t1["total_bugs"], "crashes": t1["total_crashes"], "games": t1["total_games"],
        },
        "tester_2": {
            "username": _tag(t2["username"]), "points": t2["total_points"],
            "bugs": t2["total_bugs"], "crashes": t2["total_crashes"], "games": t2["total_games"],
        }
    }


async def _get_bug_stats_handler(period: str, bug_type: str) -> dict:
    return await get_bug_stats(period, bug_type)


async def _issue_warning(username: str, reason: str, admin_id: int) -> dict:
    tester = await get_tester_by_username(_normalize_username(username))
    if not tester:
        return {"error": f"Тестер @{_normalize_username(username)} не найден"}

    new_count = await increment_warnings(tester["telegram_id"])

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO warnings (tester_id, reason, admin_id) VALUES (?, ?, ?)",
            (tester["telegram_id"], reason, admin_id)
        )
        await db.commit()
    finally:
        await db.close()

    await log_admin(f"Предупреждение @{tester['username']}: {reason} ({new_count}/3)")

    # Деактивация при 3 предупреждениях
    deactivated = False
    if new_count >= 3:
        db2 = await get_db()
        try:
            await db2.execute(
                "UPDATE testers SET is_active = 0 WHERE telegram_id = ?",
                (tester["telegram_id"],)
            )
            await db2.commit()
        finally:
            await db2.close()
        deactivated = True
        await log_admin(f"Тестер @{tester['username']} деактивирован (3/3 предупреждений)")

    # Уведомляем тестера в ЛС
    bot = get_bot()
    if bot:
        try:
            warn_text = (
                f"⚠️ <b>Предупреждение</b>\n\n"
                f"Причина: {reason}\n"
                f"Это предупреждение <b>{new_count} из 3</b>."
            )
            if deactivated:
                warn_text += "\n\n🚫 <b>Вы деактивированы.</b> Обратитесь к администрации."
            await bot.send_message(
                chat_id=tester["telegram_id"],
                text=warn_text,
                parse_mode="HTML"
            )
        except Exception:
            pass  # Тестер мог не начать диалог с ботом

    return {
        "success": True,
        "username": _tag(tester["username"]),
        "reason": reason,
        "warnings_total": new_count,
        "max_warnings": 3,
        "deactivated": deactivated,
        "telegram_id": tester["telegram_id"],
    }


async def _create_task(brief: str, admin_id: int) -> dict:
    """Создаёт черновик задания: расширяет через ИИ и отправляет на подтверждение."""
    import html as html_module
    import anthropic
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from config import ANTHROPIC_API_KEY, MODEL_CHEAP

    # Расширяем задание через ИИ
    full_text = brief
    try:
        claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await claude_client.messages.create(
            model=MODEL_CHEAP,
            messages=[{
                "role": "user",
                "content": (
                    "Ты — координатор тестирования Umbrella, чита для Dota 2. "
                    "Пиши как координатор в чате, а не как менеджер с ТЗ.\n\n"
                    "Стиль:\n"
                    "- Коротко: что делать, где делать, куда скидывать баги\n"
                    "- Новую/неочевидную функцию поясни одним предложением — не больше\n"
                    "- Можно обращаться ко всем сразу (\"зайдите\", \"проверьте\", \"потыкайте\")\n"
                    "- Указывай конкретику: герой, аспект, шард, режим (турбо/лобби/паблик), бета или паблик билд\n"
                    "- Формат багрепорта — только если он важен (видео, debug.log, краш-лог, matchID)\n"
                    "- Два-четыре предложения — норма. Длиннее — только если реально нужно расписать условия\n"
                    "- НЕ используй HTML-теги и markdown. Только plain text и эмодзи\n\n"
                    "Правила:\n"
                    "- Только функционал, реальный для чита Dota 2\n"
                    "- Названия героев, скиллов, предметов — как в игре\n"
                    "- Не выдумывай функции, которые не упомянуты\n"
                    "- Пиши задание строго по тому, что указано. Не додумывай лишнего\n\n"
                    f"Краткое задание: {brief}"
                ),
            }],
            max_tokens=500,
        )
        full_text = response.content[0].text or brief
    except Exception as e:
        print(f"⚠️ Не удалось расширить задание: {e}")

    # Сохраняем в базу как черновик
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO tasks (admin_id, brief, full_text, status) VALUES (?, ?, ?, 'draft')",
            (admin_id, brief, full_text)
        )
        await db.commit()
        task_id = cursor.lastrowid
    finally:
        await db.close()

    # Отправляем превью админу на подтверждение
    bot = get_bot()
    if bot:
        safe_text = html_module.escape(full_text)
        preview_text = (
            f"📋 <b>Черновик задания #{task_id}</b>\n\n"
            f"{safe_text}\n\n"
            f"─────────────────\n"
            f"✏️ Отправьте свой вариант текста, чтобы заменить."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"task_publish:{task_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"task_cancel:{task_id}"),
            ]
        ])
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=preview_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            print(f"❌ Ошибка отправки превью задания: {e}")

    await log_info(f"Создан черновик задания #{task_id}")

    return {
        "success": True,
        "task_id": task_id,
        "brief": brief,
        "awaiting_confirmation": True,
    }


async def _manage_admin(action: str, username: str = None) -> dict:
    from agent.brain import clear_history

    if action == "list":
        admins = await get_all_admins()
        return {
            "admins": [
                {"username": _tag(a["username"]), "is_owner": a["is_owner"], "added_at": a["added_at"]}
                for a in admins
            ]
        }

    if not username:
        return {"error": "Не указан юзернейм"}

    clean_username = _normalize_username(username)
    tester = await get_tester_by_username(clean_username)
    if action == "add":
        if not tester:
            return {"error": f"@{clean_username} не найден в базе. Человек должен сначала написать в группу."}
        ok = await add_admin(tester["telegram_id"], tester["username"], tester["full_name"])
        if ok:
            clear_history(tester["telegram_id"])
        return {"success": ok, "action": "added", "username": _tag(tester["username"])}

    elif action == "remove":
        if not tester:
            return {"error": f"@{clean_username} не найден"}
        ok = await remove_admin(tester["telegram_id"])
        if not ok:
            return {"error": "Не удалось удалить (возможно, это владелец)"}
        clear_history(tester["telegram_id"])
        return {"success": True, "action": "removed", "username": _tag(tester["username"])}

    return {"error": f"Неизвестное действие: {action}"}


async def _search_bugs(query: str, tester: str = None) -> dict:
    db = await get_db()
    try:
        sql = """SELECT b.id, b.title, b.type, b.status, b.created_at, t.username
                 FROM bugs b
                 JOIN testers t ON b.tester_id = t.telegram_id
                 WHERE (b.title LIKE ? OR b.description LIKE ?)"""
        params = [f"%{query}%", f"%{query}%"]

        if tester:
            sql += " AND LOWER(t.username) = LOWER(?)"
            params.append(_normalize_username(tester))

        sql += " ORDER BY b.id DESC LIMIT 20"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        bugs = []
        for r in rows:
            bug = dict(r)
            if bug.get("username"):
                bug["username"] = _tag(bug["username"])
            bugs.append(bug)
        return {
            "query": query,
            "count": len(bugs),
            "bugs": bugs
        }
    finally:
        await db.close()