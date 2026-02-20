"""
Сервис формирования рейтинга + публикация в топик «Топ».
"""
from aiogram import Bot
from models.tester import get_all_testers
from models.admin import get_all_admins
from config import GROUP_ID, TOPIC_IDS


async def _get_admin_ids() -> set[int]:
    """Возвращает set telegram_id всех админов и владельца."""
    admins = await get_all_admins()
    return {a["telegram_id"] for a in admins}


async def get_rating(top_count: int = 0) -> dict:
    """
    Формирует рейтинг тестеров (без админов и владельца).
    top_count=0 — все тестеры.
    """
    all_testers = await get_all_testers()
    admin_ids = await _get_admin_ids()

    # Исключаем админов и владельца из рейтинга
    testers = [t for t in all_testers if t["telegram_id"] not in admin_ids]

    if top_count > 0:
        testers = testers[:top_count]

    rating_list = []
    for i, t in enumerate(testers, 1):
        raw = t["username"] or t["full_name"] or f"id:{t['telegram_id']}"
        tag = f"@{raw}" if t["username"] and not raw.startswith("id:") else raw
        rating_list.append({
            "position": i,
            "username": tag,
            "total_points": t["total_points"],
            "total_bugs": t["total_bugs"],
            "total_crashes": t["total_crashes"],
            "total_games": t["total_games"],
        })

    # Общая статистика тоже только по тестерам
    total_all = await get_all_testers(active_only=False)
    total_testers_only = [t for t in total_all if t["telegram_id"] not in admin_ids]
    return {
        "rating": rating_list,
        "total_testers": len(total_testers_only),
        "total_bugs": sum(t["total_bugs"] for t in total_testers_only),
        "total_games": sum(t["total_games"] for t in total_testers_only),
    }


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 балл, 2 балла, 5 баллов."""
    n_abs = abs(n)
    if 11 <= n_abs % 100 <= 19:
        return many
    mod10 = n_abs % 10
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def format_rating_message(data: dict) -> str:
    """Красиво форматирует рейтинг."""
    lines = ["🏆 <b>Топ тестеров Umbrella</b>\n"]

    for item in data["rating"]:
        pos = item["position"]
        uname = item["username"] or "?"
        pts = item["total_points"]
        bugs = item["total_bugs"]
        crashes = item["total_crashes"]
        games = item["total_games"]
        lines.append(
            f"{pos}. {uname} — {pts} {_plural(pts, 'балл', 'балла', 'баллов')} | "
            f"{bugs} {_plural(bugs, 'баг', 'бага', 'багов')}, "
            f"{crashes} {_plural(crashes, 'краш', 'краша', 'крашей')}, "
            f"{games} {_plural(games, 'игра', 'игры', 'игр')}"
        )

    lines.append(f"\nВсего: {data['total_testers']} {_plural(data['total_testers'], 'тестер', 'тестера', 'тестеров')}, "
                 f"{data['total_bugs']} {_plural(data['total_bugs'], 'баг', 'бага', 'багов')}")
    return "\n".join(lines)


async def publish_rating_to_topic(bot: Bot, data: dict, comment: str = "") -> int | None:
    """Публикует рейтинг в топик «Топ». Возвращает message_id."""
    topic_id = TOPIC_IDS.get("top")
    if not topic_id or not GROUP_ID:
        return None

    text = format_rating_message(data)
    if comment:
        text += f"\n\n{comment}"
    try:
        msg = await bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=topic_id,
            text=text,
            parse_mode="HTML",
        )
        return msg.message_id
    except Exception as e:
        print(f"❌ Ошибка публикации рейтинга: {e}")
        return None