#!/bin/sh
set -eu
# Password is passed as a psql variable, never shell-interpolated into SQL.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$DIAMOND_DB_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE diamond_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE diamond TO diamond_app;
GRANT USAGE, CREATE ON SCHEMA public TO diamond_app;
SQL
