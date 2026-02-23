"""
Обработка багрепортов.

Любое сообщение с #баг в топике багов — баг-репорт.
Если нет видео или файла, бот спрашивает кнопками.
Поддержка медиагрупп: скриншот + файл в одном сообщении.
"""
import re
import html
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument,
)
from models.tester import get_or_create_tester, get_tester_by_id
from models.bug import create_bug, get_bug, update_bug
from config import OWNER_TELEGRAM_ID
from utils.logger import log_info

YOUTUBE_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=[\w-]+|shorts/[\w-]+)|youtu\.be/[\w-]+)',
    re.IGNORECASE,
)

MISSING_MEDIA_MSG = "В сообщении не хватает материалов. Что делаем?"


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
    """Извлекает все файлы из списка сообщений (медиагруппы)."""
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
    # Обратная совместимость: старые баги без поля files
    fid = bug.get("file_id")
    ftype = bug.get("file_type")
    if fid:
        return [{"file_id": fid, "file_type": ftype or ""}]
    return []


async def _check_and_notify_owner(bug_id: int, display_number: int,
                                  script_name: str, youtube_link: str,
                                  files: list[dict],
                                  username: str, points: int):
    """Проверяет на дубли и уведомляет владельца."""
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

    await _notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=script_name, youtube_link=youtube_link,
        files=files,
        username=username, points=points,
        dup_info=dup_info,
    )


async def handle_bug_report(message: Message, media_messages: list[Message] | None = None):
    """Обрабатывает сообщение (или медиагруппу) в топике багов.

    media_messages — все сообщения медиагруппы (если есть).
    """
    user = message.from_user
    text = message.caption or message.text or ""

    # Если пришла медиагруппа, берём caption из любого сообщения
    if media_messages and len(media_messages) > 1:
        for msg in media_messages:
            t = msg.caption or msg.text or ""
            if t:
                text = t
                break

    # Извлекаем данные
    youtube_link = _extract_youtube_link(text)
    script_name = _extract_script_name(text)

    # Собираем файлы из всех сообщений медиагруппы
    all_messages = media_messages or [message]
    files = _collect_files(all_messages)

    await get_or_create_tester(user.id, user.username, user.full_name)
    from models.settings import get_points_config
    pts = await get_points_config()
    points = pts["bug_accepted"]

    has_video = bool(youtube_link)
    has_file = len(files) > 0

    # --- Всё на месте → сразу в pending ---
    if has_video and has_file:
        await _submit_bug(message, user, script_name, youtube_link,
                          files, points)
        return

    # --- Чего-то не хватает → спрашиваем кнопками ---
    bug_id, _dn = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link or "",
        files=files,
        bug_type="bug",
        points=points,
        status="waiting_media",
    )

    buttons = []
    if not has_video and has_file:
        buttons.append([InlineKeyboardButton(
            text="📤 Отправить без видео",
            callback_data=f"bug_skip_video:{bug_id}",
        )])
    elif has_video and not has_file:
        buttons.append([InlineKeyboardButton(
            text="📤 Отправить без файла",
            callback_data=f"bug_skip_file:{bug_id}",
        )])
    else:
        # Нет ни видео, ни файла
        buttons.append([
            InlineKeyboardButton(
                text="📤 Без видео",
                callback_data=f"bug_skip_video:{bug_id}",
            ),
            InlineKeyboardButton(
                text="📤 Без файла",
                callback_data=f"bug_skip_file:{bug_id}",
            ),
        ])
        buttons.append([InlineKeyboardButton(
            text="📤 Без видео и файла",
            callback_data=f"bug_skip_both:{bug_id}",
        )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.reply(MISSING_MEDIA_MSG, reply_markup=keyboard)


async def _submit_bug(message: Message, user, script_name: str,
                      youtube_link: str, files: list[dict], points: int):
    """Создаёт баг в pending и отправляет владельцу."""
    bug_id, display_number = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link,
        files=files,
        bug_type="bug",
        points=points,
        status="pending",
    )

    username = user.username or user.full_name or str(user.id)

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=script_name, youtube_link=youtube_link,
        files=files,
        username=username, points=points,
    )

    await message.reply(
        f"🐛 Баг <b>#{display_number}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )

    await log_info(f"Баг #{display_number} от @{username} ожидает подтверждения")


async def handle_file_followup(message: Message, bug_id: int):
    """Тестер прислал файл для бага в статусе waiting_file."""
    file_id, file_type = _get_file_info(message)
    if not file_id:
        await message.reply("Прикрепи файл (видео, скриншот или документ) к сообщению.")
        return

    bug = await get_bug(bug_id)
    if not bug or bug["status"] not in ("waiting_file", "waiting_media"):
        return

    new_file = {"file_id": file_id, "file_type": file_type}
    existing_files = _get_bug_files(bug)
    all_files = existing_files + [new_file]

    # Обновляем баг — прикрепляем файл
    await update_bug(bug_id, file_id=file_id, file_type=file_type,
                     files=all_files, status="pending")

    user = message.from_user
    username = user.username or user.full_name or str(user.id)
    display_number = bug.get("display_number") or bug_id

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=bug["youtube_link"],
        files=all_files,
        username=username, points=bug["points_awarded"],
    )

    await message.reply(
        f"🐛 Баг <b>#{display_number}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )
    await log_info(f"Баг #{display_number} от @{username} — файл прикреплён, ожидает подтверждения")


async def handle_video_followup(message: Message, bug_id: int):
    """Тестер прислал YouTube-ссылку для бага в статусе waiting_video."""
    text = message.caption or message.text or ""
    youtube_link = _extract_youtube_link(text)
    if not youtube_link:
        await message.reply("Отправь ссылку на видео (YouTube).")
        return

    bug = await get_bug(bug_id)
    if not bug or bug["status"] not in ("waiting_video", "waiting_media"):
        return

    # Обновляем баг — прикрепляем видео
    await update_bug(bug_id, youtube_link=youtube_link, status="pending")

    user = message.from_user
    username = user.username or user.full_name or str(user.id)
    display_number = bug.get("display_number") or bug_id
    files = _get_bug_files(bug)

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=youtube_link,
        files=files,
        username=username, points=bug["points_awarded"],
    )

    await message.reply(
        f"🐛 Баг <b>#{display_number}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )
    await log_info(f"Баг #{display_number} от @{username} — видео прикреплено, ожидает подтверждения")


async def submit_bug_as_is(bug_id: int):
    """Отправляет баг в pending как есть (по кнопке skip)."""
    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_media":
        return False

    await update_bug(bug_id, status="pending")

    # Ищем username тестера
    tester = await get_tester_by_id(bug["tester_id"])
    username = (tester.get("username") or tester.get("full_name") or
                str(bug["tester_id"])) if tester else str(bug["tester_id"])

    display_number = bug.get("display_number") or bug_id
    files = _get_bug_files(bug)

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=bug["youtube_link"],
        files=files,
        username=username, points=bug["points_awarded"],
    )

    return True


# ─────────────────────────────────────────────
#  Уведомление владельца (единая функция)
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
    """Формирует клавиатуру для уведомления владельца."""
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
                        dup_info: dict | None = None):
    """Отправляет владельцу DM с деталями бага и кнопками.

    Если есть 1 файл — отправляет его с caption и кнопками (одно сообщение).
    Если файлов несколько — отправляет медиагруппу, затем текст с кнопками.
    Если файлов нет — текстовое сообщение с кнопками.
    """
    from utils.logger import get_bot

    dn = display_number or bug_id
    bot = get_bot()
    if not bot:
        return

    text = _build_bug_text(dn, username, script_name, youtube_link,
                           files, points, dup_info)
    keyboard = _build_keyboard(bug_id, dup_info)

    try:
        if len(files) == 1:
            # Один файл — встраиваем в сообщение с caption и кнопками
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
                # Неизвестный тип — текстом
                await bot.send_message(
                    chat_id=OWNER_TELEGRAM_ID, text=text,
                    parse_mode="HTML", reply_markup=keyboard,
                )

        elif len(files) >= 2:
            # Несколько файлов — медиагруппа, затем текст с кнопками
            media = []
            for f in files:
                fid, ftype = f["file_id"], f["file_type"]
                if ftype == "photo":
                    media.append(InputMediaPhoto(media=fid))
                elif ftype == "video":
                    media.append(InputMediaVideo(media=fid))
                elif ftype == "document":
                    media.append(InputMediaDocument(media=fid))

            if media:
                # Первому элементу ставим caption
                media[0].caption = text
                media[0].parse_mode = "HTML"
                await bot.send_media_group(
                    chat_id=OWNER_TELEGRAM_ID, media=media,
                )
            # Кнопки отдельным сообщением (медиагруппа не поддерживает reply_markup)
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID,
                text=f"👆 Файлы к багу <b>#{dn}</b>. Выберите действие:",
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        else:
            # Нет файлов — просто текст
            await bot.send_message(
                chat_id=OWNER_TELEGRAM_ID, text=text,
                parse_mode="HTML", reply_markup=keyboard,
            )

    except Exception as e:
        print(f"❌ Не удалось уведомить владельца о баге #{dn}: {e}")
