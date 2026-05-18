# SimpleMem Integration Reference

This document explains what SimpleMem does, how it works internally, and how Dennis Bot adapts it for Telegram-based long-term memory.

## Summary

SimpleMem is the semantic long-term memory backend for Dennis Bot. Dennis Bot sends conversation messages to SimpleMem through MCP, and SimpleMem converts those raw messages into searchable memory facts.

Dennis Bot does not use SimpleMem as its only database. The split is:

- Dennis Bot SQLite stores Telegram provenance, conversation audit rows, memory session state, manual memory records, monitor state, stickers, and knowledge-base metadata.
- SimpleMem stores semantic memory entries in a vector database so future prompts can retrieve relevant context.
- The OpenAI key in Dennis Bot is used for Dennis Bot's final chat response.
- The provider configured inside SimpleMem is used for memory extraction, embeddings, and retrieval planning.

In normal operation, the flow is:

```text
Telegram message
  -> Dennis Bot records metadata locally
  -> Dennis Bot records user message in SimpleMem
  -> Dennis Bot retrieves relevant SimpleMem context
  -> Dennis Bot retrieves active knowledge-base context
  -> Dennis Bot builds the prompt
  -> LLM generates a response
  -> Dennis Bot sends response to Telegram
  -> Dennis Bot records assistant response in SimpleMem
```

## What SimpleMem Is

SimpleMem is an MCP server that exposes memory tools over HTTP. In this repo, the self-hosted implementation lives under:

```text
external/SimpleMem/MCP/
```

The server exposes these main endpoints:

- `GET /`: browser UI for registration/configuration.
- `POST /api/auth/register`: creates a SimpleMem user and returns a bearer token.
- `GET /api/health`: health check.
- `POST /mcp`: Streamable HTTP MCP endpoint.
- `GET /mcp`: server-to-client SSE stream for MCP.
- `DELETE /mcp`: terminate an MCP session.

The important MCP tools in our self-hosted version are:

- `memory_add`: add one dialogue and immediately process it into memory facts.
- `memory_add_batch`: add multiple dialogues.
- `memory_retrieve`: return raw relevant memory facts.
- `memory_query`: retrieve facts and synthesize an answer.
- `memory_stats`: return memory count/statistics.
- `memory_clear`: delete all memory for the SimpleMem user.

Dennis Bot primarily uses:

- `memory_add` for recording user and assistant messages.
- `memory_retrieve` for context injection.
- `memory_stats` for `/memory stats`.

It does not depend on `memory_query` for normal replies because Dennis Bot already has its own orchestrator and final LLM response path.

## How SimpleMem Stores Memory

SimpleMem does not store raw chat transcripts as its main retrieval unit. When a message is added, it uses an LLM to extract atomic, self-contained memory entries.

Example input:

```text
Darren [2026-05-15T09:00:00Z]: I prefer short direct replies.
```

Example extracted memory:

```json
{
  "lossless_restatement": "Darren prefers short direct replies.",
  "keywords": ["Darren", "short replies", "communication preference"],
  "timestamp": "2026-05-15T09:00:00Z",
  "location": null,
  "persons": ["Darren"],
  "entities": [],
  "topic": "Communication preference"
}
```

The extraction step is handled by:

```text
external/SimpleMem/MCP/server/core/memory_builder.py
```

The key ideas are:

- Coreference resolution: pronouns such as "he", "she", and "it" should be replaced with concrete names or entities.
- Temporal anchoring: relative times such as "tomorrow" should become absolute timestamps.
- Semantic compression: only durable, useful facts should become memory entries.
- Metadata extraction: people, locations, entities, keywords, and topic are stored alongside the fact.

After extraction, SimpleMem creates embeddings for the `lossless_restatement` text and stores the entry in LanceDB.

The vector store is implemented in:

```text
external/SimpleMem/MCP/server/database/vector_store.py
```

For each registered SimpleMem user, the server creates a separate LanceDB table. This gives basic tenant isolation inside the memory store.

## How SimpleMem Retrieves Memory

Retrieval is handled by:

```text
external/SimpleMem/MCP/server/core/retriever.py
```

SimpleMem can retrieve memory in several ways:

- Semantic search: embed the query and compare it against stored memory vectors.
- Keyword search: match important words from the query against stored entries.
- Structured search: filter by metadata such as people, entities, locations, or timestamps.
- Query planning: use an LLM to decide whether the question needs one search or multiple targeted searches.
- Reflection: for more complex questions, inspect whether retrieved results are sufficient and search again for missing information.

For Dennis Bot, the main retrieval path is `memory_retrieve`, not `memory_query`. That means SimpleMem returns raw facts, and Dennis Bot injects those facts into its own prompt.

This matters because Dennis Bot remains responsible for the final answer style, personality boundary, Telegram context, and knowledge-base grounding.

## How Dennis Bot Talks To SimpleMem

Dennis Bot's MCP client is implemented in:

```text
src/dennis_bot/mcp/simplemem.py
```

It uses JSON-RPC over Streamable HTTP:

```text
POST SIMPLEMEM_MCP_URL
Authorization: Bearer SIMPLEMEM_MCP_TOKEN
Accept: application/json, text/event-stream
Content-Type: application/json
```

The client first sends an MCP `initialize` request. For later requests, it includes the `Mcp-Session-Id` returned by the SimpleMem server.

The client can work with two tool shapes:

1. Lifecycle-style tools, such as `start_memory_session`, `record_message`, `retrieve_context`, and `finalize_memory_session`.
2. The self-hosted Docker SimpleMem tools, such as `memory_add`, `memory_retrieve`, and `memory_stats`.

Our self-hosted SimpleMem exposes the second shape. Dennis Bot detects that and enters Docker compatibility mode.

In Docker compatibility mode:

- `start_session` is synthetic. Dennis Bot creates a local session id such as `docker-simplemem:telegram-chat:<chat_id>`.
- `record_message` maps to `memory_add`.
- `retrieve_context` maps to `memory_retrieve`.
- `search_memory` maps to `memory_retrieve`.
- `finalize_session` is synthetic because this SimpleMem version processes every message immediately and has no explicit flush step.

This compatibility layer is why Dennis Bot can use the upstream SimpleMem MCP server without requiring SimpleMem to implement Dennis Bot's ideal session lifecycle API.

## Dennis Bot Memory Service

The main application memory layer is:

```text
src/dennis_bot/memory/service.py
```

`MemoryService` is responsible for:

- Creating or reusing the active memory session for a Telegram chat.
- Recording inbound user messages.
- Recording outbound assistant messages.
- Skipping SimpleMem writes for detected secrets.
- Recording every message locally for audit/provenance.
- Tracking whether SimpleMem extraction succeeded, failed, or was skipped.
- Finalizing local memory sessions after `SIMPLEMEM_MAX_SESSION_MESSAGES`.
- Retrieving memory context for the orchestrator.
- Managing manual memory records stored in Dennis Bot's own SQLite database.

The default session limit is:

```env
SIMPLEMEM_MAX_SESSION_MESSAGES=30
```

For hosted lifecycle-style SimpleMem, finalization would tell SimpleMem to close or summarize a session. For our Docker-compatible SimpleMem, finalization is local bookkeeping because `memory_add` processes messages immediately.

## Local Dennis Bot Tables

Dennis Bot creates its SQLite database automatically at:

```env
DATABASE_PATH=data/dennis_bot.sqlite3
```

The schema is in:

```text
migrations/0001_initial.sql
```

Memory-related tables:

- `conversation_messages`: local record of inbound and outbound Telegram messages, including whether they were included in SimpleMem.
- `memory_sessions`: local session lifecycle tracking per Telegram chat.
- `memory_records`: manual/admin memory records created with commands such as `/memory add`.
- `chats`: chat-level settings including full memory access flags.
- `users`: Telegram user metadata and admin/member role data.

This local DB is not a replacement for SimpleMem. It exists so Dennis Bot can keep operational truth even if SimpleMem is temporarily down.

For example, if a SimpleMem write fails:

- Dennis Bot still records the Telegram message locally.
- `memory_extraction_status` becomes `failed`.
- The memory session is marked `failed`.
- The bot can still keep operating, but memory quality may degrade.

## Prompt Injection Flow

The response orchestration lives in:

```text
src/dennis_bot/orchestrator/service.py
```

For each natural-language message, Dennis Bot:

1. Loads the Dennis Bot personality document.
2. Records the user message into memory.
3. Retrieves relevant memory context from SimpleMem.
4. Retrieves active knowledge context from the knowledge service.
5. Builds the final prompt.
6. Calls the chat LLM.
7. Records the assistant response into memory.

The prompt builder is:

```text
src/dennis_bot/prompts/builder.py
```

It includes these sections in the system prompt:

- Identity boundary.
- Secret handling rule.
- Personality profile.
- Memory context from SimpleMem.
- Active knowledge context from Dennis Bot's knowledge base.
- Telegram metadata.

The memory context is therefore supporting context, not the whole prompt. The bot still uses its personality, safety boundary, and current Telegram metadata on every response.

## Sensitive Content Handling

Dennis Bot has a local guard before writing to SimpleMem.

The function is:

```text
classify_sensitive_content()
```

in:

```text
src/dennis_bot/memory/service.py
```

It checks for obvious secret-like patterns, including:

- API keys.
- Secrets.
- Tokens.
- Passwords.
- Webhook secrets.
- Long token-like strings.
- Private key blocks.

If a message is classified as secret:

- It is not sent to SimpleMem.
- It is still recorded locally as a conversation message.
- Its `memory_extraction_status` is set to `skipped`.
- Metadata records the sensitivity tag.

This is important because SimpleMem stores extracted facts in a long-term vector database. We do not want credentials or private operational secrets becoming retrievable memory.

The prompt builder also includes a standing rule that the assistant should not include secrets in responses or normal conversational memory.

## Full Memory Access

Dennis Bot supports a distinction between normal chat context and full memory access.

The policy is in:

```text
src/dennis_bot/admin/policy.py
```

The orchestrator passes `full_memory_access=True` when a message comes from a trusted group or an admin-authorized context.

Current self-hosted Docker-compatible SimpleMem does not enforce per-chat filtering inside `memory_retrieve`; it stores memories per SimpleMem registered user/table. Dennis Bot still passes the access flag in metadata so the application interface is ready for stricter backends.

The design intent is:

- Normal chats should receive scoped memory context.
- Trusted group/admin flows may access broader assistant memory.
- Dennis Bot keeps chat/user metadata locally so stricter filtering can be added without changing Telegram ingestion.

## Manual Memory Commands

Telegram commands are routed in:

```text
src/dennis_bot/telegram/router.py
```

Memory command behavior is implemented by:

```text
src/dennis_bot/runtime/adapters.py
```

Supported command shape:

```text
/memory list
/memory add <text>
/memory remember <text>
/memory search <query>
/memory delete <id>
/memory stats
/memory finalize
```

Important distinction:

- Automatic conversation memory goes to SimpleMem.
- Manual `/memory add` records go into Dennis Bot's `memory_records` table.
- `/memory search` queries SimpleMem and also searches local Dennis Bot memory records.
- `/memory delete <id>` soft-deletes a local Dennis Bot `memory_records` row. It does not delete SimpleMem vector entries.
- `/memory stats` delegates to SimpleMem `memory_stats`.
- `/memory finalize` finalizes the active local session for that Telegram chat.

This gives admins a local memory-control surface without depending entirely on SimpleMem's storage model.

## Provider Setup

The self-hosted SimpleMem MCP server currently supports:

- OpenRouter.
- Ollama.

It does not directly support a plain OpenAI API key without code changes because the OpenRouter integration validates keys with the `sk-or-` prefix and calls OpenRouter-specific `/auth/key`.

Dennis Bot and SimpleMem have separate provider settings:

- Root `.env`: Dennis Bot runtime, Telegram, final response LLM, SimpleMem endpoint/token.
- `external/SimpleMem/.env`: SimpleMem's own LLM/embedding provider.

The SimpleMem MCP token is not the same thing as an OpenRouter or Ollama provider credential. The registration flow stores the provider credential inside SimpleMem and returns a bearer token that MCP clients use later. Dennis Bot only needs that bearer token; SimpleMem still needs a working provider configuration behind it.

For Dennis Bot:

```env
SIMPLEMEM_MCP_URL=http://localhost:8100/mcp
SIMPLEMEM_MCP_TOKEN=<token-returned-by-simplemem-registration>
SIMPLEMEM_TENANT_ID=dennis-bot-global
SIMPLEMEM_PROJECT=dennis-bot
SIMPLEMEM_MAX_SESSION_MESSAGES=30
```

For SimpleMem with OpenRouter:

```env
LLM_PROVIDER=openrouter
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4.1-mini
EMBEDDING_MODEL=qwen3-embedding:4b
EMBEDDING_DIMENSION=2560
```

Verify the exact embedding model id for the provider you use. The current local template uses `qwen3-embedding:4b`; some OpenRouter model ids may use provider-prefixed names.

For SimpleMem with Ollama:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen3:4b-instruct
EMBEDDING_MODEL=qwen3-embedding:4b
EMBEDDING_DIMENSION=2560
```

The embedding dimension must match the embedding model. If it is wrong, LanceDB storage/search can fail or produce invalid vector tables.

## Startup And Storage

Start self-hosted SimpleMem from:

```powershell
cd external\SimpleMem
docker compose --env-file .env up -d --build
```

It is exposed on:

```text
Web UI: http://localhost:8100/
MCP:    http://localhost:8100/mcp
```

SimpleMem storage is automatic:

- `users.db` stores registered SimpleMem users and encrypted provider keys.
- LanceDB stores memory vectors.
- Docker uses the named volume `simplemem_data`.

Dennis Bot storage is also automatic:

- SQLite database at `data/dennis_bot.sqlite3` locally.
- Migrations run on app startup.
- Docker deployments use the `dennis_bot_data` named volume.

No manual database creation is required for either service.

## Why We Adapted SimpleMem This Way

The upstream SimpleMem MCP server is a general-purpose memory service. Dennis Bot needs a Telegram-aware memory layer with operational controls.

The adaptation gives us:

- Telegram provenance: every stored or skipped message can be traced back to chat/user/message metadata.
- Local auditability: Dennis Bot has a local record even when SimpleMem is unavailable.
- Safety: secret-like content is skipped before it reaches the vector memory store.
- Compatibility: Dennis Bot can work with lifecycle-style SimpleMem tools or the Docker-compatible `memory_add`/`memory_retrieve` tools.
- Prompt ownership: SimpleMem retrieves memory, but Dennis Bot decides how to combine memory with personality, knowledge context, and Telegram metadata.
- Operational control: admins can inspect stats, add local manual memories, search, soft-delete local records, and finalize sessions.
- Future flexibility: tenant/project/session metadata is already present, even though the current self-hosted backend mostly uses a per-token table.

## Limitations To Remember

Current limitations:

- The self-hosted SimpleMem Docker toolset does not implement true session lifecycle tools.
- Local `/memory delete` does not remove SimpleMem vector entries.
- Chat/user scoping is mostly enforced by Dennis Bot metadata and local policy, not by the current SimpleMem Docker backend.
- Direct OpenAI API support for SimpleMem would require a code change.
- Memory extraction quality depends on the configured SimpleMem LLM and embedding model.
- Stored memory is only as good as the extraction step; vague or noisy messages may produce weak facts.

## Mental Model

Think of the system in layers:

```text
Dennis Bot
  Owns Telegram integration, app policy, prompt construction, local audit DB,
  manual memory records, and final response generation.

SimpleMem MCP
  Owns semantic extraction, vector storage, and retrieval of long-term facts.

LLM providers
  Dennis Bot uses one provider for replies.
  SimpleMem uses its configured provider for extraction, embeddings, and retrieval planning.
```

The key learning point is that memory is not just "saving chat history." In this design, memory is an extraction and retrieval pipeline:

```text
raw message -> structured fact -> embedding -> vector store -> relevant context -> final prompt
```

Dennis Bot wraps that pipeline with Telegram-specific session tracking, safety checks, and operational controls.
