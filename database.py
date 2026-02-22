"""
Инициализация хранилища данных (JSON-файлы).
Обратная совместимость: init_db() и close_db() сохранены для bot.py.
"""
from json_store import init_store


async def init_db():
    """Инициализирует JSON-хранилище (создаёт папку data/ и файлы)."""
    init_store()


async def close_db():
    """Ничего не делает — JSON-файлы не требуют закрытия соединения."""
    pass
