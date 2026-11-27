#!/bin/sh
set -eu
# Named volumes may initially be owned by root. PostgreSQL must run as postgres.
if [ "$(id -u)" = 0 ]; then
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  chmod 700 "$PGDATA"
  exec gosu postgres /bin/sh "$0" "$@"
fi
export PGPASSFILE=/var/lib/postgresql/.pgpass
umask 077
printf '%s:5432:replication:%s:%s\n' "$PRIMARY_HOST" "$REPLICATION_USER" "$REPLICATION_PASSWORD" > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  if [ -n "$(ls -A "$PGDATA")" ]; then
    echo '[replica] data directory not empty; refusing to overwrite' >&2
    exit 1
  fi
  echo '[replica] waiting for primary'
  until pg_isready -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" >/dev/null 2>&1; do sleep 2; done
  echo '[replica] pg_basebackup'
  pg_basebackup -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" -D "$PGDATA" -Fp -Xs -P -R -w
fi
if [ ! -f "$PGDATA/standby.signal" ]; then
  echo '[replica] standby.signal missing; refusing to start as primary' >&2
  exit 1
fi
echo '[replica] hot standby'
exec postgres "$@"
