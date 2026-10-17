#!/usr/bin/env bash
# Set or reset the deployed app's shared login (one ID, one password).
#   deploy/set-login.sh              prompts for the ID and password
#   deploy/set-login.sh --generate   ID "constructos" (or $LOGIN_ID), generates a strong password, prints it once
#   ROTATE_SESSION=1 ...             also replaces the session-signing secret
# Only a scrypt hash of the password is stored (Secret Manager); changing it signs everyone out.
set -euo pipefail
source "$(dirname "$0")/config.sh"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
gcloud config set project "$PROJECT" >/dev/null

if [ "${1:-}" = "--generate" ]; then
  LOGIN_ID="${LOGIN_ID:-constructos}"
  LOGIN_PASSWORD="$(node -e "const c=require('crypto');const a='ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';const g=()=>Array.from(c.randomBytes(5),b=>a[b%a.length]).join('');console.log([g(),g(),g(),g()].join('-'))")"
else
  read -r -p "Login ID: " LOGIN_ID
  read -r -s -p "Password (12+ characters): " LOGIN_PASSWORD; echo
  read -r -s -p "Repeat password: " AGAIN; echo
  [ "$LOGIN_PASSWORD" = "$AGAIN" ] || { echo "Passwords differ" >&2; exit 1; }
  [ "${#LOGIN_PASSWORD}" -ge 12 ] || { echo "Use at least 12 characters" >&2; exit 1; }
fi
[ -n "$LOGIN_ID" ] || { echo "ID is empty" >&2; exit 1; }

# Same hashing code the app verifies with (Node strips the TypeScript types).
HASH="$(cd "$ROOT/app" && LOGIN_PASSWORD="$LOGIN_PASSWORD" node --input-type=module -e \
  "import { hashPassword } from './src/lib/server/auth.ts'; console.log(hashPassword(process.env.LOGIN_PASSWORD))" 2>/dev/null)"
[[ "$HASH" == scrypt\$* ]] || { echo "Hashing failed" >&2; exit 1; }

put_secret() {  # name, value (value via stdin, never argv)
  gcloud secrets describe "$1" >/dev/null 2>&1 \
    || gcloud secrets create "$1" --replication-policy=user-managed --locations="$REGION" >/dev/null
  printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:$APP_SA" --role=roles/secretmanager.secretAccessor >/dev/null
}
put_secret app-login-id "$LOGIN_ID"
put_secret app-login-password-hash "$HASH"
if [ -n "${ROTATE_SESSION:-}" ] || ! gcloud secrets versions list app-session-secret --filter=state=enabled --format='value(name)' 2>/dev/null | grep -q .; then
  put_secret app-session-secret "$(openssl rand -base64 48 | tr -d '\n')"
fi

# A new revision picks up the latest secret versions.
if gcloud run services describe "$APP_SERVICE" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update "$APP_SERVICE" --region="$REGION" --quiet \
    --update-secrets="APP_LOGIN_ID=app-login-id:latest,APP_LOGIN_PASSWORD_HASH=app-login-password-hash:latest,APP_SESSION_SECRET=app-session-secret:latest" >/dev/null
fi
echo "Login set for ID: $LOGIN_ID"
[ "${1:-}" = "--generate" ] && echo "Password (shown once, store it safely): $LOGIN_PASSWORD"
exit 0
