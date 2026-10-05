#!/bin/sh
set -e

i=0
until alembic upgrade head; do
  i=$((i + 1))
  if [ "$i" -ge 10 ]; then
    echo "миграции так и не прошли"
    exit 1
  fi
  echo "база еще не готова, подожду"
  sleep 2
done

exec "$@"
