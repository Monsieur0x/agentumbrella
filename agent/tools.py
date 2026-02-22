"""
Описание всех функций (tools) для Anthropic Claude function calling.
ИИ видит эти описания и решает какую функцию вызвать.
"""
import re


# === ВСЕ ИНСТРУМЕНТЫ ===

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
                "days": {"type": "integer", "description": "Дней без активности"}
            },
            "required": ["days"]
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
        "description": "Список тестеров: имена, баллы, варны, статус.",
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
        "description": "Начислить/списать баллы тестеру (+/-). Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "amount": {"type": "integer", "description": "+/-"},
                "reason": {"type": "string"}
            },
            "required": ["username", "amount", "reason"]
        }
    },
    {
        "name": "award_points_bulk",
        "description": "Баллы нескольким тестерам или всем. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {"type": "string", "description": "Через запятую или 'all'"},
                "amount": {"type": "integer"},
                "reason": {"type": "string"}
            },
            "required": ["usernames", "amount", "reason"]
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
                "reason": {"type": "string"}
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
                "usernames": {"type": "string", "description": "Через запятую или 'all'"},
                "reason": {"type": "string"}
            },
            "required": ["usernames"]
        }
    },
    {
        "name": "remove_warning",
        "description": "Снять варн(ы). Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {"type": "string", "description": "Через запятую или 'all'"},
                "amount": {"type": "integer", "description": "0=сбросить все, по умолчанию 1"}
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
        "description": "Рейтинг тестеров (без публикации).",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {"type": "integer", "description": "0=все"}
            },
            "required": []
        }
    },
    {
        "name": "publish_rating",
        "description": "Опубликовать рейтинг в топик «Топ». Только по явной просьбе. Админ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {"type": "integer", "description": "0=все"},
                "comment": {"type": "string", "description": "Комментарий: кто тащит, кто молчит"}
            },
            "required": []
        }
    },

    # --- УПРАВЛЕНИЕ АДМИНАМИ ---
    {
        "name": "manage_admin",
        "description": "Управление админами. Владелец.",
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
        "description": "Поиск багов по ID/тексту/тестеру/статусу.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "query": {"type": "string"},
                "tester": {"type": "string"},
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
        "name": "switch_mode",
        "description": "Переключить режим бота. Владелец.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["active", "observe"]}
            },
            "required": ["mode"]
        }
    },
]


TOOL_KEYWORDS: dict[str, list[str]] = {
    "get_tester_stats": [
        r"статист", r"стат[аы]?\b",
        r"(покажи|глянь|чекни|скинь|дай)\s.*@\w",
        r"(что|как|чё|че)\s+(по|у|с)\s+@?\w",
        r"сколько\s+(у|баллов|очков|варн)",
        r"инф[аоу]\s+(по|про|о)\s+@?\w",
    ],
    "get_team_stats": [
        r"команд\w*\s+стат", r"общ\w+\s+стат", r"стат\w*\s+команд",
        r"за\s+(сегодня|недел|месяц|всё|все\s+время)",
        r"как\s+(дела\s+)?у\s+команд",
        r"обзор\s+команд",
    ],
    "get_inactive_testers": [
        r"неактивн", r"не\s+работал", r"молчат", r"молчун",
        r"пропал", r"забил", r"забив", r"слил",
        r"кто\s+(не|забил|забив|слил|пропал|молчит|afk)",
        r"afk", r"афк",
        r"давно\s+не\s+(писал|играл|тестил|работал)",
        r"без\s+активност",
    ],
    "compare_testers": [
        r"сравни", r"сравнен", r"vs\b", r"против\b",
        r"кто\s+(лучше|круче|сильнее)\s+.+\s+(или|vs)",
    ],
    "get_bug_stats": [
        r"стат\w*\s+(по\s+)?баг", r"баг\w*\s+стат",
        r"сколько\s+баг",
    ],
    "get_testers_list": [
        r"список\s+тестер", r"кто\s+в\s+команд",
        r"тестер\w*\s+(список|все|всех)",
        r"все\s+тестер", r"покажи\s+тестер",
        r"сколько\s+тестер",
        r"кто\s+с\s+варн",
        r"у\s+кого\s+варн",
    ],
    "award_points": [
        r"начисл", r"списа", r"балл",
        r"(кинь|накинь|добав|плюс[ао]ни|дай|выдай)\s.*(\d|одн|дв|тр|четыр|пят|шест|сем|восем|девят|десят)",
        r"(минус[ао]ни|штрафани|отним|сним)\s.*(\d|одн|дв|тр|четыр|пят|шест|сем|восем|девят|десят)",
        r"\d+\s*(балл|очк|поинт|pts)",
        r"(плюс|минус)\s*\d",
        r"[+-]\d+\s+@?\w",
    ],
    "award_points_bulk": [
        r"(начисл|кинь|накинь|добав|дай|выдай)\w*\s+.*всем",
        r"всем\s+.*(балл|очк|\d)",
        r"массов\w*\s+(начисл|балл)",
    ],
    "issue_warning": [
        r"предупред", r"варн",
        r"(накажи|штрафани)\s+@?\w",
        r"(дай|выдай|влепи|впаяй|кинь)\s+варн",
    ],
    "issue_warning_bulk": [
        r"варн\w*\s+.*всем", r"всем\s+.*варн",
        r"предупред\w*\s+.*всем", r"всем\s+.*предупред",
        r"массов\w*\s+варн",
    ],
    "remove_warning": [
        r"сн[яи]\w*\s+.*варн", r"сн[яи]\w*\s+.*предупр",
        r"убра\w*\s+.*варн", r"убра\w*\s+.*предупр",
        r"сброс\w*\s+.*(варн|предупр)",
        r"снять\s+.*варн", r"обнул\w*\s+.*варн",
        r"прости\w*\s+@?\w",
        r"амнист",
    ],
    "create_task": [
        r"задани", r"задач",
        r"(создай|дай|придумай|напиши|сделай)\s+(задан|задач|таск)",
        r"таск\b",
        r"(дай|придумай)\s+.*(потестить|протестить|проверить)",
    ],
    "get_rating": [
        r"рейтинг", r"топ\b", r"лидер", r"таблиц",
        r"кто\s+(лучш|топ|перв|тащит|впереди|лидир)",
        r"(покажи|дай|скинь|глянь)\s+(рейтинг|топ|таблиц)",
    ],
    "publish_rating": [
        r"(опубликуй|публик\w*|запость|отправь|скинь\s+в)\s+(рейтинг|топ|таблиц)",
        r"обнови\s+(рейтинг|топ)",
        r"(опубликуй|скинь|запость)\s+в\s+топик",
    ],
    "refresh_testers": [
        r"обнови\s+(список|тестер)", r"синхрон", r"актуализ",
        r"обновить\s+(список|тестер)",
        r"пересканируй", r"перечекай\s+тестер",
    ],
    "manage_admin": [
        r"админ",
        r"(добав|удал|убер|назнач|сним)\w*\s+админ",
    ],
    "mark_bug_duplicate": [
        r"дубл", r"дубликат",
        r"(помет|отмет|поставь)\w*\s+дубл",
        r"это\s+дубл",
    ],
    "search_bugs": [
        r"(найди|поиск|искать|ищи|покажи|глянь|дай|чекни)\s+баг",
        r"баг\w*\s+(по|от|#|\d)",
        r"баг\s*#?\d+",
        r"список\s+баг", r"все\s+баг",
        r"баги\s+(от|тестер|по|у)",
        r"(принят|отклон|пендинг|ожида)\w*\s+баг",
        r"(что|инф[аоу])\s+(с|по|про)\s+баг",
        r"где\s+баг",
    ],
    "delete_bug": [
        r"удал\w*\s+баг", r"(убери|снеси|грохни|убей|вычеркни)\s+баг",
        r"удал\w*\s+из\s+(бд|вик|weeek|базы)",
        r"удал\w*\s+все\s+баг",
    ],
    "link_login": [
        r"привяж", r"отвяж", r"логин",
        r"(привязать|отвязать|проверить)\s+логин",
        r"линк\w*\s+логин",
    ],
    "switch_mode": [
        r"режим", r"наблюдени",
        r"переключ\w*\s+(режим|на|в)",
        r"рабоч\w+\s+режим", r"observe",
        r"(включи|выключи|активируй)\s+(бот|режим)",
        r"(уйди|иди)\s+в\s+(наблюд|тень|спячк)",
    ],
}

_TOOL_BY_NAME = {t["name"]: t for t in ALL_TOOLS}

# Предкомпилированные regex для match_tools
_TOOL_PATTERNS: dict[str, list[re.Pattern]] = {
    name: [re.compile(kw) for kw in keywords]
    for name, keywords in TOOL_KEYWORDS.items()
}

# Роли → запрещённые tools
_ROLE_EXCLUDE: dict[str, set[str]] = {
    "owner":  set(),
    "admin":  {"manage_admin"},
    "tester": set(TOOL_KEYWORDS.keys()) - {"get_tester_stats", "get_rating"},
}


def get_tools_for_role(role: str) -> list:
    """Возвращает набор инструментов в зависимости от роли."""
    if role == "owner":
        return ALL_TOOLS
    if role == "admin":
        return [t for t in ALL_TOOLS if t["name"] != "manage_admin"]
    tester_tools = ["get_tester_stats", "get_rating"]
    return [t for t in ALL_TOOLS if t["name"] in tester_tools]


def match_tools(text: str, role: str) -> list:
    """Возвращает только tools, чьи ключевые слова совпали с текстом."""
    exclude = _ROLE_EXCLUDE.get(role, set())
    matched = set()
    text_lower = text.lower()
    for tool_name, patterns in _TOOL_PATTERNS.items():
        if tool_name in exclude:
            continue
        for pat in patterns:
            if pat.search(text_lower):
                matched.add(tool_name)
                break
    if not matched:
        return []
    return [_TOOL_BY_NAME[name] for name in matched if name in _TOOL_BY_NAME]
