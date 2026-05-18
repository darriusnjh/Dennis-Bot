# Dennis Bot PRD

## 1. Overview

Dennis Bot is a Telegram-accessible personal AI bot with a defined personality, persistent memory, configurable knowledge-base states, sticker-pack access, and scheduled web monitoring jobs. The bot should operate in direct messages and Telegram group chats, respond conversationally, send stickers from an owned Telegram sticker pack, remember useful context over time, and proactively check configured websites for updates.

## 2. Goals

- Provide a Telegram bot named `dennis-bot` that users can interact with through direct messages and group chats.
- Give the bot a consistent personality that shapes tone, behavior, and decision-making.
- Allow the bot to send stickers from a configured Telegram sticker pack.
- Maintain persistent memory across conversations, including automatic conversation-history storage.
- Allow the trusted Telegram group chat to access all assistant memory by default.
- Support multiple knowledge-base states, such as default knowledge, project-specific knowledge, and temporary task context.
- Run scheduled cron jobs that check configured websites for updates and notify the user or group when relevant changes are found.
- Monitor Dennis Toh's public website and Instagram activity through Bright Data APIs.
- Use a separate knowledge-update agent to update the Dennis Toh knowledge base when official-site changes are impactful enough.
- Provide clear controls for configuring sticker packs, memory behavior, web monitors, notification routing, and privacy.

## 3. Non-Goals

- Replacing Telegram clients or providing a full custom chat application.
- Sending messages, stickers, or alerts to users who have not initiated contact with the bot or added it to a chat.
- Scraping websites that prohibit automated access or require bypassing access controls.
- Making autonomous purchases, account changes, or destructive actions without explicit confirmation.
- Building a full public SaaS product in the first version.

## 4. Target Users

- Primary user: the owner/operator of `dennis-bot`.
- Secondary users: trusted members of Telegram group chats where the bot is installed.
- Admin users: people allowed to configure memory, knowledge states, monitored sites, and bot behavior.

## 5. User Stories

- As the owner, I want to message Dennis in Telegram so I can interact with the bot from the chat app I already use.
- As the owner, I want Dennis to use my sticker pack so responses can include personality-rich stickers.
- As the owner, I want Dennis to remember durable facts about me and ongoing projects so I do not need to repeat context.
- As the owner, I want memory to be inspectable and editable so incorrect memories can be removed.
- As the owner, I want Dennis to use different knowledge states so I can switch between general, project, study, work, and personal contexts.
- As the owner, I want Dennis to check selected websites on a schedule so I can get updates without manually checking them.
- As a group member, I want Dennis to respond when mentioned or when commands are used so it does not interrupt normal chat.
- As a trusted group member, I want Dennis to use all available assistant memory in the group so the group gets continuity across conversations.
- As an admin, I want official-site changes to update Dennis's knowledge base through a separate review/update agent.

## 6. Functional Requirements

### 6.1 Telegram Bot Interface

- The system must expose a Telegram Bot API integration for `dennis-bot`.
- The production deployment should be webhook-first for Telegram updates.
- The system may support long polling as a local-development fallback, but webhooks are the preferred operating mode.
- The bot must support direct messages.
- The bot must support group chats.
- In group chats, the bot must respect Telegram privacy mode expectations:
  - If privacy mode is enabled, respond only to commands, mentions, and replies to the bot.
  - If privacy mode is disabled, process group messages according to configured group rules.
- The bot must support core commands:
  - `/start` - introduce the bot and initialize chat settings.
  - `/help` - list available commands.
  - `/settings` - show configurable settings for the current chat.
  - `/memory` - inspect or manage stored memory.
  - `/kb` - list, switch, or inspect knowledge-base states.
  - `/stickers` - test sticker access and list configured sticker aliases.
  - `/watch` - manage monitored websites.
  - `/status` - show health, active knowledge state, active memory scope, and scheduled jobs.
- The bot should support natural-language requests in addition to commands where safe.

### 6.2 Sticker Pack Access

- The system must allow configuration of one or more Telegram sticker pack names.
- The system must call Telegram `getStickerSet` for configured packs and cache sticker metadata.
- The system must store sticker `file_id`s for sending through `sendSticker`.
- The bot must support sticker aliases, such as:
  - `thinking`
  - `approved`
  - `confused`
  - `celebrate`
  - `warning`
- The bot should select stickers based on conversation context and personality rules.
- Admin users must be able to test whether a configured sticker can be sent.
- If a sticker pack changes, the bot should refresh cached sticker metadata on demand and on a scheduled interval.

### 6.3 Personality

- The bot must have a configurable base personality prompt.
- Personality configuration must include:
  - tone
  - communication style
  - behavioral boundaries
  - preferred response length
  - humor level
  - when to send stickers
  - group chat behavior
- The bot must preserve personality consistency across direct messages and group chats.
- The bot must allow admin-only personality updates.
- Personality changes must be versioned so the owner can roll back to a previous configuration.

### 6.4 Memory State

- The recommended implementation must use SimpleMem MCP as the primary semantic long-term memory backend.
- SimpleMem-Cross direct integration or self-hosted SimpleMem MCP may be used later if the hosted MCP service is insufficient.
- Dennis Bot's own persistence layer must remain the source of truth for Telegram metadata, raw conversation history, permissions, deletes, and audit records.
- SimpleMem must be used for compressed long-term memory, session summaries, durable observations, semantic retrieval, and prompt context injection.
- The system must maintain memory in separate scopes:
  - `conversation_history`: append-only inbound and outbound Telegram message history.
  - `chat_session`: short-lived context for the current conversation.
  - `user_profile`: durable facts about the owner or user.
  - `project_memory`: durable facts tied to a project or topic.
  - `group_memory`: facts specific to a group chat.
  - `pending_memory`: proposed memories awaiting approval, if approval mode is enabled.
- The system must automatically write conversation history for direct messages and trusted group chats.
- The system must automatically extract durable memories from conversation history when confidence is high enough.
- The memory service must maintain one active SimpleMem MCP memory session per Telegram chat.
- A SimpleMem memory session must be finalized after 30 recorded conversation messages.
- The 30-message count includes inbound Telegram user messages and outbound assistant replies that are recorded into memory.
- On finalization, the memory service must call the appropriate SimpleMem MCP finalization/end-session tool, persist the finalization report, and start a new active memory session for the chat on the next message.
- The memory service should also finalize any active sessions during graceful shutdown to avoid losing recorded events.
- The default SimpleMem tenant should be `dennis-bot-global` so the trusted group can retrieve all assistant memory.
- The default SimpleMem project should be `dennis-bot`.
- The system must support explicit memory updates through user requests, such as "remember this" or "update your memory".
- The trusted group chat must have full assistant-memory access by default, including `user_profile`, `project_memory`, `group_memory`, and relevant conversation history.
- The group full-memory policy must be configurable by admin, but the initial product default is full memory access for the configured trusted group.
- The bot must support memory operations:
  - add memory
  - retrieve memory
  - update memory
  - delete memory
  - summarize memory
  - search memory
- The bot must tag sensitive or high-impact memories when detected so they can be audited, deleted, or restricted later.
- The bot must allow the owner to inspect all stored memory.
- The bot must include memory provenance:
  - source chat
  - source message ID where available
  - SimpleMem session ID where applicable
  - SimpleMem memory entry ID where applicable
  - created timestamp
  - updated timestamp
  - confidence or importance score
- The bot must not treat API keys, bot tokens, provider secrets, or internal credentials as normal conversational memory.

### 6.4.1 SimpleMem MCP Integration Requirements

- The system must support SimpleMem through a dedicated `MemoryService` abstraction rather than calling MCP tools directly from Telegram handlers.
- The MVP should use the hosted SimpleMem MCP endpoint if credentials are available.
- The system must not require cloning the SimpleMem repository for the hosted MCP approach.
- The `MemoryService` must connect to SimpleMem MCP over Streamable HTTP or the supported MCP transport.
- The `MemoryService` must be configured with:
  - `SIMPLEMEM_MCP_URL`
  - `SIMPLEMEM_MCP_TOKEN`
  - `SIMPLEMEM_TENANT_ID`, default `dennis-bot-global`
  - `SIMPLEMEM_PROJECT`, default `dennis-bot`
- The system must support the required SimpleMem MCP tool capabilities:
  - start memory session
  - record user or assistant message
  - retrieve context for prompt
  - search memory
  - finalize or stop memory session
  - get memory stats or health
- If the hosted MCP service does not expose all required session lifecycle tools, the fallback must be self-hosted SimpleMem MCP or direct SimpleMem-Cross integration.
- If self-hosted SimpleMem MCP is chosen later, SimpleMem storage must use persistent volumes for its SQLite database and LanceDB vector store.
- The bot must call SimpleMem MCP context retrieval before LLM response generation and inject the returned context into the response prompt.
- The bot must record both user messages and assistant replies into the active SimpleMem MCP session.
- The bot must expose memory commands that operate against both Dennis Bot's source-of-truth tables and SimpleMem retrieval:
  - `/memory search <query>`
  - `/memory stats`
  - `/memory finalize`
  - `/memory delete <id>` where supported by the source-of-truth layer
- The bot must keep canonical deletes and audit state in Dennis Bot's own database even if SimpleMem does not natively support hard deletion of every derived vector artifact.
- SimpleMem should not replace the Dennis Toh knowledge base. The knowledge base remains a versioned canonical source, while SimpleMem stores conversation-derived observations and summaries.

### 6.5 Knowledge-Base States

- The system must support multiple named knowledge-base states.
- A knowledge-base state represents a selected context package the bot can use while answering.
- Each knowledge state must include:
  - name
  - description
  - source documents or URLs
  - enabled/disabled status
  - access scope: owner-only, group-specific, or global
  - last indexed timestamp
  - version
- The bot must support switching knowledge states per chat.
- The bot must support commands to list and inspect active knowledge states.
- The system should support retrieval-augmented generation over indexed knowledge sources.
- The bot must cite internal knowledge sources when the answer relies on indexed documents, where practical.
- Knowledge updates must not overwrite previous versions without retaining a rollback path.
- The system must include a separate knowledge-update agent that can update knowledge-base states from monitored source changes or explicit user requests.
- The knowledge-update agent must be triggered when:
  - an official-site monitor detects a content change
  - an admin asks the bot to update the knowledge base
  - an admin manually runs a monitor and requests KB refresh
- The knowledge-update agent must classify detected changes as:
  - `minor`: formatting, layout, navigation, or low-value wording changes
  - `notifiable`: content changes worth sending to the group but not worth changing the knowledge base
  - `kb_impactful`: factual changes that should update the Dennis Toh knowledge base
- For `kb_impactful` changes, the knowledge-update agent must create a new knowledge-base version and record the source diff.
- For low-confidence changes, the knowledge-update agent should create a pending update proposal for admin review instead of applying the change directly.

### 6.6 Web Monitoring and Cron Jobs

- The system must allow admins to register websites or feeds for monitoring.
- Each monitor must include:
  - URL
  - name
  - schedule
  - target chat for notifications
  - change detection strategy
  - relevance filter
  - impact policy
  - notification threshold
  - enabled/disabled status
- The system must support schedule definitions such as:
  - every N minutes
  - hourly
  - daily at a configured time
  - custom cron expression
- The system must support change detection strategies:
  - RSS or Atom feed polling when available
  - sitemap or page metadata checks
  - page text hash comparison
  - selected CSS selector extraction
  - semantic summary comparison for pages where simple hashes are too noisy
- The system must store the last seen state for each monitored source.
- The default relevance rule for Dennis monitors is "any normalized content change".
- Relevance filters should classify routing and impact; they should not suppress Dennis public-activity changes unless an admin explicitly configures suppression.
- The bot must notify the configured chat when a relevant update is detected.
- Notifications must include:
  - source name
  - title or detected change summary
  - URL
  - detected timestamp
  - why the update matched the relevance filter
- The system must avoid duplicate notifications for the same update.
- The system should support manual monitor execution through `/watch run <name>`.
- The system must respect rate limits, robots.txt where applicable, and site-specific usage constraints.

### 6.6.1 Dennis Toh Public Activity Monitors

- The system must include default monitor templates for Dennis Toh's public web presence:
  - `dennis_official_site`: `https://www.dennistohsg.com/`
  - `dennis_official_site_about`: `https://www.dennistohsg.com/about`
  - `dennis_instagram`: `https://www.instagram.com/dennistohsg/`
- The official-site monitors must detect meaningful site updates, including:
  - new or changed biography text
  - new portfolio or media items
  - new work credits
  - new music, CV, or contact-page updates
  - changed outbound social links
- The official-site monitors must dispatch detected content changes to the knowledge-update agent.
- The official-site monitors must notify the configured group chat for any normalized site-content change.
- The Instagram monitor must detect all public, Bright-Data-accessible activity from the configured Dennis Toh Instagram profile.
- Instagram activity must include, where publicly available and supported by the provider:
  - profile display-name, bio, profile photo, link, and verification changes
  - new posts
  - new reels
  - new carousel posts
  - caption or media edits on existing posts or reels
  - public comments on monitored posts or reels
  - public tagged or mentioned media, if supported by the chosen Bright Data endpoint
  - public story or highlight metadata, if supported by the chosen Bright Data endpoint
- The system must store the last seen Instagram activity identifier, permalink, timestamp, content hash, activity type, media type, and engagement snapshot where available.
- Engagement-count drift alone should be stored as a snapshot but should not trigger a Telegram alert unless an admin enables metric-change alerts.
- The system must notify the configured Telegram group chat when public Instagram activity is detected.
- The notification should include:
  - activity type
  - activity URL
  - caption or profile-change excerpt
  - media type
  - posted or activity timestamp where available
  - detected timestamp
  - short AI-generated summary
- Instagram activity should update the group chat by default.
- Instagram activity should update the knowledge base only when the knowledge-update agent classifies it as `kb_impactful`, such as a new major role, production, business, award, public biography change, or official career milestone.
- The bot should update public-profile knowledge-base states when official-site changes are classified as `kb_impactful`.
- The default monitoring schedule should be configurable, with recommended starting values:
  - Instagram: every 60 minutes.
  - Official website: every 6 hours.
  - Manual refresh: `/watch run dennis_instagram` or `/watch run dennis_official_site`.

### 6.6.2 Bright Data Integration

- The system must use Bright Data as the preferred data access provider for Dennis public-activity monitors.
- Bright Data credentials and zones must be configured through environment variables or a secret manager:
  - `BRIGHTDATA_API_KEY`
  - `BRIGHTDATA_WEB_UNLOCKER_ZONE`
  - `BRIGHTDATA_INSTAGRAM_DATASET_ID_PROFILE`, if required by the chosen endpoint
  - `BRIGHTDATA_INSTAGRAM_DATASET_ID_POSTS`, if required by the chosen endpoint
  - `BRIGHTDATA_INSTAGRAM_DATASET_ID_REELS`, if required by the chosen endpoint
  - `BRIGHTDATA_INSTAGRAM_DATASET_ID_COMMENTS`, if required by the chosen endpoint
  - `BRIGHTDATA_WEBHOOK_SECRET`
- For Instagram monitoring, the system should use Bright Data's Instagram or Social Media Scraper APIs to collect structured public profile, post, reel, and supported activity data.
- For website monitoring, the system should use Bright Data Web Unlocker or an equivalent Bright Data web access endpoint to fetch clean HTML or markdown from the public website.
- The system must support both Bright Data request modes:
  - synchronous requests for low-volume checks and known URLs
  - asynchronous jobs with webhook delivery for discovery, batch collection, or slower Instagram profile/post discovery
- Bright Data asynchronous delivery should use signed webhook callbacks where available.
- The system must normalize Bright Data responses into internal monitor records before diffing, summarizing, or sending Telegram alerts.
- The system must record Bright Data run metadata:
  - provider request ID or snapshot ID
  - endpoint or dataset used
  - request mode
  - status
  - started timestamp
  - completed timestamp
  - response record count
  - cost-relevant record count where available
  - error message if failed
- The system must retry transient Bright Data failures with exponential backoff and avoid retry storms.
- The system must fail closed when Bright Data credentials are missing: monitor jobs should be disabled and `/status` should show a clear configuration error.
- The system must not use Bright Data to access private Instagram data, login-gated content, direct messages, private comments, or any non-public information.

### 6.7 Admin and Configuration

- The system must identify bot admins by Telegram user ID.
- Admin-only operations must include:
  - modifying personality
  - adding or removing monitored sites
  - changing memory policy
  - configuring trusted group chat IDs
  - changing knowledge-base states
  - enabling group-wide message reading
  - changing notification destinations
- Configuration must be stored persistently.
- Secrets must be supplied through environment variables or a secret manager, not committed to source control.
- The system must provide a startup validation check for required configuration.

### 6.8 Observability

- The system must log key events:
  - bot startup and shutdown
  - incoming update handling
  - Telegram API failures
  - scheduled job execution
  - web monitor changes detected
  - memory writes and deletes
  - knowledge-base indexing runs
  - knowledge-update agent runs and decisions
- Logs must redact secrets and sensitive message contents by default.
- The system should expose a health check for deployment monitoring.
- The system should track metrics:
  - number of messages handled
  - average response latency
  - failed Telegram API calls
  - cron job success/failure counts
  - monitor changes detected
  - memory retrieval latency

## 7. Product Behavior

### 7.1 Direct Message Behavior

- Default to full conversational behavior.
- Use owner-specific memory, global assistant memory, and the active knowledge state.
- Allow proactive notifications from web monitors if the user has started a chat with the bot.
- Allow the bot to send stickers when contextually appropriate.

### 7.2 Group Chat Behavior

- Default to low-interruption behavior.
- Respond to commands, mentions, and direct replies.
- Use all assistant memory for the configured trusted group chat.
- Use the active group knowledge state plus any global Dennis Toh knowledge states.
- Treat full memory access as an intentional trusted-group feature, not an accidental leak.
- Send stickers only when they add value and do not spam the chat.

### 7.3 Proactive Notification Behavior

- The bot may send proactive messages only to chats that have opted in.
- Web monitor alerts should be concise by default.
- The bot should batch related updates when multiple changes occur in a short period.
- Users should be able to mute, pause, or disable monitors.

## 8. Data Model

### 8.1 User

- `telegram_user_id`
- `display_name`
- `role`: owner, admin, member
- `trusted_group_memory_access`
- `created_at`
- `updated_at`

### 8.2 Chat

- `telegram_chat_id`
- `chat_type`: direct, group, supergroup, channel
- `title`
- `active_knowledge_state_id`
- `memory_policy`
- `full_memory_access_enabled`
- `sticker_policy`
- `notifications_enabled`
- `default_monitor_notifications`
- `created_at`
- `updated_at`

### 8.3 Memory

- `id`
- `scope`: conversation_history, chat_session, user_profile, project_memory, group_memory
- `owner_user_id`
- `chat_id`
- `simplemem_tenant_id`
- `simplemem_session_id`
- `simplemem_entry_id`
- `content`
- `tags`
- `importance`
- `confidence`
- `sensitivity`
- `source_message_id`
- `created_at`
- `updated_at`
- `deleted_at`

### 8.3.1 ConversationMessage

- `id`
- `telegram_chat_id`
- `telegram_user_id`
- `telegram_message_id`
- `direction`: inbound, outbound
- `message_type`: text, sticker, image, file, command, system
- `content`
- `content_hash`
- `memory_extraction_status`: pending, extracted, skipped, failed
- `simplemem_session_id`
- `included_in_simplemem`
- `created_at`

### 8.3.2 MemorySession

- `id`
- `telegram_chat_id`
- `simplemem_tenant_id`
- `simplemem_project`
- `simplemem_memory_session_id`
- `content_session_id`
- `message_count`
- `max_message_count`: default 30
- `status`: active, finalizing, finalized, failed
- `started_at`
- `finalized_at`
- `finalization_report_ref`
- `error_message`

### 8.4 KnowledgeState

- `id`
- `name`
- `description`
- `access_scope`
- `version`
- `enabled`
- `source_refs`
- `index_status`
- `last_indexed_at`
- `last_updated_by_agent_id`
- `created_at`
- `updated_at`

### 8.4.1 KnowledgeUpdateJob

- `id`
- `source_monitor_id`
- `source_url`
- `source_type`: official_site, instagram, manual
- `detected_change_ref`
- `impact_classification`: minor, notifiable, kb_impactful
- `status`: queued, running, applied, pending_review, rejected, failed
- `summary`
- `knowledge_state_id`
- `previous_version`
- `new_version`
- `created_at`
- `completed_at`

### 8.5 StickerPack

- `id`
- `pack_name`
- `title`
- `enabled`
- `last_synced_at`
- `created_at`
- `updated_at`

### 8.6 StickerAlias

- `id`
- `pack_id`
- `alias`
- `file_id`
- `emoji`
- `tags`
- `enabled`
- `created_at`
- `updated_at`

### 8.7 WebMonitor

- `id`
- `name`
- `url`
- `monitor_type`: website, instagram_profile, instagram_posts, rss, custom
- `provider`: direct, brightdata
- `provider_config_ref`
- `schedule`
- `change_detection_strategy`
- `relevance_filter`
- `impact_policy`
- `notify_on_any_change`
- `knowledge_update_enabled`
- `target_chat_id`
- `source_handle`
- `last_seen_external_id`
- `last_seen_permalink`
- `last_seen_hash`
- `last_seen_content_ref`
- `last_seen_published_at`
- `last_checked_at`
- `last_notified_at`
- `enabled`
- `created_at`
- `updated_at`

### 8.8 MonitorRun

- `id`
- `web_monitor_id`
- `provider`
- `provider_endpoint`
- `provider_request_id`
- `provider_snapshot_id`
- `request_mode`: sync, async, webhook
- `status`: queued, running, succeeded, failed, skipped
- `records_returned`
- `records_changed`
- `started_at`
- `completed_at`
- `error_code`
- `error_message`

### 8.9 SocialActivityItem

- `id`
- `web_monitor_id`
- `platform`: instagram
- `external_id`
- `activity_type`: profile_update, post, reel, carousel, comment, tagged_media, mention, story, highlight, metric_snapshot, unknown
- `actor_handle`
- `permalink`
- `media_type`: post, reel, carousel, story, unknown
- `caption`
- `caption_hash`
- `published_at`
- `detected_at`
- `thumbnail_ref`
- `engagement_snapshot`
- `raw_provider_record_ref`
- `notified_at`

## 9. Technical Architecture

### 9.1 Components

- Telegram update handler
  - Receives Telegram webhook updates in production.
  - Supports long polling as a local-development fallback.
  - Normalizes incoming messages and commands.
- Conversation orchestrator
  - Applies personality, memory, knowledge state, and safety rules.
  - Decides whether to answer, ask for clarification, store memory, or send stickers.
- LLM adapter
  - Wraps calls to the selected language model provider.
  - Handles model configuration, tool calls, retries, and timeouts.
- Memory service
  - Stores conversation history automatically.
  - Owns SimpleMem MCP session lifecycle.
  - Records user messages and assistant replies into SimpleMem MCP.
  - Finalizes memory sessions after 30 recorded conversation messages.
  - Extracts durable memory from conversations through SimpleMem finalization.
  - Retrieves, searches, and summarizes memories through SimpleMem and Dennis Bot's source-of-truth tables.
  - Maintains Telegram provenance, deletion state, and audit records outside SimpleMem.
- SimpleMem MCP client
  - Connects to the hosted SimpleMem MCP endpoint.
  - Handles MCP authentication and transport.
  - Maps SimpleMem MCP tool calls into `MemoryService` operations.
  - Reports capability and health status at startup.
- Knowledge-base service
  - Ingests documents and URLs.
  - Builds searchable indexes.
  - Retrieves relevant context for responses.
- Knowledge-update agent
  - Runs separately from normal conversation handling.
  - Reviews official-site and Instagram monitor changes.
  - Classifies impact.
  - Updates knowledge-base states when changes are `kb_impactful`.
  - Creates pending review proposals when confidence is low.
- Sticker service
  - Syncs Telegram sticker sets.
  - Resolves aliases to sticker `file_id`s.
  - Sends stickers through Telegram.
- Scheduler
  - Runs cron jobs and manual monitor executions.
- Web monitor service
  - Fetches configured sites.
  - Detects changes.
  - Filters relevance.
  - Emits notifications.
- Bright Data integration service
  - Calls Bright Data Instagram/Social Media Scraper APIs for public Instagram profile, post, reel, comment, and supported activity monitoring.
  - Calls Bright Data Web Unlocker or equivalent web access endpoints for public website checks.
  - Handles sync requests, async job polling, webhook result validation, retries, and provider error normalization.
  - Normalizes provider output into `WebMonitor`, `MonitorRun`, and `SocialActivityItem` records.
- Webhook receiver
  - Receives Telegram webhooks.
  - Receives Bright Data asynchronous delivery webhooks.
  - Verifies webhook secrets or signatures before processing payloads.
- Admin/config service
  - Manages settings, roles, and policy decisions.
- Persistence layer
  - Stores configuration, memory, knowledge metadata, monitor states, and logs.

### 9.2 Suggested Initial Stack

- Runtime: Node.js/TypeScript or Python.
- Telegram library:
  - Node.js: `grammY` or `telegraf`.
  - Python: `python-telegram-bot` or `aiogram`.
- Database: SQLite for local MVP, PostgreSQL for production.
- Memory backend: SimpleMem MCP for hosted MVP integration.
- Memory fallback options:
  - self-hosted SimpleMem MCP
  - direct SimpleMem-Cross Python integration
- Vector search: SimpleMem-managed retrieval for memory; separate knowledge-base vector indexing only if required for canonical KB retrieval.
- Scheduler:
  - Local MVP: process-based cron scheduler.
  - Production: durable job queue or hosted scheduler.
- Data access provider: Bright Data for Instagram and official-site monitoring.
- Deployment:
  - MVP: single service with Telegram webhooks and local polling fallback.
  - Production: webhook-based service with persistent storage and health checks.

## 10. Security, Privacy, and Safety

- Telegram bot token must be stored in an environment variable.
- Admin Telegram user IDs must be explicitly configured.
- The bot must reject admin commands from unauthorized users.
- The configured trusted group chat has full assistant-memory access by default.
- Full group memory access must be restricted to explicitly configured Telegram group chat IDs.
- Automatic memory writes must tag sensitive content for audit and deletion.
- Secrets, API keys, bot tokens, webhook secrets, and provider credentials must never be stored as normal conversational memory.
- SimpleMem MCP token and endpoint configuration must not be committed to source control.
- If self-hosting SimpleMem, storage paths must be backed up and protected as sensitive data because they contain conversation-derived memories.
- SimpleMem context injection must be scoped to the configured global tenant unless a future privacy model introduces tenant separation.
- Logs must not include full private messages unless debug mode is explicitly enabled.
- Web monitoring must not bypass authentication, paywalls, or anti-bot controls.
- Bright Data API keys, zones, dataset IDs, and webhook secrets must not be committed to source control.
- Instagram monitoring must be limited to public, Bright-Data-accessible activity. The bot must not attempt to access private Instagram data, login-gated activity, direct messages, or private account information.
- Bright Data raw responses must be retained only as long as needed for deduplication, debugging, and audit.
- The bot must support deleting user memory and monitor history.
- The bot should implement rate limiting per chat and per user.

## 11. Success Metrics

- Bot responds to direct messages with less than 3 seconds median latency excluding LLM latency.
- Bot can be added to a Telegram group and respond to mentions or commands.
- Bot receives Telegram updates through webhooks in the target deployment.
- Bot can send at least one sticker from the configured sticker pack.
- Owner can inspect, add, and delete memory through Telegram commands.
- Conversation history is automatically stored and searchable.
- SimpleMem MCP memory sessions finalize after 30 recorded conversation messages.
- Trusted group chat can use all assistant memory.
- Owner can switch between at least two knowledge-base states.
- Cron jobs can detect and notify at least one configured website update.
- Cron jobs can detect and notify public Instagram activity from the configured Dennis Toh Instagram page.
- Impactful official-site changes update the Dennis Toh knowledge base through the knowledge-update agent.
- No duplicate notification is sent for the same detected website update.
- No duplicate notification is sent for the same Instagram activity item.
- Admin-only commands are blocked for non-admin users.

## 12. MVP Scope

The MVP should include:

- Telegram direct-message support over webhooks.
- Telegram group support for commands, mentions, and replies.
- Configurable base personality prompt.
- One configured sticker pack with alias-based sticker sending.
- Persistent memory using SQLite or equivalent local storage.
- SimpleMem MCP as the semantic long-term memory backend.
- SimpleMem memory sessions finalized every 30 recorded conversation messages.
- Automatic conversation-history memory writes.
- Trusted group full-memory access.
- Basic memory commands: list, add, delete, search, stats, finalize.
- Knowledge-base state metadata and manual switching.
- Separate knowledge-update agent for official-site changes and explicit KB update requests.
- Basic local document or text knowledge ingestion.
- Bright Data-backed monitor configuration for Dennis Toh's official website.
- Bright Data-backed Instagram monitor for Dennis Toh's public Instagram activity.
- Web monitor configuration for RSS feeds, simple page hash checks, and Bright Data-fetched website snapshots.
- Scheduled polling jobs.
- Admin allowlist.
- Environment-based secrets.
- Basic logs and `/status`.

## 13. Post-MVP Enhancements

- Web admin dashboard.
- Advanced memory approval workflow.
- Semantic web page diffing.
- Per-group personality overrides.
- Multiple sticker packs with automatic mood matching.
- Rich knowledge ingestion from PDFs, Notion, Google Drive, or GitHub.
- Durable distributed job queue.
- Message batching and digest notifications.
- Retrieval citations in every knowledge-backed response.
- Analytics dashboard.

## 14. Milestones

### Milestone 1: Bot Foundation

- Create Telegram bot through BotFather.
- Implement Telegram webhook update handling.
- Add local long-polling fallback for development.
- Add `/start`, `/help`, `/status`.
- Add admin allowlist.
- Add configuration validation.

### Milestone 2: Personality and Stickers

- Add personality prompt configuration.
- Add sticker pack sync through `getStickerSet`.
- Store sticker aliases.
- Send stickers through `sendSticker`.
- Add `/stickers` command.

### Milestone 3: Memory

- Add persistent memory storage.
- Add SimpleMem MCP client configuration.
- Add `MemoryService` abstraction around SimpleMem MCP.
- Add `MemorySession` tracking.
- Add automatic conversation-history writes.
- Add SimpleMem session rollover after 30 recorded conversation messages.
- Add durable memory extraction from conversation history.
- Add memory CRUD commands.
- Add memory retrieval to responses.
- Add trusted group full-memory access.

### Milestone 4: Knowledge States

- Add knowledge-state model.
- Add active state per chat.
- Add basic knowledge ingestion.
- Add knowledge-update agent.
- Add official-site change-to-KB update flow.
- Add explicit "update knowledge base" trigger.
- Add retrieval into bot responses.
- Add `/kb` commands.

### Milestone 5: Web Monitors and Cron

- Add monitor model.
- Add Bright Data configuration and credential validation.
- Add Bright Data integration service.
- Add scheduler.
- Add Dennis Toh official-site monitor using Bright Data Web Unlocker or equivalent web access endpoint.
- Add Dennis Toh Instagram activity monitor using Bright Data Instagram/Social Media Scraper APIs.
- Add Bright Data asynchronous webhook receiver where available.
- Add group-chat notifications for any normalized Dennis website or Instagram content change.
- Add RSS and page hash checks for non-Bright Data sources.
- Add deduped Telegram notifications.
- Add `/watch` commands.

### Milestone 6: Hardening

- Add tests for command authorization, full group memory access, sticker alias resolution, knowledge-update agent behavior, and web monitor deduplication.
- Add structured logging.
- Add deployment documentation.
- Add backup and restore guidance for memory and configuration.

## 15. Open Questions

- Which language stack should be used for the first implementation?
- Does the hosted SimpleMem MCP endpoint expose all required session lifecycle tools, or do we need self-hosted MCP for full control?
- What sticker pack name and sticker aliases should be configured initially?
- What knowledge sources should be included in the first knowledge-base state?
- What is the trusted Telegram group chat ID for full memory access and monitor notifications?
- What exact impact threshold should the knowledge-update agent use before automatically updating the Dennis Toh knowledge base?
- Which Bright Data Instagram endpoints support the required activity types for stories, highlights, mentions, tagged media, and comments?
- Should metric-only Instagram changes, such as follower or like-count movement, send group alerts or remain stored snapshots only?
- Which Bright Data endpoint/dataset IDs and zones will be used for the production account?
- Where should the bot be deployed for always-on cron jobs?
