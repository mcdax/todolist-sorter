# Todolist-Sorter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI service that receives Todoist webhooks and reorders list items by LLM-assigned categories, with a content→category cache letting known items skip both LLM and debounce, and echo-suppression to prevent reorder loops.

**Architecture:** Single FastAPI app, SQLite persistence (SortingProject + CategoryCache), provider-agnostic `TaskBackend` protocol (Todoist as sole MVP impl), per-project `asyncio.Lock` serializing sorts, leading+trailing-edge debouncer, pydantic-ai for categorization, in-memory suppression tracker to drop reorder echoes. Spec: `docs/superpowers/specs/2026-04-18-todolist-sorter-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pydantic-settings, pydantic-ai, httpx, respx (tests), pytest + pytest-asyncio.

---

## File Structure

```
app/
├── __init__.py
├── main.py                 FastAPI app factory, wiring, /healthz
├── config.py               Settings via pydantic-settings
├── db.py                   SQLModel engine, session dependency
├── models.py               SortingProject, CategoryCache
├── normalize.py            content_key(text)
├── suppression.py          SuppressionTracker (loop prevention)
├── debouncer.py            Per-project debouncer
├── sorter.py               pydantic-ai agent, prompt, sort pipeline
├── backends/
│   ├── __init__.py
│   ├── base.py             TaskBackend Protocol, Task model
│   ├── registry.py         name → backend instance
│   └── todoist.py          TodoistBackend
└── routes/
    ├── __init__.py
    ├── deps.py             API-key auth dependency
    ├── projects.py         CRUD + categories + cache + manual sort
    └── webhook.py          POST /webhook/{provider}

tests/
├── __init__.py
├── conftest.py             fixtures
├── test_config.py
├── test_normalize.py
├── test_models.py
├── test_db.py
├── test_backend_base.py
├── test_backend_registry.py
├── test_todoist_backend.py
├── test_sorter.py
├── test_debouncer.py
├── test_suppression.py
├── test_auth_dep.py
├── test_routes_projects.py
├── test_routes_categories.py
├── test_routes_cache.py
├── test_routes_webhook.py
├── test_main.py
└── test_integration.py

pyproject.toml
.env.example
.gitignore
Dockerfile
docker-compose.yml
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "todolist-sorter"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pydantic-ai>=0.0.13",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
venv/
.env
data/
.pytest_cache/
dist/
build/
*.egg-info/
```

- [ ] **Step 3: Create `.env.example`**

```
TODOIST_CLIENT_SECRET=your-todoist-webhook-client-secret
TODOIST_API_TOKEN=your-todoist-api-token
LLM_MODEL=anthropic:claude-sonnet-4-6
LLM_API_KEY=your-llm-api-key
APP_API_KEY=generate-a-long-random-string
DATABASE_URL=sqlite:///./data/app.db
DEFAULT_DEBOUNCE_SECONDS=5
SUPPRESSION_WINDOW_SECONDS=30
```

- [ ] **Step 4: Create empty `app/__init__.py` and `tests/__init__.py`**

Both files empty.

- [ ] **Step 5: Install and verify**

Run: `python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'`
Expected: completes without errors; `pytest --version` prints a version.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example app/__init__.py tests/__init__.py
git commit -m "chore: project scaffolding"
```

---

### Task 2: Settings (pydantic-settings)

**Files:**
- Create: `app/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:
```python
from app.config import Settings, get_settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("TODOIST_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TODOIST_API_TOKEN", "token")
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-sonnet-4-6")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("APP_API_KEY", "app-key")
    get_settings.cache_clear()

    s = Settings()

    assert s.todoist_client_secret == "secret"
    assert s.todoist_api_token == "token"
    assert s.llm_model == "anthropic:claude-sonnet-4-6"
    assert s.llm_api_key == "llm-key"
    assert s.app_api_key == "app-key"
    assert s.database_url == "sqlite:///./data/app.db"
    assert s.default_debounce_seconds == 5
    assert s.suppression_window_seconds == 30
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/config.py`:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    todoist_client_secret: str
    todoist_api_token: str
    llm_model: str
    llm_api_key: str
    app_api_key: str
    database_url: str = "sqlite:///./data/app.db"
    default_debounce_seconds: int = 5
    suppression_window_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run — should pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: pydantic-settings based Settings"
```

---

### Task 3: `content_key` normalization

**Files:**
- Create: `app/normalize.py`
- Create: `tests/test_normalize.py`

- [ ] **Step 1: Write failing tests**

`tests/test_normalize.py`:
```python
import pytest

from app.normalize import content_key


@pytest.mark.parametrize("raw,expected", [
    ("Apples", "apples"),
    ("  Yogurt  ", "yogurt"),
    ("Whole milk\t 3.5%", "whole milk 3.5%"),
    ("🍎 Apples", "🍎 apples"),
    ("A   B\nC", "a b c"),
    ("", ""),
])
def test_content_key(raw, expected):
    assert content_key(raw) == expected
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/normalize.py`:
```python
import re

_WS = re.compile(r"\s+")


def content_key(text: str) -> str:
    return _WS.sub(" ", text.strip()).lower()
```

- [ ] **Step 4: Run — should pass**

Run: `pytest tests/test_normalize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/normalize.py tests/test_normalize.py
git commit -m "feat: content_key normalization helper"
```

---

### Task 4: Data models (SortingProject, CategoryCache)

**Files:**
- Create: `app/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test**

`tests/test_models.py`:
```python
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CategoryCache, SortingProject


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_sorting_project_roundtrip():
    engine = _engine()
    with Session(engine) as s:
        p = SortingProject(
            name="Lidl",
            provider="todoist",
            external_project_id="123",
            categories=["🥬 Vegetables", "🍎 Fruit"],
            description="Supermarket route",
        )
        s.add(p); s.commit(); s.refresh(p)
        loaded = s.exec(select(SortingProject)).one()
        assert loaded.name == "Lidl"
        assert loaded.categories == ["🥬 Vegetables", "🍎 Fruit"]
        assert loaded.enabled is True
        assert loaded.debounce_seconds == 5


def test_category_cache_composite_key():
    engine = _engine()
    with Session(engine) as s:
        pid = uuid4()
        s.add(SortingProject(id=pid, name="L", provider="todoist",
                             external_project_id="1", categories=["A"]))
        s.commit()
        s.add(CategoryCache(project_id=pid, content_key="apples",
                            category_name="A"))
        s.commit()
        row = s.exec(select(CategoryCache)).one()
        assert row.category_name == "A"


def test_unique_provider_external():
    engine = _engine()
    with Session(engine) as s:
        s.add(SortingProject(name="A", provider="todoist",
                             external_project_id="1", categories=[]))
        s.commit()
        s.add(SortingProject(name="B", provider="todoist",
                             external_project_id="1", categories=[]))
        with pytest.raises(IntegrityError):
            s.commit()
```

- [ ] **Step 2: Run — should fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app/models.py`:
```python
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SortingProject(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("provider", "external_project_id",
                         name="uq_provider_external"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    provider: str
    external_project_id: str = Field(index=True)
    provider_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    categories: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    description: str | None = None
    debounce_seconds: int = 5
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CategoryCache(SQLModel, table=True):
    project_id: UUID = Field(
        foreign_key="sortingproject.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    content_key: str = Field(primary_key=True)
    category_name: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run — should pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: SortingProject + CategoryCache SQLModel entities"
```

---

### Task 5: Database engine + session dependency

**Files:**
- Create: `app/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s
```

- [ ] **Step 2: Write failing test**

`tests/test_db.py`:
```python
from sqlmodel import Session

from app.db import create_db_and_tables, get_session, make_engine


def test_make_engine_and_create_tables():
    engine = make_engine("sqlite://")
    create_db_and_tables(engine)


def test_get_session_yields_session():
    engine = make_engine("sqlite://")
    create_db_and_tables(engine)
    gen = get_session(engine)
    sess = next(gen)
    assert isinstance(sess, Session)
    try:
        next(gen)
    except StopIteration:
        pass
```

- [ ] **Step 3: Run — should fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

`app/db.py`:
```python
from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def create_db_and_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def get_session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Run — should pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: database engine + session helpers with FK pragma"
```

---

### Task 6: TaskBackend protocol + Task model

**Files:**
- Create: `app/backends/__init__.py` (empty)
- Create: `app/backends/base.py`
- Create: `tests/test_backend_base.py`

- [ ] **Step 1: Create package init**

`app/backends/__init__.py`: empty.

- [ ] **Step 2: Write failing test**

`tests/test_backend_base.py`:
```python
from app.backends.base import Task, TaskBackend


def test_task_model():
    t = Task(id="abc", content="Apples")
    assert t.id == "abc"
    assert t.content == "Apples"


def test_taskbackend_is_importable():
    assert TaskBackend is not None
```

- [ ] **Step 3: Run — should fail**

Expected: FAIL.

- [ ] **Step 4: Implement**

`app/backends/base.py`:
```python
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from app.models import SortingProject


class Task(BaseModel):
    id: str
    content: str


@runtime_checkable
class TaskBackend(Protocol):
    name: ClassVar[str]

    async def get_tasks(self, project: SortingProject) -> list[Task]: ...
    async def reorder(
        self, project: SortingProject, ordered_ids: list[str]
    ) -> None: ...
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...
    def extract_project_id(self, payload: dict) -> str | None: ...
    def extract_trigger_content(self, payload: dict) -> str | None: ...
    def extract_event_name(self, payload: dict) -> str | None: ...
    def extract_item_id(self, payload: dict) -> str | None: ...
```

- [ ] **Step 5: Run — should pass**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/backends/__init__.py app/backends/base.py tests/test_backend_base.py
git commit -m "feat: TaskBackend protocol + Task model"
```

---

### Task 7: Backend registry

**Files:**
- Create: `app/backends/registry.py`
- Create: `tests/test_backend_registry.py`

- [ ] **Step 1: Write failing test**

`tests/test_backend_registry.py`:
```python
import pytest

from app.backends.registry import BackendRegistry


class _FakeBackend:
    name = "fake"


def test_register_and_get():
    r = BackendRegistry()
    b = _FakeBackend()
    r.register(b)
    assert r.get("fake") is b


def test_get_unknown_raises():
    r = BackendRegistry()
    with pytest.raises(KeyError):
        r.get("nope")


def test_register_duplicate_raises():
    r = BackendRegistry()
    r.register(_FakeBackend())
    with pytest.raises(ValueError):
        r.register(_FakeBackend())
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/backends/registry.py`:
```python
from app.backends.base import TaskBackend


class BackendRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, TaskBackend] = {}

    def register(self, backend: TaskBackend) -> None:
        if backend.name in self._by_name:
            raise ValueError(f"backend '{backend.name}' already registered")
        self._by_name[backend.name] = backend

    def get(self, name: str) -> TaskBackend:
        if name not in self._by_name:
            raise KeyError(f"unknown backend '{name}'")
        return self._by_name[name]

    def names(self) -> list[str]:
        return list(self._by_name)
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backends/registry.py tests/test_backend_registry.py
git commit -m "feat: BackendRegistry"
```

---

### Task 8: TodoistBackend — HMAC verification

**Files:**
- Create: `app/backends/todoist.py`
- Create: `tests/test_todoist_backend.py`

- [ ] **Step 1: Write failing test**

`tests/test_todoist_backend.py`:
```python
import base64
import hashlib
import hmac
import json

from app.backends.todoist import TodoistBackend


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_verify_webhook_valid():
    secret = "s3cret"
    body = json.dumps({"event_name": "item:added"}).encode()
    sig = _sign(secret, body)
    b = TodoistBackend(api_token="t", client_secret=secret)
    assert b.verify_webhook({"X-Todoist-Hmac-SHA256": sig}, body) is True


def test_verify_webhook_invalid():
    b = TodoistBackend(api_token="t", client_secret="s3cret")
    assert b.verify_webhook(
        {"X-Todoist-Hmac-SHA256": "wrongsig"}, b"{}"
    ) is False


def test_verify_webhook_missing_header():
    b = TodoistBackend(api_token="t", client_secret="s3cret")
    assert b.verify_webhook({}, b"{}") is False


def test_header_lookup_case_insensitive():
    secret = "s3cret"
    body = b"{}"
    sig = _sign(secret, body)
    b = TodoistBackend(api_token="t", client_secret=secret)
    assert b.verify_webhook({"x-todoist-hmac-sha256": sig}, body) is True
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/backends/todoist.py`:
```python
import base64
import hashlib
import hmac
from typing import ClassVar


class TodoistBackend:
    name: ClassVar[str] = "todoist"

    def __init__(self, api_token: str, client_secret: str) -> None:
        self._api_token = api_token
        self._client_secret = client_secret

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        sig = _get_header(headers, "X-Todoist-Hmac-SHA256")
        if not sig:
            return False
        expected = base64.b64encode(
            hmac.new(self._client_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(sig, expected)


def _get_header(headers: dict[str, str], name: str) -> str | None:
    lower = name.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return None
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backends/todoist.py tests/test_todoist_backend.py
git commit -m "feat(todoist): HMAC webhook verification"
```

---

### Task 9: TodoistBackend — payload parsing

**Files:**
- Modify: `app/backends/todoist.py`
- Modify: `tests/test_todoist_backend.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_todoist_backend.py`:
```python
def test_extract_project_id_item_event():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {
        "event_name": "item:added",
        "event_data": {"id": "111", "content": "Apples", "project_id": "999"},
    }
    assert b.extract_project_id(payload) == "999"


def test_extract_project_id_missing():
    b = TodoistBackend(api_token="t", client_secret="s")
    assert b.extract_project_id({"event_name": "item:added"}) is None


def test_extract_event_name():
    b = TodoistBackend(api_token="t", client_secret="s")
    assert b.extract_event_name({"event_name": "item:updated"}) == "item:updated"
    assert b.extract_event_name({}) is None


def test_extract_item_id():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {"event_data": {"id": "42"}}
    assert b.extract_item_id(payload) == "42"
    assert b.extract_item_id({}) is None


def test_extract_trigger_content_on_add_or_update():
    b = TodoistBackend(api_token="t", client_secret="s")
    for ev in ("item:added", "item:updated"):
        payload = {
            "event_name": ev,
            "event_data": {"id": "1", "content": "Apples", "project_id": "9"},
        }
        assert b.extract_trigger_content(payload) == "Apples"


def test_extract_trigger_content_none_for_other_events():
    b = TodoistBackend(api_token="t", client_secret="s")
    payload = {
        "event_name": "item:completed",
        "event_data": {"id": "1", "content": "Apples", "project_id": "9"},
    }
    assert b.extract_trigger_content(payload) is None
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/backends/todoist.py`:
```python
_TRIGGER_EVENTS = {"item:added", "item:updated"}


class TodoistBackend:
    # ... existing methods

    def extract_project_id(self, payload: dict) -> str | None:
        data = payload.get("event_data") or {}
        pid = data.get("project_id")
        return str(pid) if pid is not None else None

    def extract_event_name(self, payload: dict) -> str | None:
        ev = payload.get("event_name")
        return str(ev) if ev else None

    def extract_item_id(self, payload: dict) -> str | None:
        data = payload.get("event_data") or {}
        iid = data.get("id")
        return str(iid) if iid is not None else None

    def extract_trigger_content(self, payload: dict) -> str | None:
        if payload.get("event_name") not in _TRIGGER_EVENTS:
            return None
        data = payload.get("event_data") or {}
        content = data.get("content")
        return str(content) if content else None
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backends/todoist.py tests/test_todoist_backend.py
git commit -m "feat(todoist): payload parsing helpers"
```

---

### Task 10: TodoistBackend — `get_tasks`

**Files:**
- Modify: `app/backends/todoist.py`
- Modify: `tests/test_todoist_backend.py`

- [ ] **Step 1: Add failing test**

Append:
```python
import httpx
import pytest

from app.models import SortingProject


@pytest.mark.asyncio
async def test_get_tasks_happy_path(respx_mock):
    respx_mock.get("https://api.todoist.com/rest/v2/tasks").mock(
        return_value=httpx.Response(200, json=[
            {"id": "111", "content": "Apples", "project_id": "999", "order": 1},
            {"id": "222", "content": "Milk", "project_id": "999", "order": 2},
        ])
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )

    tasks = await b.get_tasks(project)

    assert [t.id for t in tasks] == ["111", "222"]
    assert tasks[0].content == "Apples"
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/backends/todoist.py`:
```python
import httpx

from app.backends.base import Task
from app.models import SortingProject


_REST_BASE = "https://api.todoist.com/rest/v2"


class TodoistBackend:
    # ...

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=httpx.Timeout(15.0),
        )

    async def get_tasks(self, project: SortingProject) -> list[Task]:
        async with self._client() as c:
            r = await c.get(
                f"{_REST_BASE}/tasks",
                params={"project_id": project.external_project_id},
            )
            r.raise_for_status()
            return [
                Task(id=str(item["id"]), content=item["content"])
                for item in r.json()
            ]
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backends/todoist.py tests/test_todoist_backend.py
git commit -m "feat(todoist): get_tasks via REST v2"
```

---

### Task 11: TodoistBackend — `reorder`

**Files:**
- Modify: `app/backends/todoist.py`
- Modify: `tests/test_todoist_backend.py`

Uses Todoist Sync v9 `item_reorder` command (REST v2 has no batch reorder).

- [ ] **Step 1: Add failing test**

Append:
```python
import json as _json
from urllib.parse import parse_qs


@pytest.mark.asyncio
async def test_reorder_calls_sync_api(respx_mock):
    route = respx_mock.post("https://api.todoist.com/sync/v9/sync").mock(
        return_value=httpx.Response(200, json={"sync_status": {}})
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )

    await b.reorder(project, ["222", "111", "333"])

    assert route.called
    form = parse_qs(route.calls.last.request.content.decode())
    commands = _json.loads(form["commands"][0])
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd["type"] == "item_reorder"
    assert cmd["args"]["items"] == [
        {"id": "222", "child_order": 1},
        {"id": "111", "child_order": 2},
        {"id": "333", "child_order": 3},
    ]


@pytest.mark.asyncio
async def test_reorder_skips_empty(respx_mock):
    route = respx_mock.post("https://api.todoist.com/sync/v9/sync").mock(
        return_value=httpx.Response(200, json={})
    )
    b = TodoistBackend(api_token="tok", client_secret="s")
    project = SortingProject(
        name="Lidl", provider="todoist",
        external_project_id="999", categories=[],
    )
    await b.reorder(project, [])
    assert not route.called
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/backends/todoist.py`:
```python
import json
import uuid


_SYNC_URL = "https://api.todoist.com/sync/v9/sync"


class TodoistBackend:
    # ...

    async def reorder(
        self, project: SortingProject, ordered_ids: list[str]
    ) -> None:
        if not ordered_ids:
            return
        command = {
            "type": "item_reorder",
            "uuid": str(uuid.uuid4()),
            "args": {
                "items": [
                    {"id": tid, "child_order": i + 1}
                    for i, tid in enumerate(ordered_ids)
                ]
            },
        }
        async with self._client() as c:
            r = await c.post(
                _SYNC_URL,
                data={"commands": json.dumps([command])},
            )
            r.raise_for_status()
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backends/todoist.py tests/test_todoist_backend.py
git commit -m "feat(todoist): reorder via Sync API item_reorder"
```

---

### Task 12: Sorter — prompt + schemas

**Files:**
- Create: `app/sorter.py`
- Create: `tests/test_sorter.py`

- [ ] **Step 1: Write failing test**

`tests/test_sorter.py`:
```python
from app.backends.base import Task
from app.sorter import Assignment, CategorizedItems, render_prompt


def test_render_prompt_with_hits_and_misses():
    prompt = render_prompt(
        categories=["🥬 Vegetables", "🍎 Fruit"],
        description="Supermarket route",
        hits={"Apples": "🍎 Fruit"},
        misses=[Task(id="42", content="Cinnamon")],
    )

    assert "🥬 Vegetables" in prompt
    assert "🍎 Fruit" in prompt
    assert "Supermarket route" in prompt
    assert "Apples" in prompt
    assert "Cinnamon" in prompt
    assert "id=42" in prompt


def test_render_prompt_no_hits_omits_reference_block():
    prompt = render_prompt(
        categories=["A", "B"],
        description=None,
        hits={},
        misses=[Task(id="1", content="X")],
    )
    assert "Already assigned" not in prompt


def test_schemas():
    a = Assignment(item_id="1", category_name="A")
    c = CategorizedItems(assignments=[a])
    assert c.assignments[0].item_id == "1"
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/sorter.py`:
```python
from pydantic import BaseModel

from app.backends.base import Task


class Assignment(BaseModel):
    item_id: str
    category_name: str


class CategorizedItems(BaseModel):
    assignments: list[Assignment]


SYSTEM_PROMPT = (
    "You categorize shopping list items into the given categories. "
    "Respond strictly in the required JSON schema. Pick exactly one "
    "category from the list for each item to be categorized. Do not "
    "invent categories and do not change the reference assignments."
)


def render_prompt(
    *,
    categories: list[str],
    description: str | None,
    hits: dict[str, str],
    misses: list[Task],
) -> str:
    lines: list[str] = []
    lines.append("Categories (in this order):")
    for i, name in enumerate(categories, 1):
        lines.append(f"  {i}. {name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    if hits:
        lines.append("Already assigned (for reference only, do not change):")
        for content, cat in hits.items():
            lines.append(f"  - {content} → {cat}")
        lines.append("")
    lines.append("Please categorize:")
    for task in misses:
        lines.append(f'  - id={task.id}, content="{task.content}"')
    return "\n".join(lines)
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sorter.py tests/test_sorter.py
git commit -m "feat(sorter): prompt rendering + pydantic output schemas"
```

---

### Task 13: Sorter — `categorize` with pydantic-ai agent

**Files:**
- Modify: `app/sorter.py`
- Modify: `tests/test_sorter.py`

- [ ] **Step 1: Add failing test**

Append:
```python
import pytest
from pydantic_ai.models.test import TestModel

from app.sorter import categorize


@pytest.mark.asyncio
async def test_categorize_with_test_model():
    fixed = {
        "assignments": [
            {"item_id": "1", "category_name": "🍎 Fruit"},
            {"item_id": "2", "category_name": "🥬 Vegetables"},
        ]
    }
    model = TestModel(custom_output_args=fixed)

    result = await categorize(
        model=model,
        categories=["🥬 Vegetables", "🍎 Fruit"],
        description=None,
        hits={},
        misses=[Task(id="1", content="Apples"), Task(id="2", content="Lettuce")],
    )

    ids = {a.item_id: a.category_name for a in result.assignments}
    assert ids == {"1": "🍎 Fruit", "2": "🥬 Vegetables"}
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/sorter.py`:
```python
from pydantic_ai import Agent
from pydantic_ai.models import Model


async def categorize(
    *,
    model: Model | str,
    categories: list[str],
    description: str | None,
    hits: dict[str, str],
    misses: list[Task],
) -> CategorizedItems:
    agent = Agent(
        model,
        output_type=CategorizedItems,
        system_prompt=SYSTEM_PROMPT,
    )
    prompt = render_prompt(
        categories=categories,
        description=description,
        hits=hits,
        misses=misses,
    )
    result = await agent.run(prompt)
    return result.output
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sorter.py tests/test_sorter.py
git commit -m "feat(sorter): categorize() via pydantic-ai Agent"
```

---

### Task 14: Sorter — response validation

**Files:**
- Modify: `app/sorter.py`
- Modify: `tests/test_sorter.py`

- [ ] **Step 1: Add failing tests**

Append:
```python
from app.sorter import validate_assignments


def test_validate_assignments_drops_invalid_category():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="1", category_name="Fruit"),
        Assignment(item_id="2", category_name="MadeUp"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit", "Vegetables"], requested_ids={"1", "2"},
    )
    assert len(valid) == 1
    assert valid[0].item_id == "1"


def test_validate_assignments_drops_unknown_item():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="99", category_name="Fruit"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit"], requested_ids={"1"},
    )
    assert valid == []


def test_validate_assignments_deduplicates_item_id():
    raw = CategorizedItems(assignments=[
        Assignment(item_id="1", category_name="Fruit"),
        Assignment(item_id="1", category_name="Vegetables"),
    ])
    valid = validate_assignments(
        raw, categories=["Fruit", "Vegetables"], requested_ids={"1"},
    )
    assert len(valid) == 1
    assert valid[0].category_name == "Fruit"
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/sorter.py`:
```python
def validate_assignments(
    result: CategorizedItems,
    *,
    categories: list[str],
    requested_ids: set[str],
) -> list[Assignment]:
    cat_set = set(categories)
    seen: set[str] = set()
    valid: list[Assignment] = []
    for a in result.assignments:
        if a.item_id not in requested_ids:
            continue
        if a.item_id in seen:
            continue
        if a.category_name not in cat_set:
            continue
        seen.add(a.item_id)
        valid.append(a)
    return valid
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sorter.py tests/test_sorter.py
git commit -m "feat(sorter): validate LLM assignment responses"
```

---

### Task 15: Sorter — reorder computation

**Files:**
- Modify: `app/sorter.py`
- Modify: `tests/test_sorter.py`

- [ ] **Step 1: Add failing tests**

Append:
```python
from app.sorter import compute_reorder


def test_compute_reorder_groups_by_category_preserves_intra_order():
    tasks = [
        Task(id="T1", content="Milk"),
        Task(id="T2", content="Apples"),
        Task(id="T3", content="Yogurt"),
        Task(id="T4", content="Lettuce"),
    ]
    categories = ["🥬 Vegetables", "🍎 Fruit", "🥛 Dairy"]
    assignments = {
        "T1": "🥛 Dairy",
        "T2": "🍎 Fruit",
        "T3": "🥛 Dairy",
        "T4": "🥬 Vegetables",
    }

    ordered = compute_reorder(tasks, categories, assignments)

    assert ordered == ["T4", "T2", "T1", "T3"]


def test_compute_reorder_orphans_go_to_end():
    tasks = [
        Task(id="T1", content="Cinnamon"),
        Task(id="T2", content="Apples"),
    ]
    ordered = compute_reorder(
        tasks, ["🍎 Fruit"], {"T2": "🍎 Fruit"},
    )
    assert ordered == ["T2", "T1"]
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/sorter.py`:
```python
def compute_reorder(
    tasks: list[Task],
    categories: list[str],
    assignments: dict[str, str],
) -> list[str]:
    cat_index = {name: i for i, name in enumerate(categories)}
    orphan_index = len(categories)
    positioned = []
    for pos, t in enumerate(tasks):
        cat_name = assignments.get(t.id)
        idx = cat_index.get(cat_name, orphan_index) if cat_name else orphan_index
        positioned.append((idx, pos, t.id))
    positioned.sort(key=lambda x: (x[0], x[1]))
    return [tid for _, _, tid in positioned]
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sorter.py tests/test_sorter.py
git commit -m "feat(sorter): deterministic reorder computation"
```

---

### Task 16: Sorter — `sort_project` pipeline

**Files:**
- Modify: `app/sorter.py`
- Modify: `tests/test_sorter.py`

`sort_project` signature accepts an `on_reorder` callback so loop-prevention is wired externally (Task 18 will provide it).

- [ ] **Step 1: Add failing tests**

Append:
```python
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlmodel import Session

from app.models import CategoryCache, SortingProject
from app.sorter import sort_project


@pytest.mark.asyncio
async def test_sort_project_all_hits_skips_llm(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🥬 Vegetables", "🍎 Fruit"],
    ))
    session.add(CategoryCache(project_id=pid, content_key="apples",
                              category_name="🍎 Fruit"))
    session.add(CategoryCache(project_id=pid, content_key="lettuce",
                              category_name="🥬 Vegetables"))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Apples"),
        Task(id="T2", content="Lettuce"),
    ])
    backend.reorder = AsyncMock()

    async def _spy(**_):
        raise AssertionError("LLM should not be called")

    reorder_callback_calls: list[tuple] = []

    def _on_reorder(pid_, ids):
        reorder_callback_calls.append((pid_, set(ids)))

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_spy, on_reorder=_on_reorder,
    )

    args = backend.reorder.await_args.args
    assert args[1] == ["T2", "T1"]
    assert reorder_callback_calls == [(pid, {"T1", "T2"})]


@pytest.mark.asyncio
async def test_sort_project_partial_miss_calls_llm_and_writes_cache(session: Session):
    pid = uuid4()
    session.add(SortingProject(
        id=pid, name="Lidl", provider="todoist",
        external_project_id="999",
        categories=["🥬 Vegetables", "🍎 Fruit"],
    ))
    session.add(CategoryCache(project_id=pid, content_key="apples",
                              category_name="🍎 Fruit"))
    session.commit()

    backend = MagicMock()
    backend.get_tasks = AsyncMock(return_value=[
        Task(id="T1", content="Apples"),
        Task(id="T2", content="Cinnamon"),
    ])
    backend.reorder = AsyncMock()

    async def _llm(**kw):
        assert {t.id for t in kw["misses"]} == {"T2"}
        return CategorizedItems(assignments=[
            Assignment(item_id="T2", category_name="🍎 Fruit"),
        ])

    await sort_project(
        project_id=pid, session=session,
        backend=backend, llm_model="x",
        categorize_fn=_llm, on_reorder=lambda p, ids: None,
    )

    cinnamon = session.get(CategoryCache, (pid, "cinnamon"))
    assert cinnamon is not None
    assert cinnamon.category_name == "🍎 Fruit"
    backend.reorder.assert_awaited_once()
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `app/sorter.py`:
```python
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models import CategoryCache, SortingProject
from app.normalize import content_key as _content_key

log = logging.getLogger(__name__)


CategorizeFn = Callable[..., Awaitable[CategorizedItems]]
ReorderCallback = Callable[[UUID, list[str]], None]


async def sort_project(
    *,
    project_id: UUID,
    session: Session,
    backend: Any,
    llm_model: Any,
    categorize_fn: CategorizeFn = categorize,
    on_reorder: ReorderCallback = lambda _pid, _ids: None,
) -> None:
    project = session.get(SortingProject, project_id)
    if not project or not project.enabled:
        return

    tasks = await backend.get_tasks(project)
    if len(tasks) < 2:
        return

    keys = {t.id: _content_key(t.content) for t in tasks}
    cache_rows = session.exec(
        select(CategoryCache).where(CategoryCache.project_id == project_id)
    ).all()
    cached = {c.content_key: c.category_name for c in cache_rows}

    assignments: dict[str, str] = {}
    hit_contents: dict[str, str] = {}
    misses: list[Task] = []
    for t in tasks:
        k = keys[t.id]
        if k in cached:
            assignments[t.id] = cached[k]
            hit_contents[t.content] = cached[k]
        else:
            misses.append(t)

    if misses:
        try:
            result = await categorize_fn(
                model=llm_model,
                categories=project.categories,
                description=project.description,
                hits=hit_contents,
                misses=misses,
            )
        except Exception:
            log.exception("LLM categorization failed for project %s", project_id)
            return
        valid = validate_assignments(
            result,
            categories=project.categories,
            requested_ids={m.id for m in misses},
        )
        for a in valid:
            assignments[a.item_id] = a.category_name
            miss_content = next(m.content for m in misses if m.id == a.item_id)
            _upsert_cache(session, project_id,
                          _content_key(miss_content), a.category_name)
        session.commit()

    current = await backend.get_tasks(project)
    current_ids = {t.id for t in current}
    ordered = [
        tid for tid in compute_reorder(current, project.categories, assignments)
        if tid in current_ids
    ]
    if len(ordered) < 2:
        return
    await backend.reorder(project, ordered)
    on_reorder(project_id, ordered)


def _upsert_cache(
    session: Session, project_id: UUID, ckey: str, category_name: str
) -> None:
    existing = session.get(CategoryCache, (project_id, ckey))
    if existing:
        if existing.category_name != category_name:
            existing.category_name = category_name
            existing.updated_at = datetime.now(timezone.utc)
        return
    session.add(CategoryCache(
        project_id=project_id, content_key=ckey, category_name=category_name,
    ))
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sorter.py tests/test_sorter.py
git commit -m "feat(sorter): cache-aware sort_project pipeline with reorder callback"
```

---

### Task 17: Debouncer — leading + trailing edge + lock

**Files:**
- Create: `app/debouncer.py`
- Create: `tests/test_debouncer.py`

- [ ] **Step 1: Write failing tests**

`tests/test_debouncer.py`:
```python
import asyncio
from uuid import UUID, uuid4

import pytest

from app.debouncer import ProjectDebouncer


@pytest.mark.asyncio
async def test_leading_edge_fires_immediately():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.1)
    await asyncio.sleep(0.01)
    assert fired == [pid]


@pytest.mark.asyncio
async def test_trailing_edge_collapses_burst():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.05)
    await asyncio.sleep(0.01)
    for _ in range(5):
        await d.touch(pid, delay=0.05)
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_fire_now_bypasses_delay():
    pid = uuid4()
    fired: list[UUID] = []

    async def runner(p):
        fired.append(p)

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0.05)
    await asyncio.sleep(0.01)
    await d.fire_now(pid)
    await asyncio.sleep(0.02)
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_lock_serializes_per_project():
    pid = uuid4()
    starts: list[float] = []
    ends: list[float] = []

    async def runner(_):
        starts.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        ends.append(asyncio.get_event_loop().time())

    d = ProjectDebouncer(runner)
    await d.touch(pid, delay=0)
    await asyncio.sleep(0.005)
    await d.fire_now(pid)
    await asyncio.sleep(0.2)
    assert starts[1] >= ends[0]
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/debouncer.py`:
```python
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID


RunFn = Callable[[UUID], Awaitable[None]]


@dataclass
class _PerProjectState:
    last_event_at: float | None = None
    pending: asyncio.Task | None = None
    sort_running: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProjectDebouncer:
    def __init__(self, run_fn: RunFn) -> None:
        self._run = run_fn
        self._state: dict[UUID, _PerProjectState] = {}

    def _get(self, project_id: UUID) -> _PerProjectState:
        st = self._state.get(project_id)
        if st is None:
            st = _PerProjectState()
            self._state[project_id] = st
        return st

    async def touch(self, project_id: UUID, delay: float = 5.0) -> None:
        st = self._get(project_id)
        now = time.monotonic()
        last = st.last_event_at
        st.last_event_at = now

        if last is None or (now - last) > delay:
            self._cancel_if_sleeping(st)
            st.pending = asyncio.create_task(self._run_after(project_id, 0))
        else:
            self._cancel_if_sleeping(st)
            st.pending = asyncio.create_task(self._run_after(project_id, delay))

    async def fire_now(self, project_id: UUID) -> None:
        st = self._get(project_id)
        st.last_event_at = time.monotonic()
        self._cancel_if_sleeping(st)
        st.pending = asyncio.create_task(self._run_after(project_id, 0))

    def _cancel_if_sleeping(self, st: _PerProjectState) -> None:
        if st.pending is not None and not st.sort_running and not st.pending.done():
            st.pending.cancel()

    async def _run_after(self, project_id: UUID, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        st = self._get(project_id)
        async with st.lock:
            st.sort_running = True
            try:
                await self._run(project_id)
            finally:
                st.sort_running = False
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/debouncer.py tests/test_debouncer.py
git commit -m "feat(debouncer): leading+trailing edge, fire_now, per-project lock"
```

---

### Task 18: Loop prevention — SuppressionTracker

**Files:**
- Create: `app/suppression.py`
- Create: `tests/test_suppression.py`

- [ ] **Step 1: Write failing tests**

`tests/test_suppression.py`:
```python
import time
from uuid import uuid4

from app.suppression import SuppressionTracker


def test_unmarked_project_is_not_suppressed():
    t = SuppressionTracker()
    assert t.is_suppressed(uuid4(), "42") is False


def test_marked_ids_are_suppressed_within_window():
    t = SuppressionTracker()
    pid = uuid4()
    t.mark(pid, ["1", "2", "3"], window_seconds=1.0)
    assert t.is_suppressed(pid, "1") is True
    assert t.is_suppressed(pid, "2") is True
    assert t.is_suppressed(pid, "99") is False


def test_expires_after_window():
    t = SuppressionTracker(clock=lambda: 1000.0)
    pid = uuid4()
    t.mark(pid, ["1"], window_seconds=0.5)
    # Move the clock past the deadline
    t._clock = lambda: 1001.0  # type: ignore[assignment]
    assert t.is_suppressed(pid, "1") is False


def test_remark_replaces_previous_set():
    t = SuppressionTracker()
    pid = uuid4()
    t.mark(pid, ["1"], window_seconds=60)
    t.mark(pid, ["2"], window_seconds=60)
    assert t.is_suppressed(pid, "1") is False
    assert t.is_suppressed(pid, "2") is True
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/suppression.py`:
```python
import time
from collections.abc import Callable, Iterable
from uuid import UUID


class SuppressionTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[UUID, tuple[frozenset[str], float]] = {}

    def mark(
        self, project_id: UUID, item_ids: Iterable[str], window_seconds: float
    ) -> None:
        deadline = self._clock() + window_seconds
        self._entries[project_id] = (frozenset(item_ids), deadline)

    def is_suppressed(self, project_id: UUID, item_id: str) -> bool:
        entry = self._entries.get(project_id)
        if entry is None:
            return False
        ids, deadline = entry
        if self._clock() >= deadline:
            self._entries.pop(project_id, None)
            return False
        return item_id in ids
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/suppression.py tests/test_suppression.py
git commit -m "feat(suppression): SuppressionTracker for reorder-echo drop"
```

---

### Task 19: API-key auth dependency

**Files:**
- Create: `app/routes/__init__.py` (empty)
- Create: `app/routes/deps.py`
- Create: `tests/test_auth_dep.py`

- [ ] **Step 1: Create package init**

Empty `app/routes/__init__.py`.

- [ ] **Step 2: Write failing test**

`tests/test_auth_dep.py`:
```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.routes.deps import require_api_key


def _app(expected: str) -> FastAPI:
    app = FastAPI()

    @app.get("/guarded", dependencies=[Depends(require_api_key(expected))])
    def _g():
        return {"ok": True}

    return app


def test_missing_key_rejected():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded").status_code == 401


def test_wrong_key_rejected():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded", headers={"X-API-Key": "wrong"}).status_code == 401


def test_correct_key_accepted():
    c = TestClient(_app("s3cret"))
    assert c.get("/guarded", headers={"X-API-Key": "s3cret"}).status_code == 200
```

- [ ] **Step 3: Run — should fail**

Expected: FAIL.

- [ ] **Step 4: Implement**

`app/routes/deps.py`:
```python
from fastapi import Header, HTTPException, status


def require_api_key(expected: str):
    async def _dep(x_api_key: str | None = Header(default=None)):
        if x_api_key != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API key",
            )
    return _dep
```

- [ ] **Step 5: Run — should pass**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/__init__.py app/routes/deps.py tests/test_auth_dep.py
git commit -m "feat(routes): X-API-Key auth dependency"
```

---

### Task 20: Projects CRUD routes

**Files:**
- Create: `app/routes/projects.py` (partial)
- Modify: `tests/conftest.py`
- Create: `tests/test_routes_projects.py`

- [ ] **Step 1: Extend `tests/conftest.py`**

Append:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine


@pytest.fixture()
def sort_trigger_spy():
    calls: list = []

    def spy(pid):
        calls.append(pid)

    return calls, spy


@pytest.fixture()
def app_with_projects(engine: Engine, sort_trigger_spy):
    from app.db import get_session
    from app.routes.projects import build_router

    _, spy = sort_trigger_spy
    app = FastAPI()
    app.include_router(build_router(
        api_key="testkey",
        session_dep=lambda: get_session(engine),
        on_sort_requested=spy,
    ))
    return app


@pytest.fixture()
def client(app_with_projects):
    return TestClient(app_with_projects)
```

- [ ] **Step 2: Write failing tests**

`tests/test_routes_projects.py`:
```python
AUTH = {"X-API-Key": "testkey"}


def test_create_project(client):
    r = client.post("/projects", json={
        "name": "Lidl", "provider": "todoist",
        "external_project_id": "999",
        "categories": ["🥬 Vegetables", "🍎 Fruit"],
    }, headers=AUTH)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Lidl"
    assert "id" in body


def test_list_projects(client):
    client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH)
    r = client.get("/projects", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_get_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.get(f"/projects/{p['id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["name"] == "A"


def test_update_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.put(f"/projects/{p['id']}", json={
        "name": "B", "enabled": False,
        "debounce_seconds": 10, "description": "new",
    }, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "B"
    assert body["enabled"] is False
    assert body["debounce_seconds"] == 10
    assert body["description"] == "new"


def test_delete_project(client):
    p = client.post("/projects", json={
        "name": "A", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    assert client.delete(f"/projects/{p['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/projects/{p['id']}", headers=AUTH).status_code == 404


def test_auth_required(client):
    assert client.get("/projects").status_code == 401
```

- [ ] **Step 3: Run — should fail**

Expected: FAIL.

- [ ] **Step 4: Implement**

`app/routes/projects.py`:
```python
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.models import SortingProject
from app.routes.deps import require_api_key


SortTrigger = Callable[[UUID], None]


class ProjectCreate(BaseModel):
    name: str
    provider: str
    external_project_id: str
    categories: list[str] = []
    description: str | None = None
    debounce_seconds: int = 5


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    debounce_seconds: int | None = None


class ProjectOut(BaseModel):
    id: UUID
    name: str
    provider: str
    external_project_id: str
    categories: list[str]
    description: str | None
    enabled: bool
    debounce_seconds: int


def _out(p: SortingProject) -> ProjectOut:
    return ProjectOut(
        id=p.id, name=p.name, provider=p.provider,
        external_project_id=p.external_project_id,
        categories=p.categories, description=p.description,
        enabled=p.enabled, debounce_seconds=p.debounce_seconds,
    )


def build_router(
    *,
    api_key: str,
    session_dep: Callable[[], Iterator[Session]],
    on_sort_requested: SortTrigger = lambda _pid: None,
) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["projects"],
                       dependencies=[Depends(require_api_key(api_key))])

    @router.post("", response_model=ProjectOut,
                 status_code=status.HTTP_201_CREATED)
    def create(body: ProjectCreate, s: Session = Depends(session_dep)):
        p = SortingProject(
            name=body.name, provider=body.provider,
            external_project_id=body.external_project_id,
            categories=body.categories, description=body.description,
            debounce_seconds=body.debounce_seconds,
        )
        s.add(p); s.commit(); s.refresh(p)
        return _out(p)

    @router.get("", response_model=list[ProjectOut])
    def list_(s: Session = Depends(session_dep)):
        return [_out(p) for p in s.exec(select(SortingProject)).all()]

    @router.get("/{pid}", response_model=ProjectOut)
    def get(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return _out(p)

    @router.put("/{pid}", response_model=ProjectOut)
    def update(pid: UUID, body: ProjectUpdate,
               s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(p, k, v)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p); s.commit(); s.refresh(p)
        return _out(p)

    @router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
    def delete(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        s.delete(p); s.commit()

    return router
```

- [ ] **Step 5: Run — should pass**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/projects.py tests/conftest.py tests/test_routes_projects.py
git commit -m "feat(routes): project CRUD endpoints"
```

---

### Task 21: Category management + cache invalidation routes

**Files:**
- Modify: `app/routes/projects.py`
- Create: `tests/test_routes_categories.py`

- [ ] **Step 1: Write failing tests**

`tests/test_routes_categories.py`:
```python
from uuid import UUID

from sqlmodel import Session, select

from app.models import CategoryCache

AUTH = {"X-API-Key": "testkey"}


def _create(client, cats):
    return client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": cats,
    }, headers=AUTH).json()


def _seed(session, pid, entries):
    pid = UUID(pid) if isinstance(pid, str) else pid
    for ckey, cat in entries:
        session.add(CategoryCache(project_id=pid, content_key=ckey,
                                  category_name=cat))
    session.commit()


def test_list_categories(client):
    p = _create(client, ["A", "B"])
    r = client.get(f"/projects/{p['id']}/categories", headers=AUTH)
    assert r.json() == ["A", "B"]


def test_add_clears_cache_and_triggers_sort(
    client, engine, sort_trigger_spy
):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.post(f"/projects/{p['id']}/categories",
                    json={"name": "C"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["A", "B", "C"]

    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []
    assert len(calls) == 1


def test_add_at_index(client):
    p = _create(client, ["A", "B"])
    r = client.post(f"/projects/{p['id']}/categories",
                    json={"name": "X", "at_index": 0}, headers=AUTH)
    assert r.json() == ["X", "A", "B"]


def test_remove_partial_invalidation(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.delete(f"/projects/{p['id']}/categories/0", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["B"]

    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        assert [row.category_name for row in rows] == ["B"]
    assert len(calls) == 1


def test_rename_clears_full_cache(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.patch(f"/projects/{p['id']}/categories/0",
                     json={"name": "AAA"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["AAA", "B"]

    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []
    assert len(calls) == 1


def test_reorder_only_keeps_cache(client, engine, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = _create(client, ["A", "B", "C"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A")])

    r = client.patch(f"/projects/{p['id']}/categories/0",
                     json={"move_to": 2}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == ["B", "C", "A"]

    with Session(engine) as s:
        assert len(s.exec(select(CategoryCache)).all()) == 1
    assert len(calls) == 1


def test_replace_with_add_clears_cache(client, engine):
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A")])

    r = client.put(f"/projects/{p['id']}/categories",
                   json={"categories": ["A", "B", "C"]}, headers=AUTH)
    assert r.status_code == 200
    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []


def test_replace_only_removes_deletes_cache_of_removed(client, engine):
    p = _create(client, ["A", "B"])
    with Session(engine) as s:
        _seed(s, p["id"], [("apple", "A"), ("lettuce", "B")])

    r = client.put(f"/projects/{p['id']}/categories",
                   json={"categories": ["A"]}, headers=AUTH)
    assert r.status_code == 200
    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        assert [r.category_name for r in rows] == ["A"]
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Add endpoints to `app/routes/projects.py`**

Inside `build_router`, after CRUD:
```python
    from pydantic import BaseModel as _BM

    class CategoryAdd(_BM):
        name: str
        at_index: int | None = None

    class CategoryPatch(_BM):
        name: str | None = None
        move_to: int | None = None

    class CategoriesReplace(_BM):
        categories: list[str]

    def _clear_cache(s: Session, pid: UUID) -> None:
        from app.models import CategoryCache
        for row in s.exec(
            select(CategoryCache).where(CategoryCache.project_id == pid)
        ).all():
            s.delete(row)

    def _clear_for_category(s: Session, pid: UUID, name: str) -> None:
        from app.models import CategoryCache
        for row in s.exec(
            select(CategoryCache).where(
                CategoryCache.project_id == pid,
                CategoryCache.category_name == name,
            )
        ).all():
            s.delete(row)

    @router.get("/{pid}/categories", response_model=list[str])
    def list_categories(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        return p.categories

    @router.put("/{pid}/categories", response_model=list[str])
    def replace_categories(
        pid: UUID, body: CategoriesReplace,
        s: Session = Depends(session_dep),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        old = set(p.categories)
        new = set(body.categories)
        if new - old:
            _clear_cache(s, pid)
        else:
            for removed in old - new:
                _clear_for_category(s, pid, removed)
        p.categories = list(body.categories)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.post("/{pid}/categories", response_model=list[str])
    def add_category(
        pid: UUID, body: CategoryAdd, s: Session = Depends(session_dep),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        idx = body.at_index if body.at_index is not None else len(cats)
        if idx < 0 or idx > len(cats):
            raise HTTPException(422, "at_index out of range")
        cats.insert(idx, body.name)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        _clear_cache(s, pid)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.delete("/{pid}/categories/{index}", response_model=list[str])
    def remove_category(
        pid: UUID, index: int, s: Session = Depends(session_dep),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        if index < 0 or index >= len(cats):
            raise HTTPException(422, "index out of range")
        removed = cats.pop(index)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        _clear_for_category(s, pid, removed)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories

    @router.patch("/{pid}/categories/{index}", response_model=list[str])
    def patch_category(
        pid: UUID, index: int, body: CategoryPatch,
        s: Session = Depends(session_dep),
    ):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        cats = list(p.categories)
        if index < 0 or index >= len(cats):
            raise HTTPException(422, "index out of range")
        renamed = body.name is not None and body.name != cats[index]
        if renamed:
            cats[index] = body.name
        if body.move_to is not None:
            target = body.move_to
            if target < 0 or target >= len(cats):
                raise HTTPException(422, "move_to out of range")
            item = cats.pop(index)
            cats.insert(target, item)
        p.categories = cats
        p.updated_at = datetime.now(timezone.utc)
        if renamed:
            _clear_cache(s, pid)
        s.add(p); s.commit(); s.refresh(p)
        on_sort_requested(pid)
        return p.categories
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/projects.py tests/test_routes_categories.py
git commit -m "feat(routes): category management with cache invalidation matrix"
```

---

### Task 22: Cache inspection + manual sort endpoints

**Files:**
- Modify: `app/routes/projects.py`
- Create: `tests/test_routes_cache.py`

- [ ] **Step 1: Write failing tests**

`tests/test_routes_cache.py`:
```python
from sqlmodel import Session, select

from app.models import CategoryCache

AUTH = {"X-API-Key": "testkey"}


def _create_seeded(client, engine):
    p = client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": ["A"],
    }, headers=AUTH).json()
    with Session(engine) as s:
        s.add(CategoryCache(project_id=p["id"], content_key="apple",
                            category_name="A"))
        s.commit()
    return p


def test_get_cache(client, engine):
    p = _create_seeded(client, engine)
    r = client.get(f"/projects/{p['id']}/cache", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == [{"content_key": "apple", "category_name": "A"}]


def test_clear_cache(client, engine):
    p = _create_seeded(client, engine)
    r = client.delete(f"/projects/{p['id']}/cache", headers=AUTH)
    assert r.status_code == 204
    with Session(engine) as s:
        assert s.exec(select(CategoryCache)).all() == []


def test_manual_sort_triggers_callback(client, sort_trigger_spy):
    calls, _ = sort_trigger_spy
    p = client.post("/projects", json={
        "name": "P", "provider": "todoist",
        "external_project_id": "1", "categories": [],
    }, headers=AUTH).json()
    r = client.post(f"/projects/{p['id']}/sort", headers=AUTH)
    assert r.status_code == 202
    assert len(calls) == 1
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Add endpoints to `app/routes/projects.py`**

Inside `build_router`, append:
```python
    @router.get("/{pid}/cache")
    def get_cache(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        from app.models import CategoryCache
        rows = s.exec(
            select(CategoryCache).where(CategoryCache.project_id == pid)
        ).all()
        return [{"content_key": r.content_key,
                 "category_name": r.category_name} for r in rows]

    @router.delete("/{pid}/cache", status_code=status.HTTP_204_NO_CONTENT)
    def clear_cache(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        _clear_cache(s, pid)
        s.commit()

    @router.post("/{pid}/sort", status_code=status.HTTP_202_ACCEPTED)
    def trigger_sort(pid: UUID, s: Session = Depends(session_dep)):
        p = s.get(SortingProject, pid)
        if not p:
            raise HTTPException(404)
        on_sort_requested(pid)
        return {"status": "queued"}
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/projects.py tests/test_routes_cache.py
git commit -m "feat(routes): cache inspection + manual sort trigger"
```

---

### Task 23: Webhook route with suppression + fast-path

**Files:**
- Create: `app/routes/webhook.py`
- Create: `tests/test_routes_webhook.py`

- [ ] **Step 1: Write failing tests**

`tests/test_routes_webhook.py`:
```python
import base64
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.db import get_session
from app.models import CategoryCache, SortingProject
from app.routes.webhook import build_webhook_router
from app.suppression import SuppressionTracker


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest.fixture()
def webhook_ctx(engine):
    touches: list[tuple] = []

    class FakeDebouncer:
        async def touch(self, pid, delay=5.0):
            touches.append(("touch", pid))
        async def fire_now(self, pid):
            touches.append(("fire_now", pid))

    registry = BackendRegistry()
    registry.register(TodoistBackend(api_token="t", client_secret="webhook-secret"))
    suppression = SuppressionTracker()

    app = FastAPI()
    app.include_router(build_webhook_router(
        registry=registry,
        debouncer=FakeDebouncer(),
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=5.0,
    ))
    return TestClient(app), touches, suppression


def _create_project(engine, external_id="999"):
    pid = uuid4()
    with Session(engine) as s:
        s.add(SortingProject(
            id=pid, name="Lidl", provider="todoist",
            external_project_id=external_id,
            categories=["🍎 Fruit"],
        ))
        s.commit()
    return pid


def _post(client, body: bytes, sig: str):
    return client.post(
        "/webhook/todoist", content=body,
        headers={"X-Todoist-Hmac-SHA256": sig,
                 "Content-Type": "application/json"},
    )


def test_rejects_invalid_signature(webhook_ctx):
    client, _, _ = webhook_ctx
    r = client.post("/webhook/todoist", content=b"{}",
                    headers={"X-Todoist-Hmac-SHA256": "wrong"})
    assert r.status_code == 401


def test_unknown_provider_404(webhook_ctx):
    client, _, _ = webhook_ctx
    r = client.post("/webhook/unknown", content=b"{}")
    assert r.status_code == 404


def test_ignores_unknown_project(webhook_ctx):
    client, touches, _ = webhook_ctx
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Apples", "project_id": "nope"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}
    assert touches == []


def test_cache_miss_touches_debouncer(webhook_ctx, engine):
    client, touches, _ = webhook_ctx
    pid = _create_project(engine)
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Cinnamon", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("touch", pid)]


def test_cache_hit_fires_now(webhook_ctx, engine):
    client, touches, _ = webhook_ctx
    pid = _create_project(engine)
    with Session(engine) as s:
        s.add(CategoryCache(project_id=pid, content_key="apples",
                            category_name="🍎 Fruit"))
        s.commit()
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("fire_now", pid)]


def test_suppressed_echo_is_dropped(webhook_ctx, engine):
    client, touches, suppression = webhook_ctx
    pid = _create_project(engine)
    suppression.mark(pid, ["T1"], window_seconds=60)
    body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert r.json() == {"status": "suppressed"}
    assert touches == []


def test_item_added_not_suppressed(webhook_ctx, engine):
    client, touches, suppression = webhook_ctx
    pid = _create_project(engine)
    suppression.mark(pid, ["T1"], window_seconds=60)
    body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "T1", "content": "Apples", "project_id": "999"},
    }).encode()
    r = _post(client, body, _sign("webhook-secret", body))
    assert r.status_code == 200
    assert touches == [("touch", pid)]
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/routes/webhook.py`:
```python
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.backends.registry import BackendRegistry
from app.models import CategoryCache, SortingProject
from app.normalize import content_key
from app.suppression import SuppressionTracker

log = logging.getLogger(__name__)


def build_webhook_router(
    *,
    registry: BackendRegistry,
    debouncer: Any,
    suppression: SuppressionTracker,
    session_dep: Callable[[], Iterator[Session]],
    default_delay: float = 5.0,
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/{provider}")
    async def receive(
        provider: str,
        request: Request,
        s: Session = Depends(session_dep),
    ):
        try:
            backend = registry.get(provider)
        except KeyError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"unknown provider '{provider}'",
            )

        body = await request.body()
        if not backend.verify_webhook(dict(request.headers), body):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid signature",
            )

        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid JSON",
            )

        external_pid = backend.extract_project_id(payload)
        if external_pid is None:
            return {"status": "ignored"}

        project = s.exec(
            select(SortingProject).where(
                SortingProject.provider == provider,
                SortingProject.external_project_id == external_pid,
                SortingProject.enabled.is_(True),
            )
        ).first()
        if not project:
            return {"status": "ignored"}

        event_name = backend.extract_event_name(payload)
        item_id = backend.extract_item_id(payload)
        if event_name == "item:updated" and item_id is not None:
            if suppression.is_suppressed(project.id, item_id):
                return {"status": "suppressed"}

        trigger = backend.extract_trigger_content(payload)
        if trigger is not None:
            cached = s.get(CategoryCache, (project.id, content_key(trigger)))
            if cached is not None:
                await debouncer.fire_now(project.id)
                return {"status": "queued"}

        await debouncer.touch(
            project.id,
            delay=project.debounce_seconds or default_delay,
        )
        return {"status": "queued"}

    return router
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/webhook.py tests/test_routes_webhook.py
git commit -m "feat(routes): webhook with HMAC + suppression + cache fast-path"
```

---

### Task 24: Main FastAPI app + wiring

**Files:**
- Create: `app/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write failing test**

`tests/test_main.py`:
```python
from fastapi.testclient import TestClient


def test_healthz(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    monkeypatch.setenv("TODOIST_CLIENT_SECRET", "s")
    monkeypatch.setenv("TODOIST_API_TOKEN", "t")
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-sonnet-4-6")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("APP_API_KEY", "app")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
```

- [ ] **Step 2: Run — should fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

`app/main.py`:
```python
import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from sqlmodel import Session

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.config import get_settings
from app.db import create_db_and_tables, get_session, make_engine
from app.debouncer import ProjectDebouncer
from app.models import SortingProject
from app.routes.projects import build_router as build_projects_router
from app.routes.webhook import build_webhook_router
from app.sorter import sort_project
from app.suppression import SuppressionTracker


def create_app() -> FastAPI:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)

    registry = BackendRegistry()
    registry.register(TodoistBackend(
        api_token=settings.todoist_api_token,
        client_secret=settings.todoist_client_secret,
    ))

    suppression = SuppressionTracker()

    def _on_reorder(pid: UUID, ids: list[str]) -> None:
        suppression.mark(
            pid, ids, window_seconds=settings.suppression_window_seconds,
        )

    async def _run_sort(project_id: UUID) -> None:
        with Session(engine) as s:
            project = s.get(SortingProject, project_id)
            if not project:
                return
            try:
                backend = registry.get(project.provider)
            except KeyError:
                return
            await sort_project(
                project_id=project_id, session=s,
                backend=backend, llm_model=settings.llm_model,
                on_reorder=_on_reorder,
            )

    debouncer = ProjectDebouncer(_run_sort)

    def _on_sort_requested(pid: UUID) -> None:
        asyncio.get_event_loop().create_task(
            debouncer.touch(pid, delay=settings.default_debounce_seconds)
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(title="Todolist Sorter", lifespan=lifespan)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    app.include_router(build_projects_router(
        api_key=settings.app_api_key,
        session_dep=lambda: get_session(engine),
        on_sort_requested=_on_sort_requested,
    ))
    app.include_router(build_webhook_router(
        registry=registry,
        debouncer=debouncer,
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=settings.default_debounce_seconds,
    ))
    return app
```

- [ ] **Step 4: Run — should pass**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: FastAPI app factory wiring all components"
```

---

### Task 25: End-to-end integration test

**Files:**
- Create: `tests/test_integration.py`

Verifies: webhook → debouncer → sort → mocked Todoist reorder, cache population, suppression marks prevent immediate echo re-trigger.

- [ ] **Step 1: Write test**

`tests/test_integration.py`:
```python
import asyncio
import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from sqlmodel import Session, select

from app.backends.registry import BackendRegistry
from app.backends.todoist import TodoistBackend
from app.db import create_db_and_tables, get_session, make_engine
from app.debouncer import ProjectDebouncer
from app.models import CategoryCache, SortingProject
from app.routes.webhook import build_webhook_router
from app.sorter import sort_project
from app.suppression import SuppressionTracker


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest.mark.asyncio
async def test_end_to_end_webhook_sort_flow():
    engine = make_engine("sqlite://")
    create_db_and_tables(engine)

    pid = uuid4()
    with Session(engine) as s:
        s.add(SortingProject(
            id=pid, name="Lidl", provider="todoist",
            external_project_id="999",
            categories=["🥬 Vegetables", "🍎 Fruit"],
            debounce_seconds=0,
        ))
        s.commit()

    registry = BackendRegistry()
    registry.register(TodoistBackend(
        api_token="test-token", client_secret="secret",
    ))
    suppression = SuppressionTracker()

    llm = TestModel(custom_output_args={
        "assignments": [
            {"item_id": "T1", "category_name": "🍎 Fruit"},
            {"item_id": "T2", "category_name": "🥬 Vegetables"},
        ]
    })

    def _on_reorder(pid_, ids):
        suppression.mark(pid_, ids, window_seconds=30)

    async def _run_sort(project_id):
        with Session(engine) as s:
            project = s.get(SortingProject, project_id)
            backend = registry.get(project.provider)
            await sort_project(
                project_id=project_id, session=s,
                backend=backend, llm_model=llm,
                on_reorder=_on_reorder,
            )

    debouncer = ProjectDebouncer(_run_sort)

    app = FastAPI()
    app.include_router(build_webhook_router(
        registry=registry, debouncer=debouncer,
        suppression=suppression,
        session_dep=lambda: get_session(engine),
        default_delay=0,
    ))

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.todoist.com/rest/v2/tasks").mock(
            return_value=httpx.Response(200, json=[
                {"id": "T1", "content": "Apples",
                 "project_id": "999", "order": 1},
                {"id": "T2", "content": "Lettuce",
                 "project_id": "999", "order": 2},
            ])
        )
        reorder_route = mock.post(
            "https://api.todoist.com/sync/v9/sync"
        ).mock(return_value=httpx.Response(200, json={"sync_status": {}}))

        body = json.dumps({
            "event_name": "item:added",
            "event_data": {"id": "T2", "content": "Lettuce",
                           "project_id": "999"},
        }).encode()
        sig = _sign("secret", body)

        with TestClient(app) as c:
            r = c.post("/webhook/todoist", content=body,
                       headers={"X-Todoist-Hmac-SHA256": sig,
                                "Content-Type": "application/json"})
            assert r.status_code == 200

        for _ in range(100):
            if reorder_route.called:
                break
            await asyncio.sleep(0.02)
        assert reorder_route.called

        form = parse_qs(reorder_route.calls.last.request.content.decode())
        commands = json.loads(form["commands"][0])
        items = commands[0]["args"]["items"]
        assert [it["id"] for it in items] == ["T2", "T1"]

    with Session(engine) as s:
        rows = s.exec(select(CategoryCache)).all()
        ck_to_cat = {r.content_key: r.category_name for r in rows}
        assert ck_to_cat == {"apples": "🍎 Fruit", "lettuce": "🥬 Vegetables"}

    # Suppression was marked: echo item:updated for T1/T2 would be dropped.
    assert suppression.is_suppressed(pid, "T1") is True
    assert suppression.is_suppressed(pid, "T2") is True
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_integration.py -v`
Expected: PASS (requires Tasks 1-24 complete).

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: end-to-end webhook → sort → reorder with suppression"
```

---

### Task 26: Dockerfile + docker-compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

RUN mkdir -p /app/data
EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    restart: unless-stopped
```

- [ ] **Step 3: (Optional) Verify build**

Run: `docker compose build`
Expected: succeeds. Skip if Docker is unavailable.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "chore: Dockerfile + docker-compose for deployment"
```

---

### Task 27: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: all green.

- [ ] **Step 2: Smoke-test the server**

Copy `.env.example` to `.env` with placeholder values, then:
Run: `uvicorn app.main:create_app --factory --port 8000 --reload`
Expected: starts; `curl http://localhost:8000/healthz` returns `{"status": "ok"}`.

- [ ] **Step 3: Tag**

```bash
git status   # should be clean
git tag v0.1.0
```
