"""
Сервис проверки дублей багов через Anthropic Claude (дешёвая модель).
Использует общий throttle из agent.client для защиты от rate limit.
"""
import json
from config import MODEL, DUPLICATE_CHECK_LIMIT
from models.bug import get_recent_bugs
from agent.client import call_claude


async def check_duplicate(title: str, description: str) -> dict:
    """
    Проверяет, есть ли среди существующих багов дубль нового.

    Возвращает:
        {"is_duplicate": bool, "similar_bug_id": int|null, "explanation": str}
    """
    existing = await get_recent_bugs(limit=DUPLICATE_CHECK_LIMIT)
    print(f"[DUP-CHECK] Проверка \"{title[:50]}\" среди {len(existing)} багов")

    if not existing:
        print(f"[DUP-CHECK] Нет существующих багов для сравнения")
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": "Нет существующих багов для сравнения"}

    # Предфильтрация: сначала баги с тем же скриптом, потом остальные
    title_lower = title.lower().strip()
    candidates = [b for b in existing if title_lower in (b.get("title") or "").lower()]
    if not candidates:
        candidates = existing

    # Формируем список существующих багов с описаниями
    bugs_text = "\n".join(
        f"- ID #{b.get('display_number') or b['id']}: {b['title']} | Описание: {b.get('description', '')} ({b['type']})"
        for b in candidates
    )

    prompt = f"""Ты — система проверки дублей багов. Сравни новый баг с существующими.

Новый баг:
Скрипт: {title}
Шаги/описание: {description}

Существующие баги:
{bugs_text}

Есть ли среди существующих баг, который описывает ТУ ЖЕ САМУЮ или ОЧЕНЬ ПОХОЖУЮ проблему?
Дубль — это когда:
1. Описана одна и та же конкретная проблема
2. ИЛИ описания очень похожи по смыслу (одни и те же шаги, тот же результат)
3. ИЛИ речь идёт о том же скрипте и том же типе проблемы

Будь внимателен к ПОХОЖИМ описаниям — даже если формулировки разные, но суть одна, это дубль.

Ответь СТРОГО в формате JSON, без пояснений:
{{"is_duplicate": true/false, "similar_bug_id": число_или_null, "explanation": "краткое пояснение на русском"}}"""

    try:
        response = await call_claude(
            model=MODEL,
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
        is_dup = bool(result.get("is_duplicate", False))
        sim_id = result.get("similar_bug_id")
        print(f"[DUP-CHECK] Результат: {'дубль' if is_dup else 'не дубль'}" + (f" (похож на #{sim_id})" if sim_id else ""))
        return {
            "is_duplicate": is_dup,
            "similar_bug_id": sim_id,
            "explanation": result.get("explanation", ""),
        }
    except json.JSONDecodeError as e:
        print(f"[DUP-CHECK] ERROR парсинг JSON: {e}")
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": "Не удалось распарсить ответ ИИ"}
    except Exception as e:
        print(f"[DUP-CHECK] ERROR API: {e}")
        return {"is_duplicate": False, "similar_bug_id": None, "explanation": f"Ошибка API: {str(e)[:100]}"}
