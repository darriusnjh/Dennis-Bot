#!/bin/sh
set -e

DATABASE_PATH="${DATABASE_PATH:-/app/data/dennis_bot.sqlite3}"
DATA_DIR="$(dirname "$DATABASE_PATH")"

mkdir -p "$DATA_DIR"

if chown -R dennis:dennis "$DATA_DIR"; then
  exec gosu dennis "$@"
fi

echo "Warning: could not chown $DATA_DIR; running as current container user." >&2
exec "$@"
