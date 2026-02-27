# Задача для Claude Code

Ты работаешь с Telegram-ботом Umbrella Bot — координатор тестирования чита для Dota 2. Бот использует Anthropic Claude API с function calling (tools).

## Контекст проекта

Структура:
- `agent/system_prompt.py` — системный промпт для Claude
- `agent/brain.py` — ядро: история, вызов API, цикл tool_use
- `agent/tools.py` — описания tools + regex-фильтр `match_tools`
- `agent/tool_executor.py` — выполнение tools (бизнес-логика)
- `handlers/message_router.py` — роутинг сообщений из Telegram
- `handlers/callback_handler.py` — inline-кнопки
- `handlers/bug_handler.py` — багрепорты
- `services/duplicate_checker.py` — проверка дублей багов через Claude
- `config.py` — конфиг, модели, лимиты

Роли: owner (руководитель) → admin → tracker → tester. Каждая роль видит только свои tools.

## Известные проблемы (из анализа)

### 1. Системный промпт (КРИТИЧНО)
Текущий промпт в `system_prompt.py` — плоский список правил без структуры. Claude плохо следует инструкциям, путает tools, не подставляет username из реплая.

Нужно переписать с XML-секциями: `<формат>`, `<тон>`, `<роль>`, `<выполнение_команд>`, `<составные_команды>`, `<инструменты>`. Добавить конкретные примеры маппинга "фраза пользователя → вызов tool с параметрами". Убрать противоречия (промпт говорит "без приветствий", а INSTANT_REPLIES приветствует).

### 2. match_tools режет контекст (КРИТИЧНО)
`tools.py:match_tools()` — regex-фильтр, который отправляет Claude только "угаданные" tools. При составных командах ("варн неактивным") матчит только часть нужных tools. Фоллбэк всё равно отдаёт все tools.

Нужно: убрать match_tools, всегда использовать get_tools_for_role(role). Regex-экономия не оправдана на Haiku.

### 3. MAX_HISTORY слишком мал
`config.py`: admin=2, owner=3 пары. После "покажи неактивных" → "выдай им варн" Claude уже не помнит список.

Нужно: admin=4, owner=5.

### 4. История теряет tool calls
`brain.py:327` — в историю сохраняется только финальный текст, а не факт вызова tool. Claude не помнит что делал.

Нужно: сохранять краткое summary вида "[вызвано: award_points @petrov +5] Начислил 5 баллов."

### 5. Tool descriptions неточные
- `award_points_bulk.usernames` — string "через запятую или all", но нет примера. Claude может отправить JSON array.
- `remove_warning.amount` — "0=сбросить все" неочевидно, лучше -1.
- `get_inactive_testers.days` — стоит required, но в executor есть default=7. Убрать из required.
- `search_bugs` — все параметры optional, required пустой. Claude может вызвать без параметров.

### 6. Мелкие проблемы
- `MAX_TOKENS=1024` — может не хватить для рейтинга/bulk операций. Поднять до 2048.
- `_safe_reply` — нет fallback при невалидном HTML от Claude. Добавить try/except с parse_mode=None.
- `_tag()` — не делает HTML escape для username. Добавить html.escape().
- `_SILENT_TOOLS` для create_task — если tool вернул ошибку, пользователь ничего не увидит (return None). Проверять результат.
- `_call_claude` retry только для 529, добавить 500 и ConnectionError.
- duplicate_checker промпт слишком агрессивен — два бага на одном скрипте с разными проблемами ложно помечаются дублями.

## Что делать

Прочитай каждый файл проекта целиком, чтобы понять связи. Затем:

1. Перепиши `agent/system_prompt.py` — структурированный промпт с XML-секциями, примерами, без противоречий
2. Упрости `agent/tools.py` — убери match_tools и TOOL_KEYWORDS, оставь только get_tools_for_role
3. Исправь `agent/brain.py` — история с tool summary, проверка _SILENT_TOOLS на ошибки, увеличенные лимиты
4. Поправь tool descriptions в `agent/tools.py` — примеры, defaults, убрать required где не нужно
5. Исправь `config.py` — MAX_HISTORY, MAX_TOKENS
6. Добавь fallback в `handlers/message_router.py:_safe_reply` и html.escape в `tool_executor.py:_tag`
7. Улучши промпт в `services/duplicate_checker.py`

Применяй изменения файл за файлом. После каждого файла объясни что изменил и почему.
