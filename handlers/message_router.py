"""
Роутер сообщений — определяет КТО написал, ГДЕ написал, и решает что делать.

Это главный обработчик всех входящих сообщений.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message
from config import GROUP_ID, TOPIC_NAMES, TOPIC_IDS, DEBUG_TOPICS
from models.admin import is_admin, is_owner
from models.tester import get_or_create_tester
from agent.brain import process_message
from utils.logger import log_info

router = Router()


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
    bot_info = await bot.get_me()

    # === Роутинг по топикам ===

    # Топик «Баги» или «Краши» → автообработка багрепортов
    if topic in ("bugs", "crashes") and message.text:
        # На этапе 1+2 — просто подтверждаем получение
        # Полная обработка будет в Этапе 4
        from handlers.bug_handler import handle_bug_report
        await handle_bug_report(message, topic, role)
        return

    # Топик «Отчёты» → обработка скриншотов
    if topic == "reports" and message.photo:
        # На этапе 1+2 — заглушка
        # Полная обработка будет в Этапе 6
        await message.reply("📸 Скриншот получен! (Обработка скриншотов будет доступна позже)")
        return

    # В остальных топиках — отвечаем только если обращаются к боту
    if topic in ("general", "tasks", "top", "logs"):
        if not is_bot_mentioned(message, bot_info):
            return  # Игнорируем

    # === Отправляем в мозг агента ===
    if not message.text:
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
        # Отправляем ответ
        await message.reply(response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.reply(
            f"⚠️ Ошибка при обработке.\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )


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
        await message.answer(response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.answer(
            f"⚠️ Ошибка при обработке. Проверь ANTHROPIC_API_KEY в .env\n\n"
            f"<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
