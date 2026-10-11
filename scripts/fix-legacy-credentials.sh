#!/usr/bin/env bash

# Hand-run recovery for the v0.2.4 -> v0.2.5 database credential rename.
# This script is intentionally not called by Compose or application startup.

set -euo pipefail

readonly OLD_ROLE="cpic_user"
readonly NEW_ROLE="zaropgx_user"
readonly OLD_DATABASE="cpic_db"
readonly NEW_DATABASE="zaropgx_db"
readonly TEMP_ROLE="zaropgx_credential_migrator"
readonly TEMP_ROLE_COMMENT="Managed by scripts/fix-legacy-credentials.sh"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

psql_as() {
    local role="$1"
    shift
    docker compose exec -T db \
        psql -X -v ON_ERROR_STOP=1 -A -t -q -U "$role" -d postgres "$@"
}

scalar_as() {
    local role="$1"
    local query="$2"
    local value

    value="$(psql_as "$role" -c "$query")"
    printf '%s' "${value//$'\r'/}"
}

role_exists() {
    local administrator="$1"
    local role="$2"

    scalar_as "$administrator" \
        "SELECT count(*) FROM pg_roles WHERE rolname = '$role';"
}

database_exists() {
    local administrator="$1"
    local database="$2"

    scalar_as "$administrator" \
        "SELECT count(*) FROM pg_database WHERE datname = '$database';"
}

temporary_role_comment() {
    local administrator="$1"

    scalar_as "$administrator" \
        "SELECT COALESCE(shobj_description(oid, 'pg_authid'), '') FROM pg_roles WHERE rolname = '$TEMP_ROLE';"
}

command -v docker >/dev/null 2>&1 || die "docker is required."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."

if ! docker compose exec -T db sh -c 'exit 0' >/dev/null 2>&1; then
    die "The db service is not running. Start it with the legacy DB_USER and DB_NAME first."
fi

if ! docker compose exec -T db sh -eu -c 'test -n "${POSTGRES_PASSWORD:-}"'; then
    die "POSTGRES_PASSWORD is empty in the db container; refusing to rename credentials."
fi

administrator=""
if psql_as "$NEW_ROLE" -c "SELECT 1;" >/dev/null 2>&1; then
    administrator="$NEW_ROLE"
elif psql_as "$OLD_ROLE" -c "SELECT 1;" >/dev/null 2>&1; then
    administrator="$OLD_ROLE"
else
    die "Neither $OLD_ROLE nor $NEW_ROLE can administer the cluster."
fi

old_role_present="$(role_exists "$administrator" "$OLD_ROLE")"
new_role_present="$(role_exists "$administrator" "$NEW_ROLE")"
old_database_present="$(database_exists "$administrator" "$OLD_DATABASE")"
new_database_present="$(database_exists "$administrator" "$NEW_DATABASE")"

case "${old_role_present}:${new_role_present}" in
    1:0 | 0:1) ;;
    1:1)
        die "Both $OLD_ROLE and $NEW_ROLE exist; refusing to guess which role owns the data."
        ;;
    *)
        die "Expected exactly one of $OLD_ROLE or $NEW_ROLE to exist."
        ;;
esac

case "${old_database_present}:${new_database_present}" in
    1:0 | 0:1) ;;
    1:1)
        die "Both $OLD_DATABASE and $NEW_DATABASE exist; refusing to merge databases."
        ;;
    *)
        die "Expected exactly one of $OLD_DATABASE or $NEW_DATABASE to exist."
        ;;
esac

changed=0

if [[ "$old_role_present" == "1" ]]; then
    temp_role_present="$(role_exists "$administrator" "$TEMP_ROLE")"

    if [[ "$temp_role_present" == "1" ]]; then
        [[ "$(temporary_role_comment "$administrator")" == "$TEMP_ROLE_COMMENT" ]] ||
            die "$TEMP_ROLE already exists and was not created by this script."
    else
        psql_as "$administrator" <<SQL
BEGIN;
CREATE ROLE $TEMP_ROLE LOGIN SUPERUSER;
COMMENT ON ROLE $TEMP_ROLE IS '$TEMP_ROLE_COMMENT';
COMMIT;
SQL
    fi

    # PostgreSQL will not rename the current session user. The temporary local
    # superuser performs the rename, then the configured password is restored
    # in case the legacy cluster stored an MD5 verifier that the rename clears.
    docker compose exec -T db \
        psql -X -1 -v ON_ERROR_STOP=1 -U "$TEMP_ROLE" -d postgres <<SQL
\getenv zaropgx_password POSTGRES_PASSWORD
ALTER ROLE $OLD_ROLE RENAME TO $NEW_ROLE;
ALTER ROLE $NEW_ROLE PASSWORD :'zaropgx_password';
SQL

    administrator="$NEW_ROLE"
    changed=1
fi

if [[ "$old_database_present" == "1" ]]; then
    active_connections="$(
        scalar_as "$administrator" \
            "SELECT count(*) FROM pg_stat_activity WHERE datname = '$OLD_DATABASE';"
    )"

    if [[ "$active_connections" != "0" ]]; then
        die "$OLD_DATABASE has active connections. Stop database clients and run this script again."
    fi

    psql_as "$administrator" \
        -c "ALTER DATABASE $OLD_DATABASE RENAME TO $NEW_DATABASE;"
    changed=1
fi

if [[ "$(role_exists "$administrator" "$TEMP_ROLE")" == "1" ]]; then
    [[ "$(temporary_role_comment "$administrator")" == "$TEMP_ROLE_COMMENT" ]] ||
        die "$TEMP_ROLE exists but is not owned by this script; it was not removed."
    psql_as "$administrator" -c "DROP ROLE $TEMP_ROLE;"
fi

if [[ "$changed" == "1" ]]; then
    printf 'Renamed %s/%s to %s/%s.\n' \
        "$OLD_ROLE" "$OLD_DATABASE" "$NEW_ROLE" "$NEW_DATABASE"
else
    printf 'Database role and name are already current; no changes made.\n'
fi

printf 'Set DB_USER=%s and DB_NAME=%s, then restart the stack.\n' \
    "$NEW_ROLE" "$NEW_DATABASE"
