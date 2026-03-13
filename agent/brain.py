"""
🧠 МОЗГ АГЕНТА — единый модуль: Claude API клиент, системный промпт,
определения инструментов, исполнение инструментов, основной цикл обработки.

По образцу ChatGptController: всё в одном файле-контроллере.
"""
import json
import re
import time
import asyncio
import html as html_module
from collections import OrderedDict
from datetime import datetime, timedelta

import anthropic
import httpx

from config import (
    MODEL, MAX_TOKENS, MAX_TOOL_ROUNDS, MAX_HISTORY,
    MAX_USERS_CACHE, ANTHROPIC_API_KEY, SEARCH_BUGS_LIMIT,
)
from models.tester import (
    get_tester_by_username, get_all_testers, increment_warnings,
    decrement_warnings, reset_warnings, reset_all_warnings, set_tester_active,
)
from models.bug import (
    get_bug, mark_duplicate, get_bug_stats,
    delete_bug, delete_all_bugs, clear_weeek_task_id,
)
from models.admin import add_admin, remove_admin, get_all_admins, get_admin_ids
from services.points_service import award_points, award_points_bulk
from services.rating_service import get_rating
from json_store import (
    async_load, async_update,
    POINTS_LOG_FILE, WARNINGS_FILE, TESTERS_FILE, BUGS_FILE, TASKS_FILE,
)
from utils.logger import log_info, log_admin, get_bot


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   CLAUDE API КЛИЕНТ                              ║
# ╚══════════════════════════════════════════════════════════════════╝

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MIN_INTERVAL = 1.0
_last_request_time = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle():
    """Ждём если запросы слишком частые."""
    global _last_request_time
    async with _throttle_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < MIN_INTERVAL:
            await asyncio.sleep(MIN_INTERVAL - elapsed)
        _last_request_time = time.time()


async def call_claude(max_retries: int = 3, **kwargs):
    """Обёртка над client.messages.create с throttle и retry."""
    for attempt in range(max_retries):
        await _throttle()
        try:
            return await client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 500, 529) and attempt < max_retries - 1:
                wait = 2 ** attempt * 2
                print(f"[CLAUDE-CLIENT] {e.status_code}, retry {attempt + 1} через {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 2
                print(f"[CLAUDE-CLIENT] Network error ({type(e).__name__}), retry {attempt + 1} через {wait}с...")
                await asyncio.sleep(wait)
            else:
                raise


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   СИСТЕМНЫЙ ПРОМПТ                               ║
# ╚══════════════════════════════════════════════════════════════════╝

def get_system_prompt(context: dict) -> str:
    """
    Формирует системный промпт с контекстом.
    context: {"username": ..., "role": ..., "topic": ...}
    """
    role = context.get('role', 'tester')
    username = context.get('username', 'unknown')
    topic = context.get('topic', 'unknown')

    if role == "owner":
        role_block = (
            "Пользователь: РУКОВОДИТЕЛЬ (высшая роль).\n"
            "Доступно ВСЁ: manage_admin (добавить/удалить админа), switch_mode, "
            "баллы, варны, задания, баги, рейтинг, логины, обновление тестеров.\n"
            "Если руководитель просит снять/добавить/удалить админа — ВЫПОЛНЯЙ СРАЗУ через manage_admin."
        )
    elif role == "admin":
        role_block = (
            "Пользователь: АДМИН.\n"
            "Доступно: баллы, варны, задания, баги, рейтинг, логины, обновление тестеров.\n"
            "НЕ доступно: manage_admin, switch_mode (только руководитель)."
        )
    else:
        role_block = "Пользователь: ТЕСТЕР. Только свои баллы и рейтинг. НЕ вызывай другие функции."

    if role in ("owner", "admin"):
        role_sections = """
<функции>
• get_rating — ПОКАЗАТЬ топ по баллам («покажи рейтинг», «топ», «таблица»). НЕ путай с get_testers_list.
• publish_rating — ОПУБЛИКОВАТЬ в топик «Топ». ТОЛЬКО по явному «опубликуй», «запости», «отправь в топ».
• get_testers_list — список с варнами и статусом («список тестеров», «кто есть», «покажи варны»).
• delete_bug без уточнения → target="both". Все баги → delete_all=true, target="db_only".
</функции>

<составные_команды>
«варн неактивным»: get_inactive_testers → issue_warning_bulk(usernames=результат)
«баллы всем»: award_points_bulk(usernames="all", ...)
«баллы активным»: get_testers_list → award_points_bulk(usernames=результат)
«самые активные» → get_rating | «бездельники / афк» → get_inactive_testers
</составные_команды>
"""
    else:
        role_sections = ""

    prompt = f"""Ты — свой чувак в чате тестирования Umbrella (чит для Dota 2). Координируешь тестирование.
Роли: руководитель, админ, тестер.

@{username} | {role} | топик: {topic}
{role_block}

<классификация>
ПЕРЕД действием определи тип сообщения:

1. КОМАНДА — глагол + цель → выполняй сразу.
   «начисли @petrov 5 за баг» → award_points | «удали баг #12» → delete_bug | «опубликуй рейтинг» → publish_rating

2. ЗАПРОС — просят данные → вызови tool.
   «сколько баллов у @petrov?» → get_tester_stats | «кто неактивен?» → get_inactive_testers | «покажи рейтинг» → get_rating

3. БОЛТОВНЯ — нет команды → ответь текстом, БЕЗ tools.
   «ок», «👍», «молодец», «@petrov красавчик» — комплимент, НЕ команда.

4. НЕОДНОЗНАЧНОЕ → считай болтовнёй. При сомнении — переспроси коротко.
   «@petrov 5» → «начислить 5 баллов petrov?» | «сделай с рейтингом» → «показать или опубликовать?»
</классификация>

<реплай>
Сообщения с реплаем приходят в формате: (контекст: реплай на @username) текст
@username из контекста — цель команды, если после контекста идёт команда.
Если после контекста НЕ команда (ок, 👍, красавчик) — это просто болтовня.
Реплай сам по себе НЕ команда. Команда — глагол действия в тексте.
</реплай>
{role_sections}
<формат>
• Telegram HTML: <b>, <i>, <code>. НИКОГДА Markdown.
• Списки через • или —. Склоняй: 1 балл, 2 балла, 5 баллов.
• formatted_message из tool result → отправь как есть.
</формат>

<тон>
Неформальный, на «ты», коротко. Мат/подъёбки ок. БЕЗ приветствий, извинений, подлизы.
Эмодзи только системные (❗️✅🏆⚠️). Слово «владелец» → «руководитель».
НИКОГДА не тегай админов/руководителя через @ — только текстом.
</тон>

<правила>
• НЕ выдумывай данные — вызывай функцию. Нет функции — скажи прямо.
• НИКОГДА не ври что выполнил действие если не вызвал функцию. Не вызвал tool = не сделал.
• НЕ раскрывай названия tools/API.
• Команды — ВЫПОЛНЯЙ СРАЗУ без подтверждения.
• Причина не указана → «Без причины». НИКОГДА не спрашивай причину.
</правила>"""

    return prompt


def get_chat_prompt() -> str:
    """Промпт для свободного чат-режима (без инструментов координатора)."""
    return """Ты — просто участник чата. Не координатор, не бот, не ассистент. У тебя нет tools и функций — просто общайся.

Формат: Telegram HTML (<b>, <i>, <code>). НЕ Markdown. Списки через • или —.

Тон: неформальный, на «ты», коротко. Лёгкий мат ок. Подъёбки ок. Терминология Dota 2 и гейминга свободно. Обсуждаешь любые темы.

НЕ пытайся выполнять команды, начислять баллы, управлять тестерами — у тебя нет таких функций в этом режиме. Если просят что-то сделать — скажи что сейчас в режиме чата."""


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   ОПРЕДЕЛЕНИЯ ИНСТРУМЕНТОВ                       ║
# ╚══════════════════════════════════════════════════════════════════╝

ALL_TOOLS = [
    # --- АНАЛИТИКА ---
    {
        "name": "get_tester_stats",
        "description": "Статистика тестера: баллы, баги, игры, варны.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "@username или имя"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "get_team_stats",
        "description": "Общая статистика команды за период.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "week", "month", "all"]
                }
            },
            "required": ["period"]
        }
    },
    {
        "name": "get_inactive_testers",
        "description": "Тестеры без активности за N дней.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Дней без активности (по умолчанию 7)"}
            },
            "required": []
        }
    },
    {
        "name": "compare_testers",
        "description": "Сравнить двух тестеров.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username1": {"type": "string"},
                "username2": {"type": "string"}
            },
            "required": ["username1", "username2"]
        }
    },
    {
        "name": "get_bug_stats",
        "description": "Статистика багов за период.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "week", "month", "all"]},
                "type": {"type": "string", "enum": ["bug", "all"]}
            },
            "required": ["period"]
        }
    },
    {
        "name": "get_testers_list",
        "description": "Список тестеров: имена, баллы, варны, статус. Для просмотра варнов и состава команды.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_inactive": {"type": "boolean"}
            },
            "required": []
        }
    },

    # --- БАЛЛЫ ---
    {
        "name": "award_points",
        "description": "Начислить/списать баллы одному тестеру (+/-). Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "amount": {"type": "integer", "description": "Количество (+/-)"},
                "reason": {"type": "string", "description": "Причина (по умолчанию 'Без причины')"}
            },
            "required": ["username", "amount"]
        }
    },
    {
        "name": "award_points_bulk",
        "description": "Баллы нескольким тестерам или всем сразу. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {"type": "string", "description": "Юзернеймы через запятую (petrov,ivanov) или 'all' для всех"},
                "amount": {"type": "integer", "description": "Количество (+/-)"},
                "reason": {"type": "string", "description": "Причина (по умолчанию 'Без причины')"}
            },
            "required": ["usernames", "amount"]
        }
    },

    # --- ПРЕДУПРЕЖДЕНИЯ ---
    {
        "name": "issue_warning",
        "description": "Выдать варн тестеру. Админ. Без причины → 'Без причины'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "reason": {"type": "string", "description": "Причина (по умолчанию 'Без причины')"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "issue_warning_bulk",
        "description": "Варн нескольким/всем тестерам. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {"type": "string", "description": "Юзернеймы через запятую или 'all'"},
                "reason": {"type": "string", "description": "Причина (по умолчанию 'Без причины')"}
            },
            "required": ["usernames"]
        }
    },
    {
        "name": "remove_warning",
        "description": "Снять варн(ы) тестеру/тестерам. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {"type": "string", "description": "Юзернеймы через запятую или 'all'"},
                "amount": {"type": "integer", "description": "Сколько варнов снять (по умолчанию 1). 0 = сбросить все варны", "default": 1}
            },
            "required": ["usernames"]
        }
    },

    # --- ЗАДАНИЯ ---
    {
        "name": "create_task",
        "description": "Создать задание для тестеров. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brief": {"type": "string", "description": "Описание задания"}
            },
            "required": ["brief"]
        }
    },

    # --- РЕЙТИНГ ---
    {
        "name": "get_rating",
        "description": "Показать рейтинг тестеров (без публикации). Для просмотра топа по баллам.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {"type": "integer", "description": "Сколько показать. 0 или не указано = все тестеры"}
            },
            "required": []
        }
    },
    {
        "name": "publish_rating",
        "description": "Опубликовать рейтинг в топик «Топ». Вызывай ТОЛЬКО если пользователь явно попросил опубликовать/запостить. Для просмотра используй get_rating. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {"type": "integer", "description": "0 = все"},
                "comment": {"type": "string", "description": "Комментарий к рейтингу"}
            },
            "required": []
        }
    },

    # --- УПРАВЛЕНИЕ АДМИНАМИ ---
    {
        "name": "manage_admin",
        "description": "Управление админами. Только руководитель.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "list"]},
                "username": {"type": "string"}
            },
            "required": ["action"]
        }
    },

    # --- ОБНОВЛЕНИЕ СПИСКА ТЕСТЕРОВ ---
    {
        "name": "refresh_testers",
        "description": "Синхронизировать список тестеров с группой. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # --- БАГИ ---
    {
        "name": "mark_bug_duplicate",
        "description": "Пометить баг дублём. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"}
            },
            "required": ["bug_id"]
        }
    },
    {
        "name": "search_bugs",
        "description": "Поиск багов. Укажи хотя бы один фильтр: bug_id, query, tester или status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer", "description": "ID бага"},
                "query": {"type": "string", "description": "Текстовый поиск"},
                "tester": {"type": "string", "description": "@username тестера"},
                "status": {"type": "string", "enum": ["pending", "accepted", "rejected", "duplicate", "all"]}
            },
            "required": []
        }
    },
    {
        "name": "delete_bug",
        "description": "Удалить баг(и). Админ. delete_all=true для всех.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "target": {"type": "string", "enum": ["db_only", "weeek_only", "both"]},
                "delete_all": {"type": "boolean"}
            },
            "required": ["target"]
        }
    },

    # --- ИГРОВЫЕ ЛОГИНЫ ---
    {
        "name": "link_login",
        "description": "Привязать/отвязать игровой логин к тестеру. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["link", "unlink", "check"], "description": "link/unlink/check"},
                "login": {"type": "string", "description": "Игровой логин"},
                "username": {"type": "string", "description": "@username тестера (для link)"}
            },
            "required": ["action", "login"]
        }
    },
    {
        "name": "get_logins_list",
        "description": "Список привязанных логинов и тестеров без привязки. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "switch_mode",
        "description": "Переключить режим бота. Только руководитель.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["active", "observe"]}
            },
            "required": ["mode"]
        }
    },
]


def get_tools_for_role(role: str) -> list:
    """Возвращает набор инструментов в зависимости от роли."""
    if role == "owner":
        return ALL_TOOLS
    if role == "admin":
        return [t for t in ALL_TOOLS if t["name"] != "manage_admin"]
    tester_tools = ["get_tester_stats", "get_rating"]
    return [t for t in ALL_TOOLS if t["name"] in tester_tools]


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   ИСТОРИЯ ДИАЛОГОВ                               ║
# ╚══════════════════════════════════════════════════════════════════╝

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
    """Сбрасывает историю диалога для пользователя (при смене роли)."""
    _conversation_history.pop(caller_id, None)


def clear_all_history():
    """Полный сброс всей памяти бота — все диалоги всех пользователей."""
    _conversation_history.clear()


def _trim_history(history: list, max_messages: int):
    """Обрезает историю до max_messages сообщений."""
    while len(history) > max_messages:
        history.pop(0)


# === Мгновенные ответы БЕЗ вызова Claude API ===

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


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   TOOL EXECUTOR                                  ║
# ╚══════════════════════════════════════════════════════════════════╝

def _normalize_username(username: str) -> str:
    """Убирает @ в начале username, если есть."""
    return username.lstrip("@") if username else ""


def _tag(username: str) -> str:
    """Форматирует username текстом (без @, чтобы не тегать в Telegram). HTML-safe."""
    if not username:
        return "?"
    return html_module.escape(username.lstrip("@"))


async def execute_tool(name: str, arguments: str, caller_id: int = None, topic: str = "") -> str:
    """
    Выполняет функцию по имени и возвращает JSON-результат.
    arguments — строка JSON от ИИ.
    """
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return json.dumps({"error": "Не удалось разобрать аргументы"}, ensure_ascii=False)

    try:
        result = await _dispatch(name, args, caller_id, topic)
        print(f"[TOOL-EXEC] {name} → OK")
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[TOOL-EXEC] {name} → ERROR: {e}")
        return json.dumps({"error": f"Ошибка: {str(e)}"}, ensure_ascii=False)


_ADMIN_TOOLS = {
    "award_points", "award_points_bulk", "issue_warning", "issue_warning_bulk",
    "remove_warning", "create_task", "mark_bug_duplicate", "search_bugs",
    "delete_bug", "publish_rating", "refresh_testers", "link_login", "get_logins_list",
}
_OWNER_TOOLS = {"manage_admin", "switch_mode"}


async def _check_permission(name: str, caller_id: int) -> str | None:
    """Возвращает сообщение об ошибке если нет прав, иначе None."""
    if name not in _ADMIN_TOOLS and name not in _OWNER_TOOLS:
        return None
    from models.admin import is_admin, is_owner
    if name in _OWNER_TOOLS:
        if not await is_owner(caller_id or 0):
            return "Только для руководителя"
    if name in _ADMIN_TOOLS:
        cid = caller_id or 0
        if await is_admin(cid) or await is_owner(cid):
            return None
        return "Недостаточно прав"
    return None


async def _dispatch(name: str, args: dict, caller_id: int = None, topic: str = "") -> dict:
    """Маршрутизация вызовов функций."""

    # === Проверка прав ===
    perm_error = await _check_permission(name, caller_id)
    if perm_error:
        return {"error": perm_error}

    # === АНАЛИТИКА ===
    if name == "get_tester_stats":
        return await _get_tester_stats(args["username"], caller_id)

    elif name == "get_team_stats":
        return await _get_team_stats(args.get("period", "all"))

    elif name == "get_inactive_testers":
        return await _get_inactive_testers(args.get("days", 7))

    elif name == "compare_testers":
        return await _compare_testers(args["username1"], args["username2"])

    elif name == "get_testers_list":
        return await _get_testers_list(args.get("include_inactive", False))

    elif name == "get_bug_stats":
        return await _get_bug_stats_handler(args.get("period", "all"), args.get("type", "all"))

    # === БАЛЛЫ ===
    elif name == "award_points":
        reason = args.get("reason", "Без причины")
        result = await award_points(
            args["username"], args["amount"], reason, caller_id
        )
        if result.get("success"):
            await log_admin(
                f"{result['username']}: {'+' if args['amount'] > 0 else ''}{args['amount']} б. ({reason})"
            )
        return result

    elif name == "award_points_bulk":
        usernames = args.get("usernames", "all")
        reason = args.get("reason", "Без причины")
        result = await award_points_bulk(usernames, args["amount"], reason, caller_id)
        if result.get("success_count", 0) > 0:
            await log_admin(f"Массовое начисление: {args['amount']} б. ({reason}) — {result['success_count']} тестерам")
        return result

    # === ПРЕДУПРЕЖДЕНИЯ ===
    elif name == "issue_warning":
        return await _issue_warning(args["username"], args.get("reason", "Без причины"), caller_id)

    elif name == "issue_warning_bulk":
        return await _issue_warning_bulk(args["usernames"], args.get("reason", "Без причины"), caller_id)

    elif name == "remove_warning":
        return await _remove_warning(args["usernames"], args.get("amount", 1), caller_id)

    # === ЗАДАНИЯ ===
    elif name == "create_task":
        return await _create_task(args["brief"], caller_id)

    # === РЕЙТИНГ ===
    elif name == "get_rating":
        data = await get_rating(args.get("top_count", 0))
        from services.rating_service import format_rating_message
        data["formatted_message"] = format_rating_message(data)
        return data

    elif name == "publish_rating":
        data = await get_rating(args.get("top_count", 0))
        comment = args.get("comment", "")
        from services.rating_service import publish_rating_to_topic, format_rating_message
        formatted = format_rating_message(data)
        if comment:
            formatted += f"\n\n{comment}"
        data["formatted_message"] = formatted

        bot = get_bot()
        if not bot:
            data["published"] = False
            return data

        # ЛС → превью + кнопки подтверждения
        if topic == "private":
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            top_count = args.get("top_count", 0)
            cb_data = f"rating_publish:{top_count}"
            preview_text = (
                f"📋 <b>Превью рейтинга</b>\n\n"
                f"{formatted}\n\n"
                f"─────────────────\n"
                f"Опубликовать в топик «Топ»?"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Опубликовать", callback_data=cb_data),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="rating_cancel"),
                ]
            ])
            try:
                await bot.send_message(
                    chat_id=caller_id,
                    text=preview_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as e:
                print(f"❌ Ошибка отправки превью рейтинга: {e}")
            data["published"] = False
            data["awaiting_confirmation"] = True
            return data

        # Группа → публикуем сразу
        msg_id = await publish_rating_to_topic(bot, data, comment)
        data["published"] = bool(msg_id)
        if msg_id:
            await log_admin("Рейтинг опубликован в топик «Топ»")
        return data

    # === АДМИНЫ ===
    elif name == "manage_admin":
        return await _manage_admin(args["action"], args.get("username"))

    # === БАГИ ===
    elif name == "mark_bug_duplicate":
        await mark_duplicate(args["bug_id"])
        await log_info(f"Баг #{args['bug_id']} помечен как дубль")
        return {"success": True, "bug_id": args["bug_id"], "status": "duplicate"}

    elif name == "search_bugs":
        return await _search_bugs(args.get("query"), args.get("tester"), args.get("bug_id"), args.get("status"))

    elif name == "delete_bug":
        return await _delete_bug(args.get("bug_id"), args["target"], args.get("delete_all", False))

    elif name == "refresh_testers":
        return await _refresh_testers()

    elif name == "link_login":
        return await _link_login(args["action"], args["login"], args.get("username"))

    elif name == "get_logins_list":
        return await _get_logins_list()

    elif name == "switch_mode":
        return await _switch_mode(args["mode"])

    else:
        return {"error": f"Неизвестная функция: {name}"}


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   РЕАЛИЗАЦИИ ИНСТРУМЕНТОВ                        ║
# ╚══════════════════════════════════════════════════════════════════╝

async def _get_testers_list(include_inactive: bool = False) -> dict:
    testers = await get_all_testers(active_only=not include_inactive)
    admin_ids_set = await get_admin_ids()
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
    return {
        "total": len(testers),
        "testers": [
            {
                "username": _tag(t["username"]),
                "full_name": t["full_name"],
                "total_points": t["total_points"],
                "warnings_count": t["warnings_count"],
                "is_active": t["is_active"],
            }
            for t in testers
        ]
    }


async def _get_tester_stats(username: str, caller_id: int = None) -> dict:
    from models.tester import get_tester_by_id
    tester = await get_tester_by_username(_normalize_username(username))
    if not tester and caller_id:
        tester = await get_tester_by_id(caller_id)
    if not tester:
        return {"error": f"Тестер @{_normalize_username(username)} не найден"}
    return {
        "username": _tag(tester["username"]),
        "full_name": tester["full_name"],
        "total_points": tester["total_points"],
        "total_bugs": tester["total_bugs"],
        "total_games": tester["total_games"],
        "warnings_count": tester["warnings_count"],
        "is_active": tester["is_active"],
        "registered": tester["created_at"],
    }


async def _get_team_stats(period: str) -> dict:
    testers = await get_all_testers()
    admin_ids_set = await get_admin_ids()
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
    bugs = await get_bug_stats(period)

    period_filter = {
        "today": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }

    if period in period_filter:
        cutoff = datetime.now() - period_filter[period]
        points_data = await async_load(POINTS_LOG_FILE)
        items = points_data.get("items", [])

        period_points_map = {}
        period_games_map = {}
        period_bugs_map = {}
        for entry in items:
            created = entry.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created)
            except (ValueError, TypeError):
                continue
            if dt >= cutoff:
                tid = entry.get("tester_id")
                period_points_map[tid] = period_points_map.get(tid, 0) + entry.get("amount", 0)
                source = entry.get("source", "")
                if source == "game":
                    period_games_map[tid] = period_games_map.get(tid, 0) + 1
                elif source == "bug":
                    period_bugs_map[tid] = period_bugs_map.get(tid, 0) + 1

        total_points = sum(period_points_map.values())
        total_games = sum(period_games_map.values())

        for t in testers:
            t["_period_points"] = period_points_map.get(t["telegram_id"], 0)
            t["_period_games"] = period_games_map.get(t["telegram_id"], 0)
            t["_period_bugs"] = period_bugs_map.get(t["telegram_id"], 0)
        testers_sorted = sorted(testers, key=lambda t: t["_period_points"], reverse=True)
        top3 = testers_sorted[:3]
    else:
        total_points = sum(t["total_points"] for t in testers)
        total_games = sum(t["total_games"] for t in testers)
        for t in testers:
            t["_period_points"] = t["total_points"]
            t["_period_games"] = t["total_games"]
            t["_period_bugs"] = t["total_bugs"]
        top3 = testers[:3] if testers else []

    return {
        "period": period,
        "total_testers": len(testers),
        "total_points": total_points,
        "total_games": total_games,
        "bugs_stats": bugs,
        "top_3": [
            {"username": _tag(t["username"]),
             "points": t["_period_points"],
             "bugs": t["_period_bugs"], "games": t["_period_games"]}
            for t in top3
        ],
        "average_points": round(total_points / len(testers), 1) if testers else 0,
    }


async def _get_inactive_testers(days: int) -> dict:
    """Тестеры без активности за N дней."""
    cutoff = datetime.now() - timedelta(days=days)
    testers = await get_all_testers(active_only=True)
    admin_ids_set = await get_admin_ids()
    testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
    points_data = await async_load(POINTS_LOG_FILE)
    items = points_data.get("items", [])

    last_activity = {}
    for entry in items:
        tid = entry.get("tester_id")
        created = entry.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            continue
        if tid not in last_activity or dt > last_activity[tid]:
            last_activity[tid] = dt

    inactive = []
    for t in testers:
        tid = t["telegram_id"]
        la = last_activity.get(tid)
        if la is None or la < cutoff:
            inactive.append({
                "username": _tag(t["username"]),
                "full_name": t["full_name"],
                "last_activity": la.isoformat() if la else None,
            })

    return {
        "days": days,
        "inactive_count": len(inactive),
        "testers": inactive,
    }


async def _compare_testers(u1: str, u2: str) -> dict:
    t1 = await get_tester_by_username(_normalize_username(u1))
    t2 = await get_tester_by_username(_normalize_username(u2))
    if not t1:
        return {"error": f"Тестер @{_normalize_username(u1)} не найден"}
    if not t2:
        return {"error": f"Тестер @{_normalize_username(u2)} не найден"}

    return {
        "tester_1": {
            "username": _tag(t1["username"]), "points": t1["total_points"],
            "bugs": t1["total_bugs"], "games": t1["total_games"],
        },
        "tester_2": {
            "username": _tag(t2["username"]), "points": t2["total_points"],
            "bugs": t2["total_bugs"], "games": t2["total_games"],
        }
    }


async def _get_bug_stats_handler(period: str, bug_type: str) -> dict:
    return await get_bug_stats(period, bug_type)


async def _issue_warning(username: str, reason: str, admin_id: int) -> dict:
    tester = await get_tester_by_username(_normalize_username(username))
    if not tester:
        return {"error": f"Тестер @{_normalize_username(username)} не найден"}

    new_count = await increment_warnings(tester["telegram_id"])

    def add_warning(data):
        entry_id = data.get("next_id", 1)
        data["next_id"] = entry_id + 1
        if "items" not in data:
            data["items"] = []
        data["items"].append({
            "id": entry_id,
            "tester_id": tester["telegram_id"],
            "reason": reason,
            "admin_id": admin_id,
            "created_at": datetime.now().isoformat(),
        })
        return data

    await async_update(WARNINGS_FILE, add_warning)
    await log_admin(f"Предупреждение {_tag(tester['username'])}: {reason} ({new_count}/3)")

    deactivated = False
    if new_count >= 3:
        await set_tester_active(tester["telegram_id"], False)
        deactivated = True
        await log_admin(f"Тестер {_tag(tester['username'])} деактивирован (3/3 предупреждений)")

    bot = get_bot()
    if bot:
        try:
            warn_text = (
                f"⚠️ <b>Предупреждение</b>\n\n"
                f"Причина: {reason}\n"
                f"Это предупреждение <b>{new_count} из 3</b>."
            )
            if deactivated:
                warn_text += "\n\n🚫 <b>Вы деактивированы.</b> Обратитесь к администрации."
            await bot.send_message(
                chat_id=tester["telegram_id"],
                text=warn_text,
                parse_mode="HTML"
            )
        except Exception:
            pass

    return {
        "success": True,
        "username": _tag(tester["username"]),
        "reason": reason,
        "warnings_total": new_count,
        "max_warnings": 3,
        "deactivated": deactivated,
        "telegram_id": tester["telegram_id"],
    }


async def _issue_warning_bulk(usernames: str, reason: str, admin_id: int) -> dict:
    """Выдаёт варны нескольким тестерам или всем сразу."""
    usernames = usernames.strip()

    if usernames.lower() == "all":
        testers = await get_all_testers(active_only=True)
        admin_ids_set = await get_admin_ids()
        testers = [t for t in testers if t["telegram_id"] not in admin_ids_set]
        names = [t["username"] for t in testers if t.get("username")]
    else:
        names = [_normalize_username(u.strip()) for u in usernames.split(",") if u.strip()]

    if not names:
        return {"error": "Не указаны юзернеймы"}

    results = []
    for uname in names:
        result = await _issue_warning(uname, reason, admin_id)
        results.append(result)

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": success_count > 0,
        "results": results,
        "success_count": success_count,
        "reason": reason,
    }


async def _remove_warning(usernames: str, amount: int, _admin_id: int) -> dict:
    """Снимает варны у одного, нескольких или всех тестеров."""
    usernames = usernames.strip()

    if usernames.lower() == "all":
        affected = await reset_all_warnings()
        def clear_all(data):
            data["items"] = []
            return data
        await async_update(WARNINGS_FILE, clear_all)
        await log_admin(f"Сброшены все варны ({affected} тестеров)")
        return {
            "success": True,
            "action": "reset_all",
            "affected_count": affected,
        }

    names = [_normalize_username(u.strip()) for u in usernames.split(",") if u.strip()]
    if not names:
        return {"error": "Не указаны юзернеймы"}

    results = []
    for uname in names:
        tester = await get_tester_by_username(uname)
        if not tester:
            results.append({"username": uname, "error": "не найден"})
            continue

        old_count = tester["warnings_count"]
        if old_count == 0:
            results.append({"username": _tag(tester["username"]), "warnings": 0, "skipped": True})
            continue

        if amount == 0:
            new_count = await reset_warnings(tester["telegram_id"])
            def remove_all_for_tester(data, tid=tester["telegram_id"]):
                data["items"] = [w for w in data.get("items", []) if w.get("tester_id") != tid]
                return data
            await async_update(WARNINGS_FILE, remove_all_for_tester)
        else:
            new_count = await decrement_warnings(tester["telegram_id"], amount)
            def remove_last_n(data, tid=tester["telegram_id"], n=amount):
                items = data.get("items", [])
                tester_warnings = [(i, w) for i, w in enumerate(items) if w.get("tester_id") == tid]
                tester_warnings.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
                indices_to_remove = {idx for idx, _ in tester_warnings[:n]}
                data["items"] = [w for i, w in enumerate(items) if i not in indices_to_remove]
                return data
            await async_update(WARNINGS_FILE, remove_last_n)

        if not tester["is_active"] and new_count < 3:
            await set_tester_active(tester["telegram_id"], True)

        await log_admin(f"Снят варн {_tag(tester['username'])}: {old_count} → {new_count}")

        bot = get_bot()
        if bot:
            try:
                text = (
                    f"✅ <b>Варн снят</b>\n\n"
                    f"Предупреждений: <b>{new_count} из 3</b>."
                )
                if not tester["is_active"] and new_count < 3:
                    text += "\n\n🔓 <b>Вы снова активны.</b>"
                await bot.send_message(
                    chat_id=tester["telegram_id"],
                    text=text,
                    parse_mode="HTML"
                )
            except Exception:
                pass

        results.append({
            "username": _tag(tester["username"]),
            "old_warnings": old_count,
            "new_warnings": new_count,
            "reactivated": not tester["is_active"] and new_count < 3,
        })

    success_count = sum(1 for r in results if "error" not in r and not r.get("skipped"))
    return {
        "success": success_count > 0,
        "results": results,
        "success_count": success_count,
    }


async def _create_task(brief: str, admin_id: int) -> dict:
    """Создаёт черновик задания: расширяет через ИИ и отправляет на подтверждение."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    full_text = brief
    try:
        response = await call_claude(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Ты — координатор тестирования Umbrella, чита для Dota 2. "
                    "Пиши как координатор в чате, а не как менеджер с ТЗ.\n\n"
                    "Стиль:\n"
                    "- Коротко: что делать, где делать, куда скидывать баги\n"
                    "- Новую/неочевидную функцию поясни одним предложением — не больше\n"
                    "- Можно обращаться ко всем сразу (\"зайдите\", \"проверьте\", \"потыкайте\")\n"
                    "- Указывай конкретику: герой, аспект, шард, режим (турбо/лобби/паблик), бета или паблик билд\n"
                    "- Формат багрепорта — только если он важен (видео, debug.log, краш-лог, matchID)\n"
                    "- Два-четыре предложения — норма. Длиннее — только если реально нужно расписать условия\n"
                    "- НЕ используй HTML-теги и markdown. Только plain text и эмодзи\n"
                    "- Не добавляй заголовок или номер задания — только текст задания\n\n"
                    "Правила:\n"
                    "- Только функционал, реальный для чита Dota 2\n"
                    "- Названия героев, скиллов, предметов — как в игре\n"
                    "- Не выдумывай функции, которые не упомянуты\n"
                    "- Пиши задание строго по тому, что указано. Не додумывай лишнего\n\n"
                    f"Краткое задание: {brief}"
                ),
            }],
            max_tokens=500,
        )
        full_text = response.content[0].text or brief
    except Exception as e:
        print(f"⚠️ Не удалось расширить задание: {e}")

    result = {}

    def create(data):
        task_id = data.get("next_id", 1)
        data["next_id"] = task_id + 1
        if "items" not in data:
            data["items"] = {}
        data["items"][str(task_id)] = {
            "id": task_id,
            "admin_id": admin_id,
            "brief": brief,
            "full_text": full_text,
            "message_id": None,
            "status": "draft",
            "created_at": datetime.now().isoformat(),
        }
        result["task_id"] = task_id
        return data

    await async_update(TASKS_FILE, create)
    task_id = result["task_id"]

    bot = get_bot()
    if bot:
        safe_text = html_module.escape(full_text)
        preview_text = (
            f"📋 <b>Черновик задания #{task_id}</b>\n\n"
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
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=preview_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            print(f"❌ Ошибка отправки превью задания: {e}")

    await log_info(f"Создан черновик задания #{task_id}")

    return {
        "success": True,
        "task_id": task_id,
        "brief": brief,
        "awaiting_confirmation": True,
    }


async def _manage_admin(action: str, username: str = None) -> dict:
    if action == "list":
        admins = await get_all_admins()
        return {
            "admins": [
                {"username": _tag(a["username"]), "is_owner": a["is_owner"], "added_at": a["added_at"]}
                for a in admins
            ]
        }

    if not username:
        return {"error": "Не указан юзернейм"}

    clean_username = _normalize_username(username)
    tester = await get_tester_by_username(clean_username)
    if action == "add":
        if not tester:
            return {"error": f"@{clean_username} не найден в базе. Человек должен сначала написать в группу."}
        ok = await add_admin(tester["telegram_id"], tester["username"], tester["full_name"])
        if ok:
            clear_history(tester["telegram_id"])
        return {"success": ok, "action": "added", "username": _tag(tester["username"])}

    elif action == "remove":
        if not tester:
            return {"error": f"@{clean_username} не найден"}
        ok = await remove_admin(tester["telegram_id"])
        if not ok:
            return {"error": "Не удалось удалить (возможно, это руководитель)"}
        clear_history(tester["telegram_id"])
        return {"success": True, "action": "removed", "username": _tag(tester["username"])}

    return {"error": f"Неизвестное действие: {action}"}


async def _refresh_testers() -> dict:
    """Проверяет членство каждого тестера в группе и деактивирует кикнутых/ушедших."""
    from config import GROUP_ID

    bot = get_bot()
    if not bot:
        return {"error": "Бот недоступен"}
    if not GROUP_ID:
        return {"error": "GROUP_ID не задан"}

    testers = await get_all_testers(active_only=True)
    admin_ids = await get_admin_ids()

    deactivated = []
    still_active = []

    for t in testers:
        if t["telegram_id"] in admin_ids:
            continue
        try:
            member = await bot.get_chat_member(GROUP_ID, t["telegram_id"])
            if member.status in ("left", "kicked"):
                await set_tester_active(t["telegram_id"], False)
                deactivated.append(_tag(t["username"]) or t["full_name"])
            else:
                still_active.append(_tag(t["username"]) or t["full_name"])
        except Exception:
            still_active.append(_tag(t["username"]) or t["full_name"])

    if deactivated:
        await log_admin(f"Обновление тестеров: деактивированы {', '.join(deactivated)}")

    return {
        "success": True,
        "active_count": len(still_active),
        "deactivated_count": len(deactivated),
        "deactivated": deactivated,
    }


async def _search_bugs(query: str = None, tester: str = None,
                       bug_id: int = None, status: str = None) -> dict:
    bugs_data = await async_load(BUGS_FILE)
    items = bugs_data.get("items", {})
    testers_data = await async_load(TESTERS_FILE)

    if bug_id:
        bug = items.get(str(bug_id))
        if not bug:
            return {"error": f"Баг #{bug_id} не найден"}
        bug = dict(bug)
        tid_key = str(bug.get("tester_id", ""))
        t = testers_data.get(tid_key, {})
        if t.get("username"):
            bug["username"] = _tag(t["username"])
        return {"count": 1, "bugs": [bug]}

    results = []
    for b in items.values():
        if status and status != "all" and b.get("status") != status:
            continue

        if tester:
            tid_key = str(b.get("tester_id", ""))
            t = testers_data.get(tid_key, {})
            if not t.get("username") or t["username"].lower() != _normalize_username(tester).lower():
                continue

        if query:
            q = query.lower()
            title = (b.get("title") or "").lower()
            desc = (b.get("description") or "").lower()
            script = (b.get("script_name") or "").lower()
            if q not in title and q not in desc and q not in script:
                continue

        bug = dict(b)
        tid_key = str(bug.get("tester_id", ""))
        t = testers_data.get(tid_key, {})
        if t.get("username"):
            bug["username"] = _tag(t["username"])
        results.append(bug)

    results.sort(key=lambda b: b.get("id", 0), reverse=True)
    results = results[:SEARCH_BUGS_LIMIT]

    return {
        "query": query or "",
        "tester": tester or "",
        "status": status or "all",
        "count": len(results),
        "bugs": results,
    }


async def _delete_bug(bug_id: int = None, target: str = "both",
                      do_delete_all: bool = False) -> dict:
    """Удаляет баг(и) из БД и/или Weeek."""

    if do_delete_all:
        if target == "db_only":
            count = await delete_all_bugs()
            await log_info(f"Удалены все баги из БД ({count} шт.)")
            return {"success": True, "deleted_count": count, "target": "db_only"}
        elif target in ("weeek_only", "both"):
            bugs_data = await async_load(BUGS_FILE)
            items = bugs_data.get("items", {})
            weeek_bugs = [b for b in items.values() if b.get("weeek_task_id")]
            weeek_deleted = 0
            weeek_errors = 0
            if weeek_bugs:
                from services.weeek_service import delete_task as weeek_delete
                for b in weeek_bugs:
                    r = await weeek_delete(str(b["weeek_task_id"]))
                    if r.get("success"):
                        weeek_deleted += 1
                    else:
                        weeek_errors += 1

            result = {
                "success": True,
                "target": target,
                "weeek_deleted": weeek_deleted,
                "weeek_errors": weeek_errors,
            }

            if target == "both":
                count = await delete_all_bugs()
                result["db_deleted"] = count
                await log_info(f"Удалены все баги: БД ({count}), Weeek ({weeek_deleted})")
            else:
                def clear_weeek(data):
                    for key in data.get("items", {}):
                        data["items"][key]["weeek_task_id"] = None
                        data["items"][key]["weeek_board_name"] = None
                        data["items"][key]["weeek_column_name"] = None
                    return data
                await async_update(BUGS_FILE, clear_weeek)
                await log_info(f"Удалены все баги из Weeek ({weeek_deleted})")
            return result

    if not bug_id:
        return {"error": "Не указан bug_id"}

    bug = await get_bug(bug_id)
    if not bug:
        return {"error": f"Баг #{bug_id} не найден"}

    dn = bug.get("display_number") or bug_id
    result = {"bug_id": bug_id, "display_number": dn, "target": target}

    weeek_task_id = bug.get("weeek_task_id")

    if target in ("weeek_only", "both"):
        if not weeek_task_id:
            result["weeek"] = "не был отправлен в Weeek"
        else:
            from services.weeek_service import delete_task as weeek_delete
            weeek_result = await weeek_delete(weeek_task_id)
            if weeek_result.get("success"):
                result["weeek"] = "удалён из Weeek"
                if target == "weeek_only":
                    await clear_weeek_task_id(bug_id)
            else:
                result["weeek"] = f"ошибка Weeek: {weeek_result.get('error', '?')}"

    if target in ("db_only", "both"):
        deleted = await delete_bug(bug_id)
        result["db"] = "удалён из БД" if deleted else "не удалось удалить из БД"

    result["success"] = True
    await log_info(f"Баг #{dn} удалён ({target})")
    return result


async def _link_login(action: str, login: str, username: str = None) -> dict:
    """Привязать/отвязать/проверить игровой логин."""
    from models.login_mapping import link_login, unlink_login, get_telegram_id_by_login

    if action == "check":
        tid = await get_telegram_id_by_login(login)
        if tid:
            from models.tester import get_tester_by_id
            tester = await get_tester_by_id(tid)
            uname = _tag(tester["username"]) if tester else f"ID {tid}"
            return {"login": login, "linked_to": uname}
        return {"login": login, "linked_to": None}

    if action == "link":
        if not username:
            return {"error": "Не указан username тестера"}
        tester = await get_tester_by_username(_normalize_username(username))
        if not tester:
            return {"error": f"Тестер @{_normalize_username(username)} не найден"}
        await link_login(login, tester["telegram_id"])
        await log_admin(f"Логин «{login}» привязан к {_tag(tester['username'])}")
        return {"success": True, "login": login, "username": _tag(tester["username"])}

    if action == "unlink":
        await unlink_login(login)
        await log_admin(f"Логин «{login}» отвязан")
        return {"success": True, "login": login, "unlinked": True}

    return {"error": f"Неизвестное действие: {action}"}


async def _get_logins_list() -> dict:
    """Список привязанных логинов и тестеров без привязки."""
    from models.login_mapping import get_all_logins

    logins = await get_all_logins()
    testers = await get_all_testers(active_only=True)

    linked_tids = {entry["telegram_id"] for entry in logins}
    linked = []
    for entry in logins:
        tester = next((t for t in testers if t["telegram_id"] == entry["telegram_id"]), None)
        uname = _tag(tester["username"]) if tester and tester.get("username") else f"ID {entry['telegram_id']}"
        linked.append({"login": entry["login"], "tester": uname})

    unlinked = []
    for t in testers:
        if t["telegram_id"] not in linked_tids:
            unlinked.append(_tag(t["username"]) if t.get("username") else f"ID {t['telegram_id']}")

    return {
        "linked": linked,
        "linked_count": len(linked),
        "unlinked_testers": unlinked,
        "unlinked_count": len(unlinked),
    }


async def _switch_mode(mode: str) -> dict:
    """Переключает режим работы бота."""
    import config

    if mode not in ("active", "observe"):
        return {"error": f"Неизвестный режим: {mode}"}

    config.BOT_MODE = mode
    labels = {"active": "✅ Рабочий режим", "observe": "👁 Режим наблюдения"}
    label = labels[mode]

    await log_info(f"Режим бота переключён: {label}")
    return {"success": True, "mode": mode, "label": label}


# ╔══════════════════════════════════════════════════════════════════╗
# ║                   ГЛАВНЫЕ ФУНКЦИИ                                ║
# ╚══════════════════════════════════════════════════════════════════╝

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
        from services.rating_service import format_rating_message
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
    _trim_history(history, 10)

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
