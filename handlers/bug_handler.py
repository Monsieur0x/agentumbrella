"""
Обработка багрепортов — новый формат:

Тестер присылает сообщение (с прикреплённым файлом):
    Скрипт: Название скрипта
    Шаги: Описание шагов которые привели к проблеме
    Видео: https://youtu.be/ссылка

Бот сохраняет баг как pending и отправляет владельцу на подтверждение.
Все pending-баги накапливаются в очереди — владелец подтверждает каждый независимо.
"""
import re
import html
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from models.tester import get_or_create_tester
from models.bug import create_bug
from config import POINTS, OWNER_TELEGRAM_ID
from utils.logger import log_info

YOUTUBE_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=[\w-]+|youtu\.be/[\w-]+)',
    re.IGNORECASE,
)

FORMAT_HELP = (
    "👋 Оформи баг по шаблону:\n\n"
    "<b>Скрипт:</b> Название скрипта\n"
    "<b>Шаги:</b> Описание шагов которые привели к проблеме\n"
    "<b>Видео:</b> https://youtu.be/ссылка\n\n"
    "И прикрепи файл (видео, лог или скриншот) к сообщению 📎"
)


def parse_bug_report(text: str) -> dict | None:
    """
    Парсит текст багрепорта.
    Возвращает dict с script_name, steps, youtube_link или None если формат неверный.
    """
    if not text:
        return None

    # Убираем хештеги #баг / #краш чтобы не мешали парсингу
    text = re.sub(r'#(?:баг|краш)\b', '', text, flags=re.IGNORECASE).strip()

    result = {"script_name": "", "steps": "", "youtube_link": ""}
    current = None

    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        if lower.startswith("скрипт:") or lower.startswith("script:"):
            result["script_name"] = stripped.split(":", 1)[1].strip()
            current = "script_name"
        elif lower.startswith("шаги:") or lower.startswith("steps:"):
            result["steps"] = stripped.split(":", 1)[1].strip()
            current = "steps"
        elif lower.startswith("видео:") or lower.startswith("video:"):
            result["youtube_link"] = stripped.split(":", 1)[1].strip()
            current = "youtube_link"
        elif current and stripped:
            # Продолжение многострочного поля
            if current == "steps":
                result["steps"] += "\n" + stripped
            elif current == "youtube_link":
                result["youtube_link"] += stripped

    # Извлекаем/валидируем YouTube URL
    match = YOUTUBE_RE.search(result["youtube_link"])
    if match:
        result["youtube_link"] = match.group(0)
    else:
        # Ищем ссылку где угодно в тексте
        match = YOUTUBE_RE.search(text)
        if match:
            result["youtube_link"] = match.group(0)

    if not result["script_name"] or not result["steps"] or not result["youtube_link"]:
        return None

    return result


async def handle_bug_report(message: Message, topic: str, role: str = "tester"):
    """Обрабатывает сообщение в топике багов/крашей."""
    user = message.from_user
    bug_type = "crash" if topic == "crashes" else "bug"

    # Текст — в caption (если есть файл) или в text
    text = message.caption or message.text or ""

    # Определяем прикреплённый файл
    file_id = None
    file_type = None
    if message.document:
        file_id = message.document.file_id
        file_type = "document"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"

    # 1. Парсим формат
    parsed = parse_bug_report(text)
    if not parsed:
        await message.reply(FORMAT_HELP, parse_mode="HTML")
        return

    # 2. Проверяем файл
    if not file_id:
        await message.reply(
            "📎 Не забудь прикрепить файл (видео, лог или скриншот) к сообщению!",
        )
        return

    await get_or_create_tester(user.id, user.username, user.full_name)
    points = POINTS["crash_accepted"] if bug_type == "crash" else POINTS["bug_accepted"]

    # 2.5. Проверяем на дубли
    dup_result = None
    try:
        from services.duplicate_checker import check_duplicate
        dup_result = await check_duplicate(parsed["script_name"], parsed["steps"])
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")

    # 3. Сохраняем как pending
    bug_id = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        script_name=parsed["script_name"],
        steps=parsed["steps"],
        youtube_link=parsed["youtube_link"],
        file_id=file_id,
        file_type=file_type,
        bug_type=bug_type,
        points=points,
        status="pending",
    )

    # 4. Уведомляем владельца
    if dup_result and dup_result.get("is_duplicate"):
        # Возможный дубль — отправляем владельцу на проверку дубля
        await _notify_owner_duplicate(
            bug_id=bug_id,
            bug_type=bug_type,
            script_name=parsed["script_name"],
            steps=parsed["steps"],
            youtube_link=parsed["youtube_link"],
            file_id=file_id,
            file_type=file_type,
            username=user.username or user.full_name or str(user.id),
            points=points,
            similar_bug_id=dup_result.get("similar_bug_id"),
            explanation=dup_result.get("explanation", ""),
        )
    else:
        # Обычный баг — стандартный флоу
        await _notify_owner(
            bug_id=bug_id,
            bug_type=bug_type,
            script_name=parsed["script_name"],
            steps=parsed["steps"],
            youtube_link=parsed["youtube_link"],
            file_id=file_id,
            file_type=file_type,
            username=user.username or user.full_name or str(user.id),
            points=points,
        )

    # 5. Отвечаем тестеру
    emoji = "💥" if bug_type == "crash" else "🐛"
    await message.reply(
        f"{emoji} {'Краш' if bug_type == 'crash' else 'Баг'} <b>#{bug_id}</b> отправлен владельцу на подтверждение ⏳",
        parse_mode="HTML",
    )

    await log_info(
        f"{'Краш' if bug_type == 'crash' else 'Баг'} #{bug_id} от @{user.username} ожидает подтверждения"
    )


async def _notify_owner(bug_id: int, bug_type: str, script_name: str,
                        steps: str, youtube_link: str, file_id: str,
                        file_type: str, username: str, points: int):
    """Отправляет владельцу DM с деталями бага и кнопками Подтвердить/Отклонить."""
    from utils.logger import get_bot

    bot = get_bot()
    if not bot:
        return

    emoji = "💥" if bug_type == "crash" else "🐛"
    text = (
        f"{emoji} <b>{'Краш' if bug_type == 'crash' else 'Баг'} #{bug_id}</b>\n"
        f"От: @{html.escape(username)}\n\n"
        f"📄 <b>Скрипт:</b> {html.escape(script_name)}\n\n"
        f"🔢 <b>Шаги:</b>\n{html.escape(steps)}\n\n"
        f"🎥 <b>Видео:</b> {html.escape(youtube_link)}\n\n"
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
        # Файл отдельным сообщением
        if file_type == "document":
            await bot.send_document(chat_id=OWNER_TELEGRAM_ID, document=file_id)
        elif file_type == "photo":
            await bot.send_photo(chat_id=OWNER_TELEGRAM_ID, photo=file_id)
        elif file_type == "video":
            await bot.send_video(chat_id=OWNER_TELEGRAM_ID, video=file_id)
    except Exception as e:
        print(f"❌ Не удалось уведомить владельца о баге #{bug_id}: {e}")


async def _notify_owner_duplicate(bug_id: int, bug_type: str, script_name: str,
                                  steps: str, youtube_link: str, file_id: str,
                                  file_type: str, username: str, points: int,
                                  similar_bug_id: int | None, explanation: str):
    """Отправляет владельцу DM с деталями бага и пометкой о возможном дубле."""
    from utils.logger import get_bot

    bot = get_bot()
    if not bot:
        return

    emoji = "💥" if bug_type == "crash" else "🐛"
    similar_text = f"#{similar_bug_id}" if similar_bug_id else "?"
    text = (
        f"⚠️ <b>ВОЗМОЖНЫЙ ДУБЛЬ</b>\n\n"
        f"{emoji} <b>{'Краш' if bug_type == 'crash' else 'Баг'} #{bug_id}</b>\n"
        f"От: @{html.escape(username)}\n\n"
        f"📄 <b>Скрипт:</b> {html.escape(script_name)}\n\n"
        f"🔢 <b>Шаги:</b>\n{html.escape(steps)}\n\n"
        f"🎥 <b>Видео:</b> {html.escape(youtube_link)}\n\n"
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
        # Файл отдельным сообщением
        if file_type == "document":
            await bot.send_document(chat_id=OWNER_TELEGRAM_ID, document=file_id)
        elif file_type == "photo":
            await bot.send_photo(chat_id=OWNER_TELEGRAM_ID, photo=file_id)
        elif file_type == "video":
            await bot.send_video(chat_id=OWNER_TELEGRAM_ID, video=file_id)
    except Exception as e:
        print(f"❌ Не удалось уведомить владельца о возможном дубле #{bug_id}: {e}")
