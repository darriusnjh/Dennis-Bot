# Dennis Bot Operations

## Health Checks

Use the FastAPI health endpoint:

```bash
curl http://localhost:8000/health
```

The response includes:

- `ok`: false when required runtime configuration is missing.
- `mode`: `webhook` or `polling`.
- `config_errors`: human-readable missing configuration items without secret values.
- `simplemem_project` and `simplemem_tenant_id`: current memory routing identifiers.

Worker D also provides helper-level subsystem checks for later `/status` or health wiring:

- database connectivity with a lightweight `SELECT 1`.
- SimpleMem MCP health through the configured client.
- Bright Data configuration, including missing provider secret names without values.
- scheduler running state and job count.
- configured and active monitor counts.

In Docker:

```bash
docker compose ps
docker inspect --format='{{json .State.Health}}' dennis-bot-dennis-bot-1
```

## Logs

Follow service logs:

```bash
docker compose logs -f dennis-bot
```

Set `LOG_LEVEL=INFO` for normal operation. Use `DEBUG` only during short troubleshooting windows because future bot handlers may include more operational detail.

The current logging filter redacts common token, API key, bearer, bot token, and secret patterns. Treat logs as sensitive anyway because message metadata, chat IDs, and operational errors can still be private.

## Bright Data Monitors

If `BRIGHTDATA_API_KEY` is missing, scheduled monitor jobs are not registered. Manual monitor runs still fail closed and record a skipped run with a clear configuration message.

Monitor pause and resume are exposed at the monitor service layer and persisted through the monitor repository interface. A paused monitor is excluded from manual and scheduled execution until resumed.

Bright Data async webhooks are validated with `BRIGHTDATA_WEBHOOK_SECRET`, normalized into the same monitor record format as polling results, recorded as `request_mode=webhook`, and then passed through the regular dedupe, notification, and knowledge-update dispatch path.

## Backups

The Docker deployment stores the Dennis Bot database in the `dennis_bot_data` named volume at `/app/data/dennis_bot.sqlite3`.

Create a SQLite backup from the running container:

```bash
docker compose exec dennis-bot python -c "import sqlite3; src=sqlite3.connect('/app/data/dennis_bot.sqlite3'); dst=sqlite3.connect('/app/data/backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
docker cp dennis-bot-dennis-bot-1:/app/data/backup.sqlite3 ./backup.sqlite3
```

For a cold filesystem backup:

```bash
docker compose stop dennis-bot
docker run --rm -v dennis-bot_dennis_bot_data:/data -v "${PWD}:/backup" alpine tar czf /backup/dennis_bot_data.tgz -C /data .
docker compose start dennis-bot
```

Test restores periodically in a separate environment before relying on backups.

## Restore

Stop the service, restore the database or volume contents, then start it again:

```bash
docker compose stop dennis-bot
docker cp ./backup.sqlite3 dennis-bot-dennis-bot-1:/app/data/dennis_bot.sqlite3
docker compose start dennis-bot
```

If restoring a full volume archive, restore it into the `dennis_bot_data` volume while the service is stopped.

## Secret Handling

Keep real secrets in `.env`, the deployment platform secret store, or an external secret manager. Never commit:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY` if using the legacy OpenAI-compatible env name
- `SIMPLEMEM_MCP_TOKEN`
- `BRIGHTDATA_API_KEY`
- `BRIGHTDATA_WEBHOOK_SECRET`

Rotate a secret immediately if it appears in a chat, log, test fixture, screenshot, or committed file. After rotation, restart the service so the process picks up the new environment.

## Persistent Data

Back up `dennis_bot_data` before upgrades, schema migrations, or host moves. If SimpleMem is self-hosted in the future, also back up its SQLite database and LanceDB vector store because they contain conversation-derived memory.
