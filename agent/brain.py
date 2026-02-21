"""
🧠 МОЗГ АГЕНТА — ядро ИИ-агента на Anthropic Claude с function calling.
"""
import json
import re
import time
import asyncio
from collections import OrderedDict
import anthropic
from config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, MAX_TOOL_ROUNDS, MAX_HISTORY, MAX_USERS_CACHE
from agent.system_prompt import get_system_prompt, get_chat_prompt
from agent.tools import get_tools_for_role
from agent.tool_executor import execute_tool

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# === Защита от перерасхода лимита ===
MIN_INTERVAL = 1.0
_last_request_time = 0.0
_throttle_lock = asyncio.Lock()

# === История диалогов per-user с LRU-лимитом ===

_conversation_history: OrderedDict[int, list] = OrderedDict()


def _get_history(caller_id: int) -> list:
    """Получает историю для пользователя, обновляя LRU-порядок."""
    if caller_id in _conversation_history:
        _conversation_history.move_to_end(caller_id)
        return _conversation_history[caller_id]
    if len(_conversation_history) >= MAX_USERS_CACHE:
        _conversation_history.popitem(last=False)
    _conversation_history[caller_id] = []
    return _conversation_history[caller_id]


def clear_history(caller_id: int):
    """Сбрасывает историю диалога для пользователя (при смене роли)."""
    _conversation_history.pop(caller_id, None)


# Мгновенные ответы БЕЗ вызова Claude API — экономим токены
INSTANT_REPLIES = {
    # Приветствия
    "привет": "Привет! 👋 Чем могу помочь?",
    "здравствуй": "Здравствуйте! Чем могу помочь?",
    "здравствуйте": "Здравствуйте! Чем могу помочь?",
    "хай": "Хай! 👋 Что нужно?",
    "hello": "Hello! How can I help?",
    "hi": "Hi! 👋",
    # Прощания
    "пока": "Пока! 👋",
    "до свидания": "До свидания! 👋",
    # Благодарности и подтверждения — не требуют Claude
    "спасибо": "Пожалуйста! 😊",
    "благодарю": "Всегда пожалуйста! 😊",
    "ок": "👍",
    "окей": "👍",
    "круто": "😊",
    "отлично": "😊",
    "супер": "🔥",
    "класс": "😊",
    "как дела": "Всё ок, работаю. Чем помочь?",
    "что нового": "Без изменений, работаю. Чем помочь?",
    "кто ты": "Я Umbrella Bot — координирую тестирование чита для Dota 2 🤖",
    # Помощь
    "помощь": (
        "📋 <b>Команды:</b>\n\n"
        "• «Покажи рейтинг» — таблица тестеров\n"
        "• «Статистика @username» — баллы тестера\n"
        "• «Начисли @username N баллов за ...» — начислить\n"
        "• «Предупреди @username за ...» — предупреждение\n"
        "• «Сними варн @username» — снять предупреждение\n"
        "• «Сними варны всем» — сбросить все предупреждения\n"
        "• «Кто не работал N дней?» — неактивные\n"
        "• «Дай задание — ...» — создать задание\n\n"
        "💡 Можно ответить реплаем на сообщение тестера и написать команду — бот поймёт кого имеешь в виду.\n\n"
        "📝 Багрепорты → топик «Баги»\n\n"
        "🔍 <b>Управление багами:</b>\n"
        "• «Покажи баг #5» — инфо по конкретному багу\n"
        "• «Покажи баги от @username» — баги тестера\n"
        "• «Покажи принятые баги» — фильтр по статусу\n"
        "• «Удали баг #5» — удалить отовсюду\n"
        "• «Удали баг #5 из бд» — только из базы\n"
        "• «Удали баг #5 из вика» — только из Weeek"
    ),
    "help": (
        "📋 <b>Команды:</b>\n\n"
        "• Рейтинг\n• Статистика @username\n"
        "• Начисли @username N баллов за ...\n"
        "• Предупреди @username за ...\n"
        "• Сними варн @username\n"
        "• Дай задание — ...\n\n"
        "💡 Reply на сообщение тестера + команда — работает.\n\n"
        "🔍 Bug management:\n"
        "• Bug #5 — info about specific bug\n"
        "• Bugs by @username — tester's bugs\n"
        "• Delete bug #5 — remove everywhere"
    ),
    "что ты умеешь": (
        "📋 <b>Что умею:</b>\n\n"
        "• Рейтинг и статистика тестеров\n"
        "• Начисление/списание баллов\n"
        "• Предупреждения: выдать / снять / сбросить (макс 3)\n"
        "• Создание заданий для тестеров\n"
        "• Приём багрепортов → Weeek\n"
        "• Аналитика по команде\n"
        "• Поиск и удаление багов\n\n"
        "💡 Можно ответить реплаем на сообщение тестера."
    ),
}


def get_instant_reply(text: str) -> str | None:
    """Мгновенный ответ без вызова API."""
    clean = re.sub(r'[!?.,)]+$', '', text.lower().strip())
    return INSTANT_REPLIES.get(clean)


_RE_STATS = re.compile(r"^(?:стат(?:истика|а)?|статы?)\s+@?(\w+)$", re.IGNORECASE)
_RE_RATING = re.compile(r"^(?:рейтинг|топ|таблица|лидеры)$", re.IGNORECASE)


async def try_direct_command(text: str, caller_id: int) -> str | None:
    """
    Пробует выполнить команду напрямую без Claude API.
    Возвращает ответ или None если команда не распознана.
    """
    clean = text.strip()

    # --- Рейтинг ---
    if _RE_RATING.match(clean):
        from services.rating_service import get_rating, format_rating_message
        data = await get_rating()
        return format_rating_message(data)

    # --- Статистика конкретного тестера ---
    m = _RE_STATS.match(clean)
    if m:
        result_json = await execute_tool("get_tester_stats", json.dumps({"username": m.group(1)}), caller_id)
        result = json.loads(result_json)
        if result.get("error"):
            return f"⚠️ {result['error']}"
        t = result
        uname = t['username'] if t.get("username") else t.get("full_name", "?")
        return (
            f"📊 <b>Статистика {uname}</b>\n\n"
            f"⭐ Баллы: <b>{t['total_points']}</b>\n"
            f"📝 Баги: {t['total_bugs']}\n"
            f"🎮 Игры: {t['total_games']}\n"
            f"⚠️ Предупреждения: {t['warnings_count']}/3"
        )

    return None


def _max_history(role: str) -> int:
    return MAX_HISTORY.get(role, 3)


def _trim_history(history: list, role: str = "tester"):
    limit = _max_history(role) * 2
    while len(history) > limit:
        history.pop(0)




def _serialize_content(content) -> list[dict]:
    """Конвертирует SDK content блоки в dict для повторной отправки."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result



async def _throttle():
    """Ждём если запросы слишком частые."""
    global _last_request_time
    async with _throttle_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_INTERVAL:
            await asyncio.sleep(MIN_INTERVAL - elapsed)
        _last_request_time = time.time()


async def _call_claude(**kwargs):
    """Обёртка с throttle."""
    await _throttle()
    return await client.messages.create(**kwargs)


async def process_message(text: str, username: str, role: str, topic: str,
                          caller_id: int = None) -> str:
    """Главная функция мозга агента."""

    # 1. Мгновенный ответ без API
    instant = get_instant_reply(text)
    if instant:
        return instant

    # 2. Прямые команды без Claude (рейтинг, статистика @username)
    direct = await try_direct_command(text, caller_id)
    if direct:
        return direct

    context = {"username": username, "role": role, "topic": topic}
    system_prompt = get_system_prompt(context)

    model = MODEL

    # 4. Получаем историю и добавляем новое сообщение
    history = _get_history(caller_id)
    history.append({"role": "user", "content": text})
    _trim_history(history, role)

    messages = [msg.copy() for msg in history]

    # 5. Инструменты — отдаём Claude все доступные для роли,
    #    модель сама решает какой вызвать по контексту сообщения
    tools = get_tools_for_role(role)

    try:
        kwargs = {
            "model": model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "auto"}

        response = await _call_claude(**kwargs)

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        max_tool_rounds = MAX_TOOL_ROUNDS
        round_num = 0
        # Инструменты, которые сами отправляют ответ пользователю (черновик и т.д.)
        _SILENT_TOOLS = {"create_task"}
        called_silent_tool = False

        while tool_use_blocks and round_num < max_tool_rounds:
            round_num += 1

            content_dicts = _serialize_content(response.content)
            messages.append({"role": "assistant", "content": content_dicts})

            tool_results = []
            for block in tool_use_blocks:
                func_name = block.name
                func_args = json.dumps(block.input, ensure_ascii=False)
                print(f"  🔧 Вызов: {func_name}({func_args})")

                if func_name in _SILENT_TOOLS:
                    called_silent_tool = True

                result = await execute_tool(func_name, func_args, caller_id, topic)
                print(f"  📦 Результат: {result[:200]}...")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

            response = await _call_claude(
                model=model,
                system=system_prompt,
                messages=messages,
                max_tokens=MAX_TOKENS,
                tools=tools,
                tool_choice={"type": "auto"},
            )
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Если инструмент сам отправил ответ — не дублируем
        if called_silent_tool:
            text_blocks = [b for b in response.content if b.type == "text"]
            reply = text_blocks[0].text if text_blocks else ""
            history.append({"role": "assistant", "content": reply or "Готово"})
            _trim_history(history, role)
            return None

        text_blocks = [b for b in response.content if b.type == "text"]
        reply = text_blocks[0].text if text_blocks else "Готово ✅"

        # Сохраняем в историю последний assistant + tool_result обмен (сжато)
        history.append({"role": "assistant", "content": reply})
        _trim_history(history, role)

        return reply

    except anthropic.RateLimitError:
        if history and history[-1].get("role") == "user":
            history.pop()
        return "⚠️ Claude API: превышен лимит запросов. Подождите немного."
    except anthropic.AuthenticationError:
        if history and history[-1].get("role") == "user":
            history.pop()
        return "⚠️ Ошибка авторизации Claude API. Проверьте ANTHROPIC_API_KEY в .env"
    except Exception as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        print(f"❌ Ошибка brain: {e}")
        return f"⚠️ Ошибка: {str(e)[:200]}"


async def process_chat_message(text: str, caller_id: int) -> str:
    """Свободный чат без инструментов — просто болтовня."""
    from config import CHAT_MODEL

    system_prompt = get_chat_prompt()

    history = _get_history(caller_id)
    history.append({"role": "user", "content": text})
    _trim_history(history, "owner")  # даём побольше истории для контекста

    messages = [msg.copy() for msg in history]

    try:
        response = await _call_claude(
            model=CHAT_MODEL,
            system=system_prompt,
            messages=messages,
            max_tokens=MAX_TOKENS,
        )

        text_blocks = [b for b in response.content if b.type == "text"]
        reply = text_blocks[0].text if text_blocks else "чё"

        history.append({"role": "assistant", "content": reply})
        _trim_history(history, "owner")

        return reply

    except anthropic.RateLimitError:
        if history and history[-1].get("role") == "user":
            history.pop()
        return "⚠️ Claude API: превышен лимит запросов. Подождите немного."
    except Exception as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        print(f"❌ Ошибка chat brain: {e}")
        return f"⚠️ Ошибка: {str(e)[:200]}"
