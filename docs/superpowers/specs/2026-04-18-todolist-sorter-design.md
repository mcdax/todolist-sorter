# Todolist-Sorter — Design

**Date:** 2026-04-18
**Status:** Draft

## Purpose

A FastAPI service that processes incoming Todoist webhooks and reorders the items of a configured Todoist project according to a user-defined, structured category list. Categorization decisions are cached, so recurring items are placed directly without an LLM call and without debouncing.

Example use case: ordering shopping lists to match the route through a supermarket (Lidl: Vegetables → Fruit → Mushrooms → Bread → … → Drugstore → Misc).

## Scope

**In scope (MVP):**
- Todoist as the sole task provider
- Webhook-based trigger (push)
- REST API for managing sorting projects and their category lists
- LLM-based categorization via pydantic-ai
- SQLite persistence (projects + category cache)
- Single-instance deployment

**Explicitly out of scope:**
- Additional providers (TickTick, Google Tasks, Microsoft To Do) — architecture is prepared so a new backend module can be added without refactoring core components. No stub files or dead code.
- Polling trigger — added only when a non-webhook provider is integrated.
- Web UI / frontend — management exclusively via REST API (`/docs` Swagger UI is sufficient).
- Multi-user / multi-tenancy.
- Multi-instance deployment / distributed coordination.

## Architecture Overview

```
app/
├── main.py                 FastAPI app, router wiring, lifespan
├── config.py               pydantic-settings (env-based)
├── db.py                   SQLModel engine + session dependency
├── models.py               SortingProject, CategoryCache
├── normalize.py            content_key() helper
├── sorter.py               pydantic-ai agent, prompt, sort pipeline
├── debouncer.py            Per-project debouncer (leading + trailing edge)
├── suppression.py          Post-reorder echo-suppression tracker
├── backends/
│   ├── base.py             TaskBackend Protocol
│   ├── registry.py         provider name → backend instance
│   └── todoist.py          TodoistBackend (sole impl)
└── routes/
    ├── deps.py             API-key auth dependency
    ├── webhook.py          POST /webhook/{provider}
    └── projects.py         CRUD /projects + categories + cache
```

Provider-agnostic abstraction (`TaskBackend` Protocol, registry) is present, but only one implementation exists. No dead stub code for future providers.

## Configuration

All credentials and model details are read via `pydantic-settings` from environment variables:

| Env var | Purpose |
|---------|---------|
| `TODOIST_CLIENT_SECRET` | HMAC verification of incoming webhooks |
| `TODOIST_API_TOKEN` | Todoist REST API v2 (read tasks, reorder) |
| `LLM_MODEL` | pydantic-ai model identifier (e.g. `anthropic:claude-sonnet-4-6`) |
| `LLM_API_KEY` | API key for the LLM provider |
| `APP_API_KEY` | Protects management endpoints (`X-API-Key` header) |
| `DATABASE_URL` | SQLite path (default: `sqlite:///./data/app.db`) |
| `DEFAULT_DEBOUNCE_SECONDS` | Fallback for new projects (default: `5`) |
| `SUPPRESSION_WINDOW_SECONDS` | Loop-prevention window (default: `30`) |

## Data Model

```python
class SortingProject(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("provider", "external_project_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str                              # "Lidl Shopping"
    provider: str                          # "todoist"
    external_project_id: str               # Todoist project_id as string
    provider_config: dict = Field(         # provider-specific JSON bucket
        default_factory=dict, sa_column=Column(JSON))
    categories: list[str] = Field(         # ordered list
        default_factory=list, sa_column=Column(JSON))
    description: str | None = None         # optional free-text LLM context
    debounce_seconds: int = 5
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class CategoryCache(SQLModel, table=True):
    project_id: UUID = Field(foreign_key="sortingproject.id",
                             primary_key=True, ondelete="CASCADE")
    content_key: str = Field(primary_key=True)  # normalized item text
    category_name: str                          # soft reference into project.categories
    created_at: datetime
    updated_at: datetime
```

Uniqueness: `(provider, external_project_id)` for `SortingProject`, `(project_id, content_key)` for `CategoryCache`.

**Normalization** (`content_key`): `content.strip().lower()` with whitespace collapsed (`re.sub(r"\s+", " ", ...)`). Emojis and accents are preserved.

## REST API

All management endpoints require the `X-API-Key` header (`APP_API_KEY`).

### Project CRUD

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects` | List all sorting projects |
| `POST` | `/projects` | Create project |
| `GET` | `/projects/{id}` | Read single project |
| `PUT` | `/projects/{id}` | Update metadata (name, description, `enabled`, `debounce_seconds`) |
| `DELETE` | `/projects/{id}` | Delete project (cache removed via `ON DELETE CASCADE`) |
| `POST` | `/projects/{id}/sort` | Manually trigger a sort (bypasses debounce) |

### Category management

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects/{id}/categories` | Ordered category list |
| `PUT` | `/projects/{id}/categories` | Atomically replace full list: `{"categories": [...]}` |
| `POST` | `/projects/{id}/categories` | Add category: `{"name": "🧊 Frozen", "at_index": 14}` (append when `at_index` omitted) |
| `DELETE` | `/projects/{id}/categories/{index}` | Remove by 0-based index |
| `PATCH` | `/projects/{id}/categories/{index}` | Rename/move: `{"name": "...", "move_to": 3}` — both fields optional |

Every category modification automatically triggers a sort cycle following the cache-invalidation matrix below. The trigger runs under the normal project lock.

### Cache management

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects/{id}/cache` | Inspect current cache entries |
| `DELETE` | `/projects/{id}/cache` | Clear cache (forces next sort to re-run the LLM) |

### Webhook + health

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/webhook/{provider}` | Webhook endpoint (no API-key; uses provider-specific signature) |
| `GET` | `/healthz` | Liveness |

Swagger UI at `/docs`.

## Webhook Flow with Cache Fast-Path

1. `POST /webhook/todoist` with header `X-Todoist-Hmac-SHA256` and JSON body.
2. `TodoistBackend.verify_webhook(headers, body)` checks HMAC against `TODOIST_CLIENT_SECRET`. Mismatch → `401`.
3. Parse payload: `event_name`, `external_project_id`, optionally `trigger_item` (content of the item that caused the event).
4. DB lookup: `SortingProject` matching `(provider="todoist", external_project_id, enabled=True)`. No match → `200 {"status": "ignored"}`.
5. **Suppression check** (see "Loop Prevention"): if this event is an echo of our own reorder, drop → `200 {"status": "suppressed"}`.
6. **Fast-path check**: if `trigger_item.content` is present and `CategoryCache`-lookup on `(project.id, normalize(content))` hits → `Debouncer.fire_now(project.id)` (bypasses debounce).
7. Otherwise → `Debouncer.touch(project.id)` (leading + trailing edge).
8. Respond `200 {"status": "queued"}` immediately; sort runs asynchronously.

## Debouncer — Leading + Trailing Edge

**Goal:** the first change in a quiet period is processed immediately; bursts are collapsed into a single trailing sort.

Per `SortingProject`, kept in process memory:

- `last_event_at: float | None` — monotonic timestamp of last `touch()`/`fire_now()`
- `pending_task: asyncio.Task | None` — scheduled sort trigger
- `sort_in_progress: bool`
- `lock: asyncio.Lock` — serializes sorts per project

**`touch(project_id)`:**

```
now = monotonic()
delta = (now - last_event_at) if last_event_at else infinity
last_event_at = now

if delta > debounce_seconds:
    # Leading edge: immediate
    cancel_if_sleeping(pending_task)
    pending_task = create_task(run_sort(delay=0))
else:
    # Trailing edge: debounce
    cancel_if_sleeping(pending_task)
    pending_task = create_task(run_sort(delay=debounce_seconds))
```

**`fire_now(project_id)`:** like `touch`, but `delay=0` regardless of `delta`. Called from the webhook fast-path.

**`run_sort(delay)`:**

```
if delay > 0: await sleep(delay)
async with lock:
    sort_in_progress = True
    try:
        await sort_project(project_id)
    finally:
        sort_in_progress = False
```

**Cancel hygiene:** `cancel_if_sleeping` must only cancel the task while it is still sleeping (`sort_in_progress == False`). If a sort is already running, the old task is left alone; the new event creates an additional pending task that waits on the lock.

**Semantics:**
- Quiet project, first event → immediate (leading)
- Burst within `debounce_seconds` → one sort after the burst ends (trailing)
- Event with cache hit on trigger item → immediate (debounce bypassed)
- Event while sort is running → new sort queues on the lock

## Sort Pipeline

`sort_project(project_id)`:

1. Load `SortingProject` from DB; if disabled or deleted, abort.
2. `backend = registry.get(project.provider)`.
3. `tasks = await backend.get_tasks(project)` → `Task(id, content)`. If `len < 2`, abort.
4. Compute `content_key = normalize(task.content)` for each task.
5. Cache lookup: split tasks into `hits` and `misses`.
6. **If `misses` is empty** (all cached): skip LLM. Jump to step 9.
7. LLM call for `misses` using the hybrid prompt (see "LLM Interaction"), returns `CategorizedItems(assignments)`.
8. Validate response, merge hits with new LLM assignments, upsert affected `CategoryCache` rows.
9. Reorder computation (deterministic, server-side):
   - For each task: `category_index = project.categories.index(category_name_for(task))`
   - Tasks whose assigned category is not in the current list (orphans) → sort to the end (`index = len(categories)`)
   - Stable sort by `(category_index, current_todoist_position)` — preserves intra-category order.
10. **Snapshot validation before reorder**: re-fetch tasks; drop any id no longer present. Items added during the LLM call are not included in the reorder (they keep Todoist's default position); a follow-up event will place them in a later cycle.
11. `await backend.reorder(project, reorder_payload)`.
12. **Record suppression**: `suppression.mark(project_id, {t.id for t in reordered_tasks}, window=SUPPRESSION_WINDOW_SECONDS)` (see "Loop Prevention").

## LLM Interaction — Hybrid (Misses + Context)

The LLM decides only about the misses but sees existing cached assignments as context to stay consistent.

**Output schema:**
```python
class Assignment(BaseModel):
    item_id: str
    category_name: str    # must match one of project.categories exactly

class CategorizedItems(BaseModel):
    assignments: list[Assignment]
```

**Prompt structure:**
```
System: You categorize shopping list items into the given categories.
        Respond strictly in the required JSON schema. Pick exactly one
        category from the list for each item to be categorized. Do not
        invent categories and do not change the reference assignments.

User: Categories (in this order):
        1. 🥬 Vegetables
        2. 🍎 Fruit
        ...

      {description if set}

      Already assigned (for reference only, do not change):
        - Apples → 🍎 Fruit
        - Yogurt → 🥛 Dairy
        ...

      Please categorize:
        - id=abc123, content="Oats"
        - id=def456, content="Cinnamon"
```

If all items are misses (e.g. after a category add with cache clear), the "Already assigned" block is omitted.

**LLM response validation:**
- Each `assignment.item_id` must be among the requested misses (no invented ids).
- Each `assignment.category_name` must be in `project.categories` (exact string match). Invalid → item treated as orphan (placed at end); no cache write.
- No duplicate `item_id`; later duplicates dropped.
- Missing ids (LLM omitted some misses) → treated as orphans; do not block.
- Empty/invalid response → warn, skip reorder (next event triggers again).

## Cache Invalidation on Category Changes

| Action | Cache effect | Follow-up |
|--------|--------------|-----------|
| **Add** category | Full project cache cleared | Auto-triggered full re-sort. Rationale: new category may better absorb existing items |
| **Rename** category (`PATCH` with new `name`) | Full project cache cleared | Auto-triggered full re-sort. Rationale: a rename can change the category's semantics and affect items outside the renamed category (e.g. "Fruit" → "Fruit & Nuts" pulls in nuts) |
| **Remove** category | Only entries with `category_name == removed` deleted | Auto-triggered sort → LLM re-categorizes affected items |
| **Reorder** only (`PATCH` with `move_to`, no `name`) | Cache untouched | Sort triggered to reapply ordering (no LLM call; all hits) |
| **Replace** full list (`PUT`) | Diff old↔new: if any add or rename is present → full cache clear; else only remove entries for dropped categories | A single sort cycle |

Each of these sorts runs through the normal per-project lock (at most one sort per project).

## Loop Prevention

**Problem.** The sort pipeline's final step calls Todoist's `item_reorder` sync command, which updates `child_order` on multiple items. Todoist emits an `item:updated` webhook for every updated item. Without protection each echo re-enters the pipeline, triggers another sort (which computes the same order and reorders again), generates more echoes, and so on — an infinite loop.

**Guard: post-reorder suppression window.**

- After each successful `reorder()`, record in-memory: `suppression[project_id] = (set_of_reordered_item_ids, deadline_monotonic)`.
- On incoming webhook: if `event_name == "item:updated"` AND `event_data.id ∈ suppression[project_id].ids` AND `now < suppression[project_id].deadline` → drop with `200 {"status": "suppressed"}`.
- After the deadline, the suppression entry is lazily cleared on next access.
- **Default window:** 30 seconds (configurable via `SUPPRESSION_WINDOW_SECONDS`).

**What is *not* suppressed:**
- `item:added` events (new items are never echoes of our reorder; always processed).
- `item:updated` events for items *not* in the last reorder set.
- `item:updated` events after the deadline.
- Any other event type (`item:completed`, `item:deleted`, etc.) — reorder only emits `item:updated`.

**Accepted trade-off.** If the user edits an item's content within the suppression window, that edit is ignored by the sorter (Todoist still shows the edit). Any subsequent unrelated event triggers re-evaluation. The window is intentionally short to minimize this risk.

**Storage.** In-memory `dict[UUID, tuple[frozenset[str], float]]` held by a `SuppressionTracker` singleton per process. Consistent with the single-instance deployment assumption.

## Concurrency & Races

| Scenario | Handling |
|----------|----------|
| New item arrives while a sort is running | A new debounce timer starts; it waits on the project lock and runs a fresh cycle after the current one finishes |
| Item deleted/completed during LLM call | Snapshot validation (step 10) filters missing ids from the reorder payload |
| Two overlapping sort attempts per project | Impossible — `asyncio.Lock` guarantees at most one sort per project |
| Category changed while a sort is running | Config update waits on the lock like a normal sort; after the current run finishes, invalidation is applied and a fresh sort runs |
| User manually reorders in Todoist during sort | Overwritten by design — in a sorting project, the LLM is the source of truth for order |
| Webhook retry (same event twice) | Idempotent: same input → same sort; multiple debouncer touches collapse |
| Reorder echo (item:updated from our own change) | Dropped by suppression window; see "Loop Prevention" |
| Cache-hit fast-path fires twice | Both `fire_now` calls queue on the lock; the second sees the already-sorted state, reorder is idempotent |
| Crash during sort | In-flight state is in-memory; DB stays consistent; next webhook triggers a fresh sort |
| Distributed race (multiple instances) | **Out of scope** — single-instance deployment assumed |

**Guarantees:**
- **At most one** sort running per project at any time
- **Eventual consistency:** every event leads to at least one subsequent sort that sees it
- **Idempotence:** repeated events/retries do not diverge the end state

## Error Handling

| Error | Response |
|-------|----------|
| Invalid HMAC signature | `401`; no secret leakage in logs |
| Unknown Todoist project | `200 {"status": "ignored"}` |
| Unknown provider in URL | `404` |
| `APP_API_KEY` missing/wrong | `401` |
| Todoist API 5xx on fetch/reorder | Retry with exponential backoff (3 attempts, base 1s); then warn and abandon |
| Todoist API 4xx (except 429) | Log error, abort the sort cycle |
| Todoist API 429 | Respect `Retry-After` |
| LLM timeout / error | Warn, abort the sort cycle (next event retriggers) |
| LLM response validation fails | Warn, skip reorder; no cache writes |
| Invalid category name in LLM output | Treat item as orphan (end of list); no cache write |
| Category index out of range (API) | `422` |

## Tests

**Framework:** `pytest` + `pytest-asyncio` + `httpx.AsyncClient` + `respx` for HTTP mocks.

**Coverage:**
- `TodoistBackend`: fetch, reorder, HMAC verify (positive + negative), payload parsing for different event types
- `sorter`: prompt rendering, `pydantic-ai` `TestModel`/`FunctionModel` for deterministic LLM stubs, response validation (invalid category, unknown ids, duplicates), deterministic reorder computation (category ordering, orphan placement, intra-category stability), full `sort_project` pipeline with cache integration
- `debouncer`: leading edge fires immediately; trailing edge collapses bursts; `fire_now` bypasses debounce; events during sort queue on the lock; cancel never interrupts a running sort
- `suppression`: entries expire after deadline; item ids outside the set are not suppressed; `item:added` is never suppressed
- `routes/projects.py`: CRUD + API-key enforcement + category CRUD including cache-invalidation matrix (one test per action)
- `routes/webhook.py`: HMAC check, suppression dropping, fast-path cache hit vs miss, dispatch to the correct `SortingProject`, ignore path for unknown projects
- E2E: fake webhook payload → mock LLM → verify mock Todoist reorder was called with the expected ids; cache before/after; suppression prevents echo loop

## Deployment

- `Dockerfile` (python:3.12-slim, uvicorn)
- `docker-compose.yml` with volume for the SQLite file (`./data:/app/data`)
- `.env.example` with all required variables
- Start: `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000`
- Schema setup at startup via `SQLModel.metadata.create_all()` (Alembic only when schema migrations are needed)

## Open Points (to clarify during implementation)

- Exact shape of the Todoist webhook payload for each event (`item:added`, `item:updated`, `item:completed`, `item:deleted`) — trigger item content must be extractable; verify against Todoist docs during implementation.
- Todoist sync API's exact request format for `item_reorder` (form-encoded `commands=<json>` vs JSON body) — verify against current docs.
- `ondelete="CASCADE"` behaviour in SQLModel/SQLAlchemy for SQLite (requires `PRAGMA foreign_keys=ON`, already enabled in `db.py`).
