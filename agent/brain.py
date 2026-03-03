"""
🧠 МОЗГ АГЕНТА — ядро ИИ-агента на Anthropic Claude с function calling.
"""
import json
import re
from collections import OrderedDict
import anthropic
from config import MODEL, MAX_TOKENS, MAX_TOOL_ROUNDS, MAX_HISTORY, MAX_USERS_CACHE
from agent.system_prompt import get_system_prompt, get_chat_prompt
from agent.tools import get_tools_for_role
from agent.tool_executor import execute_tool
from agent.client import call_claude

# === История диалогов ===
# Ключ: chat_id (для групп — общая история, для ЛС — telegram_id пользователя)
_conversation_history: OrderedDict[int, list] = OrderedDict()


def _get_history(history_key: int) -> list:
    """Получает историю по ключу (chat_id или caller_id), обновляя LRU-порядок."""
    if history_key in _conversation_history:
        _conversation_history.move_to_end(history_key)
        return _conversation_history[history_key]
    if len(_conversation_history) >= MAX_USERS_CACHE:
        _conversation_history.popitem(last=False)
    _conversation_history[history_key] = []
    return _conversation_history[history_key]


def clear_history(caller_id: int):
    """Сбрасывает историю диалога для пользователя (при смене роли).
    В ЛС очищает per-user историю. В группе не влияет на общую историю."""
    _conversation_history.pop(caller_id, None)


def clear_all_history():
    """Полный сброс всей памяти бота — все диалоги всех пользователей."""
    _conversation_history.clear()


def _trim_history(history: list, max_messages: int):
    """Обрезает историю до max_messages сообщений."""
    while len(history) > max_messages:
        history.pop(0)


# Мгновенные ответы БЕЗ вызова Claude API — экономим токены
INSTANT_REPLIES = {
    # Приветствия
    "привет": "👋",
    "здравствуй": "👋",
    "здравствуйте": "👋",
    "хай": "👋",
    "hello": "👋",
    "hi": "👋",
    # Прощания
    "пока": "👋",
    "до свидания": "👋",
    # Благодарности и подтверждения
    "спасибо": "👍",
    "благодарю": "👍",
    "ок": "👍",
    "окей": "👍",
    "круто": "🔥",
    "отлично": "🔥",
    "супер": "🔥",
    "класс": "🔥",
    "как дела": "Всё ок, работаю.",
    "что нового": "Без изменений, работаю.",
    "кто ты": "Umbrella Bot — координирую тестирование чита для Dota 2 🤖",
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
        print(f"[DIRECT] Совпадение: рейтинг")
        from services.rating_service import get_rating, format_rating_message
        data = await get_rating()
        return format_rating_message(data)

    # --- Статистика конкретного тестера ---
    m = _RE_STATS.match(clean)
    if m:
        print(f"[DIRECT] Совпадение: статистика @{m.group(1)}")
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


async def process_message(text: str, username: str, role: str, topic: str,
                          caller_id: int = None, chat_id: int = None) -> str:
    """Главная функция мозга агента.
    chat_id — ID чата. Для групп: общая история на весь чат.
    Для ЛС: None, используется caller_id как ключ."""
    from config import MAX_GROUP_HISTORY

    # 1. Мгновенный ответ без API
    instant = get_instant_reply(text)
    if instant:
        print(f"[CLAUDE] Мгновенный ответ: \"{text.strip()[:50]}\"")
        return instant

    # 2. Прямые команды без Claude (рейтинг, статистика @username)
    direct = await try_direct_command(text, caller_id)
    if direct:
        print(f"[CLAUDE] Прямая команда без API: \"{text.strip()[:50]}\"")
        return direct

    context = {"username": username, "role": role, "topic": topic}
    system_prompt = get_system_prompt(context)

    model = MODEL

    # 4. Получаем историю: для групп — общая по chat_id, для ЛС — по caller_id
    history_key = chat_id if chat_id else caller_id
    history = _get_history(history_key)

    # В групповом чате добавляем username к сообщению для контекста
    if chat_id:
        user_text = f"@{username}: {text}"
        max_msgs = MAX_GROUP_HISTORY
    else:
        user_text = text
        max_msgs = MAX_HISTORY.get(role, 3) * 2

    history.append({"role": "user", "content": user_text})
    _trim_history(history, max_msgs)

    messages = [msg.copy() for msg in history]

    # 5. Инструменты по роли
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

        print(f"[CLAUDE] Запрос: role={role}, tools={len(tools) if tools else 0}, model={model}")
        response = await call_claude(**kwargs)
        usage = response.usage
        has_tools = any(b.type == "tool_use" for b in response.content)
        print(f"[CLAUDE] Ответ: {'tool_use' if has_tools else 'text'} (in={usage.input_tokens}, out={usage.output_tokens})")

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        max_tool_rounds = MAX_TOOL_ROUNDS
        round_num = 0
        # Инструменты, которые сами отправляют ответ пользователю (черновик и т.д.)
        _SILENT_TOOLS = {"create_task"}
        called_silent_tool = False
        silent_tool_error = None

        while tool_use_blocks and round_num < max_tool_rounds:
            round_num += 1

            content_dicts = _serialize_content(response.content)
            messages.append({"role": "assistant", "content": content_dicts})

            tool_results = []
            for block in tool_use_blocks:
                func_name = block.name
                func_args = json.dumps(block.input, ensure_ascii=False)
                print(f"[TOOL] Вызов: {func_name}({func_args[:100]})")

                if func_name in _SILENT_TOOLS:
                    called_silent_tool = True

                result = await execute_tool(func_name, func_args, caller_id, topic)
                print(f"[TOOL] {func_name} → {result[:150]}")

                # Проверяем ошибку в silent tool
                if func_name in _SILENT_TOOLS:
                    try:
                        result_data = json.loads(result)
                        if isinstance(result_data, dict) and result_data.get("error"):
                            silent_tool_error = result_data["error"]
                    except (json.JSONDecodeError, TypeError):
                        pass

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})

            cont_kwargs = {
                "model": model,
                "system": system_prompt,
                "messages": messages,
                "max_tokens": MAX_TOKENS,
            }
            if tools:
                cont_kwargs["tools"] = tools
                cont_kwargs["tool_choice"] = {"type": "auto"}
            response = await call_claude(**cont_kwargs)
            usage = response.usage
            has_tools = any(b.type == "tool_use" for b in response.content)
            print(f"[CLAUDE] Продолжение (раунд {round_num}): {'tool_use' if has_tools else 'text'} (in={usage.input_tokens}, out={usage.output_tokens})")
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Если silent tool вернул ошибку — сообщить пользователю
        if called_silent_tool and silent_tool_error:
            error_reply = f"⚠️ {silent_tool_error}"
            history.append({"role": "assistant", "content": error_reply})
            _trim_history(history, max_msgs)
            return error_reply

        # Если silent tool отработал без ошибки — не дублируем ответ
        if called_silent_tool:
            text_blocks = [b for b in response.content if b.type == "text"]
            reply = text_blocks[0].text if text_blocks else ""
            history.append({"role": "assistant", "content": reply or "Готово"})
            _trim_history(history, max_msgs)
            return None

        text_blocks = [b for b in response.content if b.type == "text"]
        reply = text_blocks[0].text if text_blocks else "Готово ✅"

        history.append({"role": "assistant", "content": reply})
        _trim_history(history, max_msgs)

        return reply

    except anthropic.RateLimitError:
        if history and history[-1].get("role") == "user":
            history.pop()
        print("[CLAUDE] RateLimitError — превышен лимит запросов")
        return "⚠️ Claude API: превышен лимит запросов. Подождите немного."
    except anthropic.AuthenticationError:
        if history and history[-1].get("role") == "user":
            history.pop()
        print("[CLAUDE] AuthenticationError — неверный API ключ")
        return "⚠️ Ошибка авторизации Claude API. Проверьте ANTHROPIC_API_KEY в .env"
    except anthropic.APIStatusError as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        if e.status_code in (400, 402):
            print(f"[CLAUDE] Баланс исчерпан: {e.status_code}")
            return "⚠️ Бот временно недоступен. Свяжитесь с руководителем."
        print(f"[CLAUDE] APIStatusError {e.status_code}: {e.message}")
        return f"⚠️ Ошибка API: {str(e)[:200]}"
    except Exception as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        print(f"[CLAUDE] ERROR: {e}")
        return f"⚠️ Ошибка: {str(e)[:200]}"


async def process_chat_message(text: str, caller_id: int) -> str:
    """Свободный чат без инструментов — просто болтовня."""
    from config import CHAT_MODEL

    system_prompt = get_chat_prompt()

    history = _get_history(caller_id)
    history.append({"role": "user", "content": text})
    _trim_history(history, 10)  # даём побольше истории для контекста

    messages = [msg.copy() for msg in history]

    try:
        print(f"[CLAUDE] Chat запрос от user_id={caller_id}, model={CHAT_MODEL}")
        response = await call_claude(
            model=CHAT_MODEL,
            system=system_prompt,
            messages=messages,
            max_tokens=MAX_TOKENS,
        )
        usage = response.usage
        print(f"[CLAUDE] Chat ответ (in={usage.input_tokens}, out={usage.output_tokens})")

        text_blocks = [b for b in response.content if b.type == "text"]
        reply = text_blocks[0].text if text_blocks else "чё"

        history.append({"role": "assistant", "content": reply})
        _trim_history(history, 10)

        return reply

    except anthropic.RateLimitError:
        if history and history[-1].get("role") == "user":
            history.pop()
        print("[CLAUDE] Chat: RateLimitError")
        return "⚠️ Claude API: превышен лимит запросов. Подождите немного."
    except anthropic.APIStatusError as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        if e.status_code in (400, 402):
            print(f"[CLAUDE] Chat: баланс исчерпан: {e.status_code}")
            return "⚠️ Бот временно недоступен. Свяжитесь с руководителем."
        print(f"[CLAUDE] Chat: APIStatusError {e.status_code}: {e.message}")
        return f"⚠️ Ошибка API: {str(e)[:200]}"
    except Exception as e:
        if history and history[-1].get("role") == "user":
            history.pop()
        print(f"[CLAUDE] Chat ERROR: {e}")
        return f"⚠️ Ошибка: {str(e)[:200]}"
