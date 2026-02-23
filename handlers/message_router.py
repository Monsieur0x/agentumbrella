"""
Роутер сообщений — определяет КТО написал, ГДЕ написал, и решает что делать.

Это главный обработчик всех входящих сообщений.
"""
import re
import time
import html
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import GROUP_ID, TOPIC_NAMES, DEBUG_TOPICS, OBSERVE_REPLY
from models.admin import is_admin, is_owner
from models.tester import get_or_create_tester, get_tester_by_id
from agent.brain import process_message, process_chat_message
from services.rating_service import get_rating, format_rating_message
from utils.logger import log_info
from json_store import async_load, async_update, BUGS_FILE, TASKS_FILE

router = Router()

# Кэш bot_info — заполняется при первом вызове
_bot_info = None

TG_MAX_MESSAGE_LENGTH = 4000  # Telegram лимит 4096, оставляем запас

# Состояние ожидания ввода своего значения награды: telegram_id → (reward_type, timestamp)
_pending_reward_input: dict[int, tuple[str, float]] = {}

_REWARD_INPUT_TTL = 300  # 5 минут


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

    # === Режим отладки: показываем ID топика ===
    if DEBUG_TOPICS:
        await message.reply(
            f"thread_id: <code>{message.message_thread_id}</code>",
            parse_mode="HTML",
        )
        return

    # Проверяем что это наша группа (если GROUP_ID задан)
    if GROUP_ID and message.chat.id != GROUP_ID:
        return

    # === Режим наблюдения: бот молчит, кроме прямого упоминания ===
    import config
    if config.BOT_MODE == "observe":
        bot_info = await _get_bot_info(bot)
        if is_bot_mentioned(message, bot_info):
            # Даём владельцу переключить режим даже в observe
            if await _handle_mode_toggle(message, user):
                return
            await message.reply(OBSERVE_REPLY)
        return

    # === Чат-режим: свободная болтовня без функций координатора ===
    if config.BOT_MODE == "chat":
        bot_info = await _get_bot_info(bot)
        if not is_bot_mentioned(message, bot_info):
            return
        if await _handle_mode_toggle(message, user):
            return
        if not message.text:
            return
        try:
            await bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            response = await process_chat_message(text=message.text, caller_id=user.id)
            if response:
                await _safe_reply(message, response, parse_mode="HTML")
        except Exception as e:
            print(f"❌ Ошибка chat: {e}")
            await message.reply(f"⚠️ Ошибка: <code>{str(e)[:300]}</code>", parse_mode="HTML")
        return

    # === Авторегистрация ===
    await get_or_create_tester(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    role = await get_role(user.id)
    bot_info = await _get_bot_info(bot)

    # === Игнорируем топик Логины (чувствительные данные) ===
    if topic == "logins":
        return

    # === Роутинг по топикам ===

    raw_text = (message.text or message.caption or "").lower()
    has_hashtag_bug = "#баг" in raw_text
    mentioned = is_bot_mentioned(message, bot_info)

    # Топик «Баги» → #баг, файл для ожидающего бага, или видео-ссылка
    if topic == "bugs":
        from handlers.bug_handler import handle_bug_report, handle_file_followup, handle_video_followup
        from utils.media_group import collect_bug_messages

        file_present = bool(message.document or message.video or message.photo or message.video_note)
        msg_text = message.text or message.caption or ""
        has_youtube = bool(re.search(
            r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/', msg_text, re.IGNORECASE
        )) if msg_text else False

        # --- Проверяем: тестер добавляет материалы к ожидающему багу ---
        if not has_hashtag_bug and (file_present or has_youtube):
            bugs_data = await async_load(BUGS_FILE)
            items = bugs_data.get("items", {})
            waiting = [b for b in items.values()
                       if b.get("tester_id") == user.id and b.get("status") == "waiting_media"]
            if waiting:
                waiting.sort(key=lambda b: b.get("id", 0), reverse=True)
                bug_id = waiting[0]["id"]
                if file_present:
                    await handle_file_followup(message, bug_id)
                if has_youtube:
                    await handle_video_followup(message, bug_id)
                return

        # --- Сообщение с #баг: буферизуем все сообщения от тестера ---
        # Telegram может разбить текст + скрин + файл на отдельные сообщения,
        # поэтому ждём ~1.5 сек и собираем всё вместе.
        if has_hashtag_bug or file_present:
            collected = await collect_bug_messages(message)
            if collected is None:
                # Не первое сообщение — уже обработается первым
                return

            # Проверяем: есть ли #баг в любом из собранных сообщений
            collected_has_bug = False
            for msg in collected:
                t = (msg.text or msg.caption or "").lower()
                if "#баг" in t:
                    collected_has_bug = True
                    break

            if collected_has_bug:
                await handle_bug_report(collected[0], media_messages=collected)
                return

        if has_hashtag_bug:
            await handle_bug_report(message)
            return
        # Без #баг и без ожидающего — игнорируем
        return

    # Во всех топиках (кроме bugs) — отвечаем только если обращаются к боту
    # через @упоминание или реплай на сообщение бота
    if not mentioned:
        return

    # === Отправляем в мозг агента ===
    if not message.text:
        return

    # === Команды владельца: переключение режима / вкл/выкл Weeek ===
    if await _handle_mode_toggle(message, user):
        return
    if await _handle_weeek_toggle(message, user):
        return

    # === Ожидание ввода своего значения награды ===
    if await _handle_pending_reward_input(message, user):
        return

    # === Настройка наград ===
    if await _handle_rewards_settings(message, user):
        return

    # Тестеры в группе — только статистика и рейтинг, без Claude API
    if role == "tester":
        handled = await _handle_tester_commands(message, user)
        if not handled:
            await message.reply(
                "Тебе доступны:\n"
                "• <b>моя статистика</b>\n"
                "• <b>рейтинг</b>\n\n"
                "Багрепорты отправляй в топик <b>Баги</b> с хештегом <b>#баг</b>.",
                parse_mode="HTML"
            )
        return

    # === Контекст реплая: если админ отвечает на сообщение тестера ===
    text_to_send = message.text
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None
    if reply_user and not reply_user.is_bot and reply_user.id != user.id:
        reply_username = reply_user.username or reply_user.full_name or str(reply_user.id)
        text_to_send = f"[ответ на сообщение @{reply_username}] {message.text}"

    # Показываем «печатает...»
    try:
        await bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    print(f"\n💬 [{role}] @{user.username} в [{topic}]: {message.text[:100]}")

    try:
        response = await process_message(
            text=text_to_send,
            username=user.username or user.full_name or str(user.id),
            role=role,
            topic=topic,
            caller_id=user.id,
        )
        if response:
            await _safe_reply(message, response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.reply(
            f"⚠️ Ошибка при обработке.\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )


async def _handle_draft_task_edit(message: Message, user) -> bool:
    """Если у админа/владельца есть черновик задания, воспринимаем текст как редактирование.
    Работает только в ЛС — в группе редактирование черновиков не оддерживается."""
    tasks_data = await async_load(TASKS_FILE)
    items = tasks_data.get("items", {})

    # Ищем последний черновик этого админа
    drafts = [t for t in items.values()
              if t.get("admin_id") == user.id and t.get("status") == "draft"]
    if not drafts:
        return False

    drafts.sort(key=lambda t: t.get("id", 0), reverse=True)
    task_id = drafts[0]["id"]
    new_text = message.text

    def updater(data):
        key = str(task_id)
        if key in data.get("items", {}):
            data["items"][key]["full_text"] = new_text
        return data

    await async_update(TASKS_FILE, updater)

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


_WEEEK_OFF_KEYWORDS = ("отключи вик", "выключи вик", "стоп вик")
_WEEEK_ON_KEYWORDS = ("включи вик", "запусти вик", "старт вик")

_MODE_OBSERVE_KEYWORDS = ("режим наблюдени", "включи наблюдени", "режим observe", "переключи на наблюдени")
_MODE_ACTIVE_KEYWORDS = ("рабочий режим", "включи рабочий", "режим актив", "переключи на рабочий")
_MODE_CHAT_KEYWORDS = ("режим чат", "включи чат", "чат режим", "переключи на чат", "режим болтовни")


async def _handle_mode_toggle(message: Message, user) -> bool:
    """Обрабатывает команды владельца для переключения режима бота. Возвращает True если обработано."""
    if not message.text:
        return False
    if not await is_owner(user.id):
        return False

    import config
    text = message.text.lower().strip()

    if any(kw in text for kw in _MODE_OBSERVE_KEYWORDS):
        config.BOT_MODE = "observe"
        await message.reply("👁 Режим переключён: <b>наблюдение</b>. Бот отвечает только на @упоминания.", parse_mode="HTML")
        return True

    if any(kw in text for kw in _MODE_ACTIVE_KEYWORDS):
        config.BOT_MODE = "active"
        await message.reply("✅ Режим переключён: <b>рабочий</b>. Бот отвечает на все сообщения.", parse_mode="HTML")
        return True

    if any(kw in text for kw in _MODE_CHAT_KEYWORDS):
        config.BOT_MODE = "chat"
        await message.reply("💬 Режим переключён: <b>чат</b>. Свободная болтовня, функции координатора отключены.", parse_mode="HTML")
        return True

    return False



async def _handle_weeek_toggle(message: Message, user) -> bool:
    """Обрабатывает команды владельца 'отключи вик' / 'включи вик'. Возвращает True если обработано."""
    if not message.text:
        return False
    if not await is_owner(user.id):
        return False

    import config
    text = message.text.lower().strip()

    if any(kw in text for kw in _WEEEK_OFF_KEYWORDS):
        config.WEEEK_ENABLED = False
        await message.reply("🔴 Weeek <b>отключён</b>. Баги будут сохраняться без отправки в Weeek.", parse_mode="HTML")
        return True

    if any(kw in text for kw in _WEEEK_ON_KEYWORDS):
        config.WEEEK_ENABLED = True
        await message.reply("🟢 Weeek <b>включён</b>. Баги снова будут отправляться в Weeek.", parse_mode="HTML")
        return True

    return False


_STATS_KEYWORDS = ("статистика", "стата", "мои баллы", "мой рейтинг", "мои очки", "сколько баллов", "мой стат")
_RATING_KEYWORDS = ("рейтинг", "топ", "таблица", "лидеры", "leaderboard")
_REWARDS_KEYWORDS = ("настройка наград", "настроить награды", "настройки наград")


async def _handle_rewards_settings(message: Message, user) -> bool:
    """Обрабатывает команду 'настройка наград' для админов/владельцев."""
    if not message.text:
        return False
    text = message.text.lower().strip()
    if not any(kw in text for kw in _REWARDS_KEYWORDS):
        return False

    role = await get_role(user.id)
    if role not in ("admin", "owner"):
        return False

    from models.settings import get_points_config
    from handlers.callback_handler import build_rewards_menu

    pts = await get_points_config()
    msg_text, keyboard = build_rewards_menu(pts)

    await message.answer(msg_text, parse_mode="HTML", reply_markup=keyboard)
    return True


async def _handle_pending_reward_input(message: Message, user) -> bool:
    """Если пользователь вводит своё значение награды — обрабатываем."""
    if user.id not in _pending_reward_input:
        return False

    reward_type, timestamp = _pending_reward_input[user.id]

    # TTL: если прошло больше 5 минут — сбрасываем
    if time.time() - timestamp > _REWARD_INPUT_TTL:
        del _pending_reward_input[user.id]
        return False

    del _pending_reward_input[user.id]
    text = (message.text or "").strip()

    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ Введите положительное целое число.")
        _pending_reward_input[user.id] = (reward_type, timestamp)
        return True

    value = int(text)
    from models.settings import set_points_value

    await set_points_value(reward_type, value)

    labels = {
        "bug_accepted": "🐛 Баг",
        "game_ap": "🎮 All Pick",
        "game_turbo": "🎮 Turbo",
    }
    label = labels.get(reward_type, reward_type)
    await message.answer(
        f"✅ {label}: <b>{value}</b> б.",
        parse_mode="HTML",
    )
    await log_info(f"Награда {reward_type} изменена на {value} (@{user.username})")
    return True


async def _handle_tester_commands(message: Message, user) -> bool:
    """Обрабатывает команды тестера: статистика или рейтинг. Возвращает True если обработано."""
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

    # === Режим наблюдения: отвечаем фиксированной фразой ===
    import config
    if config.BOT_MODE == "observe":
        # Даём владельцу переключить режим даже в observe
        if await _handle_mode_toggle(message, user):
            return
        await message.answer(OBSERVE_REPLY)
        return

    # === Чат-режим: свободная болтовня без функций координатора ===
    if config.BOT_MODE == "chat":
        if await _handle_mode_toggle(message, user):
            return
        try:
            await bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            response = await process_chat_message(text=message.text, caller_id=user.id)
            if response:
                await _safe_reply(message, response, parse_mode="HTML")
        except Exception as e:
            print(f"❌ Ошибка chat: {e}")
            await message.answer(f"⚠️ Ошибка: <code>{str(e)[:300]}</code>", parse_mode="HTML")
        return

    # Авторегистрация
    await get_or_create_tester(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    role = await get_role(user.id)

    # Тестеры в ЛС — только статистика и рейтинг, без Claude API
    if role == "tester":
        handled = await _handle_tester_commands(message, user)
        if not handled:
            await message.answer(
                "🚫 В личных сообщениях тебе доступны только:\n\n"
                "• <b>моя статистика</b> — твои баллы и показатели\n"
                "• <b>рейтинг</b> — таблица тестеров\n\n"
                "Багрепорты отправляй в топик <b>Баги</b> с хештегом <b>#баг</b>.",
                parse_mode="HTML"
            )
        return

    # === Команды владельца: переключение режима / вкл/выкл Weeek ===
    if await _handle_mode_toggle(message, user):
        return
    if await _handle_weeek_toggle(message, user):
        return

    # === Ожидание ввода своего значения награды ===
    if await _handle_pending_reward_input(message, user):
        return

    # === Настройка наград ===
    if await _handle_rewards_settings(message, user):
        return

    # Проверяем: есть ли черновик задания для редактирования
    if await _handle_draft_task_edit(message, user):
        return

    try:
        await bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    print(f"\n💬 [ЛС] [{role}] @{user.username}: {message.text[:100]}")

    try:
        response = await process_message(
            text=message.text,
            username=user.username or user.full_name or str(user.id),
            role=role,
            topic="private",
            caller_id=user.id,
        )
        if response:
            await _safe_reply(message, response, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.answer(
            f"⚠️ Ошибка при обработке. Проверь ANTHROPIC_API_KEY в .env\n\n"
            f"<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
