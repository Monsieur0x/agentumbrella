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
        "description": "Выдать предупреждение тестеру. ТОЛЬКО ДЛЯ АДМИНОВ. Если админ не указал причину — подставь 'Без причины', НЕ спрашивай.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Юзернейм тестера"},
                "reason": {"type": "string", "description": "Причина предупреждения (необязательно, по умолчанию 'Без причины')"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "issue_warning_bulk",
        "description": "Выдать предупреждение нескольким тестерам или всем сразу. ТОЛЬКО ДЛЯ АДМИНОВ. Если админ не указал причину — подставь 'Без причины', НЕ спрашивай.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {
                    "type": "string",
                    "description": "Юзернеймы через запятую или 'all' для всех тестеров"
                },
                "reason": {"type": "string", "description": "Причина предупреждения (необязательно, по умолчанию 'Без причины')"}
            },
            "required": ["usernames"]
        }
    },
    {
        "name": "remove_warning",
        "description": "Снять предупреждение (варн) у тестера, нескольких тестеров или у всех сразу. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {
                    "type": "string",
                    "description": "Юзернеймы через запятую или 'all' для всех тестеров"
                },
                "amount": {
                    "type": "integer",
                    "description": "Сколько варнов снять (0 = сбросить все варны у тестера). По умолчанию 1"
                }
            },
            "required": ["usernames"]
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

    # --- ОБНОВЛЕНИЕ СПИСКА ТЕСТЕРОВ ---
    {
        "name": "refresh_testers",
        "description": "Обновить список тестеров: проверяет кто ещё в группе, а кто вышел/кикнут. Деактивирует тех, кто больше не в группе. Вызывай когда просят обновить/синхронизировать список тестеров. ТОЛЬКО ДЛЯ АДМИНОВ.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
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
        "description": "Поиск багов: по ID, ключевым словам, тестеру, статусу. Возвращает детальную информацию включая доску и колонку Weeek. Вызывай когда спрашивают про конкретный баг, список багов, баги тестера, статистику по багам с деталями.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer", "description": "ID конкретного бага (если ищем по номеру)"},
                "query": {"type": "string", "description": "Поисковый запрос по тексту (опционально)"},
                "tester": {"type": "string", "description": "Фильтр по тестеру (опционально)"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "accepted", "rejected", "duplicate", "all"],
                    "description": "Фильтр по статусу (опционально, по умолчанию all)"
                }
            },
            "required": []
        }
    },
    {
        "name": "delete_bug",
        "description": "Удалить баг(и) из БД и/или из Weeek. ТОЛЬКО ДЛЯ АДМИНОВ. Можно удалить один баг по ID или все баги сразу (delete_all=true). target: db_only — только из базы, weeek_only — только из Weeek, both — отовсюду. Когда просят удалить ВСЕ баги — используй delete_all=true, target=db_only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer", "description": "ID бага для удаления (не нужен если delete_all=true)"},
                "target": {
                    "type": "string",
                    "enum": ["db_only", "weeek_only", "both"],
                    "description": "Откуда удалять: db_only — только БД, weeek_only — только Weeek, both — отовсюду"
                },
                "delete_all": {
                    "type": "boolean",
                    "description": "Удалить ВСЕ баги (true). По умолчанию false."
                }
            },
            "required": ["target"]
        }
    },
    {
        "name": "switch_mode",
        "description": "Переключить режим работы бота: active (рабочий — отвечает на все сообщения) или observe (наблюдение — отвечает только на @упоминания). ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["active", "observe"],
                    "description": "Режим: active — рабочий, observe — наблюдение"
                }
            },
            "required": ["mode"]
        }
    },
]


TOOL_KEYWORDS: dict[str, list[str]] = {
    "get_tester_stats":    [r"статист", r"стат\b", r"показат", r"@\w"],
    "get_team_stats":      [r"команд", r"общ\w+ стат", r"за\s+(сегодня|недел|месяц|всё)"],
    "get_inactive_testers":[r"неактивн", r"не работал", r"молчат", r"пропал"],
    "compare_testers":     [r"сравни"],
    "get_bug_stats":       [r"баг", r"краш", r"стат\w*\s+баг", r"стат\w*\s+краш"],
    "get_testers_list":    [r"список\s+тестер", r"кто в команд", r"тестер\w*", r"варн", r"сколько\s+тестер"],
    "award_points":        [r"начисл", r"списа", r"балл", r"@\w"],
    "award_points_bulk":   [r"начисл\w*\s+.*всем", r"всем\s+.*балл", r"массов"],
    "issue_warning":       [r"предупред", r"варн", r"@\w"],
    "issue_warning_bulk":  [r"варн\w*\s+.*всем", r"всем\s+.*варн", r"предупред\w*\s+.*всем", r"всем\s+.*предупред", r"массов\w*\s+варн"],
    "remove_warning":      [r"сн[яи]\w*\s+.*варн", r"сн[яи]\w*\s+.*предупр", r"убра\w*\s+.*варн", r"убра\w*\s+.*предупр", r"сброс\w*\s+.*(варн|предупр)", r"снять\s+.*варн", r"обнул\w*\s+.*варн"],
    "create_task":         [r"задани", r"создай\s+задан", r"дай\s+задани", r"таск"],
    "get_rating":          [r"рейтинг", r"топ\b", r"лидер", r"таблиц"],
    "publish_rating":      [r"опубликуй\s+(рейтинг|топ)", r"публик\w*\s+(рейтинг|топ)", r"обнови\s+(рейтинг|топ)", r"опубликуй\s+в\s+топик"],
    "refresh_testers":     [r"обнови\s+(список|тестер)", r"синхрон", r"актуализ", r"обновить\s+(список|тестер)"],
    "manage_admin":        [r"админ", r"добав\w+\s+админ", r"удал\w+\s+админ"],
    "mark_bug_duplicate":  [r"дубл", r"дубликат", r"помет\w+\s+дубл"],
    "search_bugs":         [r"найди\s+баг", r"поиск\s+баг", r"искать\s+баг", r"баг\w*\s+по", r"найди\s+краш", r"баг\s*#?\d+", r"покажи\s+баг", r"список\s+баг", r"баги\s+(от|тестер|по)", r"принят\w+\s+баг", r"отклон\w+\s+баг", r"все\s+баг"],
    "delete_bug":          [r"удал\w*\s+баг", r"удал\w*\s+краш", r"убери\s+баг", r"снес\w*\s+баг", r"удал\w*\s+из\s+(бд|вик|weeek|базы)"],
    "switch_mode":         [r"режим", r"наблюдени", r"переключ\w*\s+режим", r"рабоч\w+\s+режим", r"observe"],
}

_TOOL_BY_NAME = {t["name"]: t for t in ALL_TOOLS}

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
    import re
    exclude = _ROLE_EXCLUDE.get(role, set())
    matched = set()
    text_lower = text.lower()
    for tool_name, keywords in TOOL_KEYWORDS.items():
        if tool_name in exclude:
            continue
        for kw in keywords:
            if re.search(kw, text_lower):
                matched.add(tool_name)
                break
    if not matched:
        return []
    return [_TOOL_BY_NAME[name] for name in matched if name in _TOOL_BY_NAME]
