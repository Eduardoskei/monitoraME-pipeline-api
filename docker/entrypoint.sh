#!/bin/sh
set -eu

if [ "${1:-}" = "migrate" ]; then
    alembic upgrade head
    alembic -n logs upgrade head
    exit 0
fi

exec "$@"
