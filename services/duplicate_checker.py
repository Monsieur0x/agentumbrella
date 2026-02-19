"""
Сервис проверки дублей багов через Anthropic Claude (дешёвая модель).
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, MODEL_CHEAP
from models.bug import get_recent_bugs

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def check_duplicate(title: str, description: str) -> dict:
    """
    Проверяет, есть ли среди существующих багов дубль нового.

    Возвращает:
        {"is_duplicate": bool, "similar_bug_id": int|null, "explanation": str}
    """
    existing = await get_recent_bugs(limit=50)

    if not existing:
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": "Нет существующих багов для сравнения"}

    # Формируем список существующих багов
    bugs_text = "\n".join(
        f"- ID #{b['id']}: {b['title']} ({b['type']})"
        for b in existing
    )

    prompt = f"""Ты — система проверки дублей багов. Сравни новый баг с существующими.

Новый баг:
Заголовок: {title}
Описание: {description}

Существующие баги:
{bugs_text}

Есть ли среди существующих баг, который описывает ТУ ЖЕ САМУЮ проблему?
Похожие названия — не обязательно дубль. Дубль — это когда описана ОДНА И ТА ЖЕ конкретная проблема.

Ответь СТРОГО в формате JSON, без пояснений:
{{"is_duplicate": true/false, "similar_bug_id": число_или_null, "explanation": "краткое пояснение на русском"}}"""

    try:
        response = await client.messages.create(
            model=MODEL_CHEAP,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        text = response.content[0].text.strip()

        # Пытаемся извлечь JSON
        # Иногда модель оборачивает в ```json ... ```
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        return {
            "is_duplicate": bool(result.get("is_duplicate", False)),
            "similar_bug_id": result.get("similar_bug_id"),
            "explanation": result.get("explanation", ""),
        }
    except json.JSONDecodeError as e:
        print(f"⚠️ Ошибка парсинга JSON от Claude: {e}")
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": "Не удалось распарсить ответ ИИ"}
    except anthropic.APIError as e:
        print(f"⚠️ Ошибка Claude API при проверке дублей: {e}")
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": f"Ошибка API: {str(e)[:100]}"}