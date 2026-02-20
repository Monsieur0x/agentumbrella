"""
🧠 МОЗГ АГЕНТА — ядро ИИ-агента на Anthropic Claude с function calling.
"""
import json
import re
import time
import asyncio
from collections import OrderedDict
import anthropic
from config import ANTHROPIC_API_KEY, MODEL_AGENT, MODEL_CHEAP
from agent.system_prompt import get_system_prompt
from agent.tools import get_tools_for_role
from agent.tool_executor import execute_tool

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# === Защита от перерасхода лимита ===
MIN_INTERVAL = 1.0
_last_request_time = 0.0

# === История диалогов per-user с LRU-лимитом ===
_MAX_USERS = 200

_conversation_history: OrderedDict[int, list] = OrderedDict()


def _get_history(caller_id: int) -> list:
    """Получает историю для пользователя, обновляя LRU-порядок."""
    if caller_id in _conversation_history:
        _conversation_history.move_to_end(caller_id)
        return _conversation_history[caller_id]
    if len(_conversation_history) >= _MAX_USERS:
        _conversation_history.popitem(last=False)
    _conversation_history[caller_id] = []
    return _conversation_history[caller_id]


def clear_history(caller_id: int):
    """Сбрасывает историю диалога для пользователя (при смене роли)."""
    _conversation_history.pop(caller_id, None)


# Максимум пар сообщений в истории по ролям
_MAX_HISTORY: dict[str, int] = {
    "tester": 2,   # Тестеры спрашивают просто — короткая история
    "admin": 2,    # Админы делают команды — длинная история не нужна
    "owner": 3,    # Владельцу чуть больше контекста
}

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
    "ладно": "👍",
    "хорошо": "👍",
    "понял": "👍",
    "поняла": "👍",
    "ясно": "👍",
    "понятно": "👍",
    "да": "👍",
    "нет": "Хорошо.",
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
        "• «Кто не работал N дней?» — неактивные\n"
        "• «Дай задание — ...» — создать задание\n\n"
        "📝 Багрепорты → топик «Баги» или «Краши»"
    ),
    "help": (
        "📋 <b>Команды:</b>\n\n"
        "• Рейтинг\n• Статистика @username\n"
        "• Начисли @username N баллов за ...\n"
        "• Предупреди @username за ...\n"
        "• Дай задание — ..."
    ),
    "что ты умеешь": (
        "📋 <b>Что умею:</b>\n\n"
        "• Рейтинг и статистика тестеров\n"
        "• Начисление/списание баллов\n"
        "• Предупреждения (макс 3)\n"
        "• Создание заданий для тестеров\n"
        "• Приём багрепортов → Weeek\n"
        "• Аналитика по команде"
    ),
}


def get_instant_reply(text: str) -> str | None:
    """Мгновенный ответ без вызова API."""
    clean = text.lower().strip().rstrip("!?.,)")
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
        uname = f"@{t['username']}" if t.get("username") else t.get("full_name", "?")
        return (
            f"📊 <b>Статистика {uname}</b>\n\n"
            f"⭐ Баллы: <b>{t['total_points']}</b>\n"
            f"📝 Баги: {t['total_bugs']}\n"
            f"💥 Краши: {t['total_crashes']}\n"
            f"🎮 Игры: {t['total_games']}\n"
            f"⚠️ Предупреждения: {t['warnings_count']}/3"
        )

    return None


def _max_history(role: str) -> int:
    return _MAX_HISTORY.get(role, 3)


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


def _build_system_with_cache(system_prompt: str) -> list[dict]:
    """Оборачивает system prompt в формат с cache_control."""
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


def _build_tools_with_cache(tools: list) -> list:
    """Добавляет cache_control к последнему инструменту — кэшируются все до него."""
    if not tools:
        return tools
    result = [t.copy() for t in tools]
    result[-1] = {**result[-1], "cache_control": {"type": "ephemeral"}}
    return result


async def _throttle():
    """Ждём если запросы слишком частые."""
    global _last_request_time
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

    # 3. Выбираем модель по роли
    # Sonnet только для владельца — остальным хватает Haiku для function calling
    model = MODEL_AGENT if role == "owner" else MODEL_CHEAP

    # 4. Получаем историю и добавляем новое сообщение
    history = _get_history(caller_id)
    history.append({"role": "user", "content": text})
    _trim_history(history, role)

    messages = [msg.copy() for msg in history]

    # 5. Инструменты + prompt caching
    raw_tools = get_tools_for_role(role)
    cached_tools = _build_tools_with_cache(raw_tools)
    cached_system = _build_system_with_cache(system_prompt)

    try:
        kwargs = {
            "model": model,
            "system": cached_system,
            "messages": messages,
            "max_tokens": 1024,        # Снижено с 2048 — ответы бота короткие
            "tools": cached_tools,
            "tool_choice": {"type": "auto"},
        }

        response = await _call_claude(**kwargs)

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        max_tool_rounds = 3
        round_num = 0

        while tool_use_blocks and round_num < max_tool_rounds:
            round_num += 1

            content_dicts = _serialize_content(response.content)
            messages.append({"role": "assistant", "content": content_dicts})

            tool_results = []
            for block in tool_use_blocks:
                func_name = block.name
                func_args = json.dumps(block.input, ensure_ascii=False)
                print(f"  🔧 Вызов: {func_name}({func_args})")

                result = await execute_tool(func_name, func_args, caller_id)
                print(f"  📦 Результат: {result[:200]}...")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

            response = await _call_claude(
                model=model,
                system=cached_system,
                messages=messages,
                max_tokens=1024,
                tools=cached_tools,
                tool_choice={"type": "auto"},
            )
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

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
