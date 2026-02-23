"""
Обработка багрепортов.

Любое сообщение с #баг в топике багов — баг-репорт.
Если нет видео или файла, бот спрашивает кнопками (одно сообщение,
редактируется на каждом этапе, автоудаляется после отправки).
"""
import asyncio
import re
import html
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from models.tester import get_or_create_tester, get_tester_by_id
from models.bug import create_bug, get_bug, update_bug
from config import OWNER_TELEGRAM_ID
from utils.logger import log_info

YOUTUBE_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=[\w-]+|shorts/[\w-]+)|youtu\.be/[\w-]+)',
    re.IGNORECASE,
)


def _extract_youtube_link(text: str) -> str | None:
    """Ищет YouTube-ссылку в тексте. Возвращает URL или None."""
    match = YOUTUBE_RE.search(text)
    return match.group(0) if match else None


def _extract_script_name(text: str) -> str:
    """Извлекает текст сообщения без хештега и YouTube-ссылки."""
    clean = YOUTUBE_RE.sub('', text)
    clean = re.sub(r'#баг\b', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def _get_file_info(message: Message) -> tuple[str | None, str | None]:
    """Определяет file_id и file_type из сообщения."""
    if message.document:
        return message.document.file_id, "document"
    if message.video:
        return message.video.file_id, "video"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.video_note:
        return message.video_note.file_id, "video"
    return None, None


def _collect_files(messages: list[Message]) -> list[dict]:
    """Извлекает все файлы из списка сообщений."""
    files = []
    for msg in messages:
        fid, ftype = _get_file_info(msg)
        if fid:
            files.append({"file_id": fid, "file_type": ftype})
    return files


def _get_bug_files(bug: dict) -> list[dict]:
    """Возвращает список файлов бага (совместимо со старым и новым форматом)."""
    files = bug.get("files")
    if files:
        return files
    fid = bug.get("file_id")
    ftype = bug.get("file_type")
    if fid:
        return [{"file_id": fid, "file_type": ftype or ""}]
    return []


# ─────────────────────────────────────────────
#  Хелперы: reply + автоудаление
# ─────────────────────────────────────────────

async def _delete_after(bot, chat_id: int, message_id: int, delay: float = 5):
    """Удаляет сообщение через delay секунд (фоновая задача)."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _reply_and_delete(message: Message, text: str, delay: float = 5):
    """Отправляет reply и удаляет его через delay секунд."""
    from utils.logger import get_bot
    reply = await message.reply(text, parse_mode="HTML")
    bot = get_bot()
    if bot and reply:
        asyncio.create_task(_delete_after(bot, reply.chat.id, reply.message_id, delay))



# ─────────────────────────────────────────────
#  Уведомление руководителя
# ─────────────────────────────────────────────

async def _check_and_notify_owner(bug_id: int, display_number: int,
                                  script_name: str, youtube_link: str,
                                  files: list[dict],
                                  username: str, points: int) -> bool:
    """Проверяет на дубли и уведомляет руководителя. Возвращает True при успехе."""
    dup_result = None
    try:
        from services.duplicate_checker import check_duplicate
        dup_result = await check_duplicate(script_name, "")
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")

    dup_info = None
    if dup_result and dup_result.get("is_duplicate"):
        dup_info = {
            "similar_bug_id": dup_result.get("similar_bug_id"),
            "explanation": dup_result.get("explanation", ""),
        }

    return await _notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=script_name, youtube_link=youtube_link,
        files=files,
        username=username, points=points,
        dup_info=dup_info,
    )


# ─────────────────────────────────────────────
#  Основной обработчик багрепорта
# ─────────────────────────────────────────────

async def handle_bug_report(message: Message, media_messages: list[Message] | None = None):
    """Обрабатывает сообщение (или несколько сообщений) в топике багов."""
    user = message.from_user
    all_messages = media_messages or [message]

    # Собираем весь текст из всех сообщений
    all_texts = []
    for msg in all_messages:
        t = msg.caption or msg.text or ""
        if t.strip():
            all_texts.append(t.strip())
    combined_text = " ".join(all_texts) if all_texts else ""

    # Извлекаем данные
    youtube_link = _extract_youtube_link(combined_text)
    script_name = _extract_script_name(combined_text)
    files = _collect_files(all_messages)

    await get_or_create_tester(user.id, user.username, user.full_name)
    from models.settings import get_points_config
    pts = await get_points_config()
    points = pts["bug_accepted"]

    has_video = bool(youtube_link)
    has_file = len(files) > 0
    media_msg_ids = [msg.message_id for msg in all_messages]

    # --- Всё на месте → сразу отправляем ---
    if has_video and has_file:
        await _submit_bug(message, user, script_name, youtube_link,
                          files, points, media_msg_ids)
        return

    # --- Чего-то не хватает → одно сообщение с кнопками ---
    bug_id, dn = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link or "",
        files=files,
        bug_type="bug",
        points=points,
        status="waiting_media",
        media_message_ids=media_msg_ids,
    )

    # Описание того, чего не хватает
    missing = []
    if not has_video:
        missing.append("видео")
    if not has_file:
        missing.append("файла")
    missing_text = " и ".join(missing)

    buttons = [
        [InlineKeyboardButton(
            text="📎 Добавить материалы",
            callback_data=f"bug_add_media:{bug_id}",
        )],
        [InlineKeyboardButton(
            text=f"📤 Отправить без {missing_text}",
            callback_data=f"bug_skip_both:{bug_id}",
        )],
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    reply = await message.reply(
        f"⚠️ Баг <b>#{dn}</b>: нет {missing_text}.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Сохраняем message_id ответа бота для дальнейшего edit
    if reply:
        await update_bug(bug_id, bot_message_id=reply.message_id)


async def _submit_bug(message: Message, user, script_name: str,
                      youtube_link: str, files: list[dict], points: int,
                      media_message_ids: list[int] | None = None):
    """Создаёт баг и отправляет руководителю. Ответ → автоудаление."""
    bug_id, display_number = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link,
        files=files,
        bug_type="bug",
        points=points,
        status="pending",
        media_message_ids=media_message_ids,
    )

    username = user.username or user.full_name or str(user.id)

    success = await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=script_name, youtube_link=youtube_link,
        files=files,
        username=username, points=points,
    )

    if success:
        await _reply_and_delete(
            message,
            f"🐛 Баг <b>#{display_number}</b> отправлен на подтверждение ⏳",
        )
    else:
        await _reply_and_delete(
            message,
            f"⚠️ Баг <b>#{display_number}</b> сохранён, но не удалось уведомить руководителя.",
        )

    await log_info(f"Баг #{display_number} от @{username} ожидает подтверждения")


# ─────────────────────────────────────────────
#  Followup: тихое накопление материалов
# ─────────────────────────────────────────────

async def handle_file_followup(message: Message, bug_id: int):
    """Тестер прислал файл для бага в waiting_media. Тихо накапливаем."""
    file_id, file_type = _get_file_info(message)
    if not file_id:
        return

    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_media":
        return

    new_file = {"file_id": file_id, "file_type": file_type}
    existing_files = _get_bug_files(bug)
    all_files = existing_files + [new_file]

    existing_ids = bug.get("media_message_ids", [])
    if message.message_id not in existing_ids:
        existing_ids.append(message.message_id)

    await update_bug(bug_id, file_id=file_id, file_type=file_type, files=all_files, media_message_ids=existing_ids)
    # Не отвечаем — тестер ещё не нажал «Готово»


async def handle_video_followup(message: Message, bug_id: int):
    """Тестер прислал YouTube-ссылку для бага в waiting_media. Тихо сохраняем."""
    text = message.caption or message.text or ""
    youtube_link = _extract_youtube_link(text)
    if not youtube_link:
        return

    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_media":
        return

    existing_ids = bug.get("media_message_ids", [])
    if message.message_id not in existing_ids:
        existing_ids.append(message.message_id)

    await update_bug(bug_id, youtube_link=youtube_link, media_message_ids=existing_ids)
    # Не отвечаем — тестер ещё не нажал «Готово»


async def submit_bug_as_is(bug_id: int) -> bool:
    """Отправляет баг руководителю как есть (по кнопке). Возвращает True при успехе."""
    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_media":
        return False

    await update_bug(bug_id, status="pending")

    tester = await get_tester_by_id(bug["tester_id"])
    username = (tester.get("username") or tester.get("full_name") or
                str(bug["tester_id"])) if tester else str(bug["tester_id"])

    display_number = bug.get("display_number") or bug_id
    files = _get_bug_files(bug)

    success = await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=bug["youtube_link"],
        files=files,
        username=username, points=bug["points_awarded"],
    )

    return success


# ─────────────────────────────────────────────
#  Уведомление руководителя (единая функция)
# ─────────────────────────────────────────────

def _build_bug_text(dn: int, username: str, script_name: str,
                    youtube_link: str, files: list[dict], points: int,
                    dup_info: dict | None = None) -> str:
    """Формирует текст уведомления о баге."""
    video_text = html.escape(youtube_link) if youtube_link else "нет"
    file_count = len(files)
    file_label = f"есть ({file_count} шт.)" if file_count > 1 else ("есть" if file_count == 1 else "нет")

    if dup_info:
        similar_text = f"#{dup_info['similar_bug_id']}" if dup_info.get("similar_bug_id") else "?"
        return (
            f"⚠️ <b>ВОЗМОЖНЫЙ ДУБЛЬ</b>\n\n"
            f"🐛 <b>Баг #{dn}</b>\n"
            f"От: @{html.escape(username)}\n\n"
            f"📄 <b>Описание:</b> {html.escape(script_name or '—')}\n\n"
            f"🎥 <b>Видео:</b> {video_text}\n\n"
            f"📎 <b>Файл:</b> {file_label}\n\n"
            f"🔄 <b>Похож на:</b> баг <b>{similar_text}</b>\n"
            f"💬 <i>{html.escape(dup_info.get('explanation', ''))}</i>\n\n"
            f"💰 Баллов при подтверждении: <b>{points}</b>"
        )

    return (
        f"🐛 <b>Баг #{dn}</b>\n"
        f"От: @{html.escape(username)}\n\n"
        f"📄 <b>Описание:</b> {html.escape(script_name or '—')}\n\n"
        f"🎥 <b>Видео:</b> {video_text}\n\n"
        f"📎 <b>Файл:</b> {file_label}\n\n"
        f"💰 Баллов при подтверждении: <b>{points}</b>"
    )


def _build_keyboard(bug_id: int, dup_info: dict | None = None) -> InlineKeyboardMarkup:
    """Формирует клавиатуру для уведомления руководителя."""
    if dup_info:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔄 Да, это дубль",
                callback_data=f"dup_confirm:{bug_id}",
            )],
            [InlineKeyboardButton(
                text="✅ Не дубль — принять",
                callback_data=f"dup_notdup:{bug_id}",
            )],
            [InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"bug_reject:{bug_id}",
            )],
        ])

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bug_confirm:{bug_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bug_reject:{bug_id}"),
        ]
    ])


async def _notify_owner(bug_id: int, script_name: str,
                        youtube_link: str, files: list[dict],
                        username: str, points: int,
                        display_number: int | None = None,
                        dup_info: dict | None = None) -> bool:
    """Отправляет руководителю DM с деталями бага и кнопками. Возвращает True при успехе."""
    from utils.logger import get_bot

    dn = display_number or bug_id
    bot = get_bot()
    if not bot:
        return False

    text = _build_bug_text(dn, username, script_name, youtube_link,
                           files, points, dup_info)
    keyboard = _build_keyboard(bug_id, dup_info)

    try:
        if len(files) == 1:
            f = files[0]
            fid, ftype = f["file_id"], f["file_type"]
            if ftype == "photo":
                await bot.send_photo(
                    chat_id=OWNER_TELEGRAM_ID, photo=fid,
                    caption=text, parse_mode="HTML", reply_markup=keyboard,
                )
            elif ftype == "video":
                await bot.send_video(
                    chat_id=OWNER_TELEGRAM_ID, video=fid,
                    caption=text, parse_mode="HTML", reply_markup=keyboard,
                )
            elif ftype == "document":
                await bot.send_document(
                    chat_id=OWNER_TELEGRAM_ID, document=fid,
                    caption=text, parse_mode="HTML", reply_markup=keyboard,
                )
            else:
                await bot.send_message(
                    chat_id=OWNER_TELEGRAM_ID, text=text,
                    parse_mode="HTML", reply_markup=keyboard,
                )

        elif len(files) >= 2:
            first = True
            for f in files:
                fid, ftype = f["file_id"], f["file_type"]
                caption = text if first else None
                parse = "HTML" if first else None
                first = False
                if ftype == "photo":
                    await bot.send_photo(
                        chat_id=OWNER_TELEGRAM_ID, photo=fid,
                        caption=caption, parse_mode=parse,
                    )
                elif ftype == "video":
                    await bot.send_video(
                        chat_id=OWNER_TELEGRAM_ID, video=fid,
                        caption=caption, parse_mode=parse,
                    )
                elif ftype == "document":
                    await bot.send_document(
                        chat_id=OWNER_TELEGRAM_ID, document=fid,
                        caption=caption, parse_mode=parse,
                    )
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID,
                text=f"👆 Файлы к багу <b>#{dn}</b>. Выберите действие:",
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        else:
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID, text=text,
                parse_mode="HTML", reply_markup=keyboard,
            )

        return True

    except Exception as e:
        print(f"❌ Не удалось уведомить руководителя о баге #{dn}: {e}")
        return False
