"""
Обработка багрепортов.

Любое сообщение с #баг в топике багов — баг-репорт.
Если нет видео или файла, бот спрашивает кнопками.
"""
import re
import html
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from models.tester import get_or_create_tester
from models.bug import create_bug, get_bug
from config import OWNER_TELEGRAM_ID
from utils.logger import log_info
from database import get_db

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


async def _check_and_notify_owner(bug_id: int, display_number: int,
                                  script_name: str, youtube_link: str,
                                  file_id: str | None, file_type: str | None,
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
        file_id=file_id, file_type=file_type,
        username=username, points=points,
        dup_info=dup_info,
    )


async def handle_bug_report(message: Message):
    """Обрабатывает сообщение в топике багов."""
    user = message.from_user
    text = message.caption or message.text or ""

    # Извлекаем данные
    youtube_link = _extract_youtube_link(text)
    script_name = _extract_script_name(text)
    file_id, file_type = _get_file_info(message)

    await get_or_create_tester(user.id, user.username, user.full_name)
    from models.settings import get_points_config
    pts = await get_points_config()
    points = pts["bug_accepted"]

    has_video = bool(youtube_link)
    has_file = bool(file_id)

    # --- Всё на месте → сразу в pending ---
    if has_video and has_file:
        await _submit_bug(message, user, script_name, youtube_link,
                          file_id, file_type, points)
        return

    # --- Чего-то не хватает → спрашиваем кнопками ---
    bug_id, _dn = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link or "",
        file_id=file_id or "",
        file_type=file_type or "",
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
                      youtube_link: str, file_id: str | None,
                      file_type: str | None, points: int):
    """Создаёт баг в pending и отправляет владельцу."""
    bug_id, display_number = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=script_name,
        youtube_link=youtube_link,
        file_id=file_id or "",
        file_type=file_type or "",
        bug_type="bug",
        points=points,
        status="pending",
    )

    username = user.username or user.full_name or str(user.id)

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=script_name, youtube_link=youtube_link,
        file_id=file_id, file_type=file_type,
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

    # Обновляем баг — прикрепляем файл
    db = await get_db()
    await db.execute(
        "UPDATE bugs SET file_id = ?, file_type = ?, status = 'pending' WHERE id = ?",
        (file_id, file_type, bug_id),
    )
    await db.commit()

    user = message.from_user
    username = user.username or user.full_name or str(user.id)
    display_number = bug.get("display_number") or bug_id

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=bug["youtube_link"],
        file_id=file_id, file_type=file_type,
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
    db = await get_db()
    await db.execute(
        "UPDATE bugs SET youtube_link = ?, status = 'pending' WHERE id = ?",
        (youtube_link, bug_id),
    )
    await db.commit()

    user = message.from_user
    username = user.username or user.full_name or str(user.id)
    display_number = bug.get("display_number") or bug_id

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=youtube_link,
        file_id=bug.get("file_id"), file_type=bug.get("file_type"),
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

    db = await get_db()
    await db.execute(
        "UPDATE bugs SET status = 'pending' WHERE id = ?", (bug_id,),
    )
    await db.commit()

    # Ищем username тестера
    cursor = await db.execute(
        "SELECT username, full_name FROM testers WHERE telegram_id = ?",
        (bug["tester_id"],),
    )
    row = await cursor.fetchone()
    username = (dict(row).get("username") or dict(row).get("full_name") or
                str(bug["tester_id"])) if row else str(bug["tester_id"])

    display_number = bug.get("display_number") or bug_id

    await _check_and_notify_owner(
        bug_id=bug_id, display_number=display_number,
        script_name=bug["script_name"], youtube_link=bug["youtube_link"],
        file_id=bug.get("file_id"), file_type=bug.get("file_type"),
        username=username, points=bug["points_awarded"],
    )

    return True


# ─────────────────────────────────────────────
#  Уведомление владельца (единая функция)
# ─────────────────────────────────────────────

async def _notify_owner(bug_id: int, script_name: str,
                        youtube_link: str, file_id: str | None,
                        file_type: str | None, username: str, points: int,
                        display_number: int | None = None,
                        dup_info: dict | None = None):
    """Отправляет владельцу DM с деталями бага и кнопками.
    dup_info: {"similar_bug_id": int|None, "explanation": str} или None.
    """
    from utils.logger import get_bot

    dn = display_number or bug_id
    bot = get_bot()
    if not bot:
        return

    video_text = html.escape(youtube_link) if youtube_link else "нет"

    if dup_info:
        similar_text = f"#{dup_info['similar_bug_id']}" if dup_info.get("similar_bug_id") else "?"
        text = (
            f"⚠️ <b>ВОЗМОЖНЫЙ ДУБЛЬ</b>\n\n"
            f"🐛 <b>Баг #{dn}</b>\n"
            f"От: @{html.escape(username)}\n\n"
            f"📄 <b>Описание:</b> {html.escape(script_name or '—')}\n\n"
            f"🎥 <b>Видео:</b> {video_text}\n\n"
            f"📎 <b>Файл:</b> {'есть' if file_id else 'нет'}\n\n"
            f"🔄 <b>Похож на:</b> баг <b>{similar_text}</b>\n"
            f"💬 <i>{html.escape(dup_info.get('explanation', ''))}</i>\n\n"
            f"💰 Баллов при подтверждении: <b>{points}</b>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
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
    else:
        text = (
            f"🐛 <b>Баг #{dn}</b>\n"
            f"От: @{html.escape(username)}\n\n"
            f"📄 <b>Описание:</b> {html.escape(script_name or '—')}\n\n"
            f"🎥 <b>Видео:</b> {video_text}\n\n"
            f"📎 <b>Файл:</b> {'есть' if file_id else 'нет'}\n\n"
            f"💰 Баллов при подтверждении: <b>{points}</b>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"bug_confirm:{bug_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bug_reject:{bug_id}"),
            ]
        ])

    try:
        await bot.send_message(
            chat_id=OWNER_TELEGRAM_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        if file_id:
            if file_type == "document":
                await bot.send_document(chat_id=OWNER_TELEGRAM_ID, document=file_id)
            elif file_type == "photo":
                await bot.send_photo(chat_id=OWNER_TELEGRAM_ID, photo=file_id)
            elif file_type == "video":
                await bot.send_video(chat_id=OWNER_TELEGRAM_ID, video=file_id)
    except Exception as e:
        print(f"❌ Не удалось уведомить владельца о баге #{dn}: {e}")
