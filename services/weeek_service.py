"""
Сервис интеграции с Weeek — создание задач из багрепортов.

API docs: https://developers.weeek.net/
Base URL: https://api.weeek.net/public/v1
"""
import httpx
from config import WEEEK_API_KEY

WEEEK_PROJECT_ID = None
WEEEK_BOARDS = []  # Кэш досок: [{"id": 1, "name": "ПАТЧ"}, ...]

BASE_URL = "https://api.weeek.net/public/v1"

# Shared HTTP-клиент — переиспользует TCP-соединения
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Возвращает shared httpx-клиент."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "Authorization": f"Bearer {WEEEK_API_KEY}",
                "Content-Type": "application/json",
            },
        )
    return _http_client


async def _request(method: str, endpoint: str, data: dict = None) -> dict:
    """Выполняет асинхронный запрос к Weeek API."""
    if not WEEEK_API_KEY:
        return {"error": "WEEEK_API_KEY не задан"}

    url = f"{BASE_URL}/{endpoint}"
    client = _get_client()

    try:
        response = await client.request(
            method=method,
            url=url,
            json=data,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        try:
            return e.response.json()
        except Exception:
            print(f"❌ Weeek API {e.response.status_code}: {e.response.text[:200]}")
            return {"error": f"HTTP {e.response.status_code}"}
    except httpx.TimeoutException:
        print("❌ Weeek API: timeout")
        return {"error": "Timeout"}
    except Exception as e:
        print(f"❌ Weeek API ошибка: {e}")
        return {"error": str(e)}


async def get_projects() -> list:
    """GET /tm/projects"""
    result = await _request("GET", "tm/projects")
    return result.get("projects", [])


async def get_boards(project_id: int = None) -> list:
    """GET /tm/boards"""
    if project_id:
        result = await _request("GET", f"tm/boards?projectId={project_id}")
        boards = result.get("boards", [])
        if boards:
            return boards
    result = await _request("GET", "tm/boards")
    return result.get("boards", [])


async def find_columns_from_tasks(project_id: int) -> dict:
    """
    Находит boardColumnId для каждой доски из существующих задач.
    Возвращает {board_id: first_column_id, ...}
    """
    result = await _request("GET", f"tm/tasks?projectId={project_id}&perPage=50")
    tasks = result.get("tasks", [])
    board_columns = {}
    for task in tasks:
        bid = task.get("boardId")
        bcid = task.get("boardColumnId")
        if bid and bcid and bid not in board_columns:
            board_columns[bid] = bcid
    return board_columns


async def get_board_columns(board_id: int) -> list:
    """Возвращает список колонок доски. Пробует несколько вариантов эндпоинта."""
    # Вариант 1: tm/board-columns?boardId=
    result = await _request("GET", f"tm/board-columns?boardId={board_id}")
    cols = result.get("boardColumns") or result.get("columns") or []
    if cols:
        return cols
    # Вариант 2: tm/boards/{board_id} — поле columns внутри объекта доски
    result = await _request("GET", f"tm/boards/{board_id}")
    board = result.get("board") or result.get("data") or {}
    return board.get("columns") or board.get("boardColumns") or []


def get_cached_boards() -> list:
    """Возвращает кэшированный список досок."""
    return WEEEK_BOARDS


async def upload_attachment(task_id: str, file_bytes: bytes, filename: str) -> dict:
    """POST /tm/tasks/{task_id}/attachments — загружает файл-вложение к задаче."""
    if not WEEEK_API_KEY:
        return {"error": "WEEEK_API_KEY не задан", "success": False}

    url = f"{BASE_URL}/tm/tasks/{task_id}/attachments"
    client = _get_client()

    try:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {WEEEK_API_KEY}"},
            files={"files[]": (filename, file_bytes)},
        )
        response.raise_for_status()
        return {"success": True}
    except httpx.HTTPStatusError as e:
        print(f"❌ Weeek upload {e.response.status_code}: {e.response.text[:200]}")
        return {"success": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        print(f"❌ Weeek upload ошибка: {e}")
        return {"success": False, "error": str(e)}


async def create_task(title: str, description: str, bug_type: str = "bug",
                      tester_username: str = "", bug_id: int = 0,
                      board_column_id: int = None) -> dict:
    """
    POST /tm/tasks — создаёт задачу.
    board_column_id — конкретная колонка (выбрана владельцем).
    """
    if not WEEEK_API_KEY:
        return {"error": "WEEEK_API_KEY не задан", "success": False}

    full_desc = (
        f"{description}\n\n"
        f"---\n"
        f"Тип: {'Краш' if bug_type == 'crash' else 'Баг'}\n"
        f"Автор: @{tester_username}\n"
        f"ID в боте: #{bug_id}"
    )

    task_data = {
        "title": f"[{'CRASH' if bug_type == 'crash' else 'BUG'}] {title}",
        "description": full_desc,
        "type": "action",
        "priority": 2 if bug_type == "crash" else 1,
    }

    if WEEEK_PROJECT_ID:
        location = {"projectId": WEEEK_PROJECT_ID}
        if board_column_id:
            location["boardColumnId"] = board_column_id
        task_data["locations"] = [location]

    result = await _request("POST", "tm/tasks", task_data)

    if "error" in result and "success" not in result:
        return {"success": False, "error": result.get("error", "Неизвестная ошибка")}

    task = result.get("task", {})
    return {
        "success": True,
        "task_id": task.get("id", "unknown"),
        "title": title,
    }


async def delete_task(task_id: str) -> dict:
    """DELETE /tm/tasks/{task_id} — удаляет задачу из Weeek."""
    if not WEEEK_API_KEY:
        return {"error": "WEEEK_API_KEY не задан", "success": False}
    if not task_id:
        return {"error": "task_id пустой", "success": False}

    url = f"{BASE_URL}/tm/tasks/{task_id}"
    client = _get_client()

    try:
        response = await client.delete(url)
        response.raise_for_status()
        # Weeek может вернуть 200 с JSON или 204 без тела
        if response.status_code == 204 or not response.content:
            return {"success": True, "task_id": task_id}
        result = response.json()
        return {"success": result.get("success", True), "task_id": task_id}
    except httpx.HTTPStatusError as e:
        error_text = e.response.text[:200] if e.response.text else str(e.response.status_code)
        print(f"❌ Weeek DELETE task {task_id}: {e.response.status_code} — {error_text}")
        return {"success": False, "error": f"HTTP {e.response.status_code}: {error_text}"}
    except Exception as e:
        print(f"❌ Weeek DELETE task ошибка: {e}")
        return {"success": False, "error": str(e)}


async def setup_weeek() -> dict:
    """Инициализация: находит проект и кэширует доски."""
    global WEEEK_PROJECT_ID, WEEEK_BOARDS

    if not WEEEK_API_KEY:
        return {"error": "WEEEK_API_KEY не задан"}

    # 1. Проекты
    projects = await get_projects()
    if not projects:
        return {"error": "Нет проектов в Weeek"}

    WEEEK_PROJECT_ID = projects[0].get("id")
    proj_name = projects[0].get("name", projects[0].get("title", "?"))
    print(f"  📋 Weeek проект: {proj_name} (ID: {WEEEK_PROJECT_ID})")

    # 2. Доски
    boards = await get_boards(project_id=WEEEK_PROJECT_ID)
    if boards:
        WEEEK_BOARDS = boards
        names = ", ".join(b.get("name", "?") for b in boards)
        print(f"  📊 Weeek досок: {len(boards)} ({names})")
    else:
        print("  ⚠️ Досок нет")

    # 3. Ищем колонки из задач (для кнопок)
    col_map = await find_columns_from_tasks(WEEEK_PROJECT_ID)
    if col_map:
        for board in WEEEK_BOARDS:
            bid = board.get("id")
            if bid in col_map:
                board["_first_column_id"] = col_map[bid]
        print(f"  📌 Колонки найдены для досок: {col_map}")
    else:
        print("  ⚠️ Колонки не найдены (создайте по 1 задаче в каждой доске вручную)")

    return {
        "success": True,
        "project_id": WEEEK_PROJECT_ID,
        "boards_count": len(WEEEK_BOARDS),
    }
