# Todolist-Sorter — Design

**Datum:** 2026-04-18
**Status:** Draft

## Zweck

Ein FastAPI-Service, der eingehende Todoist-Webhooks verarbeitet und die Items eines konfigurierten Todoist-Projekts gemäss einer strukturierten Kategorienliste umsortiert. Kategorisierungs-Entscheidungen werden gecacht, sodass wiederkehrende Items ohne LLM-Call und ohne Debounce direkt einsortiert werden.

Beispielanwendung: Einkaufslisten so ordnen, dass sie der Laufroute durch den Supermarkt folgen (Lidl: Gemüse → Obst → Pilze → Brot → … → Drogerie → Sonstiges).

## Scope

**In Scope (MVP):**
- Todoist als einziger Task-Provider
- Webhook-basierter Trigger (Push)
- REST-API zur CRUD-Verwaltung von Sorting-Projekten und deren Kategorienlisten
- LLM-basierte Kategorisierung via pydantic-ai
- SQLite-Persistenz (Projekte + Category-Cache)
- Single-Instance Deployment

**Explizit nicht im Scope:**
- Weitere Provider (TickTick, Google Tasks, Microsoft To Do) — Architektur so vorbereitet, dass sie als neues Backend-Modul ergänzt werden können, ohne Kernkomponenten umzubauen. Keine Stub-Dateien oder Dead Code.
- Polling-Trigger — wird erst eingebaut, wenn ein Provider ohne Webhooks integriert wird
- Web-UI / Frontend — Verwaltung ausschliesslich über REST-API (`/docs` Swagger-UI reicht)
- Multi-User / Multi-Tenancy
- Multi-Instance Deployment / verteilte Koordination

## Architekturüberblick

```
app/
├── main.py                 FastAPI-App, Router-Wiring, Lifespan
├── config.py               pydantic-settings (ENV-basiert)
├── db.py                   SQLModel Engine + Session-Dependency
├── models.py               SortingProject, CategoryCache
├── sorter.py               pydantic-ai Agent, Prompt-Logik, Cache-Integration
├── debouncer.py            Per-Projekt Debouncer (Leading + Trailing Edge)
├── backends/
│   ├── base.py             TaskBackend Protocol
│   ├── registry.py         Provider-Name → Backend-Instanz
│   └── todoist.py          TodoistBackend (einzige Impl)
└── routes/
    ├── webhook.py          POST /webhook/{provider}
    └── projects.py         CRUD /projects + /projects/{id}/categories
```

## Konfiguration

Alle Credentials und Modell-Details werden via `pydantic-settings` aus Environment Variables gelesen:

| ENV-Var | Zweck |
|---------|-------|
| `TODOIST_CLIENT_SECRET` | HMAC-Verifikation eingehender Webhooks |
| `TODOIST_API_TOKEN` | Todoist REST API v2 Zugriff (Tasks lesen / reordern) |
| `LLM_MODEL` | pydantic-ai Model-Identifier (z.B. `anthropic:claude-sonnet-4-6`) |
| `LLM_API_KEY` | API-Key für den LLM-Provider |
| `APP_API_KEY` | Schutz der Management-Endpoints (`X-API-Key`-Header) |
| `DATABASE_URL` | SQLite-Pfad (Default: `sqlite:///./data/app.db`) |
| `DEFAULT_DEBOUNCE_SECONDS` | Fallback-Wert für neue Projekte (Default: `5`) |

## Data Model

```python
class SortingProject(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str                                  # "Lidl-Einkauf"
    provider: str                              # "todoist"
    external_project_id: str                   # Todoist project_id als String
    provider_config: dict = Field(             # flexibler JSON-Bucket
        default_factory=dict, sa_column=Column(JSON))
    categories: list[str] = Field(             # geordnete Kategorienliste
        sa_column=Column(JSON))                # z.B. ["🥬 Gemüse", "🍎 Obst", ...]
    description: str | None = None             # optionaler Freitext-Kontext für LLM
    debounce_seconds: int = 5
    enabled: bool = True
    created_at: datetime
    updated_at: datetime

    __table_args__ = (UniqueConstraint("provider", "external_project_id"),)


class CategoryCache(SQLModel, table=True):
    project_id: UUID = Field(foreign_key="sortingproject.id",
                             primary_key=True, ondelete="CASCADE")
    content_key: str = Field(primary_key=True)  # normalized Item-Text
    category_name: str                          # muss in project.categories sein (lazy validiert)
    created_at: datetime
    updated_at: datetime
```

Eindeutigkeit: `(provider, external_project_id)` für `SortingProject`, `(project_id, content_key)` für `CategoryCache`.

**Normalisierung** (`content_key`): `content.strip().lower()` mit kollabierten Whitespaces (`re.sub(r"\s+", " ", ...)`). Emojis und Umlaute bleiben erhalten.

## REST-API

Alle Management-Endpoints erfordern den `X-API-Key`-Header (`APP_API_KEY`).

### Projekt-CRUD

| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET` | `/projects` | Liste aller Sorting-Projekte |
| `POST` | `/projects` | Neues Projekt anlegen |
| `GET` | `/projects/{id}` | Einzelnes Projekt lesen |
| `PUT` | `/projects/{id}` | Projekt-Metadaten aktualisieren (Name, Beschreibung, `enabled`, `debounce_seconds`) |
| `DELETE` | `/projects/{id}` | Projekt entfernen (Cache wird via `ON DELETE CASCADE` entfernt) |
| `POST` | `/projects/{id}/sort` | Manuell Sortierung triggern (bypass Debounce) |

### Kategorien-Management

| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET` | `/projects/{id}/categories` | Geordnete Kategorienliste lesen |
| `PUT` | `/projects/{id}/categories` | Gesamte Liste atomar ersetzen: `{"categories": [...]}` |
| `POST` | `/projects/{id}/categories` | Kategorie hinzufügen: `{"name": "🧊 Tiefkühl", "at_index": 14}` (ohne `at_index` ans Ende) |
| `DELETE` | `/projects/{id}/categories/{index}` | Entfernen per 0-basiertem Index |
| `PATCH` | `/projects/{id}/categories/{index}` | Umbenennen/Verschieben: `{"name": "...", "move_to": 3}` — beide Felder optional |

Alle Kategorien-Modifikationen triggern automatisch einen Sort-Zyklus nach der Cache-Invalidations-Matrix (siehe unten). Der Trigger läuft am Projekt-Lock wie ein normaler Sort.

### Cache-Management

| Methode | Pfad | Zweck |
|---------|------|-------|
| `GET` | `/projects/{id}/cache` | Aktuelle Cache-Einträge lesen |
| `DELETE` | `/projects/{id}/cache` | Cache komplett leeren (erzwingt nächste LLM-Runde) |

### Webhook + Health

| Methode | Pfad | Zweck |
|---------|------|-------|
| `POST` | `/webhook/{provider}` | Webhook-Endpoint (keine API-Key-Auth; stattdessen provider-spezifische Signatur-Verifikation) |
| `GET` | `/healthz` | Liveness |

Swagger-UI unter `/docs` (FastAPI-Default).

## Webhook-Flow mit Cache-Fast-Path

1. `POST /webhook/todoist` mit Header `X-Todoist-Hmac-Sha256` und JSON-Body
2. `TodoistBackend.verify_webhook(headers, body)` prüft HMAC gegen `TODOIST_CLIENT_SECRET` → bei Mismatch: `401`
3. Payload parsen: `event_type`, `external_project_id`, ggf. `trigger_item` (Content des auslösenden Items, falls verfügbar)
4. DB-Lookup: `SortingProject` mit `(provider="todoist", external_project_id, enabled=True)` → wenn nichts gefunden: `200 {"status": "ignored"}`
5. **Fast-Path-Check**: Wenn `trigger_item.content` existiert und `CategoryCache`-Lookup mit `(project.id, normalize(trigger_item.content))` hit → direkt `Debouncer.fire_now(project.id)` (überspringt Debounce)
6. Sonst → `Debouncer.touch(project.id)` (Leading + Trailing Edge)
7. Response `200 {"status": "queued"}` sofort zurückgeben — Sortierung läuft asynchron

## Debouncer — Leading + Trailing Edge

**Ziel:** erste Änderung in einem ruhigen Zustand wird sofort verarbeitet; Bursts werden zusammengefasst.

Pro `SortingProject` wird im Prozess-Memory gehalten:

- `last_event_at: float | None` — Monotonic-Zeit des letzten `touch()`/`fire_now()`
- `pending_task: asyncio.Task | None` — geplanter Sort-Trigger
- `sort_in_progress: bool`
- `lock: asyncio.Lock` — serialisiert Sortierungen pro Projekt

**`touch(project_id)`:**

```
now = monotonic()
delta = (now - last_event_at) if last_event_at else infinity
last_event_at = now

if delta > debounce_seconds:
    # Leading Edge: sofort
    cancel_if_sleeping(pending_task)
    pending_task = create_task(run_sort(delay=0))
else:
    # Trailing Edge: debounce
    cancel_if_sleeping(pending_task)
    pending_task = create_task(run_sort(delay=debounce_seconds))
```

**`fire_now(project_id)`:** wie `touch`, aber immer `delay=0`, unabhängig von `delta`. Wird vom Webhook-Fast-Path aufgerufen.

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

**Cancel-Hygiene:** `cancel_if_sleeping` darf den Task nur abbrechen, solange er noch schläft (`sort_in_progress == False`). Läuft die Sortierung bereits, bleibt der alte Task unangetastet; das neue Event erzeugt einen zusätzlichen pending-Task, der am Lock wartet.

**Semantik:**
- Ruhiges Projekt, erstes Event → sofort (Leading Edge)
- Burst innerhalb `debounce_seconds` → ein Sort am Ende der Ruhe (Trailing Edge)
- Event mit Cache-Hit auf Trigger-Item → sofort, Debounce-Bypass
- Event während laufender Sortierung → neuer Sort-Task queuet am Lock

## Sort-Pipeline

`sort_project(project_id)`:

1. `SortingProject` aus DB laden; wenn `enabled=False` oder gelöscht → Abbruch
2. `backend = registry.get(project.provider)`
3. `tasks = await backend.get_tasks(project)` — Liste `Task(id, content)`. Wenn `len(tasks) < 2` → Abbruch
4. Für jedes Task `content_key = normalize(task.content)` bilden
5. Cache-Lookup für alle Tasks:
   ```
   hits   = {task.id: cached.category_name for cached in cache if content_key(task) in cache}
   misses = [task for task in tasks if task.id not in hits]
   ```
6. **Wenn `misses` leer** (alle Items gecached): kein LLM-Call. Sprung zu Schritt 9.
7. LLM-Call für `misses` mit Hybrid-Kontext-Prompt (siehe "LLM-Interaktion"). Liefert `CategorizedItems(assignments)`
8. LLM-Antwort validieren, `hits` mit LLM-Ergebnissen mergen, neue/geänderte `CategoryCache`-Einträge upserten
9. Reorder-Berechnung (deterministisch, serverseitig):
   - Für jedes Task: `category_index = project.categories.index(category_name_for(task))`
   - Items mit Kategorie ausserhalb der aktuellen Liste (orphan) → an das Ende sortieren (Index = `len(categories)`)
   - Stabiler Sort nach `(category_index, current_todoist_position)` → erhaltenes Intra-Kategorie-Ordering
10. **Snapshot-Validation vor Reorder**: erneuter `await backend.get_tasks(project)`; nur IDs die noch existieren in den Reorder-Payload aufnehmen. Zwischenzeitlich hinzugekommene Items sind nicht im Reorder-Call und behalten ihre Default-Position — ein Folge-Event wird sie einsortieren.
11. `await backend.reorder(project, reorder_payload)`

## LLM-Interaktion — Hybrid Misses + Context

Der LLM entscheidet **nur über die Misses**, bekommt aber bereits gecachte Zuordnungen als Kontext, um konsistent zu bleiben.

**Output-Schema:**
```python
class Assignment(BaseModel):
    item_id: str
    category_name: str    # muss exakt einer der project.categories sein

class CategorizedItems(BaseModel):
    assignments: list[Assignment]
```

**Prompt-Struktur:**
```
System: Du kategorisierst Einkaufslisten-Items in die gegebenen Kategorien.
        Antworte strikt im geforderten JSON-Schema. Wähle für jedes zu
        kategorisierende Item genau eine Kategorie aus der Liste. Erfinde
        keine Kategorien, ändere die Referenz-Zuordnungen nicht.

User: Kategorien (in dieser Reihenfolge):
        1. 🥬 Gemüse
        2. 🍎 Obst
        ...

      {description, falls gesetzt}

      Bereits zugeordnet (nur zur Orientierung, nicht ändern):
        - Äpfel → 🍎 Obst
        - Joghurt → 🥛 Milchprodukte
        ...

      Bitte kategorisieren:
        - id=abc123, content="Haferflocken"
        - id=def456, content="Zimt"
```

Wenn alle Items Misses sind (z.B. nach Category-Add mit Cache-Clear), entfällt der "Bereits zugeordnet"-Block.

**Validierung der LLM-Antwort:**
- Jede `assignment.item_id` muss in den angefragten Misses sein (keine erfundenen IDs)
- Jede `assignment.category_name` muss in `project.categories` sein (exakter String-Match). Invalide → Item wird als uncategorized behandelt (orphan, ans Ende).
- Keine Duplikate in `item_id`
- Fehlende IDs (LLM hat nicht alle Misses beantwortet): als orphan behandeln, nicht blocken
- Bei leerer oder ungültig-geparster Response → warnen, Reorder-Schritt skippen (nächstes Event triggert neu)

## Cache-Invalidierung bei Kategorien-Änderungen

| Aktion | Cache-Wirkung | Folge |
|--------|---------------|-------|
| **Add** category | Gesamter Cache des Projekts wird geleert | Automatisch getriggerter Full-Re-Sort → LLM re-kategorisiert alle Items. Rationale: die neue Kategorie könnte bestehende Items besser aufnehmen |
| **Rename** category (`PATCH` mit neuem `name`) | Gesamter Cache des Projekts wird geleert | Automatisch getriggerter Full-Re-Sort. Rationale: ein Rename kann die Semantik der Kategorie verändern, was auch Items anderer Kategorien neu verortet ("Obst" → "Früchte & Nüsse" zieht Nüsse an) |
| **Remove** category | Nur Cache-Entries mit `category_name == entfernter` werden gelöscht | Automatisch getriggerter Sort → LLM re-kategorisiert die betroffenen Items |
| **Reorder** only (`PATCH` mit `move_to`, ohne `name`) | Cache bleibt unangetastet | Sort triggern zum Reapply (kein LLM-Call, alles hit) |
| **Replace** full list (`PUT`) | Diff alt↔neu: wenn irgendein Add oder Rename enthalten ist → Full-Cache-Clear; sonst nur Remove-Einträge löschen | Ein einziger Sort-Zyklus |

Jeder dieser Sorts läuft über den normalen Projekt-Lock (at-most-one pro Projekt).

## Concurrency & Races

| Szenario | Behandlung |
|----------|------------|
| Neues Item während laufender Sortierung | Neuer Debounce-Timer wird gestartet; wartet am Projekt-Lock, startet danach einen frischen Durchgang, der das Item einschliesst |
| Item wird gelöscht/completed während LLM-Call | Snapshot-Validation vor Reorder (Schritt 10) filtert fehlende IDs heraus |
| Zwei überlappende Sort-Versuche pro Projekt | Durch `asyncio.Lock` unmöglich — at-most-one Sort pro Projekt |
| Kategorie wird während laufender Sortierung geändert | Config-Update wartet am Lock genauso wie ein normaler Sort; nach aktueller Runde wird die Invalidierung angewendet und neuer Sort ausgelöst |
| User reordnet manuell im Todoist während Sort | By design überschreibbar — in einem Sorting-Projekt ist der LLM die Source-of-Truth für Reihenfolge |
| Webhook-Retry (gleicher Event zweimal) | Idempotent: gleicher Input → gleicher Sort |
| Cache-Hit-Fast-Path doppelt ausgelöst | Beide `fire_now`-Calls queuen am Lock; zweiter sieht bereits sortierten Stand, Reorder ist idempotent |
| Crash während Sort | In-Flight-State ist In-Memory; DB bleibt konsistent; nächster Webhook-Event startet frischen Sort |
| Distributed Race (mehrere Instanzen) | **Out of Scope** — Single-Instance Deployment ist Grundannahme |

**Garantien:**
- **At-most-one** Sortierung pro Projekt gleichzeitig
- **Eventual consistency**: jedes Event führt garantiert zu mindestens einer nachfolgenden Sortierung, die es sieht
- **Idempotenz**: wiederholte Events oder Retries führen nicht zu abweichendem Endzustand

## Fehlerbehandlung

| Fehler | Reaktion |
|--------|----------|
| HMAC-Signatur ungültig | `401`, kein Secret-Leak in Logs |
| Unbekanntes Todoist-Projekt | `200 {"status": "ignored"}` |
| Unbekannter `provider` in URL | `404` |
| `APP_API_KEY` fehlt/falsch | `401` |
| Todoist API 5xx beim Fetch/Reorder | Retry mit Exponential Backoff (3 Versuche, Basis 1s); danach warnen und aufgeben |
| Todoist API 4xx (ausser 429) | Fehler loggen, Sort-Zyklus abbrechen |
| Todoist API 429 | `Retry-After` respektieren |
| LLM-Timeout / -Fehler | Warnen, Sort-Zyklus abbrechen (nächstes Event triggert neu) |
| LLM-Response-Validierung schlägt fehl | Warnen, Reorder skippen; keine Cache-Writes |
| Ungültiger Category-Name im LLM-Output | Item als orphan behandeln, ans Ende sortieren, Cache-Entry nicht schreiben |
| Category-Index out-of-range (API) | `422` |

## Tests

**Framework:** `pytest` + `pytest-asyncio` + `httpx.AsyncClient` + `respx` für HTTP-Mocks.

**Abdeckung:**
- `TodoistBackend`: Mock HTTP — Fetch, Reorder, HMAC-Verify (positiv + negativ), Payload-Parsing für verschiedene Event-Typen
- `sorter`: `pydantic-ai` `TestModel`/`FunctionModel` für deterministische LLM-Stubs; Validierungslogik (invalide Kategorie, fehlende IDs, Duplikate)
- `debouncer`: Leading-Edge feuert sofort; Trailing-Edge kollabiert Bursts; `fire_now` bypasst Debounce; Events während Sort queuen am Lock; Cancel bricht keine aktive Sortierung ab
- `routes/projects.py`: CRUD + API-Key-Enforcement + Category-CRUD inklusive Auto-Trigger
- `routes/webhook.py`: HMAC-Check, Fast-Path-Cache-Hit → bypass Debounce, Fast-Path-Miss → Debouncer
- Cache-Invalidation-Matrix: jede Aktion (add, remove, rename, reorder, replace) hat einen Test, der korrekten Cache-State + Sort-Trigger verifiziert
- E2E: Fake-Webhook-Payload → Mock-LLM → verifizieren dass Mock-Todoist-Reorder mit erwarteten IDs gerufen wurde; Cache-Einträge vorher/nachher prüfen

## Deployment

- `Dockerfile` (Python 3.12-slim, uvicorn)
- `docker-compose.yml` mit Volume für SQLite-Datei (`./data:/app/data`)
- `.env.example` mit allen nötigen Variablen
- Start: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Schema-Setup beim Startup via `SQLModel.metadata.create_all()` (Alembic erst wenn Schema-Migrationen nötig werden)

## Offene Punkte (zu klären während Implementierung)

- Exakter Shape des Todoist-Webhook-Payloads für jedes Event (`item:added`, `item:updated`, `item:completed`, `item:deleted`) — Trigger-Item-Content muss daraus extrahierbar sein; Abgleich gegen Todoist-Doku beim Implementieren
- Todoist REST v2 Endpoint-Details für Batch-Reorder (REST `/tasks/reorder` vs. Sync-API `item_reorder`) — abhängig von API-Limits und Auth-Scopes
- Exakte `ondelete="CASCADE"`-Syntax in SQLModel/SQLAlchemy für SQLite (Foreign-Key-Enforcement muss per Pragma aktiviert werden)
