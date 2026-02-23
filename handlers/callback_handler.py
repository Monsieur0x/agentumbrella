"""
Обработка нажатий inline-кнопок:

Новый флоу багов:
- bug_confirm:{bug_id}          — руководитель подтвердил баг → начисляем баллы, показываем доски
- bug_reject:{bug_id}           — руководитель отклонил баг → уведомляем тестера
- weeek_board:{bug_id}:{board}  — руководитель выбрал доску → показываем колонки
- weeek_col:{bug_id}:{board}:{col} — руководитель выбрал колонку → создаём задачу в Weeek
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
from models.bug import mark_duplicate, get_bug, update_bug
from models.tester import update_tester_points, update_tester_stats
from utils.logger import log_info, log_admin, get_bot
from json_store import async_load, async_update, POINTS_LOG_FILE, TASKS_FILE
from datetime import datetime

router = Router()


def _safe_html_text(callback: CallbackQuery) -> str:
    """Возвращает html_text сообщения, безопасный для конкатенации с HTML."""
    msg = callback.message
    # Для медиа-сообщений текст хранится в caption
    if msg.caption is not None:
        return msg.html_text if msg.text else html.escape(msg.caption)
    return msg.html_text or html.escape(msg.text or "")


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None):
    """Редактирует сообщение: edit_caption для медиа, edit_text для текста."""
    msg = callback.message
    if msg.photo or msg.video or msg.document:
        await msg.edit_caption(
            caption=text, parse_mode="HTML", reply_markup=reply_markup,
        )
    else:
        await msg.edit_text(
            text=text, parse_mode="HTML", reply_markup=reply_markup,
        )


async def _set_bug_reactions(bug: dict, emoji: str):
    """Ставит реакцию на все сообщения тестера по багу в топике багов."""
    from aiogram.types import ReactionTypeEmoji
    from config import GROUP_ID
    bot = get_bot()
    if not bot or not GROUP_ID:
        return

    msg_ids = set()
    if bug.get("message_id"):
        msg_ids.add(bug["message_id"])
    for mid in bug.get("media_message_ids", []):
        msg_ids.add(mid)

    print(f"[REACTION] emoji={emoji} на {len(msg_ids)} сообщений")
    for mid in msg_ids:
        try:
            await bot.set_message_reaction(
                chat_id=GROUP_ID,
                message_id=mid,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception as e:
            print(f"[REACTION] ERROR msg_id={mid}: {e}")


async def _add_points_log(tester_id: int, amount: int, reason: str, source: str = "manual", admin_id: int = None):
    """Добавляет запись в лог баллов."""
    def updater(data):
        entry_id = data.get("next_id", 1)
        data["next_id"] = entry_id + 1
        if "items" not in data:
            data["items"] = []
        data["items"].append({
            "id": entry_id,
            "tester_id": tester_id,
            "amount": amount,
            "reason": reason,
            "source": source,
            "admin_id": admin_id,
            "created_at": datetime.now().isoformat(),
        })
        return data

    await async_update(POINTS_LOG_FILE, updater)


# ─────────────────────────────────────────────
#  Выбор режима работы при запуске
# ─────────────────────────────────────────────

@router.callback_query(F.data.in_({"mode_active", "mode_observe", "mode_chat"}))
async def handle_mode_select(callback: CallbackQuery):
    """Руководитель выбирает режим работы бота (при запуске или в рантайме)."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может выбирать режим", show_alert=True)
        return

    import config

    mode = callback.data  # "mode_active", "mode_observe" или "mode_chat"
    config.BOT_MODE = mode.replace("mode_", "")  # "active", "observe" или "chat"
    print(f"[CALLBACK] mode_select → {config.BOT_MODE} by @{callback.from_user.username}")

    labels = {"active": "✅ Рабочий режим", "observe": "👁 Режим наблюдения", "chat": "💬 Чат-режим"}
    label = labels.get(config.BOT_MODE, config.BOT_MODE)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Рабочий режим", callback_data="mode_active"),
            InlineKeyboardButton(text="👁 Наблюдение", callback_data="mode_observe"),
            InlineKeyboardButton(text="💬 Чат", callback_data="mode_chat"),
        ]
    ])
    await callback.message.edit_text(
        f"🟢 <b>Umbrella Bot</b>\n\n"
        f"Режим: <b>{label}</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer(f"Выбран: {label}")


async def _accept_bug(bug_id: int, bug: dict, admin_id: int) -> int:
    """Общая логика принятия бага: статус, баллы, points_log, счётчики. Возвращает начисленные баллы."""
    points = bug["points_awarded"]
    dn = bug.get("display_number") or bug_id
    print(f"[BUG-ACCEPT] #{dn} tester={bug['tester_id']}, +{points} б.")

    await update_bug(bug_id, status="accepted")
    await _set_bug_reactions(bug, "👍")
    await update_tester_points(bug["tester_id"], points)
    await update_tester_stats(bug["tester_id"], bugs=1)

    # Запись в points_log
    await _add_points_log(bug["tester_id"], points, f"Баг #{dn} принят", "bug", admin_id)

    # Уведомляем тестера в ЛС
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"✅ Твой баг "
                    f"<b>#{dn}</b> принят! +{points} б. 🎉"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    return points


# ─────────────────────────────────────────────
#  Тестер: добавить материалы / отправить как есть
# ─────────────────────────────────────────────

async def _validate_bug_button(callback: CallbackQuery) -> int | None:
    """Проверяет кнопку тестера: баг существует, waiting_media, его баг."""
    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return None
    if bug["status"] != "waiting_media":
        await callback.answer("Баг уже обработан", show_alert=True)
        return None
    if callback.from_user.id != bug["tester_id"]:
        await callback.answer("Это не твой баг", show_alert=True)
        return None
    return bug_id


@router.callback_query(F.data.startswith("bug_add_media:"))
async def handle_bug_add_media(callback: CallbackQuery):
    """Тестер хочет добавить материалы — бот ждёт файлы/ссылки, потом «Готово»."""
    bug_id = await _validate_bug_button(callback)
    if bug_id is None:
        return
    print(f"[CALLBACK] bug_add_media:{bug_id} by @{callback.from_user.username}")

    bug = await get_bug(bug_id)
    dn = bug.get("display_number") or bug_id

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Готово",
            callback_data=f"bug_send:{bug_id}",
        )],
    ])

    await callback.message.edit_text(
        f"⏳ Баг <b>#{dn}</b>: жду материалы.\n"
        f"Отправь файлы, скрины или ссылку на видео, потом нажми <b>«Готово»</b>.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bug_send:"))
async def handle_bug_send(callback: CallbackQuery):
    """Тестер нажал «Готово» — отправляем баг руководителю."""
    bug_id = await _validate_bug_button(callback)
    if bug_id is None:
        return
    print(f"[CALLBACK] bug_send:{bug_id} by @{callback.from_user.username}")

    bug = await get_bug(bug_id)
    dn = bug.get("display_number") or bug_id

    from handlers.bug_handler import submit_bug_as_is, _delete_after
    success = await submit_bug_as_is(bug_id)

    if success:
        await callback.message.edit_text(
            f"🐛 Баг <b>#{dn}</b> отправлен на подтверждение ⏳",
            parse_mode="HTML", reply_markup=None,
        )
        await callback.answer("Баг отправлен")
    else:
        await callback.message.edit_text(
            f"⚠️ Баг <b>#{dn}</b> сохранён, но не удалось уведомить руководителя.",
            parse_mode="HTML", reply_markup=None,
        )
        await callback.answer("Ошибка отправки", show_alert=True)

    # Автоудаление через 5 сек
    import asyncio
    from utils.logger import get_bot
    bot = get_bot()
    if bot:
        asyncio.create_task(_delete_after(
            bot, callback.message.chat.id, callback.message.message_id, 5,
        ))


@router.callback_query(F.data.startswith("bug_skip_both:"))
async def handle_bug_skip_both(callback: CallbackQuery):
    """Отправить как есть — без недостающих материалов."""
    bug_id = await _validate_bug_button(callback)
    if bug_id is None:
        return
    print(f"[CALLBACK] bug_skip_both:{bug_id} by @{callback.from_user.username}")

    bug = await get_bug(bug_id)
    dn = bug.get("display_number") or bug_id if bug else bug_id

    from handlers.bug_handler import submit_bug_as_is, _delete_after
    success = await submit_bug_as_is(bug_id)

    if success:
        await callback.message.edit_text(
            f"🐛 Баг <b>#{dn}</b> отправлен на подтверждение ⏳",
            parse_mode="HTML", reply_markup=None,
        )
        await callback.answer("Баг отправлен")
    else:
        await callback.message.edit_text(
            f"⚠️ Баг <b>#{dn}</b> сохранён, но не удалось уведомить руководителя.",
            parse_mode="HTML", reply_markup=None,
        )
        await callback.answer("Ошибка отправки", show_alert=True)

    # Автоудаление через 5 сек
    import asyncio
    from utils.logger import get_bot
    bot = get_bot()
    if bot:
        asyncio.create_task(_delete_after(
            bot, callback.message.chat.id, callback.message.message_id, 5,
        ))


# ─────────────────────────────────────────────
#  Подтверждение бага руководителем
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("bug_confirm:"))
async def handle_bug_confirm(callback: CallbackQuery):
    """Руководитель подтвердил баг: начисляем баллы и показываем выбор доски Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может подтверждать", show_alert=True)
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

    dn = bug.get("display_number") or bug_id
    print(f"[CALLBACK] bug_confirm:{bug_id} by @{callback.from_user.username}")
    points = await _accept_bug(bug_id, bug, callback.from_user.id)

    # Показываем выбор доски Weeek
    await _show_board_selection(callback, bug_id)
    await callback.answer(f"Баг #{dn} подтверждён, +{points} б.")
    await log_info(
        f"Баг #{dn} подтверждён руководителем {callback.from_user.username}, +{points} б."
    )


@router.callback_query(F.data.startswith("bug_reject:"))
async def handle_bug_reject(callback: CallbackQuery):
    """Руководитель отклонил баг: уведомляем тестера."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может отклонять", show_alert=True)
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

    dn = bug.get("display_number") or bug_id
    print(f"[CALLBACK] bug_reject:{bug_id} by @{callback.from_user.username}")

    await update_bug(bug_id, status="rejected")
    await _set_bug_reactions(bug, "👎")

    # Уведомляем тестера
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"❌ Твой баг "
                    f"<b>#{dn}</b> был отклонён."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await _safe_edit(
        callback,
        _safe_html_text(callback) + f"\n\n❌ <b>Отклонён</b> ({callback.from_user.username})",
    )
    await callback.answer(f"Баг #{dn} отклонён")
    print(f"[BUG-REJECT] #{dn} by @{callback.from_user.username}")
    await log_info(f"Баг #{dn} отклонён руководителем {callback.from_user.username}")


async def _show_board_selection(callback: CallbackQuery, bug_id: int):
    """Редактирует сообщение, подставляя кнопки выбора доски Weeek."""
    import config
    from services.weeek_service import get_cached_boards

    boards = get_cached_boards() if config.WEEEK_ENABLED else []
    if not boards:
        weeek_note = "Weeek отключён" if not config.WEEEK_ENABLED else "Weeek не настроен"
        await _safe_edit(
            callback,
            _safe_html_text(callback) + f"\n\n✅ <b>Подтверждён</b> ({weeek_note})",
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

    await _safe_edit(
        callback,
        _safe_html_text(callback) + "\n\n✅ <b>Подтверждён!</b> Выберите доску Weeek:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ─────────────────────────────────────────────
#  Выбор доски → колонки → создание задачи
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("weeek_board:"))
async def handle_weeek_board_select(callback: CallbackQuery):
    """Руководитель выбрал доску — показываем колонки этой доски."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    print(f"[CALLBACK] weeek_board:{bug_id}:{board_id} by @{callback.from_user.username}")

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
    """Руководитель выбрал колонку — создаём задачу в Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 else None
    print(f"[CALLBACK] weeek_col:{bug_id}:{board_id}:{col_id} by @{callback.from_user.username}")

    await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)


async def _create_weeek_task_and_finish(
    callback: CallbackQuery, bug_id: int, board_id: int, col_id: int | None
):
    """Создаёт задачу в Weeek и обновляет сообщение руководителя."""
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    from services.weeek_service import create_task as weeek_create_task, get_cached_boards, upload_attachment

    description = (
        f"Шаги: {bug.get('steps') or bug.get('description', '')}\n"
        f"Видео: {bug.get('youtube_link', '')}"
    )
    print(f"[WEEEK] Создание задачи для бага #{bug_id}, board={board_id}, col={col_id}")
    result = await weeek_create_task(
        title=bug.get("script_name") or bug.get("title", ""),
        description=description,
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
        print(f"[WEEEK] Задача создана: task_id={task_id} для бага #{bug_id}, доска={board_name}")

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

        await update_bug(bug_id, weeek_task_id=task_id, weeek_board_name=board_name, weeek_column_name=col_name)

        # Прикрепляем файлы из Telegram к задаче Weeek
        from handlers.bug_handler import _get_bug_files
        bug_files = _get_bug_files(bug)
        if bug_files and task_id:
            bot = get_bot()
            from io import BytesIO
            ext_map = {"photo": ".jpg", "video": ".mp4", "document": ""}
            for f in bug_files:
                try:
                    tg_file = await bot.get_file(f["file_id"])
                    buffer = BytesIO()
                    await bot.download_file(tg_file.file_path, buffer)
                    file_bytes = buffer.getvalue()

                    if tg_file.file_path:
                        filename = tg_file.file_path.split("/")[-1]
                    else:
                        filename = f"bug_{bug_id}{ext_map.get(f.get('file_type', ''), '')}"

                    await upload_attachment(task_id, file_bytes, filename)
                    print(f"[WEEEK] Файл прикреплён: {filename} к задаче #{task_id}")
                except Exception as e:
                    print(f"[WEEEK] ERROR: не удалось прикрепить файл к задаче #{task_id}: {e}")

        await _safe_edit(
            callback,
            _safe_html_text(callback) + f"\n\n📋 Отправлен в Weeek: <b>«{html.escape(board_name)}»</b> ✅",
        )
        await callback.answer(f"Задача создана в {board_name}")
    else:
        await callback.answer(
            f"Ошибка Weeek: {result.get('error', '?')}", show_alert=True
        )


@router.callback_query(F.data.startswith("weeek_skip:"))
async def handle_weeek_skip(callback: CallbackQuery):
    """Руководитель решил не отправлять в Weeek."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель", show_alert=True)
        return

    print(f"[CALLBACK] weeek_skip by @{callback.from_user.username}")
    await _safe_edit(
        callback,
        _safe_html_text(callback) + "\n\n⏭ Не отправлен в Weeek",
    )
    await callback.answer("Пропущено")


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
    print(f"[CALLBACK] task_publish:{task_id} by @{callback.from_user.username}")

    tasks_data = await async_load(TASKS_FILE)
    task = tasks_data.get("items", {}).get(str(task_id))
    if not task:
        await callback.answer("Задание не найдено", show_alert=True)
        return

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

            def update_published(data):
                items = data.get("items", {})
                key = str(task_id)
                if key in items:
                    items[key]["status"] = "published"
                    items[key]["message_id"] = msg.message_id
                return data

            await async_update(TASKS_FILE, update_published)
            published = True
        except Exception as e:
            print(f"[TASK] ERROR публикации задания #{task_id}: {e}")

    if not published:
        def update_status(data):
            items = data.get("items", {})
            key = str(task_id)
            if key in items:
                items[key]["status"] = "published"
            return data

        await async_update(TASKS_FILE, update_status)

    try:
        await callback.message.edit_text(
            _safe_html_text(callback) + "\n\n✅ <b>Опубликовано!</b>",
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
    await callback.answer("Задание опубликовано")
    await log_info(f"Задание #{task_id} опубликовано {callback.from_user.username}")


@router.callback_query(F.data.startswith("task_cancel:"))
async def handle_task_cancel(callback: CallbackQuery):
    """Админ отменил задание."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может отменять", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])
    print(f"[CALLBACK] task_cancel:{task_id} by @{callback.from_user.username}")

    def update_status(data):
        items = data.get("items", {})
        key = str(task_id)
        if key in items:
            items[key]["status"] = "cancelled"
        return data

    await async_update(TASKS_FILE, update_status)

    try:
        await callback.message.edit_text(
            _safe_html_text(callback) + "\n\n❌ <b>Отменено</b>",
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
    await log_info(f"Задание #{task_id} отменено {callback.from_user.username}")


# ─────────────────────────────────────────────
#  РЕЙТИНГ: подтверждение публикации из ЛС
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("rating_publish:"))
async def handle_rating_publish(callback: CallbackQuery):
    """Админ/руководитель подтвердил публикацию рейтинга из ЛС."""
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
            await callback.message.edit_text(
                _safe_html_text(callback) + "\n\n✅ <b>Опубликовано!</b>",
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
        await log_admin(f"Рейтинг опубликован в топик «Топ» ({callback.from_user.username})")
    else:
        await callback.answer("Ошибка публикации", show_alert=True)


@router.callback_query(F.data == "rating_cancel")
async def handle_rating_cancel(callback: CallbackQuery):
    """Админ/руководитель отменил публикацию рейтинга."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может отменять", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            _safe_html_text(callback) + "\n\n❌ <b>Отменено</b>",
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
    "game_ap": "🎮 All Pick",
    "game_turbo": "🎮 Turbo",
}


def build_rewards_menu(pts: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует текст и клавиатуру меню настройки наград."""
    msg_text = (
        "⚙️ <b>Настройка наград</b>\n\n"
        "Текущие значения:\n"
        f"🐛 Баг: <b>{pts['bug_accepted']}</b> б.\n"
        f"🎮 All Pick: <b>{pts['game_ap']}</b> б.\n"
        f"🎮 Turbo: <b>{pts['game_turbo']}</b> б.\n\n"
        "Выберите категорию для изменения:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐛 Награды за баги", callback_data="reward_set:bug_accepted")],
        [InlineKeyboardButton(text="🎮 All Pick", callback_data="reward_set:game_ap")],
        [InlineKeyboardButton(text="🎮 Turbo", callback_data="reward_set:game_turbo")],
    ])
    return msg_text, keyboard


@router.callback_query(F.data.startswith("reward_set:"))
async def handle_reward_set(callback: CallbackQuery):
    """Админ/руководитель выбрал категорию награды — показываем варианты значений."""
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
    """Админ/руководитель выбрал конкретное значение награды."""
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
    print(f"[REWARDS] {reward_type} → {value} by @{callback.from_user.username}")

    label = _REWARD_LABELS[reward_type]

    # Показываем обновлённое меню наград
    pts = await get_points_config()
    msg_text, keyboard = build_rewards_menu(pts)
    msg_text = f"✅ {label} установлен: <b>{value}</b> б.\n\n" + msg_text

    await callback.message.edit_text(msg_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer(f"{label}: {value} б.")
    await log_info(f"Награда {reward_type} изменена на {value} ({callback.from_user.username})")


@router.callback_query(F.data.startswith("reward_custom:"))
async def handle_reward_custom(callback: CallbackQuery):
    """Админ/руководитель хочет ввести своё значение — ставим ожидание."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может настраивать награды", show_alert=True)
        return

    reward_type = callback.data.split(":")[1]
    if reward_type not in _REWARD_LABELS:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    from handlers.message_router import _pending_reward_input
    import time
    _pending_reward_input[callback.from_user.id] = (reward_type, time.time())

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
    msg_text, keyboard = build_rewards_menu(pts)

    await callback.message.edit_text(msg_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ─────────────────────────────────────────────
#  Старый флоу (backward compat)
# ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("dup_confirm:"))
async def handle_dup_confirm(callback: CallbackQuery):
    """Руководитель подтвердил: это дубль — помечаем и уведомляем тестера."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    dn = bug.get("display_number") or bug_id
    print(f"[CALLBACK] dup_confirm:{bug_id} by @{callback.from_user.username}")
    await mark_duplicate(bug_id)
    await _set_bug_reactions(bug, "👎")

    # Уведомляем тестера
    bot = get_bot()
    if bot:
        try:
            await bot.send_message(
                chat_id=bug["tester_id"],
                text=(
                    f"🔄 Твой баг "
                    f"<b>#{dn}</b> был отклонён как дубль."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await _safe_edit(
        callback,
        _safe_html_text(callback) + f"\n\n🔄 <b>Дубль</b> (решил {callback.from_user.username})",
    )
    await callback.answer("Баг помечен как дубль")
    print(f"[BUG-DUP] #{dn} помечен дублём by @{callback.from_user.username}")
    await log_info(f"Баг #{dn} помечен как дубль ({callback.from_user.username})")


@router.callback_query(F.data.startswith("dup_notdup:"))
async def handle_dup_notdup(callback: CallbackQuery):
    """Руководитель решил: не дубль — принимаем баг, начисляем баллы, показываем доски."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    if bug["status"] != "pending":
        await callback.answer("Баг уже обработан", show_alert=True)
        return

    dn = bug.get("display_number") or bug_id
    print(f"[CALLBACK] dup_notdup:{bug_id} by @{callback.from_user.username}")
    points = await _accept_bug(bug_id, bug, callback.from_user.id)

    # Показываем выбор доски Weeek
    await _show_board_selection(callback, bug_id)
    await callback.answer(f"Не дубль — баг #{dn} принят, +{points} б.")
    await log_info(
        f"Баг #{dn} — не дубль, принят руководителем {callback.from_user.username}, +{points} б."
    )


@router.callback_query(F.data.startswith("dup_yes:"))
async def handle_dup_yes(callback: CallbackQuery):
    """DEPRECATED: старый флоу. Оставлен для кнопок, отправленных до обновления."""
    if not (await is_admin(callback.from_user.id) or await is_owner(callback.from_user.id)):
        await callback.answer("Только админ может решать", show_alert=True)
        return

    bug_id = int(callback.data.split(":")[1])
    bug = await get_bug(bug_id)
    if not bug:
        await callback.answer("Баг не найден", show_alert=True)
        return

    dn = bug.get("display_number") or bug_id
    await mark_duplicate(bug_id)

    await _safe_edit(
        callback,
        _safe_html_text(callback) + f"\n\n✅ <b>Решение:</b> дубль (подтвердил {callback.from_user.username})",
    )
    await callback.answer("Баг помечен как дубль")
    await log_info(f"Баг #{dn} помечен как дубль ({callback.from_user.username})")


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

    dn = bug.get("display_number") or bug_id

    points = await _accept_bug(bug_id, bug, callback.from_user.id)

    from services.weeek_service import create_task as weeek_create_task
    weeek_result = await weeek_create_task(
        title=bug.get("script_name") or bug.get("title", ""),
        description=bug.get("steps") or bug.get("description", ""),
        bug_id=bug_id,
    )
    weeek_info = " + Weeek ✅" if weeek_result.get("success") else ""

    await _safe_edit(
        callback,
        _safe_html_text(callback) + (
            f"\n\n✅ <b>Решение:</b> принят, +{points} б. "
            f"({callback.from_user.username}){weeek_info}"
        ),
    )
    await callback.answer(f"Баг #{dn} принят, +{points} баллов")
    await log_admin(f"Баг #{dn} принят (не дубль) {callback.from_user.username}, +{points} б.")


@router.callback_query(F.data.startswith("weeek:"))
async def handle_weeek_board_legacy(callback: CallbackQuery):
    """DEPRECATED: старый формат выбора доски. Оставлен для кнопок, отправленных до обновления."""
    if not await is_owner(callback.from_user.id):
        await callback.answer("Только руководитель может выбирать доску", show_alert=True)
        return

    parts = callback.data.split(":")
    bug_id = int(parts[1])
    board_id = int(parts[2])
    col_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

    await _create_weeek_task_and_finish(callback, bug_id, board_id, col_id)
