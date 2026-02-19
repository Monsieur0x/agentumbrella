"""
🧠 МОЗГ АГЕНТА — ядро ИИ-агента на Anthropic Claude с function calling.
"""
import json
import time
import asyncio
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

# Мгновенные ответы БЕЗ вызова Claude API — экономим лимит
INSTANT_REPLIES = {
    "привет": "Привет! 👋 Я QA Manager. Чем могу помочь?",
    "здравствуй": "Здравствуйте! Чем могу помочь?",
    "здравствуйте": "Здравствуйте! Чем могу помочь?",
    "хай": "Хай! 👋 Что нужно?",
    "hello": "Hello! How can I help?",
    "hi": "Hi! 👋",
    "как дела": "Всё работает штатно! Чем могу помочь?",
    "спасибо": "Пожалуйста! 😊",
    "благодарю": "Всегда пожалуйста! 😊",
    "пока": "Пока! 👋",
    "до свидания": "До свидания! 👋",
    "ок": "👍",
    "окей": "👍",
    "да": "👍",
    "нет": "Хорошо.",
    "понял": "👍",
    "ясно": "👍",
    "круто": "😊",
    "отлично": "😊",
    "супер": "🔥",
    "класс": "😊",
    "хорошо": "👍",
    "ладно": "👍",
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
    "что нового": "Ничего нового, работаю в штатном режиме! Чем помочь?",
}


def get_instant_reply(text: str) -> str | None:
    """Мгновенный ответ без вызова API."""
    clean = text.lower().strip().rstrip("!?.,)")
    return INSTANT_REPLIES.get(clean)


def needs_tools(text: str) -> bool:
    """Определяет, нужны ли tools для этого сообщения."""
    clean = text.lower().strip()
    tool_keywords = [
        "начисли", "баллы", "балл", "рейтинг", "топ", "статистик",
        "предупреди", "предупреждение", "задание", "задачу",
        "неактивн", "не работал", "сравни", "поиск", "найди",
        "админ", "удали", "@",
    ]
    for kw in tool_keywords:
        if kw in clean:
            return True
    return False


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
    messages = [
        {"role": "user", "content": text},
    ]

    # 2. Определяем нужны ли tools
    use_tools = needs_tools(text)
    tools = get_tools_for_role(role) if use_tools else None

    try:
        kwargs = {
            "model": MODEL_AGENT,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": 2048,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "auto"}

        response = await _call_claude(**kwargs)

        # Ищем tool_use блоки в ответе
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Если ИИ вызвал функцию
        if tool_use_blocks:
            # Добавляем ответ ассистента (включая tool_use блоки) в историю
            messages.append({"role": "assistant", "content": response.content})

            # Выполняем все инструменты и собираем результаты
            tool_results = []
            for block in tool_use_blocks:
                func_name = block.name
                # block.input уже dict (не строка, как в Groq)
                func_args = json.dumps(block.input, ensure_ascii=False)
                print(f"  🔧 Вызов: {func_name}({func_args})")

                result = await execute_tool(func_name, func_args, caller_id)
                print(f"  📦 Результат: {result[:200]}...")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            # Отправляем все результаты одним user-сообщением
            messages.append({"role": "user", "content": tool_results})

            final_response = await _call_claude(
                model=MODEL_AGENT,
                system=system_prompt,
                messages=messages,
                max_tokens=2048,
            )
            text_blocks = [b for b in final_response.content if b.type == "text"]
            return text_blocks[0].text if text_blocks else "Готово ✅"

        # Нет tool_use — просто возвращаем текст
        text_blocks = [b for b in response.content if b.type == "text"]
        return text_blocks[0].text if text_blocks else "Не могу ответить."

    except anthropic.RateLimitError:
        return "⚠️ Claude API: превышен лимит запросов. Подождите немного."
    except anthropic.AuthenticationError:
        return "⚠️ Ошибка авторизации Claude API. Проверьте ANTHROPIC_API_KEY в .env"
    except Exception as e:
        error_str = str(e)
        print(f"❌ Ошибка brain: {e}")
        return f"⚠️ Ошибка: {error_str[:200]}"
