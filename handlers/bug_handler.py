"""
Обработка багрепортов в топиках «Баги» и «Краши».
Этап 4: проверка формата → проверка дублей → Weeek → начисление баллов.
"""
import re
import html
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from models.tester import get_or_create_tester, update_tester_stats, update_tester_points
from models.bug import create_bug
from services.duplicate_checker import check_duplicate
from services.weeek_service import create_task as weeek_create_task
from config import POINTS, GROUP_ID, TOPIC_IDS
from utils.logger import log_info, log_warn, log_error

# Паттерн для проверки формата багрепорта
BUG_FORMAT_PATTERN = re.compile(
    r"(?:📝\s*)?(?:Баг|Bug|Краш|Crash)\s*[:：]\s*(.+?)(?:\n|\r)",
    re.IGNORECASE | re.DOTALL
)


def parse_bug_report(text: str) -> dict | None:
    """
    Парсит текст багрепорта. Возвращает dict с полями или None если формат неверный.
    """
    match = BUG_FORMAT_PATTERN.search(text)
    if not match:
        return None

    title = match.group(1).strip()

    # Пытаемся извлечь секции
    description = ""
    expected = ""
    actual = ""

    lines = text.split("\n")
    current_section = None
    sections = {"описание": "", "ожидаемый": "", "фактический": ""}

    for line in lines:
        lower = line.lower().strip()
        if "описание" in lower:
            current_section = "описание"
            # Берём текст после "Описание:"
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                sections["описание"] = parts[1].strip()
            continue
        elif "ожидаемый" in lower:
            current_section = "ожидаемый"
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                sections["ожидаемый"] = parts[1].strip()
            continue
        elif "фактический" in lower:
            current_section = "фактический"
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                sections["фактический"] = parts[1].strip()
            continue

        if current_section and line.strip():
            sections[current_section] += " " + line.strip()

    return {
        "title": title,
        "description": sections["описание"].strip() or text,
        "expected": sections["ожидаемый"].strip(),
        "actual": sections["фактический"].strip(),
    }


async def handle_bug_report(message: Message, topic: str, role: str):
    """Обрабатывает сообщение в топике багов/крашей."""
    if not message.text:
        return

    user = message.from_user
    text = message.text.strip()
    bug_type = "crash" if topic == "crashes" else "bug"

    # 1. Парсим формат
    parsed = parse_bug_report(text)

    if not parsed:
        await message.reply(
            "👋 Привет! Багрепорт не совсем по формату. Нужно:\n\n"
            "<b>📝 Баг: [заголовок]</b>\n\n"
            "Описание: [что произошло]\n\n"
            "Ожидаемый результат: [как должно быть]\n\n"
            "Фактический результат: [как на самом деле]\n\n"
            "Попробуй переписать 🙏",
            parse_mode="HTML"
        )
        return

    tester = await get_or_create_tester(user.id, user.username, user.full_name)
    points = POINTS["crash_accepted"] if bug_type == "crash" else POINTS["bug_accepted"]

    # 2. Проверяем дубли
    try:
        dup_check = await check_duplicate(parsed["title"], parsed["description"])
    except Exception as e:
        print(f"⚠️ Ошибка проверки дублей: {e}")
        dup_check = {"is_duplicate": False, "similar_bug_id": None, "explanation": ""}

    if dup_check.get("is_duplicate") and dup_check.get("similar_bug_id"):
        # Возможный дубль — сохраняем как pending и отправляем в Logs
        bug_id = await create_bug(
            tester_id=user.id,
            message_id=message.message_id,
            title=parsed["title"],
            description=parsed["description"],
            expected=parsed["expected"],
            actual=parsed["actual"],
            bug_type=bug_type,
            points=0,
            status="pending",
        )

        similar_id = dup_check["similar_bug_id"]
        explanation = dup_check.get("explanation", "")

        # Кнопки для админа
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Это дубль", callback_data=f"dup_yes:{bug_id}"),
                InlineKeyboardButton(text="❌ Не дубль — принять", callback_data=f"dup_no:{bug_id}:{points}"),
            ]
        ])

        # Отправляем в Logs
        log_topic = TOPIC_IDS.get("logs")
        if log_topic and GROUP_ID:
            from utils.logger import _bot
            if _bot:
                try:
                    await _bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=log_topic,
                        text=(
                            f"⚠️ <b>Возможный дубль</b>\n\n"
                            f"Новый {'краш' if bug_type == 'crash' else 'баг'}: <b>«{html.escape(parsed['title'])}»</b>\n"
                            f"От: @{html.escape(user.username or '')}\n\n"
                            f"Похож на: <b>#{similar_id}</b>\n"
                            f"Причина: {html.escape(explanation)}\n"
                        ),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception as e:
                    print(f"❌ Ошибка отправки в Logs: {e}")

        await message.reply(
            f"🔍 Баг #{bug_id} на проверке — возможно, это дубль бага #{similar_id}.\n"
            f"Админ решит принять или отклонить."
        )
        await log_warn(f"Возможный дубль: #{bug_id} ≈ #{similar_id} от @{user.username}")
        return

    # 3. Не дубль — сохраняем и начисляем баллы
    bug_id = await create_bug(
        tester_id=user.id,
        message_id=message.message_id,
        title=parsed["title"],
        description=parsed["description"],
        expected=parsed["expected"],
        actual=parsed["actual"],
        bug_type=bug_type,
        points=points,
    )

    await update_tester_points(user.id, points)
    if bug_type == "crash":
        await update_tester_stats(user.id, crashes=1)
    else:
        await update_tester_stats(user.id, bugs=1)

    # 4. Спрашиваем владельца — в какую доску отправить
    await _ask_owner_board(bug_id, parsed["title"], bug_type, user.username or "")

    # 5. Отвечаем
    emoji = "💥" if bug_type == "crash" else "✅"
    await message.reply(
        f"{emoji} {'Краш' if bug_type == 'crash' else 'Баг'} <b>#{bug_id}</b> принят! +{points} б.",
        parse_mode="HTML"
    )

    await log_info(
        f"{'Краш' if bug_type == 'crash' else 'Баг'} #{bug_id} принят от @{user.username}, +{points} б."
    )


async def _ask_owner_board(bug_id: int, title: str, bug_type: str, username: str):
    """Отправляет владельцу в ЛС кнопки выбора доски для бага."""
    from services.weeek_service import get_cached_boards
    from config import OWNER_TELEGRAM_ID
    from utils.logger import _bot

    if not _bot:
        return

    boards = get_cached_boards()
    if not boards:
        # Нет досок — создаём без доски
        from services.weeek_service import create_task as weeek_create_task
        await weeek_create_task(title=title, description="", bug_type=bug_type,
                                tester_username=username, bug_id=bug_id)
        return

    # Кнопки с досками (по 2 в ряд)
    rows = []
    row = []
    for board in boards:
        board_name = board.get("name", "?")
        board_id = board.get("id", 0)
        col_id = board.get("_first_column_id", 0)
        row.append(InlineKeyboardButton(
            text=f"📋 {board_name}",
            callback_data=f"weeek:{bug_id}:{board_id}:{col_id}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Кнопка "Не отправлять"
    rows.append([InlineKeyboardButton(text="❌ Не отправлять в Weeek", callback_data=f"weeek_skip:{bug_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    emoji = "💥" if bug_type == "crash" else "🐛"
    try:
        await _bot.send_message(
            chat_id=OWNER_TELEGRAM_ID,
            text=(
                f"{emoji} <b>Новый {'краш' if bug_type == 'crash' else 'баг'} #{bug_id}</b>\n"
                f"<b>«{html.escape(title)}»</b>\n"
                f"От: @{html.escape(username)}\n\n"
                f"📋 В какую доску Weeek отправить?"
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f"❌ Не удалось отправить выбор доски владельцу: {e}")