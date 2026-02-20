"""
Описание всех функций (tools) для Anthropic Claude function calling.
ИИ видит эти описания и решает какую функцию вызвать.
"""


# === ВСЕ ИНСТРУМЕНТЫ ===

ALL_TOOLS = [
    # --- АНАЛИТИКА ---
    {
        "name": "get_tester_stats",
        "description": "Получить статистику конкретного тестера: баллы, баги, краши, игры, предупреждения. Вызывай когда спрашивают про конкретного человека.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Юзернейм тестера (@username или имя)"
                }
            },
            "required": ["username"]
        }
    },
    {
        "name": "get_team_stats",
        "description": "Общая статистика команды за период: всего багов, крашей, игр, средние показатели, лучшие/худшие тестеры.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "week", "month", "all"],
                    "description": "Период: today — сегодня, week — неделя, month — месяц, all — всё время"
                }
            },
            "required": ["period"]
        }
    },
    {
        "name": "get_inactive_testers",
        "description": "Найти тестеров без активности за указанное количество дней. Вызывай когда спрашивают кто не работал, кто неактивен.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Количество дней без активности"
                }
            },
            "required": ["days"]
        }
    },
    {
        "name": "compare_testers",
        "description": "Сравнить показатели двух тестеров.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username1": {"type": "string", "description": "Первый тестер"},
                "username2": {"type": "string", "description": "Второй тестер"}
            },
            "required": ["username1", "username2"]
        }
    },
    {
        "name": "get_bug_stats",
        "description": "Статистика по багам: всего, дубли, по типам, за период.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "week", "month", "all"],
                    "description": "Период"
                },
                "type": {
                    "type": "string",
                    "enum": ["bug", "crash", "all"],
                    "description": "Тип: bug, crash или all"
                }
            },
            "required": ["period"]
        }
    },

    {
        "name": "get_testers_list",
        "description": "Получить полный список тестеров команды: юзернеймы, имена, баллы, предупреждения, статус (активен/деактивирован). Вызывай когда спрашивают список тестеров, кто в команде, кто с варнами, сколько тестеров.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_inactive": {
                    "type": "boolean",
                    "description": "Включить деактивированных тестеров (по умолчанию false)"
                }
            },
            "required": []
        }
    },

    # --- БАЛЛЫ ---
    {
        "name": "award_points",
        "description": "Начислить или списать баллы одному тестеру. Положительное число — начисление, отрицательное — списание. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Юзернейм тестера"},
                "amount": {"type": "integer", "description": "Количество баллов (+/-)"},
                "reason": {"type": "string", "description": "Причина начисления/списания"}
            },
            "required": ["username", "amount", "reason"]
        }
    },
    {
        "name": "award_points_bulk",
        "description": "Начислить баллы нескольким тестерам сразу или всей команде. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {
                    "type": "string",
                    "description": "Юзернеймы через запятую или 'all' для всех"
                },
                "amount": {"type": "integer", "description": "Количество баллов"},
                "reason": {"type": "string", "description": "Причина"}
            },
            "required": ["usernames", "amount", "reason"]
        }
    },

    # --- ПРЕДУПРЕЖДЕНИЯ ---
    {
        "name": "issue_warning",
        "description": "Выдать предупреждение тестеру. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Юзернейм тестера"},
                "reason": {"type": "string", "description": "Причина предупреждения"}
            },
            "required": ["username", "reason"]
        }
    },

    # --- ЗАДАНИЯ ---
    {
        "name": "create_task",
        "description": "Создать задание для тестеров. Получает краткое описание, расширяет его в подробное задание и публикует в топик 'Задания'. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brief": {"type": "string", "description": "Краткое описание задания от админа"}
            },
            "required": ["brief"]
        }
    },

    # --- РЕЙТИНГ ---
    {
        "name": "get_rating",
        "description": "Получить рейтинг тестеров (баллы, баги, краши, игры). Возвращает данные БЕЗ публикации в группу. Используй когда спрашивают рейтинг, топ, список лучших.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {
                    "type": "integer",
                    "description": "Сколько показать (0 = все)"
                }
            },
            "required": []
        }
    },
    {
        "name": "publish_rating",
        "description": "Опубликовать рейтинг в топик «Топ» в группе. ТОЛЬКО когда ЯВНО просят опубликовать/обновить рейтинг в группе. НЕ вызывай просто для показа рейтинга. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_count": {
                    "type": "integer",
                    "description": "Сколько показать (0 = все)"
                },
                "comment": {
                    "type": "string",
                    "description": "Короткий комментарий к рейтингу (1-2 предложения). Напиши от себя: кто тащит, кто молчит, общий расклад. Без пафоса, как в чате."
                }
            },
            "required": []
        }
    },

    # --- УПРАВЛЕНИЕ АДМИНАМИ ---
    {
        "name": "manage_admin",
        "description": "Управление админами: добавить, удалить, показать список. ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "list"],
                    "description": "Действие"
                },
                "username": {
                    "type": "string",
                    "description": "Юзернейм (не нужен для list)"
                }
            },
            "required": ["action"]
        }
    },

    # --- БАГИ ---
    {
        "name": "mark_bug_duplicate",
        "description": "Пометить баг как дубль. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer", "description": "ID бага"}
            },
            "required": ["bug_id"]
        }
    },
    {
        "name": "search_bugs",
        "description": "Поиск среди багов по ключевым словам или тестеру.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "tester": {"type": "string", "description": "Фильтр по тестеру (опционально)"}
            },
            "required": ["query"]
        }
    },
]


def get_tools_for_role(role: str) -> list:
    """Возвращает набор инструментов в зависимости от роли."""
    if role == "owner":
        return ALL_TOOLS  # Все инструменты

    if role == "admin":
        # Всё кроме manage_admin
        return [t for t in ALL_TOOLS if t["name"] != "manage_admin"]

    # Тестер — только просмотр своей статистики и рейтинга
    tester_tools = ["get_tester_stats", "get_rating"]
    return [t for t in ALL_TOOLS if t["name"] in tester_tools]
