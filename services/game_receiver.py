"""
HTTP-сервер для приёма данных об играх от Lua-скрипта gamecounter.lua.
Слушает POST на порту 8080, начисляет баллы тестерам за сыгранные игры.
"""
import json
from aiohttp import web

from models.login_mapping import get_telegram_id_by_login, is_match_processed, mark_match_processed
from models.tester import get_tester_by_id, update_tester_stats, update_tester_points
from models.settings import get_points_config
from database import get_db
from utils.logger import log_info


_runner: web.AppRunner | None = None


async def _handle_game(request: web.Request) -> web.Response:
    """Обработчик POST / — принимает JSON от Lua-скрипта."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"status": "bad_json"}, status=400)

    login = data.get("login")
    match_id = data.get("matchid")

    if not login or not match_id:
        return web.json_response({"status": "missing_fields"}, status=400)

    # Дедупликация: один матч = одно начисление
    if await is_match_processed(match_id):
        return web.json_response({"status": "already_processed"})

    # Найти тестера по логину
    telegram_id = await get_telegram_id_by_login(login)
    if not telegram_id:
        return web.json_response({"status": "unknown_login"})

    tester = await get_tester_by_id(telegram_id)
    if not tester:
        return web.json_response({"status": "tester_not_found"})

    # Начислить баллы
    points_config = await get_points_config()
    points = points_config.get("game_played", 1)

    await update_tester_points(telegram_id, points)
    await update_tester_stats(telegram_id, games=1)

    # Лог в points_log
    db = await get_db()
    await db.execute(
        "INSERT INTO points_log (tester_id, amount, reason, source) VALUES (?, ?, ?, ?)",
        (telegram_id, points, f"Игра #{match_id}", "game")
    )
    await db.commit()

    # Пометить матч обработанным
    await mark_match_processed(match_id)

    # Лог в Telegram
    username_display = f"@{tester['username']}" if tester.get("username") else tester.get("full_name", "?")
    gamemode = data.get("gamemode_string", "?")
    await log_info(f"Игра #{match_id} ({gamemode}): {username_display} +{points} б.")

    return web.json_response({"status": "ok", "points": points})


async def start_game_server(host: str = "0.0.0.0", port: int = 8080):
    """Запускает HTTP-сервер для приёма данных об играх."""
    global _runner

    app = web.Application()
    app.router.add_post("/", _handle_game)

    _runner = web.AppRunner(app)
    await _runner.setup()

    site = web.TCPSite(_runner, host, port)
    try:
        await site.start()
        print(f"🎮 Game receiver запущен на {host}:{port}")
    except OSError as e:
        print(f"⚠️ Game receiver: не удалось запустить на {host}:{port} — {e}")
        _runner = None


async def stop_game_server():
    """Останавливает HTTP-сервер."""
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None
        print("🎮 Game receiver остановлен")
