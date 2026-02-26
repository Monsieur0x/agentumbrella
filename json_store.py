"""
JSON-хранилище — атомарная запись, потокобезопасный доступ через asyncio.Lock.
Заменяет SQLite (aiosqlite) для всех данных бота.
"""
import json
import os
import asyncio
import tempfile

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Один Lock на файл для конкурентного доступа
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(filename: str) -> asyncio.Lock:
    if filename not in _locks:
        _locks[filename] = asyncio.Lock()
    return _locks[filename]


def _filepath(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def load(filename: str) -> dict | list:
    """Читает JSON-файл из data/. Возвращает dict или list."""
    path = _filepath(filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(filename: str, data: dict | list):
    """Атомарная запись JSON-файла: пишем во временный файл, потом переименовываем."""
    path = _filepath(filename)
    dir_path = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # На Windows нужно удалить целевой файл перед rename
        if os.path.exists(path):
            os.replace(tmp_path, path)
        else:
            os.rename(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


async def async_load(filename: str) -> dict | list:
    """Потокобезопасное чтение."""
    async with _get_lock(filename):
        return load(filename)


async def async_save(filename: str, data: dict | list):
    """Потокобезопасная запись."""
    async with _get_lock(filename):
        save(filename, data)


async def async_update(filename: str, updater):
    """Читает файл, применяет updater(data) -> data, сохраняет. Атомарно."""
    async with _get_lock(filename):
        data = load(filename)
        data = updater(data)
        save(filename, data)
        return data


# === Файлы хранилища ===
TESTERS_FILE = "testers.json"
ADMINS_FILE = "admins.json"
BUGS_FILE = "bugs.json"
POINTS_LOG_FILE = "points_log.json"
WARNINGS_FILE = "warnings.json"
SETTINGS_FILE = "settings.json"
LOGIN_MAPPING_FILE = "login_mapping.json"
PROCESSED_MATCHES_FILE = "processed_matches.json"
TASKS_FILE = "tasks.json"
TRACKERS_FILE = "trackers.json"

# Начальные данные для каждого файла
_DEFAULTS = {
    TESTERS_FILE: {},
    ADMINS_FILE: {},
    BUGS_FILE: {"next_id": 1, "next_display_number": 1, "items": {}},
    POINTS_LOG_FILE: {"next_id": 1, "items": []},
    WARNINGS_FILE: {"next_id": 1, "items": []},
    SETTINGS_FILE: {},
    LOGIN_MAPPING_FILE: {},
    PROCESSED_MATCHES_FILE: {},
    TASKS_FILE: {"next_id": 1, "items": {}},
    TRACKERS_FILE: {},
}


def init_store():
    """Создаёт папку data/ и пустые JSON-файлы если их нет."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for filename, default in _DEFAULTS.items():
        path = _filepath(filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
    print("✅ JSON-хранилище инициализировано")
