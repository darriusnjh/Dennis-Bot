# Dennis Bot Deployment

Dennis Bot is a FastAPI service. Production should use Telegram webhook mode; polling mode is a local fallback for development or private test runs.

## Required Environment

Copy `.env.example` to `.env` and set these before running a real bot:

- `TELEGRAM_BOT_TOKEN`: BotFather token.
- `ADMIN_TELEGRAM_USER_IDS`: comma-separated Telegram user IDs allowed to administer the bot.
- `OPENROUTER_API_KEY`: OpenRouter key for Dennis Bot chat responses.
- `OPENROUTER_BASE_URL`: defaults to `https://openrouter.ai/api/v1`.
- `OPENROUTER_MODEL`: defaults to `x-ai/grok-4.3`.
- Legacy `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` are still accepted for
  OpenAI-compatible deployments, but `OPENROUTER_API_KEY` takes precedence.
- `SIMPLEMEM_MCP_URL`: hosted SimpleMem MCP Streamable HTTP endpoint.
- `SIMPLEMEM_MCP_TOKEN`: hosted SimpleMem MCP credential.
- `SIMPLEMEM_TENANT_ID`: defaults to `dennis-bot-global`.
- `SIMPLEMEM_PROJECT`: defaults to `dennis-bot`.
- `DATABASE_PATH`: defaults to `data/dennis_bot.sqlite3`; Docker sets this to `/app/data/dennis_bot.sqlite3`.

Webhook deployments also require:

- `BASE_URL`: public HTTPS origin for the bot, for example `https://bot.example.com`.
- `TELEGRAM_WEBHOOK_SECRET`: secret used to validate Telegram webhook delivery.
- `TELEGRAM_USE_POLLING=false`.

Polling deployments require:

- `TELEGRAM_USE_POLLING=true`.
- No public `BASE_URL` or `TELEGRAM_WEBHOOK_SECRET` is required for local polling.

Bright Data monitoring is disabled or unhealthy until the relevant credentials are present:

- `BRIGHTDATA_API_KEY`
- `BRIGHTDATA_WEB_UNLOCKER_ZONE`
- `BRIGHTDATA_INSTAGRAM_DATASET_ID_PROFILE`
- `BRIGHTDATA_INSTAGRAM_DATASET_ID_POSTS`
- `BRIGHTDATA_INSTAGRAM_DATASET_ID_REELS`
- `BRIGHTDATA_INSTAGRAM_DATASET_ID_COMMENTS`
- `BRIGHTDATA_WEBHOOK_SECRET`

Scheduled Bright Data jobs are omitted when `BRIGHTDATA_API_KEY` is not configured. This is intentional fail-closed behavior so a deployment with missing provider credentials does not repeatedly enqueue skipped monitor runs.

Bright Data async delivery can be mounted at:

```text
https://your-public-host.example/webhooks/brightdata
```

Webhook requests must include `BRIGHTDATA_WEBHOOK_SECRET` in `X-Brightdata-Webhook-Secret`, `X-Webhook-Secret`, or an `Authorization: Bearer ...` header. Payloads must include `monitor_name` so delivered snapshots can be routed to the matching monitor.

## Docker

Build and start the service:

```bash
docker compose up --build -d
```

The compose file publishes `${APP_PORT:-8000}` and stores the Dennis Bot SQLite database in the named volume `dennis_bot_data`.
The image includes the repository `migrations/` directory so fresh containers can initialize or migrate the database.

Check health:

```bash
curl http://localhost:8000/health
docker compose ps
```

Stop the service:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you intentionally want to delete the persistent database volume.

## Webhook Mode

Set:

```env
TELEGRAM_USE_POLLING=false
BASE_URL=https://your-public-host.example
TELEGRAM_WEBHOOK_SECRET=replace-with-a-random-secret
```

The application exposes health at `/health`. Telegram webhook ingress is designed around `/webhooks/telegram`, so the Telegram webhook URL should be:

```text
https://your-public-host.example/webhooks/telegram
```

Register the webhook with Telegram after the bot is reachable over HTTPS. Keep the webhook secret out of logs and shell history where possible.

## Polling Mode

Polling mode is for local development and private test runs:

```env
TELEGRAM_USE_POLLING=true
BASE_URL=http://localhost:8000
```

Start with:

```bash
dennis-bot
```

or:

```bash
docker compose up --build
```

Polling mode still validates the bot token, admin IDs, OpenRouter key, and SimpleMem MCP configuration.

## SimpleMem MCP Hosted Setup

Use the hosted SimpleMem MCP endpoint when credentials are available:

```env
SIMPLEMEM_MCP_URL=https://mcp.simplemem.cloud/mcp
SIMPLEMEM_MCP_TOKEN=replace-with-hosted-token
SIMPLEMEM_TENANT_ID=dennis-bot-global
SIMPLEMEM_PROJECT=dennis-bot
SIMPLEMEM_MAX_SESSION_MESSAGES=30
```

Dennis Bot keeps Telegram provenance, audit state, deletes, and raw conversation history in its own database. SimpleMem MCP is the semantic long-term memory backend for session summaries, retrieval, and context injection.

If the hosted service does not provide the required session lifecycle tools, use a self-hosted SimpleMem MCP deployment later. In that case, persist and back up the SimpleMem SQLite database and LanceDB vector store separately from `dennis_bot_data`.
