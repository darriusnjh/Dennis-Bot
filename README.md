# Dennis Bot

Telegram assistant with Dennis Bot personality, SimpleMem MCP memory, sticker-pack support, knowledge-base updates, and Bright Data monitors for Dennis Toh public activity.

## Setup

1. Create a virtual environment with Python 3.11+.
2. Install dependencies:

```bash
pip install -e ".[dev]"
```

3. Copy environment settings:

```bash
cp .env.example .env
```

4. Fill in Telegram, LLM, SimpleMem MCP, Bright Data, and admin settings.

5. Start the API:

```bash
dennis-bot
```

Health check:

```bash
curl http://localhost:8000/health
```

## Current Design

- Telegram production ingress is webhook-first, with polling as a local fallback.
- Dennis Bot's database remains the source of truth for Telegram provenance, audit, and deletes.
- SimpleMem MCP is the semantic memory backend.
- Memory sessions finalize after 30 recorded conversation messages.
- The trusted Telegram group can use all assistant memory by design.

