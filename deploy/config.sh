# Shared deployment settings (no secrets). Override any of these in the environment.
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[ -n "$PROJECT" ] || { echo "Set PROJECT or run: gcloud config set project <id>" >&2; exit 1; }
REGION="${REGION:-asia-south1}"
BUCKET="${BUCKET:-${PROJECT}-constructos}"
REPO="${REPO:-constructos}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
ENGINE_SERVICE="${ENGINE_SERVICE:-constructos-engine}"
APP_SERVICE="${APP_SERVICE:-constructos-app}"
ENGINE_SA="constructos-engine@${PROJECT}.iam.gserviceaccount.com"
APP_SA="constructos-app@${PROJECT}.iam.gserviceaccount.com"
