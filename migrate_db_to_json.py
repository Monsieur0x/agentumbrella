"""
Одноразовый скрипт миграции данных из SQLite (qa_agent.db) в JSON-файлы (data/).
Запуск: python migrate_db_to_json.py
"""
import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "qa_agent.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Файл {DB_PATH} не найден. Нечего мигрировать.")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # === testers.json ===
    testers = {}
    for row in conn.execute("SELECT * FROM testers"):
        r = dict(row)
        key = str(r["telegram_id"])
        testers[key] = {
            "telegram_id": r["telegram_id"],
            "username": r["username"],
            "full_name": r["full_name"],
            "total_points": r.get("total_points", 0),
            "total_bugs": r.get("total_bugs", 0),
            "total_games": r.get("total_games", 0),
            "warnings_count": r.get("warnings_count", 0),
            "is_active": bool(r.get("is_active", 1)),
            "created_at": r.get("created_at", ""),
        }
    _save("testers.json", testers)
    print(f"✅ testers.json: {len(testers)} записей")

    # === admins.json ===
    admins = {}
    for row in conn.execute("SELECT * FROM admins"):
        r = dict(row)
        key = str(r["telegram_id"])
        admins[key] = {
            "telegram_id": r["telegram_id"],
            "username": r["username"],
            "full_name": r["full_name"],
            "is_owner": bool(r.get("is_owner", 0)),
            "added_at": r.get("added_at", ""),
        }
    _save("admins.json", admins)
    print(f"✅ admins.json: {len(admins)} записей")

    # === bugs.json ===
    bugs_items = {}
    max_id = 0
    max_dn = 0
    for row in conn.execute("SELECT * FROM bugs"):
        r = dict(row)
        bug_id = r["id"]
        dn = r.get("display_number") or bug_id
        max_id = max(max_id, bug_id)
        max_dn = max(max_dn, dn)
        bugs_items[str(bug_id)] = {
            "id": bug_id,
            "tester_id": r["tester_id"],
            "message_id": r.get("message_id"),
            "title": r.get("title"),
            "description": r.get("description"),
            "type": r.get("type", "bug"),
            "status": r.get("status", "pending"),
            "weeek_task_id": r.get("weeek_task_id"),
            "points_awarded": r.get("points_awarded", 0),
            "created_at": r.get("created_at", ""),
            "script_name": r.get("script_name"),
            "steps": r.get("steps"),
            "youtube_link": r.get("youtube_link"),
            "file_id": r.get("file_id"),
            "file_type": r.get("file_type"),
            "weeek_board_name": r.get("weeek_board_name"),
            "weeek_column_name": r.get("weeek_column_name"),
            "display_number": dn,
        }
    bugs_data = {
        "next_id": max_id + 1,
        "next_display_number": max_dn + 1,
        "items": bugs_items,
    }
    _save("bugs.json", bugs_data)
    print(f"✅ bugs.json: {len(bugs_items)} записей")

    # === points_log.json ===
    points_items = []
    max_pl_id = 0
    for row in conn.execute("SELECT * FROM points_log ORDER BY id"):
        r = dict(row)
        max_pl_id = max(max_pl_id, r["id"])
        points_items.append({
            "id": r["id"],
            "tester_id": r["tester_id"],
            "amount": r["amount"],
            "reason": r.get("reason"),
            "source": r.get("source", "manual"),
            "admin_id": r.get("admin_id"),
            "created_at": r.get("created_at", ""),
        })
    _save("points_log.json", {"next_id": max_pl_id + 1, "items": points_items})
    print(f"✅ points_log.json: {len(points_items)} записей")

    # === warnings.json ===
    warn_items = []
    max_w_id = 0
    for row in conn.execute("SELECT * FROM warnings ORDER BY id"):
        r = dict(row)
        max_w_id = max(max_w_id, r["id"])
        warn_items.append({
            "id": r["id"],
            "tester_id": r["tester_id"],
            "reason": r.get("reason"),
            "admin_id": r.get("admin_id"),
            "created_at": r.get("created_at", ""),
        })
    _save("warnings.json", {"next_id": max_w_id + 1, "items": warn_items})
    print(f"✅ warnings.json: {len(warn_items)} записей")

    # === settings.json ===
    settings = {}
    for row in conn.execute("SELECT * FROM settings"):
        r = dict(row)
        settings[r["key"]] = r["value"]
    _save("settings.json", settings)
    print(f"✅ settings.json: {len(settings)} записей")

    # === login_mapping.json ===
    logins = {}
    for row in conn.execute("SELECT * FROM login_mapping"):
        r = dict(row)
        logins[r["login"]] = r["telegram_id"]
    _save("login_mapping.json", logins)
    print(f"✅ login_mapping.json: {len(logins)} записей")

    # === processed_matches.json ===
    matches = {}
    for row in conn.execute("SELECT * FROM processed_matches"):
        r = dict(row)
        matches[str(r["match_id"])] = r.get("processed_at", "")
    _save("processed_matches.json", matches)
    print(f"✅ processed_matches.json: {len(matches)} записей")

    # === tasks.json ===
    tasks_items = {}
    max_t_id = 0
    for row in conn.execute("SELECT * FROM tasks"):
        r = dict(row)
        tid = r["id"]
        max_t_id = max(max_t_id, tid)
        tasks_items[str(tid)] = {
            "id": tid,
            "admin_id": r.get("admin_id"),
            "brief": r.get("brief"),
            "full_text": r.get("full_text"),
            "message_id": r.get("message_id"),
            "status": r.get("status", "published"),
            "created_at": r.get("created_at", ""),
        }
    _save("tasks.json", {"next_id": max_t_id + 1, "items": tasks_items})
    print(f"✅ tasks.json: {len(tasks_items)} записей")

    conn.close()
    print(f"\n🎉 Миграция завершена! Данные в папке {DATA_DIR}")


def _save(filename: str, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    migrate()
