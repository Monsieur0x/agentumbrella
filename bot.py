"""
🤖 Umbrella Bot — бот-координатор тестирования чита для Dota 2.
Точка входа. Запуск: python bot.py
"""
import asyncio
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import BOT_TOKEN, OWNER_TELEGRAM_ID, GROUP_ID, DEBUG_TOPICS
from database import init_db
from models.admin import init_owner
from handlers.message_router import router as message_router
from handlers.callback_handler import router as callback_router
from utils.logger import set_bot


async def main():
    # === Проверка конфигурации ===
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан! Скопируйте .env.example → .env и заполните.")
        sys.exit(1)

    if not OWNER_TELEGRAM_ID:
        print("❌ OWNER_TELEGRAM_ID не задан!")
        sys.exit(1)

    # === Инициализация ===
    print("🚀 Запуск Umbrella Bot...")

    # База данных
    await init_db()

    # Бот
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Логгер
    set_bot(bot)

    # Владелец в базу
    await init_owner()

    # Weeek
    from services.weeek_service import setup_weeek
    print("🔗 Подключение к Weeek...")
    weeek_result = await setup_weeek()
    if weeek_result.get("success"):
        print("✅ Weeek подключён")
    else:
        print(f"⚠️ Weeek: {weeek_result.get('error', 'не удалось подключить')} — баги будут сохраняться без Weeek")

    # Диспетчер
    dp = Dispatcher()
    dp.include_router(message_router)
    dp.include_router(callback_router)

    # === Информация о старте ===
    bot_info = await bot.get_me()
    print(f"✅ Бот: @{bot_info.username} (ID: {bot_info.id})")
    print(f"👤 Владелец: {OWNER_TELEGRAM_ID}")
    print(f"💬 Группа: {GROUP_ID}")

    if DEBUG_TOPICS:
        print("🔍 Режим отладки топиков включён")
    else:
        from config import TOPIC_IDS
        print(f"📋 Топики: {TOPIC_IDS}")

    # === Уведомление владельца + клавиатура смены режима ===
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Рабочий режим", callback_data="mode_active"),
                InlineKeyboardButton(text="👁 Наблюдение", callback_data="mode_observe"),
                InlineKeyboardButton(text="💬 Чат", callback_data="mode_chat"),
            ],
        ])
        await bot.send_message(
            OWNER_TELEGRAM_ID,
            "🟢 <b>Umbrella Bot запущен!</b>\n\n"
            f"Бот: @{bot_info.username}\n"
            f"Режим: <b>✅ Рабочий</b>\n\n"
            "Переключить режим можно кнопками ниже или командой в чате:",
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f"⚠️ Не удалось отправить сообщение владельцу: {e}")

    print(f"\n🟢 Бот запущен! Режим: ✅ Рабочий\n")

    # Game receiver (HTTP-сервер для Lua-скрипта)
    from services.game_receiver import start_game_server, stop_game_server
    await start_game_server()

    # Запускаем polling
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await stop_game_server()
        from services.weeek_service import close_client
        from database import close_db
        await close_client()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())