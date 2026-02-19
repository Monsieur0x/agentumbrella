"""
Сервис формирования рейтинга + публикация в топик «Топ».
"""
from aiogram import Bot
from models.tester import get_all_testers
from config import GROUP_ID, TOPIC_IDS


async def get_rating(top_count: int = 0) -> dict:
    """
    Формирует рейтинг тестеров.
    top_count=0 — все тестеры.
    """
    testers = await get_all_testers()

    if top_count > 0:
        testers = testers[:top_count]

    rating_list = []
    for i, t in enumerate(testers, 1):
        rating_list.append({
            "position": i,
            "username": t["username"] or t["full_name"] or f"id:{t['telegram_id']}",
            "total_points": t["total_points"],
            "total_bugs": t["total_bugs"],
            "total_crashes": t["total_crashes"],
            "total_games": t["total_games"],
        })

    total_all = await get_all_testers(active_only=False)
    return {
        "rating": rating_list,
        "total_testers": len(total_all),
        "total_bugs": sum(t["total_bugs"] for t in total_all),
        "total_games": sum(t["total_games"] for t in total_all),
    }


def format_rating_message(data: dict) -> str:
    """Красиво форматирует рейтинг для публикации."""
    from datetime import datetime, timezone, timedelta
    msk = timezone(timedelta(hours=3))
    now = datetime.now(msk).strftime("%d.%m.%Y %H:%M")

    lines = [f"🏆 <b>Рейтинг тестеров</b>\n📅 Обновлено: {now}\n"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for item in data["rating"]:
        pos = item["position"]
        medal = medals.get(pos, f"<b>{pos}.</b>")
        uname = f"@{item['username']}" if item["username"] else item["username"]
        lines.append(
            f"{medal} {uname} — <b>{item['total_points']} б.</b>"
            f"\n   📝 {item['total_bugs']} | 💥 {item['total_crashes']} | 🎮 {item['total_games']}"
        )

    lines.append(f"\n📊 Тестеров: {data['total_testers']} | "
                 f"Багов: {data['total_bugs']} | Игр: {data['total_games']}")
    return "\n".join(lines)


async def publish_rating_to_topic(bot: Bot, data: dict) -> int | None:
    """Публикует рейтинг в топик «Топ». Возвращает message_id."""
    topic_id = TOPIC_IDS.get("top")
    if not topic_id or not GROUP_ID:
        return None

    text = format_rating_message(data)
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