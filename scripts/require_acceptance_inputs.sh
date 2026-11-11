#!/bin/bash
# Fail-closed input checks shared by the legacy acceptance journey scripts.
#
# Source this file, never run it. These scripts used to carry their own
# `DATABASE_URL` (and JWT signing key) as literals, so running them with no
# environment still fired HTTP calls, worker starts and backend restarts against
# a hardcoded `localhost:5434` target that no longer listens. The contract here
# is the one `e2e_legacy_upgrade.py` already implements: a required input that is
# missing, empty or malformed stops the script before its first side effect, and
# the value is never echoed — only scheme, host, port and database name are.
#
# Every refusal in this file exits 2, before any HTTP call, worker start,
# backend restart or database write.

# Populated by require_db_target so callers can report the target safely.
DB_TARGET_SCHEME=""
DB_TARGET_HOST=""
DB_TARGET_PORT=""
DB_TARGET_NAME=""

_refuse() {
    # $1 = variable name, $2 = why. The value itself is never printed.
    printf 'REFUSING: required environment variable %s is unusable (%s).\n' "$1" "$2" >&2
    printf 'Expected: postgresql://<user>:<password>@<host>:<port>/<database>\n' >&2
    printf 'No HTTP call, worker, backend restart or database write was made.\n' >&2
    exit 2
}

require_db_target() {
    local _name="${1:-DATABASE_URL}"
    local _val="${!_name:-}"
    local _rest _userinfo _hostport

    if [ -z "$_val" ]; then
        _refuse "$_name" "not set, or set to an empty string"
    fi
    case "$_val" in
        *://*) ;;
        *) _refuse "$_name" "no '://' separator found" ;;
    esac

    DB_TARGET_SCHEME="${_val%%://*}"
    case "$DB_TARGET_SCHEME" in
        postgresql|postgresql+psycopg2) ;;
        *) _refuse "$_name" "scheme '${DB_TARGET_SCHEME}://' is not postgresql or postgresql+psycopg2" ;;
    esac

    _rest="${_val#*://}"
    case "$_rest" in
        */*) ;;
        *) _refuse "$_name" "no /<database> after the host" ;;
    esac

    # authority = everything before the first '/', which is <userinfo>@<host:port>.
    # The userinfo may itself contain '@' only percent-encoded, so the host is
    # taken after the LAST '@' and any remaining '@' in the userinfo is refused
    # rather than guessed at.
    local _authority="${_rest%%/*}"
    case "$_authority" in
        *@*) ;;
        *) _refuse "$_name" "no <user>:<password>@ before the host" ;;
    esac
    _userinfo="${_authority%@*}"
    _hostport="${_authority##*@}"

    case "$_userinfo" in
        *@*) _refuse "$_name" "password contains an unencoded '@' (percent-encode it as %40)" ;;
    esac

    DB_TARGET_HOST="$_hostport"
    DB_TARGET_NAME="${_rest#*/}"
    DB_TARGET_NAME="${DB_TARGET_NAME%%\?*}"
    DB_TARGET_PORT=""
    case "$DB_TARGET_HOST" in
        *:*)
            DB_TARGET_PORT="${DB_TARGET_HOST##*:}"
            DB_TARGET_HOST="${DB_TARGET_HOST%:*}"
            case "$DB_TARGET_PORT" in
                ''|*[!0-9]*) _refuse "$_name" "port '${DB_TARGET_PORT}' is not a number" ;;
            esac
            if [ "$DB_TARGET_PORT" -lt 1 ] || [ "$DB_TARGET_PORT" -gt 65535 ]; then
                _refuse "$_name" "port ${DB_TARGET_PORT} is outside 1-65535"
            fi
            ;;
    esac
    [ -n "$DB_TARGET_HOST" ] || _refuse "$_name" "empty host"
    case "$DB_TARGET_HOST" in
        *@*) _refuse "$_name" "host still contains '@'" ;;
    esac
    [ -n "$DB_TARGET_NAME" ] || _refuse "$_name" "empty database name"
    return 0
}

require_secret() {
    # $1 = variable name, $2 = minimum length in characters.
    local _name="$1" _min="${2:-32}"
    local _val="${!_name:-}"

    if [ -z "$_val" ]; then
        printf 'REFUSING: required environment variable %s is not set (or is empty).\n' "$_name" >&2
        printf 'No HTTP call, worker, backend restart or database write was made.\n' >&2
        exit 2
    fi
    if [ "${#_val}" -lt "$_min" ]; then
        # Only the length is reported; the value is never echoed.
        printf 'REFUSING: %s is %d characters, below the required minimum of %d.\n' \
            "$_name" "${#_val}" "$_min" >&2
        printf 'No HTTP call, worker, backend restart or database write was made.\n' >&2
        exit 2
    fi
    return 0
}

describe_db_target() {
    # Safe to print: credentials are never included.
    if [ -n "$DB_TARGET_PORT" ]; then
        printf '%s host=%s port=%s database=%s\n' \
            "$DB_TARGET_SCHEME" "$DB_TARGET_HOST" "$DB_TARGET_PORT" "$DB_TARGET_NAME"
    else
        printf '%s host=%s port=(server default) database=%s\n' \
            "$DB_TARGET_SCHEME" "$DB_TARGET_HOST" "$DB_TARGET_NAME"
    fi
}
