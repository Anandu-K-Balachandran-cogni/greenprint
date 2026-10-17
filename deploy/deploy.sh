#!/usr/bin/env bash
# Build both images with Cloud Build and deploy them to Cloud Run.
#   engine: private (IAM-only), 8 vCPU / 16 GiB for CP-SAT, CPU always allocated while an instance is up
#           so background jobs keep running; scales to zero when idle (an idle instance stays up to
#           ~15 min after the last request, longer than any job). One instance: the project's regional CPU
#           quota covers two 8-vCPU instances, and a rollout briefly runs old and new side by side.
#           Gemini: API key primary (Vertex AI timed out on ~1 in 4 brief extractions), Vertex AI fallback.
#   app:    web UI behind the shared login (deploy/set-login.sh), 2 vCPU / 2 GiB, one warm instance;
#           calls the engine with an ID token.
# ONLY=engine or ONLY=app deploys just one service.
set -euo pipefail
source "$(dirname "$0")/config.sh"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet || echo -dirty)}"
gcloud config set project "$PROJECT" >/dev/null

if [ "${ONLY:-}" != app ]; then
gcloud builds submit "$ROOT/engine" --region="$REGION" --tag="$REGISTRY/engine:$TAG" --quiet
gcloud run deploy "$ENGINE_SERVICE" --image="$REGISTRY/engine:$TAG" --region="$REGION" \
  --service-account="$ENGINE_SA" --no-allow-unauthenticated \
  --cpu=8 --memory=16Gi --no-cpu-throttling --cpu-boost --min-instances=0 --max-instances=1 --max=1 --concurrency=20 --timeout=300 \
  --set-secrets="GEMINI_API_KEY=gemini-api-key:latest,MAPS_SOLAR_KEY=maps-solar-key:latest" \
  --set-env-vars="CONSTRUCTOS_STORE=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT,CONSTRUCTOS_BUCKET=$BUCKET,GOOGLE_GENAI_USE_VERTEXAI=false,GEMINI_VERTEX_FALLBACK=true,GOOGLE_CLOUD_LOCATION=global,CONSTRUCTOS_JOB_WORKERS=2" \
  --quiet
ENGINE_URL="$(gcloud run services describe "$ENGINE_SERVICE" --region="$REGION" --format='value(status.url)')"
gcloud run services add-iam-policy-binding "$ENGINE_SERVICE" --region="$REGION" \
  --member="serviceAccount:$APP_SA" --role=roles/run.invoker >/dev/null
fi
ENGINE_URL="$(gcloud run services describe "$ENGINE_SERVICE" --region="$REGION" --format='value(status.url)')"
[ "${ONLY:-}" = engine ] && { echo "Engine (private): $ENGINE_URL"; exit 0; }

gcloud builds submit "$ROOT/app" --region="$REGION" --tag="$REGISTRY/app:$TAG" --quiet
gcloud secrets describe app-login-password-hash >/dev/null 2>&1 \
  || { echo "No login configured: run deploy/set-login.sh first" >&2; exit 1; }
gcloud run deploy "$APP_SERVICE" --image="$REGISTRY/app:$TAG" --region="$REGION" \
  --service-account="$APP_SA" --allow-unauthenticated \
  --cpu=2 --memory=2Gi --cpu-boost --min-instances=1 --max-instances=3 --max=3 --concurrency=40 --timeout=300 \
  --set-secrets="APP_LOGIN_ID=app-login-id:latest,APP_LOGIN_PASSWORD_HASH=app-login-password-hash:latest,APP_SESSION_SECRET=app-session-secret:latest,MAPS_BROWSER_KEY=maps-browser-key:latest" \
  --set-env-vars="ENGINE_URL=$ENGINE_URL,ENGINE_AUTH=google-id-token,PROTOCOL_HEADER=x-forwarded-proto,HOST_HEADER=host,ADDRESS_HEADER=x-forwarded-for,XFF_DEPTH=1,HANDOFF_UPLOADS_ENABLED=false,HANDOFF_BUCKET=,ASSISTANT_SHARES_ENABLED=false,BODY_SIZE_LIMIT=12M" \
  --quiet
echo "Engine (private): $ENGINE_URL"
echo "App:              $(gcloud run services describe "$APP_SERVICE" --region="$REGION" --format='value(status.url)')"
