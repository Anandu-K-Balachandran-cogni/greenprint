#!/usr/bin/env bash
# One-time (idempotent) Google Cloud setup for ConstructOS.
# Firestore holds project metadata and runs; Cloud Storage holds immutable diagrams and exports;
# each service runs as its own least-privilege identity; Gemini is reached through Vertex AI.
set -euo pipefail
source "$(dirname "$0")/config.sh"
gcloud config set project "$PROJECT" >/dev/null

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com aiplatform.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com \
  secretmanager.googleapis.com

gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 \
  || gcloud firestore databases create --database="(default)" --location="$REGION" --type=firestore-native

gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention

gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" \
       --description="ConstructOS container images"

for sa in constructos-engine constructos-app; do
  gcloud iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$sa" --display-name="ConstructOS ${sa#constructos-}"
done

# Engine: Firestore read/write, Vertex AI (Gemini), objects in its own bucket only.
for role in roles/datastore.user roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$ENGINE_SA" --role="$role" --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$ENGINE_SA" --role=roles/storage.objectAdmin >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$APP_SA" --role=roles/logging.logWriter --condition=None >/dev/null

# Gemini API key as a fallback behind Vertex AI (new projects start with low Vertex quotas).
# Read from the git-ignored .env and piped straight to Secret Manager; it is never printed.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
gcloud secrets describe gemini-api-key >/dev/null 2>&1 \
  || gcloud secrets create gemini-api-key --replication-policy=user-managed --locations="$REGION"
if ! gcloud secrets versions list gemini-api-key --filter=state=enabled --format='value(name)' | grep -q .; then
  grep -E '^GEMINI_API_KEY=' "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '\r\n"' | gcloud secrets versions add gemini-api-key --data-file=-
fi
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:$ENGINE_SA" \
  --role=roles/secretmanager.secretAccessor >/dev/null

# Cloud Build runs as the default compute identity: let it push images and write build logs.
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
BUILD_SA="${NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$BUILD_SA" --role="$role" --condition=None >/dev/null
done
echo "Setup complete: Firestore (default) in $REGION, gs://$BUCKET, $REGISTRY"
