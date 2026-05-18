#!/bin/sh
set -e

DATABASE_PATH="${DATABASE_PATH:-/app/data/dennis_bot.sqlite3}"
DATA_DIR="$(dirname "$DATABASE_PATH")"

mkdir -p "$DATA_DIR"
chown -R dennis:dennis "$DATA_DIR"

exec gosu dennis "$@"
