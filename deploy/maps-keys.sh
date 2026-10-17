#!/usr/bin/env bash
# Google Maps Platform keys for Greenprint (idempotent; key strings go straight to Secret Manager, never to the terminal).
#   maps-browser-key: Maps JavaScript + Places + Geocoding, usable only from the app's addresses (HTTP referrers)
#   maps-solar-key:   Solar API only, used server-side by the engine
set -euo pipefail
source "$(dirname "$0")/config.sh"
if [ -z "${APP_URLS:-}" ]; then   # the app's two run.app addresses, plus local development
  NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
  URL=$(gcloud run services describe "$APP_SERVICE" --region "$REGION" --format='value(status.url)')
  APP_URLS="https://${APP_SERVICE}-${NUMBER}.${REGION}.run.app/*,${URL}/*,http://127.0.0.1:4189/*,http://localhost:5173/*"
fi

make_key() { # name display targets... (extra flags last)
  local secret=$1 display=$2; shift 2
  if gcloud secrets versions list "$secret" --filter=state=enabled --format='value(name)' 2>/dev/null | grep -q .; then
    echo "$secret: already set"; return
  fi
  gcloud secrets describe "$secret" >/dev/null 2>&1 || gcloud secrets create "$secret" --replication-policy=user-managed --locations="$REGION" >/dev/null
  gcloud services api-keys create --display-name="$display" "$@" --format=json 2>/dev/null \
    | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["response"]["keyString"])' \
    | gcloud secrets versions add "$secret" --data-file=- >/dev/null
  echo "$secret: created"
}

make_key maps-browser-key "Greenprint maps (browser)" \
  --api-target=service=maps-backend.googleapis.com --api-target=service=places.googleapis.com \
  --api-target=service=geocoding-backend.googleapis.com --allowed-referrers="$APP_URLS"
make_key maps-solar-key "Greenprint solar (server)" --api-target=service=solar.googleapis.com

gcloud secrets add-iam-policy-binding maps-browser-key --member="serviceAccount:$APP_SA" --role=roles/secretmanager.secretAccessor >/dev/null
gcloud secrets add-iam-policy-binding maps-solar-key --member="serviceAccount:$ENGINE_SA" --role=roles/secretmanager.secretAccessor >/dev/null
echo "access granted: app → maps-browser-key, engine → maps-solar-key"
