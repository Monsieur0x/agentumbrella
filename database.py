"""
База данных SQLite — инициализация таблиц и вспомогательные функции.
"""
import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "qa_agent.db")


async def init_db():
    """Создаёт все таблицы если их нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            -- Тестеры
            CREATE TABLE IF NOT EXISTS testers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                total_points INTEGER DEFAULT 0,
                total_bugs INTEGER DEFAULT 0,
                total_crashes INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                warnings_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Администраторы
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                is_owner BOOLEAN DEFAULT 0,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Баги
            CREATE TABLE IF NOT EXISTS bugs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tester_id BIGINT NOT NULL,
                message_id BIGINT,
                title TEXT,
                description TEXT,
                expected TEXT,
                actual TEXT,
                type TEXT DEFAULT 'bug',
                status TEXT DEFAULT 'pending',
                weeek_task_id TEXT,
                points_awarded INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Отчёты (скриншоты)
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tester_id BIGINT NOT NULL,
                message_id BIGINT,
                games_count INTEGER DEFAULT 0,
                claimed_count INTEGER DEFAULT 0,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'accepted',
                points_awarded INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Лог баллов
            CREATE TABLE IF NOT EXISTS points_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tester_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                source TEXT DEFAULT 'manual',
                admin_id BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Предупреждения
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tester_id BIGINT NOT NULL,
                reason TEXT,
                admin_id BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Задания
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id BIGINT,
                brief TEXT,
                full_text TEXT,
                message_id BIGINT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    print("✅ База данных инициализирована")


async def get_db() -> aiosqlite.Connection:
    """Возвращает соединение с БД. Не забудьте закрыть!"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db
