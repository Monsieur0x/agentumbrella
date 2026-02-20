"""
Роутер сообщений — определяет КТО написал, ГДЕ написал, и решает что делать.

Это главный обработчик всех входящих сообщений.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message
from config import GROUP_ID, TOPIC_NAMES, TOPIC_IDS, DEBUG_TOPICS
from models.admin import is_admin, is_owner
from models.tester import get_or_create_tester, get_tester_by_id
from agent.brain import process_message
from services.rating_service import get_rating, format_rating_message
from utils.logger import log_info

router = Router()

# Кэш bot_info — заполняется при первом вызове
_bot_info = None

TG_MAX_MESSAGE_LENGTH = 4000  # Telegram лимит 4096, оставляем запас


async def _get_bot_info(bot: Bot):
    """Возвращает кэшированный bot_info."""
    global _bot_info
    if _bot_info is None:
        _bot_info = await bot.get_me()
    return _bot_info


async def _safe_reply(message: Message, text: str, **kwargs):
    """Отправляет ответ, разбивая на части если длина превышает лимит Telegram."""
    if len(text) <= TG_MAX_MESSAGE_LENGTH:
        await message.reply(text, **kwargs)
        return

    # Разбиваем на части по TG_MAX_MESSAGE_LENGTH символов
    parts = []
    while text:
        if len(text) <= TG_MAX_MESSAGE_LENGTH:
            parts.append(text)
            break
        # Ищем последний перенос строки в пределах лимита
        cut = text.rfind("\n", 0, TG_MAX_MESSAGE_LENGTH)
        if cut == -1:
            cut = TG_MAX_MESSAGE_LENGTH
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")

    for part in parts:
        await message.reply(part, **kwargs)


def get_topic_name(message: Message) -> str:
    """Определяет название топика по message_thread_id."""
    thread_id = message.message_thread_id
    if thread_id is None:
        return "general"
    return TOPIC_NAMES.get(thread_id, f"unknown_{thread_id}")


async def get_role(telegram_id: int) -> str:
    """Определяет роль пользователя: owner / admin / tester."""
    if await is_owner(telegram_id):
        return "owner"
    if await is_admin(telegram_id):
        return "admin"
    return "tester"


def is_bot_mentioned(message: Message, bot_info) -> bool:
    """Проверяет, обращаются ли к боту (реплай или @упоминание)."""
    # Реплай на сообщение бота
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id == bot_info.id:
            return True
    # @упоминание
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention_text = message.text[entity.offset:entity.offset + entity.length]
                if mention_text.lower() == f"@{bot_info.username.lower()}":
                    return True
    return False


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message, bot: Bot):
    """Обрабатывает все сообщения в группе."""
    if not message.from_user or message.from_user.is_bot:
        return

    user = message.from_user
    topic = get_topic_name(message)

    # === Режим отладки: показываем ID топиков ===
    if DEBUG_TOPICS:
        await message.reply(
            f"🔍 Debug:\n"
            f"chat_id: <code>{message.chat.id}</code>\n"
            f"thread_id: <code>{message.message_thread_id}</code>\n"
            f"topic: {topic}\n"
            f"user_id: <code>{user.id}</code>\n"
            f"username: @{user.username}\n\n"
            f"👆 Скопируй chat_id в GROUP_ID, а thread_id — в нужный TOPIC_*"
        )
        return

    # Проверяем что это наша группа (если GROUP_ID задан)
    if GROUP_ID and message.chat.id != GROUP_ID:
        return

    # === Авторегистрация ===
    await get_or_create_tester(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    role = await get_role(user.id)
    bot_info = await _get_bot_info(bot)

    # === Полностью игнорируем топик General ===
    if topic == "general":
        return

    # === Роутинг по топикам ===

    raw_text = (message.text or message.caption or "").lower()
    has_hashtag_bug = "#баг" in raw_text or "#краш" in raw_text
    has_hashtag_report = "#отчёт" in raw_text or "#отчет" in raw_text
    mentioned = is_bot_mentioned(message, bot_info)

    # Топик «Баги» или «Краши» → только по хештегу #баг / #краш
    if topic in ("bugs", "crashes"):
        if has_hashtag_bug:
            from handlers.bug_handler import handle_bug_report
            await handle_bug_report(message, topic, role)
            return
        if mentioned:
            pass  # Пропускаем в мозг агента ниже
        else:
            return  # Обычное сообщение — игнор

    # Топик «Отчёты» → только по хештегу #отчёт
    if topic == "reports":
        if has_hashtag_report and message.photo:
            await message.reply("📸 Скриншот получен! (Обработка скриншотов будет доступна позже)")
            return
        if mentioned:
            pass  # Пропускаем в мозг агента
        else:
            return  # Игнор

    # В остальных топиках — отвечаем только если обращаются к боту
    if topic in ("tasks", "top", "logs"):
        if not mentioned:
            return

    # === Отправляем в мозг агента ===
    if not message.text:
        return

    # Тестеры в группе — только статистика и рейтинг, без Claude API
    if role == "tester":
        handled = await _handle_tester_dm(message, user)
        if not handled:
            await message.reply(
                "Тебе доступны:\n"
                "• <b>моя статистика</b>\n"
                "• <b>рейтинг</b>\n\n"
                "Багрепорты оформляй с хештегом <b>#баг</b> или <b>#краш</b>.",
                parse_mode="HTML"
            )
        return

    # Показываем «печатает...»
    await bot.send_chat_action(message.chat.id, "typing")

    print(f"\n💬 [{role}] @{user.username} в [{topic}]: {message.text[:100]}")

    try:
        response = await process_message(
            text=message.text,
            username=user.username or user.full_name or str(user.id),
            role=role,
            topic=topic,
            caller_id=user.id,
        )
        await _safe_reply(message, response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.reply(
            f"⚠️ Ошибка при обработке.\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )


async def _handle_draft_task_edit(message: Message, user) -> bool:
    """Если у админа/владельца есть черновик задания, воспринимаем текст как редактирование."""
    from database import get_db
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import html

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM tasks WHERE admin_id = ? AND status = 'draft' ORDER BY id DESC LIMIT 1",
            (user.id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False

        task_id = row[0]
        new_text = message.text

        await db.execute(
            "UPDATE tasks SET full_text = ? WHERE id = ?",
            (new_text, task_id)
        )
        await db.commit()
    finally:
        await db.close()

    safe_text = html.escape(new_text)
    preview_text = (
        f"📋 <b>Черновик задания #{task_id}</b> (отредактировано)\n\n"
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
    await message.answer(preview_text, parse_mode="HTML", reply_markup=keyboard)
    return True


_STATS_KEYWORDS = ("статистика", "стата", "мои баллы", "мой рейтинг", "мои очки", "сколько баллов", "мой стат")
_RATING_KEYWORDS = ("рейтинг", "топ", "таблица", "лидеры", "leaderboard")


async def _handle_tester_dm(message: Message, user) -> bool:
    """Обрабатывает ЛС тестера: статистика или рейтинг. Возвращает True если обработано."""
    if not message.text:
        return False

    text = message.text.lower().strip()

    # --- Своя статистика ---
    if any(kw in text for kw in _STATS_KEYWORDS):
        tester = await get_tester_by_id(user.id)
        if not tester:
            await message.answer("Ты ещё не зарегистрирован. Напиши что-нибудь в группе.")
            return True
        uname = f"@{tester['username']}" if tester["username"] else tester["full_name"] or str(user.id)
        await message.answer(
            f"📊 <b>Твоя статистика</b>\n\n"
            f"👤 {uname}\n"
            f"⭐ Баллы: <b>{tester['total_points']}</b>\n"
            f"📝 Баги: {tester['total_bugs']}\n"
            f"💥 Краши: {tester['total_crashes']}\n"
            f"🎮 Игры: {tester['total_games']}\n"
            f"⚠️ Предупреждения: {tester['warnings_count']}/3",
            parse_mode="HTML"
        )
        return True

    # --- Рейтинг ---
    if any(kw in text for kw in _RATING_KEYWORDS):
        data = await get_rating()
        await message.answer(format_rating_message(data), parse_mode="HTML")
        return True

    return False


@router.message(F.chat.type == "private")
async def handle_private_message(message: Message, bot: Bot):
    """Обрабатывает личные сообщения боту."""
    if not message.from_user or not message.text:
        return

    user = message.from_user

    # Авторегистрация
    await get_or_create_tester(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    role = await get_role(user.id)

    # Тестеры в ЛС — только статистика и рейтинг, без Claude API
    if role == "tester":
        handled = await _handle_tester_dm(message, user)
        if not handled:
            await message.answer(
                "🚫 В личных сообщениях тебе доступны только:\n\n"
                "• <b>моя статистика</b> — твои баллы и показатели\n"
                "• <b>рейтинг</b> — таблица тестеров\n\n"
                "Багрепорты отправляй в топик <b>Баги</b> или <b>Краши</b>.",
                parse_mode="HTML"
            )
        return

    # Проверяем: есть ли черновик задания для редактирования
    if await _handle_draft_task_edit(message, user):
        return

    await bot.send_chat_action(message.chat.id, "typing")

    print(f"\n💬 [ЛС] [{role}] @{user.username}: {message.text[:100]}")

    try:
        response = await process_message(
            text=message.text,
            username=user.username or user.full_name or str(user.id),
            role=role,
            topic="private",
            caller_id=user.id,
        )
        await _safe_reply(message, response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.answer(
            f"⚠️ Ошибка при обработке. Проверь ANTHROPIC_API_KEY в .env\n\n"
            f"<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
