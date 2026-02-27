"""
Описание всех функций (tools) для Anthropic Claude function calling.
ИИ видит эти описания и решает какую функцию вызвать.
"""


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

    # --- УПРАВЛЕНИЕ ТРЕКЕРАМИ ---
    {
        "name": "manage_tracker",
        "description": "Управление трекерами (тестер с правом выдавать баллы). Только руководитель.",
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
        return [t for t in ALL_TOOLS if t["name"] not in ("manage_admin", "manage_tracker")]
    if role == "tracker":
        tracker_tools = {"get_tester_stats", "get_rating", "award_points", "award_points_bulk",
                         "get_team_stats", "get_inactive_testers", "compare_testers",
                         "get_bug_stats", "get_testers_list"}
        return [t for t in ALL_TOOLS if t["name"] in tracker_tools]
    tester_tools = ["get_tester_stats", "get_rating"]
    return [t for t in ALL_TOOLS if t["name"] in tester_tools]
