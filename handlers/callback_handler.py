"""
Обработка нажатий inline-кнопок:
- dup_yes:{bug_id}  — подтвердить дубль
- dup_no:{bug_id}:{points} — не дубль, принять баг
- report_accept:{report_id}:{count} — принять отчёт с N играми
- report_reject:{report_id} — отклонить отчёт
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from models.admin import is_admin, is_owner
from models.bug import mark_duplicate, get_bug
from models.tester import update_tester_points, update_tester_stats
from services.weeek_service import create_task as weeek_create_task
from utils.logger import log_info, log_admin
from database import get_db

router = Router()


@router.callback_query(F.data.startswith("dup_yes:"))
async def handle_dup_yes(callback: CallbackQuery):
    """Админ подтвердил: это дубль."""
    user_id = callback.from_user.id
    if not (await is_admin(user_id) or await is_owner(user_id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    await mark_duplicate(bug_id)

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ <b>Решение:</b> дубль (подтвердил @{callback.from_user.username})",
        parse_mode="HTML"
    )
    await callback.answer("Баг помечен как дубль")
    await log_info(f"Баг #{bug_id} помечен как дубль (@{callback.from_user.username})")


@router.callback_query(F.data.startswith("dup_no:"))
async def handle_dup_no(callback: CallbackQuery):
    """Админ решил: не дубль, принять."""
    user_id = callback.from_user.id
    if not (await is_admin(user_id) or await is_owner(user_id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    parts = callback.data.split(":")
    try:
        bug_id = int(parts[1])
        points = int(parts[2]) if len(parts) > 2 else 3
        if points <= 0:
            points = 3
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return

    # Получаем баг
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Принимаем баг
    db = await get_db()
    try:
        await db.execute(
            "UPDATE bugs SET status = 'accepted', points_awarded = ? WHERE id = ?",
            (points, bug_id)
        )
        await db.commit()
    finally:
        await db.close()

    # Начисляем баллы
    await update_tester_points(bug["tester_id"], points)
    if bug["type"] == "crash":
        await update_tester_stats(bug["tester_id"], crashes=1)
    else:
        await update_tester_stats(bug["tester_id"], bugs=1)

    # Weeek
    weeek_info = ""
    weeek_result = await weeek_create_task(
        title=bug["title"],
        description=bug["description"],
        bug_type=bug["type"],
        bug_id=bug_id,
    )
    if weeek_result.get("success"):
        weeek_info = " + Weeek ✅"

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ <b>Решение:</b> принят, +{points} б. (@{callback.from_user.username}){weeek_info}",
        parse_mode="HTML"
    )
    await callback.answer(f"Баг #{bug_id} принят, +{points} баллов")
    await log_admin(f"Баг #{bug_id} принят (не дубль) @{callback.from_user.username}, +{points} б.")


@router.callback_query(F.data.startswith("weeek:"))
async def handle_weeek_board(callback: CallbackQuery):
    """Владелец выбрал доску для бага."""
    user_id = callback.from_user.id
    if not await is_owner(user_id):
        await callback.answer("Только владелец может выбирать доску", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

    # Получаем баг
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Создаём задачу в Weeek
    from services.weeek_service import create_task as weeek_create_task
    result = await weeek_create_task(
        title=bug["title"],
        description=bug["description"],
        bug_type=bug.get("type", "bug"),
        tester_username="",
        bug_id=bug_id,
        board_column_id=col_id,
    )

    if result.get("success"):
        # Сохраняем task_id в базу
        db = await get_db()
        try:
            await db.execute(
                "UPDATE bugs SET weeek_task_id = ? WHERE id = ?",
                (str(result.get("task_id", "")), bug_id)
            )
            await db.commit()
        finally:
            await db.close()

        # Находим имя доски
        from services.weeek_service import get_cached_boards
        board_name = "?"
        for b in get_cached_boards():
            if b.get("id") == board_id:
                board_name = b.get("name", "?")
                break

        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ Отправлен в доску <b>«{board_name}»</b>",
            parse_mode="HTML"
        )
        await callback.answer(f"Задача создана в {board_name}")
        await log_info(f"Баг #{bug_id} → Weeek доска «{board_name}»")
    else:
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ Ошибка Weeek: {result.get('error', '?')}",
            parse_mode="HTML"
        )
        await callback.answer("Ошибка создания задачи", show_alert=True)


@router.callback_query(F.data.startswith("weeek_skip:"))
async def handle_weeek_skip(callback: CallbackQuery):
    """Владелец решил не отправлять в Weeek."""
    user_id = callback.from_user.id
    if not await is_owner(user_id):
        await callback.answer("Только владелец", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        callback.message.text + "\n\n⏭ Не отправлен в Weeek",
        parse_mode="HTML"
    )
    await callback.answer("Пропущено")


@router.callback_query(F.data.startswith("report_accept:"))
async def handle_report_accept(callback: CallbackQuery):
    """Админ принял отчёт с определённым количеством игр."""
    user_id = callback.from_user.id
    if not (await is_admin(user_id) or await is_owner(user_id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    parts = callback.data.split(":")
    try:
        report_id = int(parts[1])
        games = int(parts[2])
        if games <= 0:
            games = 1
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        report = await cursor.fetchone()
        if not report:
            await callback.answer("Отчёт не найден", show_alert=True)
            return

        report = dict(report)
        points = games  # 1 балл за игру

        await db.execute(
            "UPDATE reports SET status = 'accepted', games_count = ?, points_awarded = ? WHERE id = ?",
            (games, points, report_id)
        )
        await db.commit()
    finally:
        await db.close()

    await update_tester_points(report["tester_id"], points)
    await update_tester_stats(report["tester_id"], games=games)

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Принято: {games} игр, +{points} б. (@{callback.from_user.username})",
        parse_mode="HTML"
    )
    await callback.answer(f"Принято {games} игр")
    await log_info(f"Отчёт #{report_id}: принято {games} игр, +{points} б.")


@router.callback_query(F.data.startswith("report_reject:"))
async def handle_report_reject(callback: CallbackQuery):
    """Админ отклонил отчёт."""
    user_id = callback.from_user.id
    if not (await is_admin(user_id) or await is_owner(user_id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    parts = callback.data.split(":")
    report_id = int(parts[1])

    db = await get_db()
    try:
        await db.execute("UPDATE reports SET status = 'rejected' WHERE id = ?", (report_id,))
        await db.commit()
    finally:
        await db.close()

    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ Отклонён (@{callback.from_user.username})",
        parse_mode="HTML"
    )
    await callback.answer("Отчёт отклонён")
    await log_info(f"Отчёт #{report_id} отклонён @{callback.from_user.username}")