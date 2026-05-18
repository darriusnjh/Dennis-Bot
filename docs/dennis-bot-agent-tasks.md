# Dennis Bot Agent Task Plan

## Purpose

This document breaks the Dennis Bot PRD into implementation tasks that can be assigned to coding agents. Each task has a bounded ownership area so agents can work in parallel with fewer conflicts.

## Coordination Rules

- Agents are not alone in the codebase. Do not revert or overwrite edits outside the assigned write scope.
- Keep implementations aligned with [docs/dennis-bot-prd.md](./dennis-bot-prd.md).
- Prefer small, composable services over logic embedded directly in Telegram handlers.
- Secrets must be loaded from environment variables and must not be committed.
- Memory uses Dennis Bot's database as the source of truth and SimpleMem MCP as the semantic long-term memory backend.
- Memory sessions must finalize after 30 recorded conversation messages.
- Telegram production ingress is webhook-first.

## Suggested Implementation Order

1. Agent 1: Project foundation and configuration.
2. Agent 2: Telegram webhook bot shell.
3. Agent 3: Persistence schema and repositories.
4. Agent 4: SimpleMem MCP memory service.
5. Agent 5: LLM conversation orchestrator and personality wiring.
6. Agent 6: Telegram sticker service.
7. Agent 7: Knowledge-base service and knowledge-update agent.
8. Agent 8: Bright Data monitoring and scheduler.
9. Agent 9: Admin commands and group policy.
10. Agent 10: Observability, tests, and deployment hardening.

## Agent 1: Project Foundation And Config

Write scope:

- `pyproject.toml` or `package.json`
- `.env.example`
- `src/config/*`
- `src/app.*`
- `README.md`

Responsibilities:

- Choose and scaffold the runtime stack.
- Define configuration loading for Telegram, LLM provider, Bright Data, SimpleMem MCP, database paths, trusted group chat ID, and admin user IDs.
- Add startup validation that reports missing required configuration without leaking secret values.

Deliverables:

- Runnable application entrypoint.
- Typed configuration object or equivalent.
- `.env.example` with all required variables.
- README setup instructions.

Acceptance criteria:

- App starts with valid env vars.
- App fails with a clear error when required config is missing.
- No secrets are hard-coded.

## Agent 2: Telegram Webhook Bot Shell

Write scope:

- `src/telegram/*`
- `src/webhooks/*`
- `src/routes/*`

Dependencies:

- Agent 1 config foundation.

Responsibilities:

- Implement Telegram webhook receiver.
- Add local long-polling fallback for development.
- Normalize Telegram updates into internal message events.
- Support direct messages and group chats.
- Implement initial commands:
  - `/start`
  - `/help`
  - `/status`

Deliverables:

- Telegram webhook endpoint.
- Telegram update normalization layer.
- Basic command router.

Acceptance criteria:

- Bot can receive webhook updates.
- Bot can run in local polling mode.
- `/status` shows configuration and subsystem health without exposing secrets.

## Agent 3: Persistence Schema And Repositories

Write scope:

- `src/db/*`
- `src/repositories/*`
- `migrations/*`

Dependencies:

- Agent 1 config foundation.

Responsibilities:

- Implement Dennis Bot's source-of-truth database schema.
- Include tables for:
  - users
  - chats
  - conversation messages
  - memory records
  - memory sessions
  - knowledge states
  - knowledge update jobs
  - sticker packs
  - sticker aliases
  - web monitors
  - monitor runs
  - social activity items
- Add repository methods for create, read, update, search, and audit operations where needed.

Deliverables:

- Migration files.
- Repository layer.
- Database initialization command.

Acceptance criteria:

- Fresh database can be created from migrations.
- Conversation messages can be stored with Telegram provenance.
- Memory sessions can track `message_count`, `status`, and SimpleMem IDs.

## Agent 4: SimpleMem MCP Memory Service

Write scope:

- `src/memory/*`
- `src/mcp/*` or equivalent MCP client module
- tests for memory service

Dependencies:

- Agent 1 config foundation.
- Agent 3 persistence schema.

Responsibilities:

- Implement `MemoryService` abstraction around SimpleMem MCP.
- Configure hosted SimpleMem MCP using `SIMPLEMEM_MCP_URL` and `SIMPLEMEM_MCP_TOKEN`.
- Use `project = "dennis-bot"` and default `tenant_id = "dennis-bot-global"`.
- Maintain one active SimpleMem MCP session per Telegram chat.
- Record user messages and assistant replies.
- Finalize and end the SimpleMem session after 30 recorded conversation messages.
- Start a new session for the next message after finalization.
- Retrieve memory context before LLM generation.
- Provide memory search and stats methods for commands.
- Keep Dennis Bot DB as the source of truth for raw messages, audit state, and deletes.
- Fail clearly if the configured MCP endpoint does not expose required session lifecycle tools.

Deliverables:

- Memory service API.
- SimpleMem MCP client.
- SimpleMem MCP health check.
- Session rollover logic at 30 messages.
- Memory search/context retrieval.

Acceptance criteria:

- After 30 recorded messages, the active session is finalized and marked finalized in the DB.
- Message 31 starts a new active memory session.
- Retrieved memory context can be injected into a prompt.
- Missing MCP URL/token or unavailable MCP tools fail clearly at startup or health check.

## Agent 5: LLM Conversation Orchestrator And Personality

Write scope:

- `src/orchestrator/*`
- `src/llm/*`
- `src/prompts/*`

Dependencies:

- Agent 2 Telegram shell.
- Agent 4 memory service.

Responsibilities:

- Load [config/personality/dennis-bot.md](../config/personality/dennis-bot.md).
- Load active knowledge-state summaries.
- Retrieve SimpleMem MCP context for the incoming message.
- Build the response prompt with:
  - Dennis Bot identity boundary
  - personality profile
  - memory context
  - active knowledge context
  - Telegram chat metadata
- Route decisions for normal answer, sticker, memory command, or knowledge-update command.

Deliverables:

- Conversation orchestration service.
- LLM adapter with timeout and retry behavior.
- Prompt assembly tests.

Acceptance criteria:

- Bot responses use the Dennis Bot personality profile.
- The bot does not claim to be the real Dennis Toh.
- Memory context is included before response generation.
- Operational secrets are excluded from prompt context.

## Agent 6: Telegram Sticker Service

Write scope:

- `src/stickers/*`
- sticker-related command handlers
- tests for sticker alias resolution

Dependencies:

- Agent 2 Telegram shell.
- Agent 3 persistence schema.

Responsibilities:

- Sync Telegram sticker packs using `getStickerSet`.
- Store sticker `file_id`s and aliases.
- Send stickers using `sendSticker`.
- Add `/stickers` command support:
  - list aliases
  - test alias
  - refresh pack

Deliverables:

- Sticker sync service.
- Sticker send service.
- Sticker command handlers.

Acceptance criteria:

- Configured sticker pack can be synced.
- At least one sticker alias can be sent to a test chat.
- Missing or invalid alias returns a clear Telegram message.

## Agent 7: Knowledge-Base Service And Knowledge-Update Agent

Write scope:

- `src/knowledge/*`
- `src/agents/knowledge_update/*`
- `knowledge_base/*`
- tests for knowledge-update classification

Dependencies:

- Agent 3 persistence schema.
- Agent 5 orchestrator for explicit update commands.

Responsibilities:

- Manage versioned knowledge states.
- Load and index [knowledge_base/dennis-toh.md](../knowledge_base/dennis-toh.md).
- Implement knowledge-update jobs.
- Classify detected source changes:
  - `minor`
  - `notifiable`
  - `kb_impactful`
- Apply `kb_impactful` changes by creating a new knowledge version.
- Create pending review jobs for low-confidence changes.
- Support explicit "update knowledge base" requests from admins.

Deliverables:

- Knowledge-state repository/service.
- Knowledge-update agent.
- Versioning and rollback metadata.

Acceptance criteria:

- Official-site content changes can create a knowledge-update job.
- `kb_impactful` changes produce a new knowledge version.
- Low-confidence changes are marked `pending_review`.
- Knowledge source provenance is preserved.

## Agent 8: Bright Data Monitoring And Scheduler

Write scope:

- `src/monitors/*`
- `src/brightdata/*`
- `src/scheduler/*`
- monitor command handlers
- tests for monitor deduplication

Dependencies:

- Agent 3 persistence schema.
- Agent 7 knowledge-update service.

Responsibilities:

- Implement Bright Data API client.
- Implement official-site monitor for:
  - `https://www.dennistohsg.com/`
  - `https://www.dennistohsg.com/about`
- Implement Instagram activity monitor for:
  - `https://www.instagram.com/dennistohsg/`
- Track all public, Bright-Data-accessible Instagram activity supported by selected endpoints.
- Normalize provider responses into monitor records.
- Detect any normalized content change.
- Send group notifications for site or Instagram changes.
- Dispatch official-site changes and impactful Instagram changes to the knowledge-update agent.
- Support Bright Data async webhook delivery where available.

Deliverables:

- Bright Data client.
- Scheduler jobs.
- Webhook receiver for Bright Data async delivery.
- Deduplication logic.
- `/watch` command support.

Acceptance criteria:

- Manual `/watch run dennis_official_site` works.
- Manual `/watch run dennis_instagram` works.
- Duplicate provider records do not send duplicate Telegram alerts.
- Monitor changes create `MonitorRun` records.

## Agent 9: Admin Commands And Trusted Group Policy

Write scope:

- `src/admin/*`
- command handlers for settings, memory, KB, and watches
- authorization tests

Dependencies:

- Agent 2 Telegram shell.
- Agent 3 persistence schema.
- Agent 4 memory service.
- Agent 7 knowledge service.
- Agent 8 monitor service.

Responsibilities:

- Enforce admin allowlist by Telegram user ID.
- Enforce trusted group chat ID for full memory access.
- Implement commands:
  - `/settings`
  - `/memory search`
  - `/memory stats`
  - `/memory finalize`
  - `/kb list`
  - `/kb status`
  - `/kb update`
  - `/watch list`
  - `/watch run`
  - `/watch pause`
  - `/watch resume`
- Ensure unauthorized users cannot mutate settings.

Deliverables:

- Admin command handlers.
- Authorization middleware.
- Trusted group policy checks.

Acceptance criteria:

- Non-admin users cannot change config or run admin commands.
- Trusted group receives full memory behavior.
- Non-configured groups do not accidentally receive full memory access.

## Agent 10: Observability, Tests, And Deployment

Write scope:

- `tests/*`
- `Dockerfile`
- `docker-compose.yml`
- deployment docs
- logging/metrics modules

Dependencies:

- All implementation agents.

Responsibilities:

- Add structured logging with secret redaction.
- Add health checks for:
  - Telegram
  - database
  - SimpleMem
  - Bright Data
  - scheduler
- Add integration tests for:
  - webhook handling
  - SimpleMem 30-message rollover
  - trusted group full memory
  - sticker alias resolution
  - knowledge-update classification
  - Bright Data monitor deduplication
  - admin authorization
- Add Docker deployment with persistent volumes for:
  - Dennis Bot DB
- If self-hosting SimpleMem later: SimpleMem SQLite and SimpleMem LanceDB
- Add backup and restore documentation.

Deliverables:

- Test suite.
- Health check endpoint.
- Docker/deployment files.
- Deployment and backup docs.

Acceptance criteria:

- Tests pass locally.
- Health endpoint reports subsystem status.
- Persistent memory survives container restart.
- Logs redact token-like values.
