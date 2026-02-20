"""
Обработка нажатий inline-кнопок:

Новый флоу багов:
- bug_confirm:{bug_id}          — владелец подтвердил баг → начисляем баллы, показываем доски
- bug_reject:{bug_id}           — владелец отклонил баг → уведомляем тестера
- weeek_board:{bug_id}:{board}  — владелец выбрал доску → показываем колонки
- weeek_col:{bug_id}:{board}:{col} — владелец выбрал колонку → создаём задачу в Weeek
- weeek_skip:{bug_id}           — не отправлять в Weeek

Старый флоу (backward compat):
- dup_yes:{bug_id}              — подтвердить дубль
- dup_no:{bug_id}:{points}      — не дубль, принять баг
- weeek:{bug_id}:{board}:{col}  — выбор доски (старый формат)

Отчёты:
- report_accept:{report_id}:{count}
- report_reject:{report_id}
"""
import html
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from models.admin import is_admin, is_owner
from models.bug import mark_duplicate, get_bug
from models.tester import update_tester_points, update_tester_stats
from utils.logger import log_info, log_admin, get_bot
from database import get_db

router = Router()


# ─────────────────────────────────────────────
#  НОВЫЙ ФЛОУ: подтверждение бага владельцем
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("bug_confirm:"))
async def handle_bug_confirm(callback: CallbackQuery):
    """Владелец подтвердил баг: начисляем баллы и показываем выбор доски Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может подтверждать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Защита от двойного нажатия
    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    points = bug["points_awarded"]

    # Принимаем баг и начисляем баллы
    db = await get_db()
    try:
        await db.execute(
            "UPDATE bugs SET status = 'accepted' WHERE id = ?", (bug_id,)
        )
        await db.commit()
    finally:
        await db.close()

    await update_tester_points(bug["tester_id"], points)
    if bug["type"] == "crash":
        await update_tester_stats(bug["tester_id"], crashes=1)
    else:
        await update_tester_stats(bug["tester_id"], bugs=1)

    # Уведомляем тестера в ЛС
    bot = get_bot()
    if bot:
        try:
            emoji = "💥" if bug["type"] == "crash" else "✅"
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"{emoji} Твой {'краш' if bug['type'] == 'crash' else 'баг'} "
                    f"<b>#{bug_id}</b> принят! +{points} б. 🎉"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Показываем выбор доски Weeek
    await _show_board_selection(callback, bug_id)
    await callback.answer(f"Баг #{bug_id} подтверждён, +{points} б.")
    await log_info(
        f"Баг #{bug_id} подтверждён владельцем @{callback.from_user.username}, +{points} б."
    )


@router.callback_query(F.data.startswith("bug_reject:"))
async def handle_bug_reject(callback: CallbackQuery):
    """Владелец отклонил баг: уведомляем тестера."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может отклонять", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Защита от двойного нажатия
    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    db = await get_db()
    try:
        await db.execute("UPDATE bugs SET status = 'rejected' WHERE id = ?", (bug_id,))
        await db.commit()
    finally:
        await db.close()

    # Уведомляем тестера
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"❌ Твой {'краш' if bug['type'] == 'crash' else 'баг'} "
                    f"<b>#{bug_id}</b> был отклонён."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.message.edit_text(
        (callback.message.text or "") + f"\n\n❌ <b>Отклонён</b> (@{callback.from_user.username})",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer(f"Баг #{bug_id} отклонён")
    await log_info(f"Баг #{bug_id} отклонён владельцем @{callback.from_user.username}")


async def _show_board_selection(callback: CallbackQuery, bug_id: int):
    """Редактирует сообщение, подставляя кнопки выбора доски Weeek."""
    from services.weeek_service import get_cached_boards

    boards = get_cached_boards()
    if not boards:
        await callback.message.edit_text(
            (callback.message.text or "") + "\n\n✅ <b>Подтверждён</b> (Weeek не настроен)",
            parse_mode="HTML",
            reply_markup=None,
        )
        return

    rows = []
    row = []
    for board in boards:
        row.append(InlineKeyboardButton(
            text=f"📋 {board.get('name', '?')}",
            callback_data=f"weeek_board:{bug_id}:{board.get('id', 0)}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="❌ Не отправлять в Weeek",
        callback_data=f"weeek_skip:{bug_id}",
    )])

    await callback.message.edit_text(
        (callback.message.text or "") + "\n\n✅ <b>Подтверждён!</b> Выберите доску Weeek:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ─────────────────────────────────────────────
#  Выбор доски → колонки → создание задачи
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("weeek_board:"))
async def handle_weeek_board_select(callback: CallbackQuery):
    """Владелец выбрал доску — показываем колонки этой доски."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])

    from services.weeek_service import get_board_columns, get_cached_boards

    columns = await get_board_columns(board_id)

    if not columns:
        # Нет колонок через API — используем кэшированную первую колонку
        col_id = None
        for b in get_cached_boards():
            if b.get("id") == board_id:
                col_id = b.get("_first_column_id")
                break
        await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)
        return

    # Показываем колонки
    rows = []
    row = []
    for col in columns:
        row.append(InlineKeyboardButton(
            text=f"📌 {col.get('name', '?')}",
            callback_data=f"weeek_col:{bug_id}:{board_id}:{col.get('id', 0)}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer("Выберите колонку")


@router.callback_query(F.data.startswith("weeek_col:"))
async def handle_weeek_col_select(callback: CallbackQuery):
    """Владелец выбрал колонку — создаём задачу в Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 else None

    await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)


async def _create_weeek_task_and_finish(
    callback: CallbackQuery, bug_id: int, board_id: int, col_id: int | None
):
    """Создаёт задачу в Weeek и обновляет сообщение владельца."""
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    from services.weeek_service import create_task as weeek_create_task, get_cached_boards, upload_attachment

    description = (
        f"Шаги: {bug.get('steps') or bug.get('description', '')}\n"
        f"Видео: {bug.get('youtube_link', '')}"
    )
    result = await weeek_create_task(
        title=bug.get("script_name") or bug.get("title", ""),
        description=description,
        bug_type=bug.get("type", "bug"),
        tester_username="",
        bug_id=bug_id,
        board_column_id=col_id,
    )

    board_name = "?"
    for b in get_cached_boards():
        if b.get("id") == board_id:
            board_name = b.get("name", "?")
            break

    if result.get("success"):
        task_id = str(result.get("task_id", ""))

        db = await get_db()
        try:
            await db.execute(
                "UPDATE bugs SET weeek_task_id = ? WHERE id = ?",
                (task_id, bug_id),
            )
            await db.commit()
        finally:
            await db.close()

        # Прикрепляем файл из Telegram к задаче Weeek
        file_id = bug.get("file_id")
        file_type = bug.get("file_type")
        if file_id and task_id:
            try:
                bot = get_bot()
                from io import BytesIO

                tg_file = await bot.get_file(file_id)
                buffer = BytesIO()
                await bot.download_file(tg_file.file_path, buffer)
                file_bytes = buffer.getvalue()

                # Определяем имя файла
                ext_map = {"photo": ".jpg", "video": ".mp4", "document": ""}
                if tg_file.file_path:
                    filename = tg_file.file_path.split("/")[-1]
                else:
                    filename = f"bug_{bug_id}{ext_map.get(file_type, '')}"

                await upload_attachment(task_id, file_bytes, filename)
            except Exception as e:
                print(f"⚠️ Не удалось прикрепить файл к задаче Weeek #{task_id}: {e}")

        await callback.message.edit_text(
            (callback.message.text or "") + f"\n\n📋 Отправлен в Weeek: <b>«{html.escape(board_name)}»</b> ✅",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer(f"Задача создана в {board_name}")
        await log_info(f"Баг #{bug_id} → Weeek «{board_name}»")
    else:
        await callback.answer(
            f"Ошибка Weeek: {result.get('error', '?')}", show_alert=True
        )


@router.callback_query(F.data.startswith("weeek_skip:"))
async def handle_weeek_skip(callback: CallbackQuery):
    """Владелец решил не отправлять в Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        (callback.message.text or "") + "\n\n⏭ Не отправлен в Weeek",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer("Пропущено")
    await log_info(f"Баг #{bug_id} — Weeek пропущен")


# ─────────────────────────────────────────────
#  ЗАДАНИЯ: подтверждение / отмена черновика
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("task_publish:"))
async def handle_task_publish(callback: CallbackQuery):
    """Админ подтвердил задание — публикуем в топик «Задания»."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может публиковать", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        task = await cursor.fetchone()
        if not task:
            await callback.answer("Задание не найдено", show_alert=True)
            return
        task = dict(task)

        if task.get("status") != "draft":
            await callback.answer("Задание уже обработано", show_alert=True)
            return

        # Публикуем в топик
        from config import GROUP_ID, TOPIC_IDS
        from datetime import datetime

        bot = get_bot()
        topic_id = TOPIC_IDS.get("tasks")
        published = False
        if topic_id and GROUP_ID and bot:
            now = datetime.now().strftime("%d.%m.%Y")
            safe_text = html.escape(task['full_text'])
            message_text = (
                f"📋 <b>Задание #{task_id}</b> | {now}\n\n"
                f"{safe_text}\n\n"
                f"📝 Баги → топик «Баги», краши → «Краши», скрины → «Отчёты»."
            )
            try:
                msg = await bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=topic_id,
                    text=message_text,
                    parse_mode="HTML",
                )
                await db.execute(
                    "UPDATE tasks SET status = 'published', message_id = ? WHERE id = ?",
                    (msg.message_id, task_id)
                )
                await db.commit()
                published = True
            except Exception as e:
                print(f"❌ Ошибка публикации задания: {e}")

        if not published:
            await db.execute("UPDATE tasks SET status = 'published' WHERE id = ?", (task_id,))
            await db.commit()
    finally:
        await db.close()

    try:
        original_html = callback.message.html_text or html.escape(callback.message.text or "")
        await callback.message.edit_text(
            original_html + "\n\n✅ <b>Опубликовано!</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        # Фоллбэк без HTML если edit_text упал
        try:
            await callback.message.edit_text(
                (callback.message.text or "") + "\n\n✅ Опубликовано!",
                reply_markup=None,
            )
        except Exception:
            pass
    await callback.answer("Задание опубликовано")
    await log_info(f"Задание #{task_id} опубликовано @{callback.from_user.username}")


@router.callback_query(F.data.startswith("task_cancel:"))
async def handle_task_cancel(callback: CallbackQuery):
    """Админ отменил задание."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может отменять", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    db = await get_db()
    try:
        await db.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
        await db.commit()
    finally:
        await db.close()

    try:
        original_html = callback.message.html_text or html.escape(callback.message.text or "")
        await callback.message.edit_text(
            original_html + "\n\n❌ <b>Отменено</b>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        try:
            await callback.message.edit_text(
                (callback.message.text or "") + "\n\n❌ Отменено",
                reply_markup=None,
            )
        except Exception:
            pass
    await callback.answer("Задание отменено")
    await log_info(f"Задание #{task_id} отменено @{callback.from_user.username}")


# ─────────────────────────────────────────────
#  Старый флоу (backward compat)
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("dup_confirm:"))
async def handle_dup_confirm(callback: CallbackQuery):
    """Владелец подтвердил: это дубль — помечаем и уведомляем тестера."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    await mark_duplicate(bug_id)

    # Уведомляем тестера
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"🔄 Твой {'краш' if bug['type'] == 'crash' else 'баг'} "
                    f"<b>#{bug_id}</b> был отклонён как дубль."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.message.edit_text(
        (callback.message.text or "") + f"\n\n🔄 <b>Дубль</b> (решил @{callback.from_user.username})",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer("Баг помечен как дубль")
    await log_info(f"Баг #{bug_id} помечен как дубль (@{callback.from_user.username})")


@router.callback_query(F.data.startswith("dup_notdup:"))
async def handle_dup_notdup(callback: CallbackQuery):
    """Владелец решил: не дубль — принимаем баг, начисляем баллы, показываем доски."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    points = bug["points_awarded"]

    # Принимаем баг и начисляем баллы
    db = await get_db()
    try:
        await db.execute(
            "UPDATE bugs SET status = 'accepted' WHERE id = ?", (bug_id,)
        )
        await db.commit()
    finally:
        await db.close()

    await update_tester_points(bug["tester_id"], points)
    if bug["type"] == "crash":
        await update_tester_stats(bug["tester_id"], crashes=1)
    else:
        await update_tester_stats(bug["tester_id"], bugs=1)

    # Уведомляем тестера
    bot = get_bot()
    if bot:
        try:
            emoji = "💥" if bug["type"] == "crash" else "✅"
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"{emoji} Твой {'краш' if bug['type'] == 'crash' else 'баг'} "
                    f"<b>#{bug_id}</b> принят! +{points} б. 🎉"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Показываем выбор доски Weeek
    await _show_board_selection(callback, bug_id)
    await callback.answer(f"Не дубль — баг #{bug_id} принят, +{points} б.")
    await log_info(
        f"Баг #{bug_id} — не дубль, принят владельцем @{callback.from_user.username}, +{points} б."
    )


@router.callback_query(F.data.startswith("dup_yes:"))
async def handle_dup_yes(callback: CallbackQuery):
    """Админ подтвердил: это дубль (старый флоу)."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    await mark_duplicate(bug_id)

    await callback.message.edit_text(
        (callback.message.text or "") + f"\n\n✅ <b>Решение:</b> дубль (подтвердил @{callback.from_user.username})",
        parse_mode="HTML",
    )
    await callback.answer("Баг помечен как дубль")
    await log_info(f"Баг #{bug_id} помечен как дубль (@{callback.from_user.username})")


@router.callback_query(F.data.startswith("dup_no:"))
async def handle_dup_no(callback: CallbackQuery):
    """Админ решил: не дубль, принять."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
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

    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Защита от двойного нажатия
    if bug["status"] == "accepted":
        await callback.answer("Баг уже принят", show_alert=True)
        return

    db = await get_db()
    try:
        await db.execute(
            "UPDATE bugs SET status = 'accepted', points_awarded = ? WHERE id = ?",
            (points, bug_id),
        )
        await db.commit()
    finally:
        await db.close()

    await update_tester_points(bug["tester_id"], points)
    if bug["type"] == "crash":
        await update_tester_stats(bug["tester_id"], crashes=1)
    else:
        await update_tester_stats(bug["tester_id"], bugs=1)

    from services.weeek_service import create_task as weeek_create_task
    weeek_result = await weeek_create_task(
        title=bug["title"],
        description=bug["description"],
        bug_type=bug["type"],
        bug_id=bug_id,
    )
    weeek_info = " + Weeek ✅" if weeek_result.get("success") else ""

    await callback.message.edit_text(
        (callback.message.text or "") + (
            f"\n\n✅ <b>Решение:</b> принят, +{points} б. "
            f"(@{callback.from_user.username}){weeek_info}"
        ),
        parse_mode="HTML",
    )
    await callback.answer(f"Баг #{bug_id} принят, +{points} баллов")
    await log_admin(f"Баг #{bug_id} принят (не дубль) @{callback.from_user.username}, +{points} б.")


@router.callback_query(F.data.startswith("weeek:"))
async def handle_weeek_board_legacy(callback: CallbackQuery):
    """Старый формат выбора доски (backward compat)."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может выбирать доску", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

    await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)


# ─────────────────────────────────────────────
#  Отчёты (скриншоты)
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_accept:"))
async def handle_report_accept(callback: CallbackQuery):
    """Админ принял отчёт с определённым количеством игр."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
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

        # Защита от двойного нажатия
        if report.get("status") == "accepted" and report.get("points_awarded", 0) > 0:
            await callback.answer("Отчёт уже принят", show_alert=True)
            return

        points = games

        await db.execute(
            "UPDATE reports SET status = 'accepted', games_count = ?, points_awarded = ? WHERE id = ?",
            (games, points, report_id),
        )
        await db.commit()
    finally:
        await db.close()

    await update_tester_points(report["tester_id"], points)
    await update_tester_stats(report["tester_id"], games=games)

    await callback.message.edit_text(
        (callback.message.text or "") + (
            f"\n\n✅ Принято: {games} игр, +{points} б. (@{callback.from_user.username})"
        ),
        parse_mode="HTML",
    )
    await callback.answer(f"Принято {games} игр")
    await log_info(f"Отчёт #{report_id}: принято {games} игр, +{points} б.")


@router.callback_query(F.data.startswith("report_reject:"))
async def handle_report_reject(callback: CallbackQuery):
    """Админ отклонил отчёт."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    report_id = int(callback.data.split(":")[1])

    db = await get_db()
    try:
        await db.execute("UPDATE reports SET status = 'rejected' WHERE id = ?", (report_id,))
        await db.commit()
    finally:
        await db.close()

    await callback.message.edit_text(
        (callback.message.text or "") + f"\n\n❌ Отклонён (@{callback.from_user.username})",
        parse_mode="HTML",
    )
    await callback.answer("Отчёт отклонён")
    await log_info(f"Отчёт #{report_id} отклонён @{callback.from_user.username}")
