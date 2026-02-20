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
#  Выбор режима работы при запуске
# ─────────────────────────────────────────────

@router.callback_query(F.data.in_({"mode_active", "mode_observe"}))
async def handle_mode_select(callback: CallbackQuery):
    """Владелец выбирает режим работы бота (при запуске или в рантайме)."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может выбирать режим", show_alert=True)
        return

    import config

    mode = callback.data  # "mode_active" или "mode_observe"
    config.BOT_MODE = mode.replace("mode_", "")  # "active" или "observe"

    labels = {"active": "✅ Рабочий режим", "observe": "👁 Режим наблюдения"}
    label = labels.get(config.BOT_MODE, config.BOT_MODE)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Рабочий режим", callback_data="mode_active"),
            InlineKeyboardButton(text="👁 Режим наблюдения", callback_data="mode_observe"),
        ]
    ])
    await callback.message.edit_text(
        f"🟢 <b>Umbrella Bot</b>\n\n"
        f"Режим: <b>{label}</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer(f"Выбран: {label}")

    # При первом запуске — разблокируем старт
    try:
        from bot import mode_selected_event
        mode_selected_event.set()
    except ImportError:
        pass


async def _accept_bug(bug_id: int, bug: dict, admin_id: int) -> int:
    """Общая логика принятия бага: статус, баллы, points_log, счётчики. Возвращает начисленные баллы."""
    points = bug["points_awarded"]

    db = await get_db()
    await db.execute(
        "UPDATE bugs SET status = 'accepted' WHERE id = ?", (bug_id,)
    )
    await db.commit()

    await update_tester_points(bug["tester_id"], points)
    if bug["type"] == "crash":
        await update_tester_stats(bug["tester_id"], crashes=1)
    else:
        await update_tester_stats(bug["tester_id"], bugs=1)

    # Запись в points_log
    db = await get_db()
    await db.execute(
        "INSERT INTO points_log (tester_id, amount, reason, source, admin_id) VALUES (?, ?, ?, ?, ?)",
        (bug["tester_id"], points,
         f"{'Краш' if bug['type'] == 'crash' else 'Баг'} #{bug_id} принят",
         "bug", admin_id)
    )
    await db.commit()

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

    return points


# ─────────────────────────────────────────────
#  Тестер: отправить без файла / прикрепить файл
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("bug_nofile_yes:"))
async def handle_bug_nofile_yes(callback: CallbackQuery):
    """Тестер решил отправить баг без файла."""
    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "waiting_file":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    # Только автор бага может нажимать
    if callback.from_user.id != bug["tester_id"]:
        await callback.answer("Это не твой баг", show_alert=True)
        return

    from handlers.bug_handler import submit_bug_without_file
    success = await submit_bug_without_file(bug_id)

    if success:
        await callback.message.edit_text(
            f"🐛 Баг <b>#{bug_id}</b> отправлен владельцу на подтверждение ⏳",
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer("Баг отправлен")
    else:
        await callback.answer("Не удалось отправить баг", show_alert=True)


@router.callback_query(F.data.startswith("bug_nofile_no:"))
async def handle_bug_nofile_no(callback: CallbackQuery):
    """Тестер хочет прикрепить файл — ждём."""
    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "waiting_file":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    if callback.from_user.id != bug["tester_id"]:
        await callback.answer("Это не твой баг", show_alert=True)
        return

    await callback.message.edit_text(
        f"📎 Отправь файл в этот топик — он прикрепится к багу <b>#{bug_id}</b>.",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer()


# ─────────────────────────────────────────────
#  Подтверждение бага владельцем
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

    points = await _accept_bug(bug_id, bug, callback.from_user.id)

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
    await db.execute("UPDATE bugs SET status = 'rejected' WHERE id = ?", (bug_id,))
    await db.commit()

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
    import config
    from services.weeek_service import get_cached_boards

    boards = get_cached_boards() if config.WEEEK_ENABLED else []
    if not boards:
        weeek_note = "Weeek отключён" if not config.WEEEK_ENABLED else "Weeek не настроен"
        await callback.message.edit_text(
            (callback.message.text or "") + f"\n\n✅ <b>Подтверждён</b> ({weeek_note})",
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

        # Определяем имя колонки
        col_name = ""
        if col_id:
            from services.weeek_service import get_board_columns as _get_cols
            try:
                cols = await _get_cols(board_id)
                for c in cols:
                    if c.get("id") == col_id:
                        col_name = c.get("name", "")
                        break
            except Exception:
                pass

        db = await get_db()
        await db.execute(
            "UPDATE bugs SET weeek_task_id = ?, weeek_board_name = ?, weeek_column_name = ? WHERE id = ?",
            (task_id, board_name, col_name, bug_id),
        )
        await db.commit()

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
            f"📝 Баги → топик «Баги», скрины → «Отчёты»."
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
    await db.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
    await db.commit()

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
#  РЕЙТИНГ: подтверждение публикации из ЛС
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("rating_publish:"))
async def handle_rating_publish(callback: CallbackQuery):
    """Админ/владелец подтвердил публикацию рейтинга из ЛС."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может публиковать", show_alert=True)
        return

    parts = callback.data.split(":")
    top_count = int(parts[1]) if len(parts) > 1 and parts[1] else 0

    from services.rating_service import get_rating, publish_rating_to_topic

    data = await get_rating(top_count)
    bot = get_bot()
    if not bot:
        await callback.answer("Бот недоступен", show_alert=True)
        return

    msg_id = await publish_rating_to_topic(bot, data, "")
    if msg_id:
        try:
            original_html = callback.message.html_text or html.escape(callback.message.text or "")
            await callback.message.edit_text(
                original_html + "\n\n✅ <b>Опубликовано!</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            try:
                await callback.message.edit_text(
                    (callback.message.text or "") + "\n\n✅ Опубликовано!",
                    reply_markup=None,
                )
            except Exception:
                pass
        await callback.answer("Рейтинг опубликован")
        await log_admin(f"Рейтинг опубликован в топик «Топ» (@{callback.from_user.username})")
    else:
        await callback.answer("Ошибка публикации", show_alert=True)


@router.callback_query(F.data == "rating_cancel")
async def handle_rating_cancel(callback: CallbackQuery):
    """Админ/владелец отменил публикацию рейтинга."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может отменять", show_alert=True)
        return

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
    await callback.answer("Публикация отменена")


# ─────────────────────────────────────────────
#  НАСТРОЙКА НАГРАД
# ─────────────────────────────────────────────

_REWARD_LABELS = {
    "bug_accepted": "🐛 Баг",
    "crash_accepted": "💥 Краш",
    "game_played": "🎮 Игра",
}


@router.callback_query(F.data.startswith("reward_set:"))
async def handle_reward_set(callback: CallbackQuery):
    """Админ/владелец выбрал категорию награды — показываем варианты значений."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может настраивать награды", show_alert=True)
        return

    reward_type = callback.data.split(":")[1]
    if reward_type not in _REWARD_LABELS:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    from models.settings import get_points_config
    pts = await get_points_config()
    current = pts.get(reward_type, 0)
    label = _REWARD_LABELS[reward_type]

    rows = []
    row = []
    for val in [1, 2, 3, 4, 5]:
        marker = " ✓" if val == current else ""
        row.append(InlineKeyboardButton(
            text=f"{val}{marker}",
            callback_data=f"reward_val:{reward_type}:{val}",
        ))
    rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✏️ Своё значение",
        callback_data=f"reward_custom:{reward_type}",
    )])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад",
        callback_data="rewards_menu",
    )])

    await callback.message.edit_text(
        f"⚙️ <b>{label}</b>\n\n"
        f"Текущее значение: <b>{current}</b> б.\n\n"
        f"Выберите новое значение:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reward_val:"))
async def handle_reward_val(callback: CallbackQuery):
    """Админ/владелец выбрал конкретное значение награды."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может настраивать награды", show_alert=True)
        return

    parts = callback.data.split(":")
    reward_type = parts[1]
    value = int(parts[2])

    if reward_type not in _REWARD_LABELS:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    from models.settings import set_points_value, get_points_config
    await set_points_value(reward_type, value)

    label = _REWARD_LABELS[reward_type]

    # Показываем обновлённое меню наград
    pts = await get_points_config()
    msg_text = (
        f"✅ {label} установлен: <b>{value}</b> б.\n\n"
        "⚙️ <b>Настройка наград</b>\n\n"
        "Текущие значения:\n"
        f"🐛 Баг: <b>{pts['bug_accepted']}</b> б.\n"
        f"💥 Краш: <b>{pts['crash_accepted']}</b> б.\n"
        f"🎮 Игра: <b>{pts['game_played']}</b> б.\n\n"
        "Выберите категорию для изменения:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐛 Награды за баги", callback_data="reward_set:bug_accepted")],
        [InlineKeyboardButton(text="💥 Награды за краши", callback_data="reward_set:crash_accepted")],
        [InlineKeyboardButton(text="🎮 Награды за игры", callback_data="reward_set:game_played")],
    ])

    await callback.message.edit_text(msg_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer(f"{label}: {value} б.")
    await log_info(f"Награда {reward_type} изменена на {value} (@{callback.from_user.username})")


@router.callback_query(F.data.startswith("reward_custom:"))
async def handle_reward_custom(callback: CallbackQuery):
    """Админ/владелец хочет ввести своё значение — ставим ожидание."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может настраивать награды", show_alert=True)
        return

    reward_type = callback.data.split(":")[1]
    if reward_type not in _REWARD_LABELS:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    from handlers.message_router import _pending_reward_input
    _pending_reward_input[callback.from_user.id] = reward_type

    label = _REWARD_LABELS[reward_type]
    await callback.message.edit_text(
        f"✏️ <b>{label}</b>\n\n"
        f"Введите количество баллов (положительное целое число):",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "rewards_menu")
async def handle_rewards_menu(callback: CallbackQuery):
    """Возврат в главное меню настройки наград."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может настраивать награды", show_alert=True)
        return

    from models.settings import get_points_config
    pts = await get_points_config()

    msg_text = (
        "⚙️ <b>Настройка наград</b>\n\n"
        "Текущие значения:\n"
        f"🐛 Баг: <b>{pts['bug_accepted']}</b> б.\n"
        f"💥 Краш: <b>{pts['crash_accepted']}</b> б.\n"
        f"🎮 Игра: <b>{pts['game_played']}</b> б.\n\n"
        "Выберите категорию для изменения:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐛 Награды за баги", callback_data="reward_set:bug_accepted")],
        [InlineKeyboardButton(text="💥 Награды за краши", callback_data="reward_set:crash_accepted")],
        [InlineKeyboardButton(text="🎮 Награды за игры", callback_data="reward_set:game_played")],
    ])

    await callback.message.edit_text(msg_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


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

    points = await _accept_bug(bug_id, bug, callback.from_user.id)

    # Показываем выбор доски Weeek
    await _show_board_selection(callback, bug_id)
    await callback.answer(f"Не дубль — баг #{bug_id} принят, +{points} б.")
    await log_info(
        f"Баг #{bug_id} — не дубль, принят владельцем @{callback.from_user.username}, +{points} б."
    )


@router.callback_query(F.data.startswith("dup_yes:"))
async def handle_dup_yes(callback: CallbackQuery):
    """DEPRECATED: старый флоу. Оставлен для кнопок, отправленных до обновления."""
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
    """DEPRECATED: старый флоу. Оставлен для кнопок, отправленных до обновления."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    parts = callback.data.split(":")
    try:
        bug_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return

    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    # Защита от двойного нажатия
    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    points = bug["points_awarded"] or 3

    points = await _accept_bug(bug_id, bug, callback.from_user.id)

    from services.weeek_service import create_task as weeek_create_task
    weeek_result = await weeek_create_task(
        title=bug.get("script_name") or bug.get("title", ""),
        description=bug.get("steps") or bug.get("description", ""),
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
    """DEPRECATED: старый формат выбора доски. Оставлен для кнопок, отправленных до обновления."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только владелец может выбирать доску", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

    await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)

