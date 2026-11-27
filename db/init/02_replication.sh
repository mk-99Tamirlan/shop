#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v repl_user="$REPLICATION_USER" -v repl_pass="$REPLICATION_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I WITH REPLICATION LOGIN', :'repl_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'repl_user') \gexec
ALTER ROLE :"repl_user" WITH REPLICATION LOGIN PASSWORD :'repl_pass';
SQL
rule="host replication ${REPLICATION_USER} 172.30.0.0/16 scram-sha-256"
grep -Fxq "$rule" "$PGDATA/pg_hba.conf" || printf '\n%s\n' "$rule" >> "$PGDATA/pg_hba.conf"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c 'SELECT pg_reload_conf();'
