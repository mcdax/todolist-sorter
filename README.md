# todolist-sorter

A self-hosted FastAPI service that listens for Todoist webhooks and automatically
reorders list items to match a user-defined category order. Add groceries in any
order; the service sorts them into the route through your store before you arrive.
Designed for a single user running their own instance.

---

## Architecture at a glance

The request path for an incoming webhook looks like this:

1. **Webhook received** — `POST /webhook/todoist` verifies the HMAC-SHA256 signature
   (using `TODOIST_CLIENT_SECRET`). Invalid signatures are rejected with 401.
2. **Suppression check** — if the event is `item:updated` for an item that was
   reordered within the last `SUPPRESSION_WINDOW_SECONDS` (default 30 s), the
   event is dropped silently. This prevents echo loops because Todoist fires an
   `item:updated` event for every item the service reorders.
3. **Cache fast-path** — the item's content is normalised to a `content_key`
   (lower-case, stripped). If a cache hit is found, the sort is queued immediately
   (`fire_now`) instead of waiting for the debounce delay.
4. **Debouncer** — a leading+trailing-edge debouncer collapses bursts of events
   (e.g. a paste of multiple items) into a single sort run.
5. **Sort run** — a per-project `asyncio.Lock` serialises concurrent sort
   requests. Unknown items are sent to the LLM (pydantic-ai) for categorisation;
   the result is written to the cache. Items that cannot be matched to any
   category are logged at WARNING level as orphans.
6. **Reorder** — the Todoist Sync API (`item_reorder`) sets `child_order` on all
   items in one call. Item IDs that were just reordered are registered in the
   suppression tracker.

---

## Prerequisites

- **Python 3.11+** (pyproject.toml requires `>=3.11`; the Docker image uses 3.12)
- **SQLite** — no separate database server required
- A **Todoist** account with API access (see [Todoist setup](#todoist-setup))
- An **LLM provider** key — Anthropic (`claude-sonnet-4-6`) by default; any
  model string supported by pydantic-ai works

---

## Environment variables

Copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TODOIST_CLIENT_SECRET` | yes | — | Webhook client secret from the Todoist app console. Used to verify the HMAC-SHA256 signature on every incoming webhook. |
| `TODOIST_API_TOKEN` | yes | — | Personal API token from Todoist settings. Used to fetch tasks and call the Sync API to reorder them. |
| `LLM_MODEL` | yes | — | pydantic-ai model string, e.g. `anthropic:claude-sonnet-4-6`. |
| `LLM_API_KEY` | yes | — | API key for the LLM provider (e.g. an Anthropic API key). |
| `APP_API_KEY` | yes | — | Secret key required on all management API calls as the `X-API-Key` header. Generate a long random string. |
| `DATABASE_URL` | no | `sqlite:///./data/app.db` | SQLAlchemy database URL. The `data/` directory is created on startup. |
| `DEFAULT_DEBOUNCE_SECONDS` | no | `5` | Seconds to wait after the last webhook event before triggering a sort. |
| `SUPPRESSION_WINDOW_SECONDS` | no | `30` | Seconds to suppress `item:updated` echo events for items that were just reordered. |

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

---

## Run with Docker

```bash
docker compose up -d --build
```

The compose file mounts `./data` into the container so the SQLite database
persists across restarts. The `.env` file is loaded automatically.

---

## Todoist setup

### 1. Create a Todoist app

1. Go to https://developer.todoist.com/appconsole.html
2. Click **Create new app** and give it a name (e.g. `todolist-sorter`).
3. Note the **Client Secret** — this becomes `TODOIST_CLIENT_SECRET` in your `.env`.
4. Note the **Client ID** — you will need it for the OAuth URL below.

### 2. Generate a personal API token

1. Go to https://todoist.com/app/settings/integrations/developer
2. Copy the token shown under **API token** — this becomes `TODOIST_API_TOKEN`.

### 3. Configure the webhook callback

In the app console:

1. Set the **Webhook callback URL** to `https://your-public-host/webhook/todoist`.
   - Local development: expose the service via ngrok, cloudflared, or a similar
     tunnel. The tunnel URL must match exactly what you enter here.
2. Enable these event types:
   - `item:added`
   - `item:updated`
3. Leave `item:completed` and `item:deleted` disabled — the service does not use
   them.

### 4. Authorise the app (required for webhook delivery)

Todoist only delivers webhooks for users who have authorised the app via OAuth.
You do not need to implement a full OAuth flow for a self-hosted single-user
deployment — authorising once is enough.

Construct the OAuth URL:

```
https://todoist.com/oauth/authorize?client_id=YOUR_CLIENT_ID&scope=data:read_write&state=anything
```

Visit the URL in a browser, log in if prompted, and click **Agree**. You do not
need to handle the redirect or exchange the code for a token — the act of
authorising registers you as an app user and webhooks start arriving.

If webhooks still do not arrive, re-check Todoist's current documentation at
https://developer.todoist.com/sync/v9/#webhooks — OAuth details may change.

### 5. Find your Todoist project ID

Open the project in the Todoist web UI. The URL looks like:

```
https://app.todoist.com/app/project/2345678901
```

The numeric ID at the end is the `external_project_id` you pass when creating a
sorting project.

---

## Create a sorting project

Use the CLI to create a project and load its category list from a text file (one
category per line):

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
  --external-id 2345678901 \
  --description "Route through the store: entrance → checkout" \
  --categories-file lidl.txt
```

The command prints the created project as JSON, including its UUID which you will
need for subsequent commands.

Equivalent curl call:

```bash
curl -s -X POST http://localhost:8000/projects \
  -H "X-API-Key: $TODOLIST_SORTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Lidl Shopping",
    "provider": "todoist",
    "external_project_id": "2345678901",
    "description": "Route through the store: entrance → checkout",
    "categories": ["🥬 Vegetables", "🍎 Fruit", "🍞 Bread", "🥛 Dairy", "🧹 Household"]
  }'
```

### Delete a project

```bash
todolist-sorter projects delete <uuid>
# or
curl -X DELETE http://localhost:8000/projects/<uuid> \
  -H "X-API-Key: $TODOLIST_SORTER_API_KEY"
```

Associated cache rows are removed automatically via `ON DELETE CASCADE`.

---

## CLI reference

The `todolist-sorter` CLI wraps the REST API. Two environment variables replace
the corresponding options on every call:

| Variable | Option | Description |
|---|---|---|
| `TODOLIST_SORTER_URL` | `--url` | Server base URL. Default: `http://localhost:8000`. |
| `TODOLIST_SORTER_API_KEY` | `--api-key` | API key (`X-API-Key`). Required. |

### Command overview

```
projects list                           List all projects
projects create   --name --external-id [--provider] [--description]
                  [--debounce-seconds] [--categories-file]
projects show     PROJECT_ID            Print project details as JSON
projects update   PROJECT_ID [--name] [--description]
                  [--enabled|--disabled] [--debounce-seconds]
projects delete   PROJECT_ID [--yes]

categories list   PROJECT_ID
categories add    PROJECT_ID NAME [--at-index]
categories remove PROJECT_ID INDEX
categories rename PROJECT_ID INDEX NEW_NAME
categories move   PROJECT_ID INDEX --to TARGET_INDEX
categories replace PROJECT_ID CATEGORIES_FILE

cache show        PROJECT_ID
cache clear       PROJECT_ID [--yes]

sort              PROJECT_ID           Queue an immediate sort
```

Full help is available at any level:

```bash
todolist-sorter --help
todolist-sorter projects --help
todolist-sorter categories --help
```

### Category index notes

Category indices are 0-based and shown by `categories list`. Renaming or removing
a category clears the cache entries for the affected items so they are
re-categorised on the next sort run.

---

## Observability

The service logs at INFO level for each sort cycle:

- `cache hit: <item> → <category>` for items resolved from cache
- `LLM categorized: <item> → <category>` for items sent to the LLM
- `reordered N items: [...]` with the final ordered content list

Items the LLM cannot assign to any category are logged at WARNING:

```
WARNING orphan: <item content>
```

Orphans are placed at the end of the list, after all categorised items.

---

## Troubleshooting

**Webhooks are not arriving**

- Confirm the callback URL in the app console is publicly reachable and matches
  exactly (scheme, host, path).
- Confirm `TODOIST_CLIENT_SECRET` matches the Client Secret in the app console.
- Confirm you have authorised the app via the OAuth URL (step 4 above).
- Confirm `item:added` and `item:updated` are enabled in the app console.

**401 on all management API calls**

The `X-API-Key` header value must match `APP_API_KEY` exactly. Check for
leading/trailing whitespace. The CLI reads the key from `TODOLIST_SORTER_API_KEY`.

**Items do not re-sort after renaming a category**

This is expected behaviour. Renaming a category clears the full cache for the
project; the next sort run re-categorises all items from scratch. If sorting still
does not happen, check that the project is enabled (`projects show <uuid>`) and
that the Todoist project has at least two items.

**Webhook response shows `"status": "suppressed"`**

This is normal immediately after a reorder. The suppression window (default 30 s,
controlled by `SUPPRESSION_WINDOW_SECONDS`) drops `item:updated` echo events for
items that were just reordered. The events resume being processed after the window
expires.

**`LLM_API_KEY` error on startup**

The application validates all required settings on startup. If any required
variable is missing, pydantic-settings raises a `ValidationError` with the
missing field names. Check that `.env` is present in the working directory and
that all required variables are set.
