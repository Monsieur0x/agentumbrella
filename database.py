"""
База данных SQLite — инициализация таблиц и вспомогательные функции.
Использует единое разделяемое соединение с WAL-mode для конкурентного доступа.
"""
import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "qa_agent.db")

# Единое разделяемое соединение
_shared_db: aiosqlite.Connection | None = None


async def init_db():
    """Создаёт все таблицы если их нет и открывает shared-соединение."""
    global _shared_db

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
                type TEXT DEFAULT 'bug',
                status TEXT DEFAULT 'pending',
                weeek_task_id TEXT,
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

            -- Настройки (награды и т.д.)
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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

        # Миграция: добавляем status в tasks если его нет
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'published'")
            await db.commit()
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                print(f"⚠️ Миграция tasks.status: {e}")

        # Миграция: добавляем новые поля для багов если их нет
        # SAFETY: col_name/col_type hardcoded below, never from user input
        new_bug_columns = [
            ("script_name", "TEXT"),
            ("steps", "TEXT"),
            ("youtube_link", "TEXT"),
            ("file_id", "TEXT"),
            ("file_type", "TEXT"),
            ("weeek_board_name", "TEXT"),
            ("weeek_column_name", "TEXT"),
            ("display_number", "INTEGER"),
        ]
        for col_name, col_type in new_bug_columns:
            try:
                await db.execute(f"ALTER TABLE bugs ADD COLUMN {col_name} {col_type}")
                await db.commit()
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    print(f"⚠️ Миграция bugs.{col_name}: {e}")

        # Индексы для ускорения запросов
        await db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_bugs_tester_id ON bugs(tester_id);
            CREATE INDEX IF NOT EXISTS idx_bugs_status ON bugs(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bugs_display_number ON bugs(display_number);
            CREATE INDEX IF NOT EXISTS idx_points_log_tester_id ON points_log(tester_id);
            CREATE INDEX IF NOT EXISTS idx_points_log_created_at ON points_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_warnings_tester_id ON warnings(tester_id);
        """)
        await db.commit()

        # Миграция: заполняем display_number для существующих багов
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bugs WHERE display_number IS NULL"
        )
        row = await cursor.fetchone()
        if row[0] > 0:
            await db.execute("""
                UPDATE bugs SET display_number = (
                    SELECT COUNT(*) FROM bugs b2 WHERE b2.id <= bugs.id
                ) WHERE display_number IS NULL
            """)
            await db.commit()

    # Открываем shared-соединение с WAL-mode
    _shared_db = await aiosqlite.connect(DB_PATH)
    _shared_db.row_factory = aiosqlite.Row
    await _shared_db.execute("PRAGMA journal_mode=WAL")

    print("✅ База данных инициализирована")


async def get_db() -> aiosqlite.Connection:
    """Возвращает shared-соединение с БД. НЕ закрывайте его!"""
    global _shared_db
    if _shared_db is None:
        _shared_db = await aiosqlite.connect(DB_PATH)
        _shared_db.row_factory = aiosqlite.Row
        await _shared_db.execute("PRAGMA journal_mode=WAL")
    return _shared_db


async def close_db():
    """Закрывает shared-соединение. Вызывать при остановке бота."""
    global _shared_db
    if _shared_db:
        await _shared_db.close()
        _shared_db = None
