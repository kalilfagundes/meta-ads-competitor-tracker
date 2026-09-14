#!/bin/sh
# Creates .env for docker compose by asking a few questions.
#
#   ./setup.sh             interactive
#   ./setup.sh --defaults  no questions: bundled Postgres and Garage, port 8000
#   ./setup.sh --force     allow replacing an existing .env
#
# The bundled services' users and passwords can be typed in or left to the
# defaults (passwords are then generated); the session secret is always generated.
# Everything else (admin account, competitors, country, schedule) is set in the
# browser afterwards. See .env.example for every variable.
set -eu

cd "$(dirname "$0")"

DEFAULTS=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --defaults) DEFAULTS=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

# Byte-wise character ranges in the checks below.
LC_ALL=C
export LC_ALL

# random_chars CHARSET LENGTH
# Filters finite blocks of /dev/urandom: cutting an endless stream short with head
# makes tr fail with "Broken pipe" where SIGPIPE is ignored (e.g. CI runners).
random_chars() {
  out=""
  while [ "${#out}" -lt "$2" ]; do
    out="$out$(head -c 512 /dev/urandom | tr -dc "$1")"
  done
  printf '%s' "$out" | cut -c "1-$2"
}

random_secret() {
  random_chars 'A-Za-z0-9' 40
}

random_hex() {
  random_chars 'a-f0-9' 64
}

# ask VAR "Question" "default"
ask() {
  if [ "$DEFAULTS" = 1 ]; then eval "$1=\$3"; return; fi
  printf '%s [%s]: ' "$2" "$3"
  read -r answer || answer=""
  eval "$1=\${answer:-\$3}"
}

# ask_required VAR "Question"
ask_required() {
  while :; do
    printf '%s: ' "$2"
    read -r answer || answer=""
    [ -n "$answer" ] && break
    echo "  This value is required."
  done
  eval "$1=\$answer"
}

# ask_secret VAR "Question" (not echoed)
ask_secret() {
  while :; do
    printf '%s: ' "$2"
    stty -echo 2>/dev/null || true
    read -r answer || answer=""
    stty echo 2>/dev/null || true
    echo
    [ -n "$answer" ] && break
    echo "  This value is required."
  done
  eval "$1=\$answer"
}

# ask_username VAR "Question" "default" MIN_LENGTH
# Lowercase letters, digits and _, starting with a letter; Postgres reserves pg_.
ask_username() {
  while :; do
    ask "$1" "$2" "$3"
    eval "value=\$$1"
    case "$value" in
      *[!a-z0-9_]*|[!a-z]*) echo "  Use lowercase letters, digits and _, starting with a letter." ;;
      pg_*) echo "  Names starting with pg_ are reserved." ;;
      *) [ "${#value}" -ge "$4" ] && break; echo "  Use at least $4 characters." ;;
    esac
  done
}

# ask_password VAR "Question" (not echoed; empty = generate one)
# At least 16 characters, no spaces or quotes (Garage's rule for secret keys).
ask_password() {
  if [ "$DEFAULTS" = 1 ]; then eval "$1=\$(random_secret)"; return; fi
  while :; do
    printf '%s (Enter to generate one): ' "$2"
    stty -echo 2>/dev/null || true
    read -r answer || answer=""
    stty echo 2>/dev/null || true
    echo
    if [ -z "$answer" ]; then answer=$(random_secret); break; fi
    case "$answer" in
      *[!!-~]*|*"'"*) echo "  Use letters, digits and symbols, without spaces or '." ;;
      *) [ "${#answer}" -ge 16 ] && break; echo "  Use at least 16 characters." ;;
    esac
  done
  eval "$1=\$answer"
}

# A value typed by the user, quoted for .env (single quotes: taken literally).
quoted() {
  case "$1" in
    *"'"*) echo "Values can't contain a single quote (')." >&2; exit 1 ;;
  esac
  printf "'%s'" "$1"
}

if [ -f .env ] && [ "$FORCE" != 1 ]; then
  if [ "$DEFAULTS" = 1 ]; then
    echo ".env already exists; leaving it alone (use --force to replace it)."
    exit 0
  fi
  echo ".env already exists."
  echo "Existing data keeps the old users and passwords, so the new ones won't be accepted."
  printf 'Replace it? [y/N]: '
  read -r answer || answer=""
  case "$answer" in y|Y|yes|YES) ;; *) echo "Nothing changed."; exit 0 ;; esac
fi

echo "This creates .env for docker compose. Press Enter to accept the value in brackets."
echo

ask WEB_PORT "Port for the web app" "8000"

echo
echo "Media storage (a copy of every ad image and video):"
echo "  1) Built-in Garage, runs with the app (recommended)"
echo "  2) External S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2...)"
ask STORAGE "Choose" "1"

if [ "$STORAGE" = 2 ]; then
  printf 'Endpoint URL (leave empty for AWS S3): '
  read -r S3_ENDPOINT || S3_ENDPOINT=""
  ask_required S3_BUCKET "Bucket"
  ask S3_REGION "Region" "auto"
  ask_required S3_ACCESS_KEY_ID "Access key ID"
  ask_secret S3_SECRET_ACCESS_KEY "Secret access key"
  ask PATH_STYLE "Path-style addressing? (some self-hosted providers need it) y/N" "n"
  case "$PATH_STYLE" in y|Y|yes|YES) S3_FORCE_PATH_STYLE=true ;; *) S3_FORCE_PATH_STYLE=false ;; esac
  STORAGE_PROFILE=""
  S3_LINES="S3_ENDPOINT=$(quoted "$S3_ENDPOINT")
S3_BUCKET=$(quoted "$S3_BUCKET")
S3_REGION=$(quoted "$S3_REGION")
S3_ACCESS_KEY_ID=$(quoted "$S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY=$(quoted "$S3_SECRET_ACCESS_KEY")
S3_FORCE_PATH_STYLE=$S3_FORCE_PATH_STYLE"
else
  ask_username STORAGE_KEY "Storage access key (like a user name)" "tracker_media" 8
  ask_password STORAGE_SECRET "Storage secret key"
  STORAGE_PROFILE="garage"
  S3_LINES="S3_ENDPOINT=http://garage:3900
S3_BUCKET=ads-media
S3_REGION=garage
S3_ACCESS_KEY_ID=$(quoted "$STORAGE_KEY")
S3_SECRET_ACCESS_KEY=$(quoted "$STORAGE_SECRET")
S3_FORCE_PATH_STYLE=true
GARAGE_RPC_SECRET=$(random_hex)
GARAGE_ADMIN_TOKEN=$(random_secret)"
fi

echo
echo "Database:"
echo "  1) Built-in Postgres, runs with the app (recommended)"
echo "  2) Your own Postgres (14 or newer)"
ask DATABASE "Choose" "1"

if [ "$DATABASE" = 2 ]; then
  ask_required POSTGRES_HOST "Host"
  ask POSTGRES_PORT "Port" "5432"
  ask POSTGRES_DB "Database name" "tracker"
  ask_required POSTGRES_USER "User"
  ask_secret POSTGRES_PASSWORD "Password"
  ask POSTGRES_SSLMODE "SSL mode (disable, require, verify-full)" "require"
  DB_PROFILE=""
  DB_LINES="POSTGRES_HOST=$(quoted "$POSTGRES_HOST")
POSTGRES_PORT=$(quoted "$POSTGRES_PORT")
POSTGRES_DB=$(quoted "$POSTGRES_DB")
POSTGRES_USER=$(quoted "$POSTGRES_USER")
POSTGRES_PASSWORD=$(quoted "$POSTGRES_PASSWORD")
POSTGRES_SSLMODE=$(quoted "$POSTGRES_SSLMODE")"
else
  ask_username POSTGRES_USER "Database user" "tracker" 3
  ask_password POSTGRES_PASSWORD "Database password"
  DB_PROFILE="postgres"
  DB_LINES="POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=tracker
POSTGRES_USER=$(quoted "$POSTGRES_USER")
POSTGRES_PASSWORD=$(quoted "$POSTGRES_PASSWORD")
POSTGRES_SSLMODE="
fi

PROFILES=$(printf '%s,%s' "$DB_PROFILE" "$STORAGE_PROFILE" | sed 's/^,//; s/,$//')

SETUP_TOKEN=$(random_chars 'A-Za-z0-9' 24)

umask 077
cat > .env <<EOF
# Created by setup.sh. Keep this file private: it holds passwords.
# Every variable is described in .env.example.

# Bundled services to run: postgres, garage (empty = use your own).
COMPOSE_PROFILES=$PROFILES
WEB_PORT=$(quoted "$WEB_PORT")

# Database
$DB_LINES

# Media storage
$S3_LINES

# Signs the login cookie.
SESSION_SECRET=$(random_secret)

# Asked by the setup wizard when creating the administrator.
SETUP_TOKEN=$SETUP_TOKEN
EOF

echo
echo "Created .env."
echo "Next: docker compose up -d"
echo "Then open http://localhost:$WEB_PORT and follow the setup wizard."
echo "Setup code (asked when you create the administrator): $SETUP_TOKEN"
