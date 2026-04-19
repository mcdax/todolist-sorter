# todolist-sorter

A self-hosted FastAPI service that listens for Todoist webhooks and automatically
reorders list items to match a user-defined category order. Add groceries in any
order; the service sorts them into the route through your store before you arrive.
Optionally, an LLM-driven transformation layer can fix typos and add emoji to
the item content on the fly. Designed for a single user running their own instance.

---

## Architecture at a glance

The request path for an incoming webhook looks like this:

1. **Webhook received** — `POST /webhook/todoist` verifies the HMAC-SHA256 signature
   (using `TODOIST_CLIENT_SECRET`). Invalid signatures are rejected with 401.
2. **Suppression check** — if the event is `item:updated` for an item that was
   reordered or content-updated within the last `SUPPRESSION_WINDOW_SECONDS`
   (default 30 s), the event is dropped silently. This prevents echo loops
   because Todoist fires an `item:updated` event for every item the service
   reorders or updates.
3. **Cache fast-path** — the item's content is normalised to a `content_key`
   (lower-case, whitespace collapsed). If a cache hit is found, the sort is
   queued immediately (`fire_now`) instead of waiting for the debounce delay.
4. **Debouncer** — a leading+trailing-edge debouncer collapses bursts of events
   (e.g. a paste of multiple items) into a single sort run.
5. **Sort run** — a per-project `asyncio.Lock` serialises concurrent sort
   requests. Unknown items are sent to the LLM (pydantic-ai) for categorisation
   and, when `additional_instructions` is set, transformation. Results are
   written to the cache. Items that cannot be matched to any category are
   logged at WARNING as orphans.
6. **Write-back** — content updates (via Sync API `item_update`) and reorder
   (`item_reorder`) are sent to Todoist. No-op reorders are short-circuited.
   The affected item IDs are registered in the suppression tracker before the
   writes so echoes get dropped.

A resilient retry is built in: if the LLM call fails, the service waits up to
two further intervals (2 s and 5 s) and re-fetches the task list on each retry,
so items added while the service was backing off are folded into the retried
batch.

---

## Prerequisites

- **Python 3.11+** (`pyproject.toml` requires `>=3.11`; the Docker image uses 3.12)
- **SQLite** — no separate database server required
- A **Todoist** account with API access (see [Todoist setup](#todoist-setup))
- An **LLM provider** key — Anthropic by default, also Ollama (Cloud), OpenAI,
  Google, Mistral, Groq, Cohere via pydantic-ai

### Quickstart

1. `todolist-sorter init` — generate `.env` interactively
2. `docker compose up -d --build` (or `uvicorn app.main:create_app --factory`)
3. Open `https://your-host/setup` and follow the on-screen steps (Todoist app
   creation, webhook registration, OAuth authorisation, first sorting project)

---

## Environment variables

Copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TODOIST_CLIENT_ID` | yes | — | Client ID from the Todoist app console. Used in the OAuth callback to exchange the authorization code for an access token so Todoist marks the app as installed. |
| `TODOIST_CLIENT_SECRET` | yes | — | Webhook client secret from the Todoist app console. Used to verify the HMAC-SHA256 signature on every incoming webhook. |
| `TODOIST_API_TOKEN` | yes | — | Personal API token from Todoist settings. Used to fetch tasks and call the Sync API to reorder/update them. |
| `LLM_MODEL` | yes | — | pydantic-ai model string. Prefix selects the provider: `anthropic:claude-sonnet-4-6`, `openai:gpt-4o-mini`, `ollama:glm-4.5`, `google:gemini-1.5-pro`, `mistral:mistral-large-latest`, `groq:llama-3.3-70b`, `cohere:command-r-plus`. |
| `LLM_API_KEY` | yes | — | API key for the LLM provider. Automatically exported to the correct provider env var (e.g. `ANTHROPIC_API_KEY`, `OLLAMA_API_KEY`) on startup. |
| `LLM_BASE_URL` | no | — | Custom base URL for OpenAI-compatible providers. Set to `https://ollama.com/v1` when `LLM_MODEL=ollama:*` (Ollama Cloud). Ignored for other providers. |
| `APP_API_KEY` | no | auto-generated | Secret required on management API calls as the `X-API-Key` header. If empty or left at the placeholder value, a 32-byte `secrets.token_urlsafe` is generated on first start and persisted to `<data-dir>/.api_key`. |
| `DATABASE_URL` | no | `sqlite:///./data/app.db` | SQLAlchemy database URL. The data directory is created on startup. |
| `DEFAULT_DEBOUNCE_SECONDS` | no | `5` | Seconds to wait after the last webhook event before triggering a sort. |
| `SUPPRESSION_WINDOW_SECONDS` | no | `30` | Seconds to suppress `item:updated` echo events for items that were just reordered or content-updated. |
| `LOG_LEVEL` | no | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `WEBHOOK_DEBUG` | no | unset | When set, a webhook HMAC mismatch is logged at WARNING with the received signature, expected signature, and a body preview. Useful for diagnosing misconfigured client secrets. |

---

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

cp .env.example .env
# edit .env and fill in all required values

uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

The API is now available at `http://localhost:8000`. The interactive docs are at
`http://localhost:8000/docs`.

When the service is placed behind a reverse proxy that terminates TLS, pass
`--proxy-headers --forwarded-allow-ips=*` to uvicorn so it trusts the
`X-Forwarded-Proto` header. The provided `Dockerfile` already does this.

---

## Run with Docker

```bash
docker compose up -d --build
```

The compose file mounts `./data` into the container so the SQLite database and
the auto-generated API key persist across restarts. The `.env` file is loaded
automatically. The container runs uvicorn with `--proxy-headers`, so a reverse
proxy terminating TLS in front of it (nginx, Caddy, SWAG…) works out of the box.

---

## Todoist setup

### 1. Create a Todoist app

1. Go to https://developer.todoist.com/appconsole.html
2. Click **Create new app** and give it a name (e.g. `todolist-sorter`).
3. Note the **Client ID** → `TODOIST_CLIENT_ID` in `.env`.
4. Note the **Client Secret** → `TODOIST_CLIENT_SECRET` in `.env`.

### 2. Generate a personal API token

1. Go to https://todoist.com/app/settings/integrations/developer
2. Copy the token → `TODOIST_API_TOKEN` in `.env`.

### 3. Configure the webhook callback

In the app console:

1. Set the **Webhook callback URL** to `https://your-public-host/webhook/todoist`.
   Local development: expose the service via ngrok, cloudflared, or similar;
   the tunnel URL must match exactly what you enter here.
2. Set the **OAuth redirect URL** to `https://your-public-host/oauth/callback`.
3. Enable these event types:
   - `item:added`
   - `item:updated`
4. Leave `item:completed` and `item:deleted` disabled — the service does not
   use them.

### 4. Authorise the app (required for webhook delivery)

Todoist only delivers webhooks for users who have authorised the app via OAuth.
Open `https://your-host/setup` in a browser and click **Authorize with Todoist**.
The service builds the OAuth URL from `TODOIST_CLIENT_ID` + the request host and
handles the redirect at `/oauth/callback` by exchanging the authorization code
for an access token (which is discarded; the service uses `TODOIST_API_TOKEN`
for all subsequent Todoist calls).

Once the installed-apps page at https://todoist.com/app/settings/integrations/installed
lists your app, webhooks start arriving.

### 5. Find your Todoist project ID

Open the project in the Todoist web UI. The URL ends with a numeric id:

```
https://app.todoist.com/app/project/2345678901
```

That number is the `external_project_id` you pass when creating a sorting
project. The CLI can list them interactively; see [Create a sorting project](#create-a-sorting-project).

---

## Create a sorting project

### With the CLI (interactive picker)

Omit `--external-id` and the CLI fetches the available Todoist projects and
prompts you to pick one:

```bash
export TODOLIST_SORTER_API_KEY=your-app-api-key

cat > lidl.txt <<'EOF'
🥬 Vegetables
🍎 Fruit
🍞 Bread
🥛 Dairy
🧹 Household
EOF

todolist-sorter projects create \
  --name "Lidl Shopping" \
  --description "Route through the store: entrance → checkout" \
  --additional-instructions "Fix obvious typos. Prepend a fitting emoji." \
  --categories-file lidl.txt
```

### With curl

```bash
curl -s -X POST http://localhost:8000/projects \
  -H "X-API-Key: $TODOLIST_SORTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Lidl Shopping",
    "provider": "todoist",
    "external_project_id": "2345678901",
    "categories": ["🥬 Vegetables", "🍎 Fruit", "🍞 Bread", "🥛 Dairy", "🧹 Household"],
    "description": "Route through the store: entrance → checkout",
    "additional_instructions": "Fix obvious typos. Prepend a fitting emoji.",
    "debounce_seconds": 5
  }'
```

---

## OpenAPI spec

A full OpenAPI 3.1 spec is:

- served by the running app at `GET /openapi.json` (FastAPI default)
- checked in as [`openapi.json`](openapi.json) for offline consumption

Point other LLMs / tool chains at the checked-in file to let them call the
API correctly without running the server. Regenerate it whenever routes
change:

```bash
python scripts/export_openapi.py          # writes ./openapi.json
python scripts/export_openapi.py --yaml   # writes ./openapi.yaml (needs pyyaml)
```

---

## REST API reference

All management endpoints require `X-API-Key: $APP_API_KEY`. The webhook,
OAuth callback, health, and setup endpoints are public.

### Data schemas

#### Project

```json
{
  "id": "b67d9207-d045-4e52-bdca-9c3961831896",
  "name": "Lidl Shopping",
  "provider": "todoist",
  "external_project_id": "2345678901",
  "categories": ["🥬 Vegetables", "🍎 Fruit"],
  "description": "Route through the store: entrance → checkout",
  "additional_instructions": "Fix obvious typos. Prepend a fitting emoji.",
  "enabled": true,
  "debounce_seconds": 5
}
```

| Field | Type | Writable? | Notes |
|---|---|---|---|
| `id` | UUID | no | Server-assigned. |
| `name` | string | yes | Free text; for humans. |
| `provider` | string | on create only | `"todoist"` is the only supported value today. |
| `external_project_id` | string | on create only | The provider's project id (numeric string for Todoist). Unique per `(provider, external_project_id)`. |
| `categories` | string[] | via `/categories` endpoints | Ordered. Items are placed in this order; unknown items sort to the end. |
| `description` | string \| null | yes | Free-text context for the LLM (e.g. which store this is). |
| `additional_instructions` | string \| null | yes | Optional prompt extension telling the LLM to also transform the item content (fix typos, add emoji, …). Writing this field through `PUT /projects/{id}` clears the project's cache and triggers a sort. |
| `enabled` | boolean | yes | Disabled projects ignore incoming webhooks. |
| `debounce_seconds` | int | yes | Overrides `DEFAULT_DEBOUNCE_SECONDS` for this project. |

#### CacheEntry

```json
{
  "content_key": "äpfel",
  "category_name": "🍎 Fruit",
  "transformed_content": "🍎 Äpfel"
}
```

`transformed_content` is `null` unless `additional_instructions` is set for
the project. When the LLM produces a transformation, the service writes two
cache rows: one keyed on the normalised original, one keyed on the normalised
transformed form (with `transformed_content=null`) to prevent a second LLM
call when the echo of our own update comes back.

---

### Health

#### `GET /healthz`

Liveness probe.

Response `200`:
```json
{"status": "ok"}
```

---

### Setup

#### `GET /setup`

Self-service HTML page that:
- Lists each required credential with a ✓/✗ status
- Shows the expected OAuth redirect URI (built from the request host)
- Renders an **Authorize with Todoist** button linking to the authorize URL
- Displays the number of configured sorting projects

Useful as a first-run landing page. No auth required.

#### `GET /setup/status`

JSON version of the same information. No auth required.

Response `200`:
```json
{
  "credentials": {
    "todoist_client_id":     {"set": true, "placeholder": false},
    "todoist_client_secret": {"set": true, "placeholder": false},
    "todoist_api_token":     {"set": true, "placeholder": false},
    "llm_api_key":           {"set": true, "placeholder": false},
    "app_api_key":           {"set": true, "placeholder": false, "auto_generated": true}
  },
  "todoist_authorized": true,
  "projects_count": 1,
  "llm_model": "anthropic:claude-sonnet-4-6",
  "oauth": {
    "authorize_url": "https://todoist.com/oauth/authorize?client_id=...&scope=data:read_write&state=setup&redirect_uri=...",
    "redirect_uri": "https://your-host/oauth/callback",
    "redirect_uri_matches": true
  }
}
```

The CLI `todolist-sorter status` consumes this endpoint.

---

### OAuth

#### `GET /oauth/callback`

Target of the Todoist OAuth redirect. Expects `code` and optionally `state`.

Query params: `code`, `state`, `error`, `error_description`.

| Scenario | Response |
|---|---|
| `error=…` present | `400`, HTML body containing the error |
| `code` missing | `400`, HTML body |
| Code exchange with Todoist returns non-2xx | `500`, HTML with the response body |
| Successful code exchange | `200`, HTML "App installed" + marker file `<data-dir>/.todoist_authorized` written |

---

### Providers (remote-side queries)

#### `GET /providers/{provider}/projects`

Lists the remote provider's projects (for Todoist, every accessible project
under the configured API token). Used by the CLI's interactive picker.

Path params:
- `provider` — `"todoist"` (only supported value today)

Response `200`:
```json
[
  {"id": "6GpggJhGW2xM5g3F", "name": "Einkaufsliste"},
  {"id": "6MxG5GxwMX3qx8rJ", "name": "Gemeinsam"}
]
```

Error: `404` if `provider` unknown.

---

### Projects CRUD

#### `GET /projects`

List sorting projects. Returns `Project[]`.

#### `POST /projects`

Create a sorting project.

Request body:
```json
{
  "name": "Lidl Shopping",
  "provider": "todoist",
  "external_project_id": "2345678901",
  "categories": ["🥬 Vegetables", "🍎 Fruit"],
  "description": "optional",
  "additional_instructions": "optional",
  "debounce_seconds": 5
}
```

| Field | Type | Required |
|---|---|---|
| `name` | string | yes |
| `provider` | string | yes |
| `external_project_id` | string | yes |
| `categories` | string[] | no (default `[]`) |
| `description` | string \| null | no |
| `additional_instructions` | string \| null | no |
| `debounce_seconds` | int | no (default `5`) |

Responses:
- `201` — `Project` JSON
- `409` — Duplicate `(provider, external_project_id)`; body `{"detail": "..."}`
- `401` — Missing or wrong `X-API-Key`

#### `GET /projects/{id}`

Returns `Project`. `404` if not found.

#### `PUT /projects/{id}`

Partial update. Only fields provided in the body are modified.

Request body (all fields optional):
```json
{
  "name": "…",
  "description": "…",
  "additional_instructions": "…",
  "enabled": true,
  "debounce_seconds": 10
}
```

Behaviour:
- Changing `additional_instructions` (incl. `null ↔ value`) clears the project's
  cache and triggers a sort.
- Other field changes do not touch the cache.

Responses:
- `200` — updated `Project`
- `404` — project not found

#### `DELETE /projects/{id}`

Deletes the project and (via `ON DELETE CASCADE`) all its `CategoryCache` rows.

Responses:
- `204` — deleted
- `404` — project not found

#### `POST /projects/{id}/sort`

Manually trigger a sort cycle, bypassing the debounce.

Responses:
- `202` — `{"status": "queued"}`
- `404` — project not found

---

### Categories

All category endpoints operate on a specific project's `categories` list and
trigger a sort after the modification. Indices are **0-based**.

#### `GET /projects/{id}/categories`

Returns `string[]` — the current ordered list.

#### `PUT /projects/{id}/categories`

Atomic full replace.

Request body:
```json
{"categories": ["🥬 Vegetables", "🍎 Fruit", "🍞 Bread"]}
```

Cache impact: if the new list introduces any name not present before (add or
rename), the full project cache is cleared; otherwise only the rows for removed
category names are deleted.

Response `200`: the new category list.

#### `POST /projects/{id}/categories`

Insert a category.

Request body:
```json
{"name": "🧊 Frozen", "at_index": 5}
```

`at_index` is optional (default: append). Must be in `[0, len]` or `422`.

Cache impact: full clear (a new category may better fit existing items).

Response `200`: the updated list.

#### `DELETE /projects/{id}/categories/{index}`

Remove by index. Cache impact: only rows for the removed category name are
deleted.

Response `200`: the updated list. `422` if `index` out of range.

#### `PATCH /projects/{id}/categories/{index}`

Rename and/or move a category in one call.

Request body (either or both):
```json
{"name": "New name", "move_to": 3}
```

Cache impact:
- rename (`name` changes) → full cache clear (rename may change semantics)
- move-only (`name` omitted or unchanged) → cache untouched

Responses:
- `200` — updated list
- `422` — index or `move_to` out of range

---

### Cache

#### `GET /projects/{id}/cache`

Dump the project's cache.

Response `200`:
```json
[
  {"content_key": "äpfel", "category_name": "🍎 Fruit", "transformed_content": "🍎 Äpfel"},
  {"content_key": "milch", "category_name": "🥛 Dairy", "transformed_content": null}
]
```

#### `DELETE /projects/{id}/cache`

Clear all cache rows for the project. The next sort re-queries the LLM for
every item.

Response: `204`.

---

### Webhook

#### `POST /webhook/{provider}`

Provider-specific event endpoint. For `provider=todoist`:

- Header `X-Todoist-Hmac-SHA256` is required; the body is verified against
  `TODOIST_CLIENT_SECRET`.
- Handles the event types `item:added` and `item:updated`.

Response `200`:
```json
{"status": "queued"}     // event accepted, sort scheduled
{"status": "ignored"}    // unknown project or missing project_id
{"status": "suppressed"} // echo of a just-performed reorder/update
```

Errors:
- `401` — invalid signature
- `404` — unknown provider
- `400` — invalid JSON body

The endpoint returns `200` even for "ignored" / "suppressed" so Todoist does
not retry.

---

## CLI reference

The `todolist-sorter` CLI wraps the REST API.

| Variable | Option | Description |
|---|---|---|
| `TODOLIST_SORTER_URL` | `--url` | Server base URL. Default: `http://localhost:8000`. |
| `TODOLIST_SORTER_API_KEY` | `--api-key` | API key (`X-API-Key`). Required by commands that hit management endpoints; `status` and `init` do not need it. |

### Commands

```
projects list                                List all projects
projects create   [--name] [--external-id] [--provider todoist]
                  [--description] [--additional-instructions]
                  [--debounce-seconds] [--categories-file]
                  # Omit --external-id for the interactive picker
projects show     PROJECT_ID                 Print project details as JSON
projects update   PROJECT_ID [--name] [--description]
                  [--additional-instructions] [--enabled|--disabled]
                  [--debounce-seconds]
projects delete   PROJECT_ID [--yes]

categories list   PROJECT_ID
categories add    PROJECT_ID NAME [--at-index]
categories remove PROJECT_ID INDEX
categories rename PROJECT_ID INDEX NEW_NAME
categories move   PROJECT_ID INDEX --to TARGET_INDEX
categories replace PROJECT_ID CATEGORIES_FILE

cache show        PROJECT_ID
cache clear       PROJECT_ID [--yes]

sort              PROJECT_ID                 Queue an immediate sort

remote list       [--provider todoist]       List the provider's remote projects

status                                       Show server setup status (no API key needed)
init              [--output PATH] [--force]  Generate a .env file interactively
```

Help is available at any level:

```bash
todolist-sorter --help
todolist-sorter projects --help
```

---

## Observability

The service logs at INFO level for each sort cycle:

```
sort_project start: project='Lidl' total_tasks=8
cache hit: Apples → 🍎 Fruit
2 item(s) need LLM (attempt 1/3): ['Cinnamon', 'Oats']
LLM categorized: Cinnamon → 🥬 Spices
LLM categorized: Oats → 🥜 Nüsse & Trockenfrüchte
will update content: 6gQ... 'oats' → '🥜 Oats'
updated 1 item contents
reordered 8 items: ['🥬 Broccoli', '🍎 Apples', ...]
```

When nothing changed (echo events after our own reorder):

```
sort_project start: project='Lidl' total_tasks=8
nothing to do: order unchanged, no content updates
```

Items the LLM cannot assign to any category are logged at WARNING as
`orphan: <content>` and placed at the end of the list.

When the LLM call fails, the service retries up to two further times with
a 2 s / 5 s backoff. Each retry re-fetches the task list so items added in
the meantime are folded into the retried batch.

---

## Notes on content handling

### `additional_instructions`

When set on a project, the LLM is asked to return a `transformed_content`
alongside the category for each miss. The service writes both back to
Todoist (`item_update`) and caches them. Typical prompts:

- `Fix obvious typos. Prepend a fitting emoji if none is present.`
- `Normalise German capitalisation. Keep brand names untouched.`

Changing `additional_instructions` via `PUT /projects/{id}` clears the
project's cache so everything is re-evaluated under the new instructions.

### Quantity prefixes (`5 Milch`, `500g Mehl`)

Quantities are handled end-to-end: the LLM categorises `5 Milch` as Dairy,
and with `additional_instructions` it typically transforms to `🥛 5 Milch`.
Each distinct quantity produces its own cache entry because the normalised
content key includes the leading number. This is fine for correctness but
not deduplicating across quantities — adding `5 Milch` and later `3 Milch`
are two LLM calls, not one.

---

## Troubleshooting

### Webhooks are not arriving

Run `todolist-sorter status` (or open `/setup`) to diagnose:

- Confirm the callback URL in the Todoist app console is publicly reachable
  and matches exactly (scheme, host, path).
- Confirm `TODOIST_CLIENT_SECRET` matches the Client Secret in the app
  console. Set `WEBHOOK_DEBUG=1` to log the received vs expected HMAC on
  mismatch.
- Confirm you have authorised the app via `/setup` — Todoist will not
  deliver webhooks until the OAuth exchange has completed.
- Confirm `item:added` and `item:updated` are enabled in the app console.

### 401 on all management API calls

The `X-API-Key` header value must match `APP_API_KEY` exactly. When `APP_API_KEY`
is empty or a placeholder, the service auto-generates a key on first start
and logs it once as:

```
WARNING Auto-generated APP_API_KEY: <key> (saved to ./data/.api_key)
```

Copy that key into `TODOLIST_SORTER_API_KEY`. The key is persisted to
`<data-dir>/.api_key` and reused across restarts.

### Webhook response shows `"status": "suppressed"`

Normal immediately after a reorder or content update. The suppression
window drops `item:updated` echoes for just-written items. Normal
processing resumes after `SUPPRESSION_WINDOW_SECONDS`.

### Items do not re-sort after renaming a category

Renaming a category clears the full cache for the project; the next sort
run re-categorises all items from scratch. If sorting still does not
happen:

- `projects show <uuid>` — confirm the project is `enabled=true`.
- Confirm the Todoist project has at least two items (a single-item list
  cannot be reordered).

### LLM call keeps timing out

The LLM call runs with the HTTP client's own timeouts; on top of that,
the service retries twice with 2 s / 5 s backoff. Persistent failures
indicate a config problem (wrong model string, wrong `LLM_BASE_URL`,
expired key) rather than flakiness. Check the logs for
`LLM categorization failed for project ... after 3 attempts`.

### `LLM_API_KEY` error on startup

The `Settings` class only soft-validates credentials — missing values
produce 500s later, not an immediate crash. Use `/setup` to confirm
every required variable has a non-placeholder value.
