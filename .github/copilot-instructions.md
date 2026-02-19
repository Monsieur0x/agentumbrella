## Copilot / AI agent instructions for this repository

Purpose: concise guidance for AI coding agents to be productive quickly in this codebase.

- **Big picture**: `bot.py` is the entrypoint. Incoming Telegram messages are routed by `handlers/message_router.py` to handlers which either call business logic in `services/*`/`models/*` or forward user text to the agent brain in `agent/brain.py`.
- **Agent flow**: `agent/brain.py` composes a system prompt (`agent/system_prompt.py`) and (unless message is very simple) supplies `tools` (from `agent/tools.py`) to Groq via `AsyncGroq`. Groq may return `tool_calls`; those are handled by `agent/tool_executor.py`, which maps tool names to real code (services/models). After tool execution the brain sends results back to Groq for the final assistant message.

- **Where tools live**: tool definitions (JSON Schema-like) are in `agent/tools.py` in the `ALL_TOOLS` list. The name in `function.name` must map to a branch in `agent/tool_executor._dispatch`.
  - Example: the tool `award_points` is defined in `agent/tools.py` and implemented in `agent/tool_executor.py` by calling `services.points_service.award_points`.

- **Async-first code**: most top-level flows are async (aiogram + AsyncGroq). Use `async` I/O when adding handlers or services. Follow existing style: functions return JSON-serializable dicts and `agent/tool_executor.execute_tool` returns a JSON string.

- **Key patterns & conventions**:
  - Short/simple user text bypasses tools: see `SIMPLE_PHRASES` and `is_simple_message()` in `agent/brain.py`.
  - Tools use JSON-schema `parameters` and Groq sends arguments as a JSON string; `execute_tool` expects to json.loads the `arguments` before dispatch.
  - Role-based tool filtering: use `get_tools_for_role(role)` in `agent/tools.py` (roles: `owner`, `admin`, default/tester).
  - Admin/owner checks: tools marked "ТОЛЬКО ДЛЯ АДМИНОВ" in `agent/tools.py` rely on `caller_id` being passed into `execute_tool` and services performing authorization.
  - Database access pattern: use `database.get_db()` to open an async connection and always `close()` it in finally blocks (see `agent/tool_executor._get_inactive_testers` and others).

- **How to add a new tool (concrete steps)**:
  1. Add a new entry to `agent/tools.py` in `ALL_TOOLS` with `function.name`, `description`, and `parameters` schema.
  2. Implement handling in `agent/tool_executor._dispatch(name, args, caller_id)` — return a dict result.
  3. Prefer placing business logic in `services/` and call it from `_dispatch`.
  4. Ensure the returned value is JSON serializable; `execute_tool` will json.dumps the result.

- **Run / debug / test** (from README):
  - Install deps: `pip install -r requirements.txt`
  - Copy env: `cp .env.example .env` (Windows: `copy .env.example .env`) and fill values (`BOT_TOKEN`, `GROQ_API_KEY`, `OWNER_TELEGRAM_ID`, `GROUP_ID`, topic IDs).
  - Run: `python bot.py` — you'll see startup messages and DB initialization.
  - Tests: there is `test_groq.py` at project root; run tests with `pytest`.

- **Integrations & external dependencies**:
  - Groq: configured via `GROQ_API_KEY`, models `MODEL_AGENT` and `MODEL_CHEAP` in `config.py`; code uses `groq.AsyncGroq` in `agent/brain.py` and `agent/tool_executor.py`.
  - Telegram: `aiogram` is used; routers are defined in `handlers/` and included in `bot.py`.
  - Weeek: optional integration via `services/weeek_service.py` — `bot.py` calls `setup_weeek()` at startup.
  - SQLite DB via `database.py` and `models/*` CRUD helpers.

- **Files to inspect for examples** (start here):
  - `bot.py` — startup, dispatcher and debug topic flow
  - `agent/brain.py` — function-calling flow, `is_simple_message` logic
  - `agent/tools.py` — tool schema and `get_tools_for_role`
  - `agent/tool_executor.py` — mapping from tool names to concrete implementations
  - `handlers/message_router.py` — how messages reach the brain or services

- **Do not modify without tests**: changes to tool schemas or names are breaking changes — update both `agent/tools.py` and `_dispatch` simultaneously and add/adjust tests in `test_groq.py`.

If anything here is unclear or you'd like more detail about a particular area (specific handlers, the DB schema, or adding new integrations), tell me which part and I will expand or adjust this file.
