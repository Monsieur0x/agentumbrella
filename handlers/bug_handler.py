"""
Обработка багрепортов — новая система:

Все тестеры отправляют баги в один общий топик.
Обязательные элементы:
  - Название скрипта (текст в сообщении)
  - Видео (ссылка YouTube)
  - Файл (вложение)

Логика проверки:
  1. Нет текста или YouTube-ссылки → блокируем
  2. Есть текст + YouTube, нет файла → спрашиваем "отправить без файла?"
  3. Всё на месте → автоматически в pending владельцу
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

REJECT_MSG = (
    "Баг не принят. Убедись, что в сообщении есть: "
    "название скрипта и ссылка на видео (YouTube). "
    "Исправь и отправь заново."
)

NO_FILE_MSG = "Ты забыл добавить файл. Отправить без файла на проверку?"


def _extract_youtube_link(text: str) -> str | None:
    """Ищет YouTube-ссылку в тексте. Возвращает URL или None."""
    match = YOUTUBE_RE.search(text)
    return match.group(0) if match else None


def _extract_script_name(text: str) -> str:
    """Извлекает текст сообщения без YouTube-ссылки — это название/описание бага."""
    # Убираем YouTube-ссылку из текста
    clean = YOUTUBE_RE.sub('', text).strip()
    # Убираем лишние пробелы
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


async def handle_bug_report(message: Message):
    """Обрабатывает сообщение в топике багов."""
    user = message.from_user
    text = message.caption or message.text or ""

    # Извлекаем данные
    youtube_link = _extract_youtube_link(text)
    script_name = _extract_script_name(text)
    file_id, file_type = _get_file_info(message)

    # --- Проверка 1: нет текста или нет YouTube ---
    if not script_name or not youtube_link:
        await message.reply(REJECT_MSG)
        return

    await get_or_create_tester(user.id, user.username, user.full_name)
    from models.settings import get_points_config
    pts = await get_points_config()
    points = pts["bug_accepted"]

    # --- Проверка 2: нет файла → спрашиваем ---
    if not file_id:
        # Сохраняем баг со статусом waiting_file
        bug_id = await create_bug(
            tester_id=user.id,
            message_id=message.message_id,
            script_name=script_name,
            youtube_link=youtube_link,
            bug_type="bug",
            points=points,
            status="waiting_file",
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отправить без файла",
                    callback_data=f"bug_nofile_yes:{bug_id}",
                ),
                InlineKeyboardButton(
                    text="Нет, прикреплю файл",
                    callback_data=f"bug_nofile_no:{bug_id}",
                ),
            ]
        ])

        await message.reply(NO_FILE_MSG, reply_markup=keyboard)
        return

    # --- Проверка 3: всё на месте → отправляем в pending ---
    await _submit_bug(message, user, script_name, youtube_link,
                      file_id, file_type, points)


async def _submit_bug(message: Message, user, script_name: str,
                      youtube_link: str, file_id: str | None,
                      file_type: str | None, points: int):
    """Создаёт баг в pending и отправляет владельцу."""
    # Проверяем на дубли
    dup_result = None
    try:
        from services.duplicate_checker import check_duplicate
        dup_result = await check_duplicate(script_name, "")
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")

    bug_id = await create_bug(
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

    if dup_result and dup_result.get("is_duplicate"):
        await _notify_owner_duplicate(
            bug_id=bug_id, script_name=script_name,
            youtube_link=youtube_link,
            file_id=file_id, file_type=file_type,
            username=username, points=points,
            similar_bug_id=dup_result.get("similar_bug_id"),
            explanation=dup_result.get("explanation", ""),
        )
    else:
        await _notify_owner(
            bug_id=bug_id, script_name=script_name,
            youtube_link=youtube_link,
            file_id=file_id, file_type=file_type,
            username=username, points=points,
        )

    await message.reply(
        f"🐛 Баг <b>#{bug_id}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )

    await log_info(f"Баг #{bug_id} от @{username} ожидает подтверждения")


async def handle_file_followup(message: Message, bug_id: int):
    """Тестер прислал файл для бага в статусе waiting_file."""
    file_id, file_type = _get_file_info(message)
    if not file_id:
        await message.reply("Прикрепи файл (видео, скриншот или документ) к сообщению.")
        return

    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_file":
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
    points = bug["points_awarded"]

    # Проверяем на дубли
    dup_result = None
    try:
        from services.duplicate_checker import check_duplicate
        dup_result = await check_duplicate(bug["script_name"], "")
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")

    if dup_result and dup_result.get("is_duplicate"):
        await _notify_owner_duplicate(
            bug_id=bug_id, script_name=bug["script_name"],
            youtube_link=bug["youtube_link"],
            file_id=file_id, file_type=file_type,
            username=username, points=points,
            similar_bug_id=dup_result.get("similar_bug_id"),
            explanation=dup_result.get("explanation", ""),
        )
    else:
        await _notify_owner(
            bug_id=bug_id, script_name=bug["script_name"],
            youtube_link=bug["youtube_link"],
            file_id=file_id, file_type=file_type,
            username=username, points=points,
        )

    await message.reply(
        f"🐛 Баг <b>#{bug_id}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )
    await log_info(f"Баг #{bug_id} от @{username} — файл прикреплён, ожидает подтверждения")


async def submit_bug_without_file(bug_id: int):
    """Отправляет баг в pending без файла (по кнопке «Да»)."""
    bug = await get_bug(bug_id)
    if not bug or bug["status"] != "waiting_file":
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

    # Проверяем на дубли
    dup_result = None
    try:
        from services.duplicate_checker import check_duplicate
        dup_result = await check_duplicate(bug["script_name"], "")
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")

    if dup_result and dup_result.get("is_duplicate"):
        await _notify_owner_duplicate(
            bug_id=bug_id, script_name=bug["script_name"],
            youtube_link=bug["youtube_link"],
            file_id=None, file_type=None,
            username=username,
            points=bug["points_awarded"],
            similar_bug_id=dup_result.get("similar_bug_id"),
            explanation=dup_result.get("explanation", ""),
        )
    else:
        await _notify_owner(
            bug_id=bug_id, script_name=bug["script_name"],
            youtube_link=bug["youtube_link"],
            file_id=None, file_type=None,
            username=username,
            points=bug["points_awarded"],
        )

    return True


# ─────────────────────────────────────────────
#  Уведомления владельцу
# ─────────────────────────────────────────────

async def _notify_owner(bug_id: int, script_name: str,
                        youtube_link: str, file_id: str | None,
                        file_type: str | None, username: str, points: int):
    """Отправляет владельцу DM с деталями бага и кнопками."""
    from utils.logger import get_bot

    bot = get_bot()
    if not bot:
        return

    text = (
        f"🐛 <b>Баг #{bug_id}</b>\n"
        f"От: @{html.escape(username)}\n\n"
        f"📄 <b>Описание:</b> {html.escape(script_name)}\n\n"
        f"🎥 <b>Видео:</b> {html.escape(youtube_link)}\n\n"
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
        print(f"❌ Не удалось уведомить владельца о баге #{bug_id}: {e}")


async def _notify_owner_duplicate(bug_id: int, script_name: str,
                                  youtube_link: str, file_id: str | None,
                                  file_type: str | None, username: str,
                                  points: int, similar_bug_id: int | None,
                                  explanation: str):
    """Отправляет владельцу DM с пометкой о возможном дубле."""
    from utils.logger import get_bot

    bot = get_bot()
    if not bot:
        return

    similar_text = f"#{similar_bug_id}" if similar_bug_id else "?"
    text = (
        f"⚠️ <b>ВОЗМОЖНЫЙ ДУБЛЬ</b>\n\n"
        f"🐛 <b>Баг #{bug_id}</b>\n"
        f"От: @{html.escape(username)}\n\n"
        f"📄 <b>Описание:</b> {html.escape(script_name)}\n\n"
        f"🎥 <b>Видео:</b> {html.escape(youtube_link)}\n\n"
        f"📎 <b>Файл:</b> {'есть' if file_id else 'нет'}\n\n"
        f"🔄 <b>Похож на:</b> баг <b>{similar_text}</b>\n"
        f"💬 <i>{html.escape(explanation)}</i>\n\n"
        f"💰 Баллов при подтверждении: <b>{points}</b>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔄 Да, это дубль",
                callback_data=f"dup_confirm:{bug_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Не дубль — принять",
                callback_data=f"dup_notdup:{bug_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"bug_reject:{bug_id}",
            ),
        ],
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
        print(f"❌ Не удалось уведомить владельца о возможном дубле #{bug_id}: {e}")
