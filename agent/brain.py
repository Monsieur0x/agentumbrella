"""
🧠 МОЗГ АГЕНТА — ядро ИИ-агента на Anthropic Claude с function calling.
"""
import json
import time
import asyncio
from collections import defaultdict
import anthropic
from config import ANTHROPIC_API_KEY, MODEL_AGENT
from agent.system_prompt import get_system_prompt
from agent.tools import get_tools_for_role
from agent.tool_executor import execute_tool

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# === Защита от перерасхода лимита ===

# Минимальный интервал между запросами к Claude (секунды)
MIN_INTERVAL = 1.0
_last_request_time = 0.0

# === История диалогов per-user ===
# {caller_id: [{"role": ..., "content": ...}, ...]}
_conversation_history: dict[int, list] = defaultdict(list)
# Максимум сообщений в истории (пар user+assistant)
MAX_HISTORY_PAIRS = 5

# Мгновенные ответы БЕЗ вызова Claude API — только приветствия и помощь
INSTANT_REPLIES = {
    "привет": "Привет! 👋 Я QA Manager. Чем могу помочь?",
    "здравствуй": "Здравствуйте! Чем могу помочь?",
    "здравствуйте": "Здравствуйте! Чем могу помочь?",
    "хай": "Хай! 👋 Что нужно?",
    "hello": "Hello! How can I help?",
    "hi": "Hi! 👋",
    "пока": "Пока! 👋",
    "до свидания": "До свидания! 👋",
    "помощь": (
        "📋 <b>Что я умею:</b>\n\n"
        "• «Покажи рейтинг» — таблица тестеров\n"
        "• «Статистика @username» — баллы тестера\n"
        "• «Начисли @username N баллов за ...» — начислить\n"
        "• «Предупреди @username за ...» — предупреждение\n"
        "• «Кто не работал N дней?» — неактивные\n"
        "• «Дай задание — ...» — создать задание\n\n"
        "📝 Багрепорты → топик «Баги» или «Краши»"
    ),
    "help": (
        "📋 <b>Commands:</b>\n\n"
        "• Show rating\n• Stats @username\n"
        "• Award @username N points for ...\n"
        "• Warn @username for ...\n"
        "• Create task — ..."
    ),
    "что ты умеешь": (
        "📋 <b>Мои возможности:</b>\n\n"
        "• Рейтинг и статистика тестеров\n"
        "• Начисление/списание баллов\n"
        "• Предупреждения (макс 3)\n"
        "• Создание заданий с ИИ-расширением\n"
        "• Приём и проверка багрепортов\n"
        "• Аналитика по команде\n"
        "• Интеграция с Weeek"
    ),
    "кто ты": "Я QA Manager — ИИ-ассистент для управления командой тестировщиков 🤖",
}


def get_instant_reply(text: str) -> str | None:
    """Мгновенный ответ без вызова API — только для приветствий."""
    clean = text.lower().strip().rstrip("!?.,)")
    return INSTANT_REPLIES.get(clean)


def _trim_history(history: list):
    """Обрезает историю до MAX_HISTORY_PAIRS пар сообщений."""
    # Считаем пары user+assistant
    while len(history) > MAX_HISTORY_PAIRS * 2:
        history.pop(0)  # Удаляем самое старое


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
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_INTERVAL:
        wait = MIN_INTERVAL - elapsed
        await asyncio.sleep(wait)
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

    context = {
        "username": username,
        "role": role,
        "topic": topic,
    }

    system_prompt = get_system_prompt(context)

    # 2. Получаем историю и добавляем новое сообщение
    history = _conversation_history[caller_id]
    history.append({"role": "user", "content": text})
    _trim_history(history)

    # Копируем историю для запроса (чтобы не мутировать при tool calls)
    messages = [msg.copy() for msg in history]

    # 3. Всегда даём Claude доступ к tools — пусть сам решает
    tools = get_tools_for_role(role)

    try:
        kwargs = {
            "model": MODEL_AGENT,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": 2048,
            "tools": tools,
            "tool_choice": {"type": "auto"},
        }

        response = await _call_claude(**kwargs)

        # Ищем tool_use блоки в ответе
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Если ИИ вызвал функцию — цикл tool calls
        max_tool_rounds = 3  # Защита от бесконечного цикла
        round_num = 0

        while tool_use_blocks and round_num < max_tool_rounds:
            round_num += 1

            # Добавляем ответ ассистента в историю запроса
            content_dicts = _serialize_content(response.content)
            messages.append({"role": "assistant", "content": content_dicts})

            # Выполняем все инструменты и собираем результаты
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

            # Отправляем все результаты
            messages.append({"role": "user", "content": tool_results})

            # Следующий раунд
            response = await _call_claude(
                model=MODEL_AGENT,
                system=system_prompt,
                messages=messages,
                max_tokens=2048,
                tools=tools,
                tool_choice={"type": "auto"},
            )
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Извлекаем текстовый ответ
        text_blocks = [b for b in response.content if b.type == "text"]
        reply = text_blocks[0].text if text_blocks else "Готово ✅"

        # Сохраняем только финальный текстовый ответ в историю
        history.append({"role": "assistant", "content": reply})
        _trim_history(history)

        return reply

    except anthropic.RateLimitError:
        # Откатываем добавленное сообщение из истории
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
        error_str = str(e)
        print(f"❌ Ошибка brain: {e}")
        return f"⚠️ Ошибка: {error_str[:200]}"
