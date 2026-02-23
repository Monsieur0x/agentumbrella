"""
Выполнение функций (tools) — связывает названия из ИИ с реальным кодом.
"""
import json
from datetime import datetime, timedelta
from models.tester import (
    get_tester_by_username, get_all_testers, increment_warnings,
    decrement_warnings, reset_warnings, reset_all_warnings, set_tester_active
)
from models.bug import get_bug, mark_duplicate, get_bug_stats, get_recent_bugs, delete_bug, delete_all_bugs, clear_weeek_task_id
from models.admin import add_admin, remove_admin, get_all_admins, get_admin_ids
from services.points_service import award_points, award_points_bulk
from services.rating_service import get_rating
from json_store import async_load, async_update, POINTS_LOG_FILE, WARNINGS_FILE, TESTERS_FILE, BUGS_FILE, TASKS_FILE
from utils.logger import log_info, log_admin, get_bot
from config import SEARCH_BUGS_LIMIT


def _normalize_username(username: str) -> str:
    """Убирает @ в начале username, если есть."""
    return username.lstrip("@") if username else ""


def _tag(username: str) -> str:
    """Форматирует username с @ для кликабельности в Telegram."""
    if not username:
        return "?"
    clean = username.lstrip("@")
    return f"@{clean}"


async def execute_tool(name: str, arguments: str, caller_id: int = None, topic: str = "") -> str:
    """
    Выполняет функцию по имени и возвращает JSON-результат.
    arguments — строка JSON от ИИ.
    """
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return json.dumps({"error": "Не удалось разобрать аргументы"}, ensure_ascii=False)

    try:
        result = await _dispatch(name, args, caller_id, topic)
        print(f"[TOOL-EXEC] {name} → OK")
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[TOOL-EXEC] {name} → ERROR: {e}")
        return json.dumps({"error": f"Ошибка: {str(e)}"}, ensure_ascii=False)


_ADMIN_TOOLS = {"award_points", "award_points_bulk", "issue_warning", "issue_warning_bulk", "remove_warning", "create_task", "mark_bug_duplicate", "search_bugs", "delete_bug", "publish_rating", "refresh_testers", "link_login", "get_logins_list"}
_OWNER_TOOLS = {"manage_admin", "switch_mode"}


async def _check_permission(name: str, caller_id: int) -> str | None:
    """Возвращает сообщение об ошибке если нет прав, иначе None."""
    if name not in _ADMIN_TOOLS and name not in _OWNER_TOOLS:
        return None
    from models.admin import is_admin, is_owner
    if name in _OWNER_TOOLS:
        if not await is_owner(caller_id or 0):
            return "Только для руководителя"
    if name in _ADMIN_TOOLS:
        if not (await is_admin(caller_id or 0) or await is_owner(caller_id or 0)):
            return "Недостаточно прав"
    return None


async def _dispatch(name: str, args: dict, caller_id: int = None, topic: str = "") -> dict:
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
        if result.get("success_count", 0) > 0:
            await log_admin(f"Массовое начисление: {args['amount']} б. ({args['reason']}) — {result['success_count']} тестерам")
        return result

    # === ПРЕДУПРЕЖДЕНИЯ ===
    elif name == "issue_warning":
        return await _issue_warning(args["username"], args.get("reason", "Без причины"), caller_id)

    elif name == "issue_warning_bulk":
        return await _issue_warning_bulk(args["usernames"], args.get("reason", "Без причины"), caller_id)

    elif name == "remove_warning":
        return await _remove_warning(args["usernames"], args.get("amount", 1), caller_id)

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
        formatted = format_rating_message(data)
        if comment:
            formatted += f"\n\n{comment}"
        data["formatted_message"] = formatted

        bot = get_bot()
        if not bot:
            data["published"] = False
            return data

        # ЛС → превью + кнопки подтверждения
        if topic == "private":
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            top_count = args.get("top_count", 0)
            # Сохраняем параметры в callback_data
            cb_data = f"rating_publish:{top_count}"
            preview_text = (
                f"📋 <b>Превью рейтинга</b>\n\n"
                f"{formatted}\n\n"
                f"─────────────────\n"
                f"Опубликовать в топик «Топ»?"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Опубликовать", callback_data=cb_data),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="rating_cancel"),
                ]
            ])
            try:
                await bot.send_message(
                    chat_id=caller_id,
                    text=preview_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as e:
                print(f"❌ Ошибка отправки превью рейтинга: {e}")
            data["published"] = False
            data["awaiting_confirmation"] = True
            return data

        # Группа → публикуем сразу
        msg_id = await publish_rating_to_topic(bot, data, comment)
        data["published"] = bool(msg_id)
        if msg_id:
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
        return await _search_bugs(args.get("query"), args.get("tester"), args.get("bug_id"), args.get("status"))

    elif name == "delete_bug":
        return await _delete_bug(args.get("bug_id"), args["target"], args.get("delete_all", False))

    elif name == "refresh_testers":
        return await _refresh_testers()

    elif name == "link_login":
        return await _link_login(args["action"], args["login"], args.get("username"))

    elif name == "get_logins_list":
        return await _get_logins_list()

    elif name == "switch_mode":
        return await _switch_mode(args["mode"])

    else:
        return {"error": f"Неизвестная функция: {name}"}


# === Реализации функций ===

async def _get_testers_list(include_inactive: bool = False) -> dict:
    testers = await get_all_testers(active_only=not include_inactive)
    admin_ids_set = await get_admin_ids()
    # Исключаем админов и руководителя — показываем только тестеров
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
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
        "total_games": tester["total_games"],
        "warnings_count": tester["warnings_count"],
        "is_active": tester["is_active"],
        "registered": tester["created_at"],
    }


async def _get_team_stats(period: str) -> dict:
    testers = await get_all_testers()
    admin_ids_set = await get_admin_ids()
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
    bugs = await get_bug_stats(period)

    # Фильтрация баллов по периоду через points_log
    period_filter = {
        "today": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }

    if period in period_filter:
        cutoff = datetime.now() - period_filter[period]
        points_data = await async_load(POINTS_LOG_FILE)
        items = points_data.get("items", [])

        period_points_map = {}
        for entry in items:
            created = entry.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created)
            except (ValueError, TypeError):
                continue
            if dt >= cutoff:
                tid = entry.get("tester_id")
                period_points_map[tid] = period_points_map.get(tid, 0) + entry.get("amount", 0)

        total_points = sum(period_points_map.values())

        for t in testers:
            t["_period_points"] = period_points_map.get(t["telegram_id"], 0)
        testers_sorted = sorted(testers, key=lambda t: t["_period_points"], reverse=True)
        top3 = testers_sorted[:3]
    else:
        total_points = sum(t["total_points"] for t in testers)
        top3 = testers[:3] if testers else []

    total_games = sum(t["total_games"] for t in testers)

    return {
        "period": period,
        "total_testers": len(testers),
        "total_points": total_points,
        "total_games": total_games,
        "bugs_stats": bugs,
        "top_3": [
            {"username": _tag(t["username"]),
             "points": t.get("_period_points", t["total_points"]),
             "bugs": t["total_bugs"], "games": t["total_games"]}
            for t in top3
        ],
        "average_points": round(total_points / len(testers), 1) if testers else 0,
    }


async def _get_inactive_testers(days: int) -> dict:
    """Тестеры без активности за N дней."""
    cutoff = datetime.now() - timedelta(days=days)
    testers = await get_all_testers(active_only=True)
    admin_ids_set = await get_admin_ids()
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
    points_data = await async_load(POINTS_LOG_FILE)
    items = points_data.get("items", [])

    # Находим последнюю активность каждого тестера
    last_activity = {}
    for entry in items:
        tid = entry.get("tester_id")
        created = entry.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            continue
        if tid not in last_activity or dt > last_activity[tid]:
            last_activity[tid] = dt

    inactive = []
    for t in testers:
        tid = t["telegram_id"]
        la = last_activity.get(tid)
        if la is None or la < cutoff:
            inactive.append({
                "username": _tag(t["username"]),
                "full_name": t["full_name"],
                "last_activity": la.isoformat() if la else None,
            })

    return {
        "days": days,
        "inactive_count": len(inactive),
        "testers": inactive,
    }


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
            "bugs": t1["total_bugs"], "games": t1["total_games"],
        },
        "tester_2": {
            "username": _tag(t2["username"]), "points": t2["total_points"],
            "bugs": t2["total_bugs"], "games": t2["total_games"],
        }
    }


async def _get_bug_stats_handler(period: str, bug_type: str) -> dict:
    return await get_bug_stats(period, bug_type)


async def _issue_warning(username: str, reason: str, admin_id: int) -> dict:
    tester = await get_tester_by_username(_normalize_username(username))
    if not tester:
        return {"error": f"Тестер @{_normalize_username(username)} не найден"}

    new_count = await increment_warnings(tester["telegram_id"])

    # Запись в warnings
    def add_warning(data):
        entry_id = data.get("next_id", 1)
        data["next_id"] = entry_id + 1
        if "items" not in data:
            data["items"] = []
        data["items"].append({
            "id": entry_id,
            "tester_id": tester["telegram_id"],
            "reason": reason,
            "admin_id": admin_id,
            "created_at": datetime.now().isoformat(),
        })
        return data

    await async_update(WARNINGS_FILE, add_warning)

    await log_admin(f"Предупреждение @{tester['username']}: {reason} ({new_count}/3)")

    # Деактивация при 3 предупреждениях
    deactivated = False
    if new_count >= 3:
        await set_tester_active(tester["telegram_id"], False)
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


async def _issue_warning_bulk(usernames: str, reason: str, admin_id: int) -> dict:
    """Выдаёт варны нескольким тестерам или всем сразу."""
    usernames = usernames.strip()

    # === Всем тестерам ===
    if usernames.lower() == "all":
        testers = await get_all_testers(active_only=True)
        admin_ids_set = await get_admin_ids()
        testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
        names = [t["username"] for t in testers if t.get("username")]
    else:
        names = [_normalize_username(u.strip()) for u in usernames.split(",") if u.strip()]

    if not names:
        return {"error": "Не указаны юзернеймы"}

    results = []
    for uname in names:
        result = await _issue_warning(uname, reason, admin_id)
        results.append(result)

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": success_count > 0,
        "results": results,
        "success_count": success_count,
        "reason": reason,
    }


async def _remove_warning(usernames: str, amount: int, admin_id: int) -> dict:
    """Снимает варны у одного, нескольких или всех тестеров."""
    usernames = usernames.strip()

    # === Снять у всех ===
    if usernames.lower() == "all":
        affected = await reset_all_warnings()
        # Удаляем все записи из warnings
        def clear_all(data):
            data["items"] = []
            return data
        await async_update(WARNINGS_FILE, clear_all)
        await log_admin(f"Сброшены все варны ({affected} тестеров)")
        return {
            "success": True,
            "action": "reset_all",
            "affected_count": affected,
        }

    # === Парсим список юзернеймов ===
    names = [_normalize_username(u.strip()) for u in usernames.split(",") if u.strip()]
    if not names:
        return {"error": "Не указаны юзернеймы"}

    results = []
    for uname in names:
        tester = await get_tester_by_username(uname)
        if not tester:
            results.append({"username": f"@{uname}", "error": "не найден"})
            continue

        old_count = tester["warnings_count"]
        if old_count == 0:
            results.append({"username": _tag(tester["username"]), "warnings": 0, "skipped": True})
            continue

        # amount=0 означает сбросить все варны
        if amount == 0:
            new_count = await reset_warnings(tester["telegram_id"])
            # Удаляем все записи варнов тестера
            def remove_all_for_tester(data, tid=tester["telegram_id"]):
                data["items"] = [w for w in data.get("items", []) if w.get("tester_id") != tid]
                return data
            await async_update(WARNINGS_FILE, remove_all_for_tester)
        else:
            new_count = await decrement_warnings(tester["telegram_id"], amount)
            # Удаляем последние N записей варнов
            def remove_last_n(data, tid=tester["telegram_id"], n=amount):
                items = data.get("items", [])
                # Находим варны этого тестера
                tester_warnings = [(i, w) for i, w in enumerate(items) if w.get("tester_id") == tid]
                # Сортируем по дате, берём последние N
                tester_warnings.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
                indices_to_remove = {idx for idx, _ in tester_warnings[:n]}
                data["items"] = [w for i, w in enumerate(items) if i not in indices_to_remove]
                return data
            await async_update(WARNINGS_FILE, remove_last_n)

        # Реактивируем если был деактивирован по варнам и теперь < 3
        if not tester["is_active"] and new_count < 3:
            await set_tester_active(tester["telegram_id"], True)

        await log_admin(f"Снят варн @{tester['username']}: {old_count} → {new_count}")

        # Уведомляем тестера в ЛС
        bot = get_bot()
        if bot:
            try:
                text = (
                    f"✅ <b>Варн снят</b>\n\n"
                    f"Предупреждений: <b>{new_count} из 3</b>."
                )
                if not tester["is_active"] and new_count < 3:
                    text += "\n\n🔓 <b>Вы снова активны.</b>"
                await bot.send_message(
                    chat_id=tester["telegram_id"],
                    text=text,
                    parse_mode="HTML"
                )
            except Exception:
                pass

        results.append({
            "username": _tag(tester["username"]),
            "old_warnings": old_count,
            "new_warnings": new_count,
            "reactivated": not tester["is_active"] and new_count < 3,
        })

    success_count = sum(1 for r in results if "error" not in r and not r.get("skipped"))
    return {
        "success": success_count > 0,
        "results": results,
        "success_count": success_count,
    }


async def _create_task(brief: str, admin_id: int) -> dict:
    """Создаёт черновик задания: расширяет через ИИ и отправляет на подтверждение."""
    import html as html_module
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from agent.brain import _call_claude
    from config import MODEL

    # Расширяем задание через ИИ
    full_text = brief
    try:
        response = await _call_claude(
            model=MODEL,
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

    # Сохраняем как черновик
    result = {}

    def create(data):
        task_id = data.get("next_id", 1)
        data["next_id"] = task_id + 1
        if "items" not in data:
            data["items"] = {}
        data["items"][str(task_id)] = {
            "id": task_id,
            "admin_id": admin_id,
            "brief": brief,
            "full_text": full_text,
            "message_id": None,
            "status": "draft",
            "created_at": datetime.now().isoformat(),
        }
        result["task_id"] = task_id
        return data

    await async_update(TASKS_FILE, create)
    task_id = result["task_id"]

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
            return {"error": "Не удалось удалить (возможно, это руководитель)"}
        clear_history(tester["telegram_id"])
        return {"success": True, "action": "removed", "username": _tag(tester["username"])}

    return {"error": f"Неизвестное действие: {action}"}


async def _refresh_testers() -> dict:
    """Проверяет членство каждого тестера в группе и деактивирует кикнутых/ушедших."""
    from config import GROUP_ID

    bot = get_bot()
    if not bot:
        return {"error": "Бот недоступен"}
    if not GROUP_ID:
        return {"error": "GROUP_ID не задан"}

    testers = await get_all_testers(active_only=True)
    admin_ids = await get_admin_ids()

    deactivated = []
    still_active = []

    for t in testers:
        if t["telegram_id"] in admin_ids:
            continue
        try:
            member = await bot.get_chat_member(GROUP_ID, t["telegram_id"])
            if member.status in ("left", "kicked"):
                await set_tester_active(t["telegram_id"], False)
                deactivated.append(_tag(t["username"]) or t["full_name"])
            else:
                still_active.append(_tag(t["username"]) or t["full_name"])
        except Exception:
            # Не удалось проверить — оставляем как есть
            still_active.append(_tag(t["username"]) or t["full_name"])

    if deactivated:
        await log_admin(f"Обновление тестеров: деактивированы {', '.join(deactivated)}")

    return {
        "success": True,
        "active_count": len(still_active),
        "deactivated_count": len(deactivated),
        "deactivated": deactivated,
    }


async def _search_bugs(query: str = None, tester: str = None,
                       bug_id: int = None, status: str = None) -> dict:
    bugs_data = await async_load(BUGS_FILE)
    items = bugs_data.get("items", {})
    testers_data = await async_load(TESTERS_FILE)

    # Поиск по конкретному ID
    if bug_id:
        bug = items.get(str(bug_id))
        if not bug:
            return {"error": f"Баг #{bug_id} не найден"}
        bug = dict(bug)
        tid_key = str(bug.get("tester_id", ""))
        t = testers_data.get(tid_key, {})
        if t.get("username"):
            bug["username"] = _tag(t["username"])
        return {"count": 1, "bugs": [bug]}

    # Поиск по фильтрам
    results = []
    for b in items.values():
        # Фильтр по статусу
        if status and status != "all" and b.get("status") != status:
            continue

        # Фильтр по тестеру
        if tester:
            tid_key = str(b.get("tester_id", ""))
            t = testers_data.get(tid_key, {})
            if not t.get("username") or t["username"].lower() != _normalize_username(tester).lower():
                continue

        # Фильтр по тексту
        if query:
            q = query.lower()
            title = (b.get("title") or "").lower()
            desc = (b.get("description") or "").lower()
            script = (b.get("script_name") or "").lower()
            if q not in title and q not in desc and q not in script:
                continue

        bug = dict(b)
        tid_key = str(bug.get("tester_id", ""))
        t = testers_data.get(tid_key, {})
        if t.get("username"):
            bug["username"] = _tag(t["username"])
        results.append(bug)

    results.sort(key=lambda b: b.get("id", 0), reverse=True)
    results = results[:SEARCH_BUGS_LIMIT]

    return {
        "query": query or "",
        "tester": tester or "",
        "status": status or "all",
        "count": len(results),
        "bugs": results,
    }


async def _delete_bug(bug_id: int = None, target: str = "both",
                      do_delete_all: bool = False) -> dict:
    """Удаляет баг(и) из БД и/или Weeek."""

    # === Удаление ВСЕХ багов ===
    if do_delete_all:
        if target == "db_only":
            count = await delete_all_bugs()
            await log_info(f"Удалены все баги из БД ({count} шт.)")
            return {"success": True, "deleted_count": count, "target": "db_only"}
        elif target in ("weeek_only", "both"):
            # Сначала удаляем из Weeek все баги у которых есть weeek_task_id
            bugs_data = await async_load(BUGS_FILE)
            items = bugs_data.get("items", {})
            weeek_bugs = [b for b in items.values()
                          if b.get("weeek_task_id")]
            weeek_deleted = 0
            weeek_errors = 0
            if weeek_bugs:
                from services.weeek_service import delete_task as weeek_delete
                for b in weeek_bugs:
                    r = await weeek_delete(str(b["weeek_task_id"]))
                    if r.get("success"):
                        weeek_deleted += 1
                    else:
                        weeek_errors += 1

            result = {
                "success": True,
                "target": target,
                "weeek_deleted": weeek_deleted,
                "weeek_errors": weeek_errors,
            }

            if target == "both":
                count = await delete_all_bugs()
                result["db_deleted"] = count
                await log_info(f"Удалены все баги: БД ({count}), Weeek ({weeek_deleted})")
            else:
                # weeek_only — очищаем ссылки
                def clear_weeek(data):
                    for key in data.get("items", {}):
                        data["items"][key]["weeek_task_id"] = None
                        data["items"][key]["weeek_board_name"] = None
                        data["items"][key]["weeek_column_name"] = None
                    return data
                await async_update(BUGS_FILE, clear_weeek)
                await log_info(f"Удалены все баги из Weeek ({weeek_deleted})")
            return result

    # === Удаление одного бага ===
    if not bug_id:
        return {"error": "Не указан bug_id"}

    bug = await get_bug(bug_id)
    if not bug:
        return {"error": f"Баг #{bug_id} не найден"}

    dn = bug.get("display_number") or bug_id
    result = {"bug_id": bug_id, "display_number": dn, "target": target}

    weeek_task_id = bug.get("weeek_task_id")

    # Удаление из Weeek
    if target in ("weeek_only", "both"):
        if not weeek_task_id:
            result["weeek"] = "не был отправлен в Weeek"
        else:
            from services.weeek_service import delete_task as weeek_delete
            weeek_result = await weeek_delete(weeek_task_id)
            if weeek_result.get("success"):
                result["weeek"] = "удалён из Weeek"
                if target == "weeek_only":
                    await clear_weeek_task_id(bug_id)
            else:
                result["weeek"] = f"ошибка Weeek: {weeek_result.get('error', '?')}"

    # Удаление из БД
    if target in ("db_only", "both"):
        deleted = await delete_bug(bug_id)
        result["db"] = "удалён из БД" if deleted else "не удалось удалить из БД"

    result["success"] = True
    await log_info(f"Баг #{dn} удалён ({target})")
    return result


async def _link_login(action: str, login: str, username: str = None) -> dict:
    """Привязать/отвязать/проверить игровой логин."""
    from models.login_mapping import link_login, unlink_login, get_telegram_id_by_login

    if action == "check":
        tid = await get_telegram_id_by_login(login)
        if tid:
            from models.tester import get_tester_by_id
            tester = await get_tester_by_id(tid)
            uname = _tag(tester["username"]) if tester else f"ID {tid}"
            return {"login": login, "linked_to": uname}
        return {"login": login, "linked_to": None}

    if action == "link":
        if not username:
            return {"error": "Не указан username тестера"}
        tester = await get_tester_by_username(_normalize_username(username))
        if not tester:
            return {"error": f"Тестер @{_normalize_username(username)} не найден"}
        await link_login(login, tester["telegram_id"])
        await log_admin(f"Логин «{login}» привязан к {_tag(tester['username'])}")
        return {"success": True, "login": login, "username": _tag(tester["username"])}

    if action == "unlink":
        await unlink_login(login)
        await log_admin(f"Логин «{login}» отвязан")
        return {"success": True, "login": login, "unlinked": True}

    return {"error": f"Неизвестное действие: {action}"}


async def _get_logins_list() -> dict:
    """Список привязанных логинов и тестеров без привязки."""
    from models.login_mapping import get_all_logins

    logins = await get_all_logins()
    testers = await get_all_testers(active_only=True)

    # Тестеры с привязкой
    linked_tids = {entry["telegram_id"] for entry in logins}
    linked = []
    for entry in logins:
        tester = next((t for t in testers if t["telegram_id"] == entry["telegram_id"]), None)
        uname = _tag(tester["username"]) if tester and tester.get("username") else f"ID {entry['telegram_id']}"
        linked.append({"login": entry["login"], "tester": uname})

    # Активные тестеры без привязки
    unlinked = []
    for t in testers:
        if t["telegram_id"] not in linked_tids:
            unlinked.append(_tag(t["username"]) if t.get("username") else f"ID {t['telegram_id']}")

    return {
        "linked": linked,
        "linked_count": len(linked),
        "unlinked_testers": unlinked,
        "unlinked_count": len(unlinked),
    }


async def _switch_mode(mode: str) -> dict:
    """Переключает режим работы бота."""
    import config

    if mode not in ("active", "observe"):
        return {"error": f"Неизвестный режим: {mode}"}

    config.BOT_MODE = mode
    labels = {"active": "✅ Рабочий режим", "observe": "👁 Режим наблюдения"}
    label = labels[mode]

    await log_info(f"Режим бота переключён: {label}")
    return {"success": True, "mode": mode, "label": label}
