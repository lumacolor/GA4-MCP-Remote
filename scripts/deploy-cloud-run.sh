#!/usr/bin/env bash
# Cloud Run へソースからビルド・デプロイする例（gcloud がログイン済みであること）
#
# 使い方:
#   export GCP_PROJECT_ID="your-project-id"
#   export GA4MCP_ALLOWED_PROPERTY_IDS="123456789"   # 複数はカンマ区切りで 1 つの値として渡す
#   export CLOUD_RUN_SERVICE_ACCOUNT="xxx@PROJECT.iam.gserviceaccount.com"  # 推奨（GA4 権限付き SA）
#   # Bearer を Secret Manager 経由で渡す場合（推奨）:
#   export GA4MCP_BEARER_SECRET_NAME="ga4-remote-mcp-bearer"
#   ./scripts/deploy-cloud-run.sh
#
# 初回テスト用に GA4MCP_ENABLE_DNS_REBINDING_PROTECTION=false。本番では許可ホスト設定後に true 推奨。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-europe-west3}"
SERVICE="${CLOUD_RUN_SERVICE:-ga4-remote-mcp}"
ALLOWED="${GA4MCP_ALLOWED_PROPERTY_IDS:?Set GA4MCP_ALLOWED_PROPERTY_IDS (e.g. 123456789 or 111,222)}"

# Deploy a pre-built, tagged image rather than building from source.
# Building at deploy time means the artifact is not the one that was tested,
# and there is no earlier tag to roll a single customer back to.
IMAGE="${GA4MCP_IMAGE:?Set GA4MCP_IMAGE to a tagged image, e.g. europe-west3-docker.pkg.dev/PROJECT/ga4-mcp/ga4-remote-mcp:v0.1.0}"
if [[ "$IMAGE" == *:latest || "$IMAGE" != *:* ]]; then
  echo "ERROR: GA4MCP_IMAGE must carry an explicit version tag, not 'latest' and not untagged." >&2
  echo "       Rollback for a single customer depends on it." >&2
  exit 1
fi

# Each customer gets their own service account, which is the only thing that
# grants GA4 access. Without it Cloud Run silently falls back to the default
# compute service account -- a shared, broadly privileged identity. That would
# break tenant isolation, so refuse instead of defaulting.
if [[ -z "${CLOUD_RUN_SERVICE_ACCOUNT:-}" ]]; then
  cat >&2 <<'MSG'
ERROR: CLOUD_RUN_SERVICE_ACCOUNT is not set.

Deploying without it runs the service as the project's default compute
service account, which is shared across services and broadly privileged.
Tenant isolation in this deployment model depends on one dedicated service
account per customer, so this script refuses to deploy without one.

    export CLOUD_RUN_SERVICE_ACCOUNT="ga4-mcp-<customer>@$GCP_PROJECT_ID.iam.gserviceaccount.com"
MSG
  exit 1
fi

# Production-auth guard
# This script always sets GA4MCP_ENV=production, which now refuses to start
# without bearer auth (see src/ga4_remote_mcp/config/settings.py
# validate_production_auth). Catch the missing secret early — before any
# gcloud calls — so the operator gets a clear error here instead of a
# crash loop on Cloud Run.
if [[ -z "${GA4MCP_BEARER_SECRET_NAME:-}" ]]; then
  cat >&2 <<MSG
ERROR: GA4MCP_BEARER_SECRET_NAME is not set.

This script deploys with GA4MCP_ENV=production, which requires
GA4MCP_AUTH_MODE=bearer + a Secret-Manager-backed GA4MCP_BEARER_TOKEN.

Create the secret first, e.g.:

    gcloud secrets create ga4-remote-mcp-bearer \\
      --replication-policy=automatic \\
      --project="\$GCP_PROJECT_ID"
    printf '%s' "<your-token>" | gcloud secrets versions add \\
      ga4-remote-mcp-bearer --data-file=- --project="\$GCP_PROJECT_ID"

Then re-run with:

    export GA4MCP_BEARER_SECRET_NAME=ga4-remote-mcp-bearer
    ./scripts/deploy-cloud-run.sh
MSG
  exit 1
fi

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT

# --set-env-vars は値にカンマがあると壊れるため YAML ファイルを使う
cat >"$ENV_FILE" <<EOF
GA4MCP_ENV: production
GA4MCP_PORT: "8080"
GA4MCP_ENABLE_DNS_REBINDING_PROTECTION: "false"
GA4MCP_LOG_LEVEL: INFO
GA4MCP_ALLOWED_PROPERTY_IDS: "${ALLOWED}"
GA4MCP_ALLOW_ALL_PROPERTIES: "false"
EOF

# Bearer + Dify 向け: 不一致時はデフォルト 403（401 だと OAuth メタデータ探索に寄りやすい）
# ガードで GA4MCP_BEARER_SECRET_NAME 必須化済みのため常に bearer モードで設定する。
echo "GA4MCP_AUTH_MODE: bearer" >>"$ENV_FILE"
BF_STATUS="${GA4MCP_BEARER_FAILURE_HTTP_STATUS:-403}"
echo "GA4MCP_BEARER_FAILURE_HTTP_STATUS: \"${BF_STATUS}\"" >>"$ENV_FILE"

echo "Project=$PROJECT_ID Region=$REGION Service=$SERVICE"

gcloud config set project "$PROJECT_ID"

DEPLOY_ARGS=(
  gcloud run deploy "$SERVICE"
  --region="$REGION"
  --image="$IMAGE"
  --platform=managed
  --allow-unauthenticated
  --port=8080
  --memory=512Mi
  --timeout=300
  --min-instances=0
  --max-instances=1
  --service-account="$CLOUD_RUN_SERVICE_ACCOUNT"
  --env-vars-file="$ENV_FILE"
)

# GA4MCP_BEARER_SECRET_NAME はガードで必須化済み。
DEPLOY_ARGS+=(
  --update-secrets="GA4MCP_BEARER_TOKEN=${GA4MCP_BEARER_SECRET_NAME}:latest"
)

"${DEPLOY_ARGS[@]}"

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
SERVICE_HOST="${SERVICE_URL#https://}"

# The Cloud Run hostname is only known after the first deploy, so DNS rebinding
# protection is switched on in a second pass once we can name the allowed host.
echo "Enabling DNS rebinding protection for host: $SERVICE_HOST"
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --update-env-vars="GA4MCP_ENABLE_DNS_REBINDING_PROTECTION=true,GA4MCP_ALLOWED_HOSTS=${SERVICE_HOST}" \
  --quiet

echo
echo "Deployed. Service URL:"
echo "$SERVICE_URL"
