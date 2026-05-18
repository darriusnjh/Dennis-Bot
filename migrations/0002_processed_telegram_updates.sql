CREATE TABLE IF NOT EXISTS processed_telegram_updates (
    update_id INTEGER PRIMARY KEY,
    telegram_chat_id INTEGER,
    telegram_message_id INTEGER,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_processed_telegram_updates_chat_message
ON processed_telegram_updates(telegram_chat_id, telegram_message_id);
