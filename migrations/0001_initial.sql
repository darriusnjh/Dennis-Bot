CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    trusted_group_memory_access INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS knowledge_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    access_scope TEXT NOT NULL DEFAULT 'global',
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_refs TEXT,
    index_status TEXT NOT NULL DEFAULT 'pending',
    last_indexed_at TEXT,
    last_updated_by_agent_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS chats (
    telegram_chat_id INTEGER PRIMARY KEY,
    chat_type TEXT NOT NULL CHECK (chat_type IN ('direct', 'group', 'supergroup', 'channel')),
    title TEXT,
    active_knowledge_state_id INTEGER REFERENCES knowledge_states(id) ON DELETE SET NULL,
    memory_policy TEXT NOT NULL DEFAULT 'auto',
    full_memory_access_enabled INTEGER NOT NULL DEFAULT 0,
    sticker_policy TEXT NOT NULL DEFAULT 'contextual',
    notifications_enabled INTEGER NOT NULL DEFAULT 1,
    default_monitor_notifications INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL REFERENCES chats(telegram_chat_id) ON DELETE CASCADE,
    telegram_user_id INTEGER REFERENCES users(telegram_user_id) ON DELETE SET NULL,
    telegram_message_id INTEGER,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type TEXT NOT NULL DEFAULT 'text' CHECK (message_type IN ('text', 'sticker', 'image', 'file', 'command', 'system')),
    content TEXT,
    content_hash TEXT,
    memory_extraction_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (memory_extraction_status IN ('pending', 'extracted', 'skipped', 'failed')),
    simplemem_session_id TEXT,
    included_in_simplemem INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (telegram_chat_id, telegram_message_id, direction)
);

CREATE TABLE IF NOT EXISTS memory_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN (
        'conversation_history', 'chat_session', 'user_profile', 'project_memory', 'group_memory', 'pending_memory'
    )),
    owner_user_id INTEGER REFERENCES users(telegram_user_id) ON DELETE SET NULL,
    chat_id INTEGER REFERENCES chats(telegram_chat_id) ON DELETE SET NULL,
    simplemem_tenant_id TEXT,
    simplemem_session_id TEXT,
    simplemem_entry_id TEXT,
    content TEXT NOT NULL,
    tags TEXT,
    importance REAL,
    confidence REAL,
    sensitivity TEXT,
    source_message_id INTEGER REFERENCES conversation_messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER NOT NULL REFERENCES chats(telegram_chat_id) ON DELETE CASCADE,
    simplemem_tenant_id TEXT NOT NULL,
    simplemem_project TEXT NOT NULL,
    simplemem_memory_session_id TEXT,
    content_session_id TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    max_message_count INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'finalizing', 'finalized', 'failed')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finalized_at TEXT,
    finalization_report_ref TEXT,
    error_message TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_sessions_one_active
ON memory_sessions(telegram_chat_id)
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS knowledge_update_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_monitor_id INTEGER REFERENCES web_monitors(id) ON DELETE SET NULL,
    source_url TEXT,
    source_type TEXT NOT NULL CHECK (source_type IN ('official_site', 'instagram', 'manual')),
    detected_change_ref TEXT,
    impact_classification TEXT CHECK (impact_classification IN ('minor', 'notifiable', 'kb_impactful')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'applied', 'pending_review', 'rejected', 'failed')),
    summary TEXT,
    knowledge_state_id INTEGER REFERENCES knowledge_states(id) ON DELETE SET NULL,
    previous_version INTEGER,
    new_version INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS sticker_packs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_name TEXT NOT NULL UNIQUE,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sticker_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id INTEGER NOT NULL REFERENCES sticker_packs(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    file_id TEXT NOT NULL,
    emoji TEXT,
    tags TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (pack_id, alias)
);

CREATE TABLE IF NOT EXISTS web_monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    monitor_type TEXT NOT NULL DEFAULT 'website'
        CHECK (monitor_type IN ('website', 'instagram_profile', 'instagram_posts', 'rss', 'custom')),
    provider TEXT NOT NULL DEFAULT 'direct' CHECK (provider IN ('direct', 'brightdata')),
    provider_config_ref TEXT,
    schedule TEXT NOT NULL,
    change_detection_strategy TEXT NOT NULL,
    relevance_filter TEXT,
    impact_policy TEXT,
    notify_on_any_change INTEGER NOT NULL DEFAULT 1,
    knowledge_update_enabled INTEGER NOT NULL DEFAULT 0,
    target_chat_id INTEGER REFERENCES chats(telegram_chat_id) ON DELETE SET NULL,
    source_handle TEXT,
    last_seen_external_id TEXT,
    last_seen_permalink TEXT,
    last_seen_hash TEXT,
    last_seen_content_ref TEXT,
    last_seen_published_at TEXT,
    last_checked_at TEXT,
    last_notified_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS monitor_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    web_monitor_id INTEGER NOT NULL REFERENCES web_monitors(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_endpoint TEXT,
    provider_request_id TEXT,
    provider_snapshot_id TEXT,
    request_mode TEXT NOT NULL CHECK (request_mode IN ('sync', 'async', 'webhook')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    records_returned INTEGER NOT NULL DEFAULT 0,
    records_changed INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at TEXT,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS monitor_change_hashes (
    monitor_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (monitor_name, content_hash)
);

CREATE TABLE IF NOT EXISTS social_activity_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    web_monitor_id INTEGER NOT NULL REFERENCES web_monitors(id) ON DELETE CASCADE,
    platform TEXT NOT NULL DEFAULT 'instagram',
    external_id TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (activity_type IN (
            'profile_update', 'post', 'reel', 'carousel', 'comment', 'tagged_media',
            'mention', 'story', 'highlight', 'metric_snapshot', 'unknown'
        )),
    actor_handle TEXT,
    permalink TEXT,
    media_type TEXT NOT NULL DEFAULT 'unknown' CHECK (media_type IN ('post', 'reel', 'carousel', 'story', 'unknown')),
    caption TEXT,
    caption_hash TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    detected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    thumbnail_ref TEXT,
    engagement_snapshot TEXT,
    raw_provider_record_ref TEXT,
    notified_at TEXT,
    UNIQUE (web_monitor_id, platform, external_id, activity_type, caption_hash)
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_chat_created
ON conversation_messages(telegram_chat_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_records_scope_chat
ON memory_records(scope, chat_id, deleted_at);

CREATE INDEX IF NOT EXISTS idx_web_monitors_enabled
ON web_monitors(enabled, name);

CREATE INDEX IF NOT EXISTS idx_monitor_runs_monitor_started
ON monitor_runs(web_monitor_id, started_at);
