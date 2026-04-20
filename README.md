# todolist-sorter

Add groceries to a Todoist list in any order — the service categorises them
with an LLM and reorders them to match the route through your supermarket.
Optionally it also fixes typos and adds emoji to item names. Designed for a
single person running their own small server.

Example — you add to your Todoist "Shopping" list:

```
bread
apples
5 milch
toilet paper
scholle
```

A few seconds later Todoist shows:

```
🍎 Apples                  ← fruit, first
🥖 Bread                   ← bakery
🐟 Scholle                 ← fish
🥛 5 Milch                 ← dairy
🧻 Toilet paper            ← household, last
```

---

## Before you start

You need:

1. A **Todoist** account — the free plan works.
2. An **LLM provider API key**. Anthropic (Claude) is the easiest default;
   Ollama Cloud, OpenAI, Google, Mistral and a few others are supported.
3. A **server** reachable from the public internet with a **TLS-enabled
   domain**. Todoist only delivers webhooks over HTTPS. If you do not already
   have a reverse proxy set up, SWAG / Caddy / Traefik all work; this guide
   assumes you do.
4. **Docker + Docker Compose** on that server.

---

## Step 1 — Create the Todoist app

Todoist only sends webhooks for users who have installed an "app" and
authorised it. You create the app once.

1. Open <https://developer.todoist.com/appconsole.html>.
2. Click **Create new app**, name it (e.g. `todolist-sorter`).
3. Leave the page open — you will come back to it in step 4. For now copy
   three values into a notepad:
   - **Client ID**
   - **Client secret**
   - Your **personal API token**, which lives on a different page:
     <https://todoist.com/app/settings/integrations/developer>

---

## Step 2 — Deploy the service

### Option A: Docker Compose (recommended)

On your server:

```bash
# 1. Create a working directory
mkdir -p ~/todolist-sorter && cd ~/todolist-sorter

# 2. Get a compose file
cat > compose.yaml <<'EOF'
services:
  todolist-sorter:
    image: ghcr.io/mcdax/todolist-sorter:latest
    container_name: todolist-sorter
    env_file: [.env]
    volumes:
      - ./data:/app/data
    ports:
      - "8000:8000"
    restart: unless-stopped
EOF

# 3. Build a .env file
docker run --rm -it -v "$PWD":/cwd -w /cwd \
    ghcr.io/mcdax/todolist-sorter:latest \
    todolist-sorter init --output /cwd/.env
```

The `init` command prompts for your Todoist values (from step 1) and your
LLM model + key. It generates a random `APP_API_KEY` for you automatically.
Accept the defaults where unsure.

Alternatively, copy
[`.env.example`](.env.example) to `.env` and fill it in by hand.

Start the service:

```bash
docker compose up -d
```

### Option B: Run directly with Python

```bash
git clone https://github.com/mcdax/todolist-sorter.git
cd todolist-sorter
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
todolist-sorter init            # interactive .env wizard
uvicorn app.main:create_app --factory --proxy-headers --forwarded-allow-ips='*'
```

---

## Step 3 — Expose the service through HTTPS

Point a subdomain (e.g. `sorter.example.com`) at your server and add a
reverse-proxy config that forwards `/` to `todolist-sorter:8000` (compose)
or `127.0.0.1:8000` (bare python). See [docs/reverse-proxy-examples.md](docs/reverse-proxy-examples.md)
if you need templates for nginx / Caddy / SWAG.

Quick check:

```bash
curl -sS https://sorter.example.com/healthz
# should print: {"status":"ok"}
```

---

## Step 4 — Finish the Todoist app

Go back to the Todoist developer console and fill in two URLs:

- **OAuth redirect URL** → `https://sorter.example.com/oauth/callback`
- **Webhook callback URL** → `https://sorter.example.com/webhook/todoist`

Enable these webhook events (leave the others off):
- `item:added`
- `item:updated`

Save.

---

## Step 5 — Authorise the app

Open **<https://sorter.example.com/setup>** in your browser. The page
shows which credentials are configured, whether the app is authorised, and
a big **Authorize with Todoist** button. Click it; Todoist asks you to
grant access; you land back on `/oauth/callback` with a "✓ App installed"
message. Webhooks will start arriving now.

If anything is red on that page, fix it and reload.

---

## Step 6 — Create a sorting project

A "sorting project" links one Todoist project to a list of categories in
the order you want items to appear.

Open `https://sorter.example.com/docs` in your browser — that's the
interactive Swagger UI. Or use the CLI.

Using the CLI locally (the CLI ships in the same image / package):

```bash
export TODOLIST_SORTER_URL=https://sorter.example.com
export TODOLIST_SORTER_API_KEY=…           # value from your .env

# List your Todoist projects
todolist-sorter remote list

# Put your category order into a file (one category per line)
cat > lidl.txt <<'EOF'
🥬 Vegetables
🍎 Fruit
🥖 Bread
🐟 Fish
🥛 Dairy
🧻 Household
EOF

# Create the sorting project (interactive picker if you omit --external-id)
todolist-sorter projects create \
  --name "Lidl" \
  --categories-file lidl.txt \
  --additional-instructions "Fix obvious typos. Prepend a fitting emoji."
```

That's it. Add an item to the linked Todoist list and watch it get moved
into place a few seconds later.

---

## What can I tweak?

| In `.env` | What it does |
|---|---|
| `LLM_MODEL` | e.g. `anthropic:claude-sonnet-4-6`, `ollama:glm-4.5`, `openai:gpt-4o-mini`. |
| `LLM_BASE_URL` | Only needed for OpenAI-compatible endpoints like Ollama Cloud (`https://ollama.com/v1`). |
| `DEFAULT_DEBOUNCE_SECONDS` | How long to wait after the last change before sorting. Default 5. |
| `SUPPRESSION_WINDOW_SECONDS` | How long to ignore the webhook echoes of our own reorder. Default 30. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. Default `INFO`. |

Per project (via `PUT /projects/{id}` or the CLI's `projects update`):
- `additional_instructions` — free text the LLM uses when rewriting item
  content. Set it to `"Fix obvious typos. Prepend a fitting emoji."` for
  the shown behaviour.
- `debounce_seconds` — overrides the global default for this project.
- `enabled` — set to `false` to pause sorting without deleting the project.

Managing categories after the fact:
```bash
todolist-sorter categories list   <project-id>
todolist-sorter categories add    <project-id> "🧊 Frozen" --at-index 5
todolist-sorter categories rename <project-id> 5 "❄ Frozen"
todolist-sorter categories move   <project-id> 5 --to 7
todolist-sorter categories remove <project-id> 5
```

---

## API reference

The API is documented via OpenAPI / Swagger, not in this README:

- **Interactive docs**: `https://<your-host>/docs` (Swagger UI) or `/redoc`.
- **Raw OpenAPI spec**: `https://<your-host>/openapi.json`.
- **Versioned copy in this repo**: [`openapi.json`](openapi.json).

All management endpoints (everything except `/healthz`, `/webhook/*`,
`/oauth/callback`, and the setup page) require an `X-API-Key: <APP_API_KEY>`
header.

Quick curl examples:

```bash
# List sorting projects
curl -H "X-API-Key: $TODOLIST_SORTER_API_KEY" https://sorter.example.com/projects

# Trigger a manual re-sort
curl -X POST -H "X-API-Key: $TODOLIST_SORTER_API_KEY" \
    https://sorter.example.com/projects/<uuid>/sort
```

To regenerate the checked-in OpenAPI file after changing routes:

```bash
python scripts/export_openapi.py
```

---

## How it works (one level deeper)

```
Todoist webhook → HMAC check → suppression check → cache fast-path
    → debouncer (collapses bursts)
    → sort cycle (locked per project):
        fetch tasks
        cache lookup; miss list → LLM (retry 0s / 2s / 5s, re-fetching on retry)
        write back transformed content (if additional_instructions is set)
        reorder via Sync API (skipped if current order already matches)
        mark suppression for all written item ids
```

`openapi.json` + [`app/`](app/) are the source of truth; this description
is just a mental model. Notable design choices:

- A **cache** keyed on the lower-cased item content maps each known item
  to its category. Most items are resolved without touching the LLM.
- When `additional_instructions` is set, the cache also stores the
  transformed form under its own key so the webhook echo of our own
  content update is a cache hit, not a second LLM call.
- The **suppression tracker** drops `item:updated` events for items the
  service just wrote to, preventing echo loops.
- A **per-project `asyncio.Lock`** keeps concurrent sort cycles from
  stepping on each other.

---

## Troubleshooting

**`/setup` shows red entries** — follow the on-screen guidance. It is the
diagnostic dashboard; nothing in the logs will ever tell you more than
this page.

**Webhooks are not arriving** — check the Todoist app console for the
`item:added` / `item:updated` events, confirm the callback URL is
reachable over HTTPS, and that you completed step 5. Set `WEBHOOK_DEBUG=1`
in `.env` to log the received vs expected HMAC on every failure.

**`401` on every API call** — the `X-API-Key` header must match
`APP_API_KEY` exactly. If you left the placeholder in `.env`, the service
auto-generates one and logs it as `WARNING Auto-generated APP_API_KEY:
<key>` on first boot. The value is persisted in `<data-dir>/.api_key`.

**Webhook responded `"status": "suppressed"`** — this is normal
immediately after a reorder. The service is ignoring its own echo events.
Wait `SUPPRESSION_WINDOW_SECONDS` (30 s by default) and try again.

**LLM call times out or returns 500** — the service retries automatically
(`0 s / 2 s / 5 s` backoff). If it gives up after three attempts, the
next webhook event will re-trigger. Persistent failures usually mean a
wrong `LLM_MODEL` string, expired `LLM_API_KEY`, or unreachable
`LLM_BASE_URL`. Check the logs for `LLM categorization failed`.

**Items do not re-sort after renaming a category** — renaming clears the
whole project's cache and kicks off a new sort. If nothing happens,
confirm the project is `enabled=true` (visible on `/setup` or via
`projects show`) and that the Todoist list has at least two items.

---

## Running tests

```bash
pip install -e '.[dev]'
pytest
```

183 tests covering the HTTP layer, the sort pipeline, the debouncer /
suppression / retry logic, and the CLI.
