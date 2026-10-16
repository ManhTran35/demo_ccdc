#!/usr/bin/env bash
# Build only reviewed, committed application source; never upload the working tree.
set -euo pipefail
umask 077

die() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
need() { [[ -n "${!1:-}" ]] || die "Set $1 (see docs/DEPLOY_AND_ROLLBACK.md)."; }
for executable in git python gcloud tar; do command -v "$executable" >/dev/null || die "$executable is required."; done
for key in GCP_PROJECT_ID GCS_BUCKET RUNTIME_SERVICE_ACCOUNT ADMIN_EMAILS IAP_AUDIENCE \
  APP_ENV ARTIFACT_REGISTRY_REPOSITORY BUILD_SERVICE_ACCOUNT BUILD_SOURCE_BUCKET \
  BUILD_LOG_BUCKET DEPLOY_RECORD_DIR; do need "$key"; done
[[ "${CLOUD_RESOURCES_APPROVED:-}" == true ]] || die 'CLOUD_RESOURCES_APPROVED=true requires owner approval of project, billing and existing resources.'
case "$APP_ENV" in
  staging) [[ "${STAGING_SYNTHETIC_ONLY:-}" == true ]] || die 'Staging requires STAGING_SYNTHETIC_ONLY=true and synthetic data only.' ;;
  production)
    [[ "${DATA_CLOUD_APPROVED:-}" == true ]] || die 'Production requires documented data approval and DATA_CLOUD_APPROVED=true.'
    [[ "${STAGING_ACCEPTANCE_APPROVED:-}" == true ]] || die 'Production requires completed staging acceptance and STAGING_ACCEPTANCE_APPROVED=true.' ;;
  *) die 'APP_ENV must be staging or production for deployment.' ;;
esac
REGION="${REGION:-asia-southeast3}"
SERVICE_NAME="${SERVICE_NAME:-ccdc-dashboard-$APP_ENV}"
GCS_PREFIX="${GCS_PREFIX:-ccdc/$APP_ENV}"
MAX_UPLOAD_MB="${MAX_UPLOAD_MB:-25}"
MAX_UNCOMPRESSED_MB="${MAX_UNCOMPRESSED_MB:-512}"
MAX_ROWS="${MAX_ROWS:-250000}"
MAX_COLUMNS="${MAX_COLUMNS:-200}"
VALIDATION_WARNING_LIMITS="${VALIDATION_WARNING_LIMITS:-}"
[[ -n "$VALIDATION_WARNING_LIMITS" ]] || VALIDATION_WARNING_LIMITS='{}'
LOG_LEVEL="${LOG_LEVEL:-INFO}"
[[ "$GCP_PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || die 'Invalid GCP_PROJECT_ID.'
[[ "$SERVICE_NAME" =~ ^[a-z][a-z0-9-]{0,29}$ ]] || die 'SERVICE_NAME must be at most 30 lowercase letters/digits/hyphens.'
[[ "$RUNTIME_SERVICE_ACCOUNT" == *@"$GCP_PROJECT_ID".iam.gserviceaccount.com ]] || die 'Use a dedicated runtime service account in this project.'
[[ "$BUILD_SERVICE_ACCOUNT" == "projects/$GCP_PROJECT_ID/serviceAccounts/"* ]] || die 'BUILD_SERVICE_ACCOUNT must be its full projects/PROJECT/serviceAccounts/EMAIL resource name.'
[[ "${BUILD_SERVICE_ACCOUNT##*/}" != "$RUNTIME_SERVICE_ACCOUNT" ]] || die 'Build and runtime service accounts must be separate.'
[[ "$GCS_BUCKET" != "$BUILD_SOURCE_BUCKET" && "$GCS_BUCKET" != "$BUILD_LOG_BUCKET" ]] || die 'Data bucket must be separate from build source/log buckets.'

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || die 'Review and commit source changes first; the working tree must be clean.'
commit_sha="$(git rev-parse HEAD)"
CLOUDSDK_PAGER=cat gcloud run deploy --help > "${TMPDIR:-/tmp}/ccdc-run-deploy-help.$$"
help_file="${TMPDIR:-/tmp}/ccdc-run-deploy-help.$$"
bucket_policy_file="${TMPDIR:-/tmp}/ccdc-bucket-policy.$$"
trap 'rm -f "$help_file" "$bucket_policy_file"; [[ -z "${work_dir:-}" ]] || rm -rf "$work_dir"' EXIT
python - "$help_file" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
for flag in ('--[no-]iap', '--[no-]allow-unauthenticated', '--[no-]invoker-iam-check', '--[no-]cpu-throttling'):
    if flag not in text:
        raise SystemExit('Upgrade the official gcloud CLI; required flag missing: ' + flag)
PY

# Read-only preflight. No bucket, project, service account or repository is created.
gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectId)' >/dev/null
gcloud billing projects describe "$GCP_PROJECT_ID" --format=json | python -c 'import json,sys; assert json.load(sys.stdin).get("billingEnabled") is True, "Project billing must be enabled"'
gcloud storage buckets describe "gs://$GCS_BUCKET" --format=json > "$bucket_policy_file"
python - "$bucket_policy_file" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
    # gcloud storage uses snake_case in its normalized JSON resource.
    pap = data.get('public_access_prevention', data.get('iamConfiguration', {}).get('publicAccessPrevention'))
    ubla = data.get('uniform_bucket_level_access', data.get('iamConfiguration', {}).get('uniformBucketLevelAccess', {}).get('enabled'))
    if isinstance(ubla, dict): ubla = ubla.get('enabled')
    if pap != 'enforced' or ubla is not True:
        raise SystemExit('Data bucket must enforce Public Access Prevention and Uniform Bucket-Level Access; verify its policy.')
finally:
    path.unlink(missing_ok=True)
PY
for bucket in "$BUILD_SOURCE_BUCKET" "$BUILD_LOG_BUCKET"; do gcloud storage buckets describe "gs://$bucket" --format='value(name)' >/dev/null; done
gcloud artifacts repositories describe "$ARTIFACT_REGISTRY_REPOSITORY" --location="$REGION" --project="$GCP_PROJECT_ID" --format='value(name)' >/dev/null
gcloud iam service-accounts describe "$RUNTIME_SERVICE_ACCOUNT" --project="$GCP_PROJECT_ID" --format='value(email)' >/dev/null
gcloud iam service-accounts describe "${BUILD_SERVICE_ACCOUNT##*/}" --project="$GCP_PROJECT_ID" --format='value(email)' >/dev/null

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/ccdc-deploy.XXXXXXXX")"
mkdir "$work_dir/source"
# Literal allowlist excludes .git/history, data, HTML, Excel, ZIP, keys, .env and local outputs.
mapfile -t source_paths < <(git ls-tree -r --name-only "$commit_sha" | python -c 'import re,sys; allowed={"Dockerfile",".dockerignore",".gcloudignore","requirements-lock.txt","app.py","core.py","ui_labels.py"}; print("\n".join(p for p in sys.stdin.read().splitlines() if p in allowed or re.fullmatch(r"services/[A-Za-z0-9_]+\.py", p)))')
[[ "${#source_paths[@]}" -ge 8 ]] || die 'Required committed cloud source files are missing.'
git archive --format=tar "$commit_sha" -- "${source_paths[@]}" | tar -xf - -C "$work_dir/source"
for required in Dockerfile .dockerignore .gcloudignore app.py core.py ui_labels.py requirements-lock.txt services/config.py; do [[ -f "$work_dir/source/$required" ]] || die "Missing committed source: $required"; done
export GCP_PROJECT_ID GCS_BUCKET GCS_PREFIX ADMIN_EMAILS IAP_AUDIENCE APP_ENV MAX_UPLOAD_MB LOG_LEVEL
export MAX_UNCOMPRESSED_MB MAX_ROWS MAX_COLUMNS VALIDATION_WARNING_LIMITS
python - "$work_dir/env.json" "$work_dir/source" <<'PY'
import json, os, pathlib, sys
sys.path.insert(0, sys.argv[2])
from services.config import Settings
keys = ['APP_ENV','GCP_PROJECT_ID','GCS_BUCKET','GCS_PREFIX','MAX_UPLOAD_MB','ADMIN_EMAILS','IAP_AUDIENCE','LOG_LEVEL',
        'MAX_UNCOMPRESSED_MB','MAX_ROWS','MAX_COLUMNS','VALIDATION_WARNING_LIMITS']
env = {key: os.environ[key] for key in keys}
env.update(STORAGE_BACKEND='gcs', LOCAL_DATA_DIR='/tmp/ccdc-unused')
Settings.from_env(env)  # Validate configuration using exactly the committed app code.
pathlib.Path(sys.argv[1]).write_text(json.dumps(env))
PY

image_tag="$REGION-docker.pkg.dev/$GCP_PROJECT_ID/$ARTIFACT_REGISTRY_REPOSITORY/$SERVICE_NAME:$commit_sha"
gcloud builds submit "$work_dir/source" --project="$GCP_PROJECT_ID" --region="$REGION" \
  --service-account="$BUILD_SERVICE_ACCOUNT" --gcs-source-staging-dir="gs://$BUILD_SOURCE_BUCKET/source" \
  --gcs-log-dir="gs://$BUILD_LOG_BUCKET/logs" --ignore-file="$work_dir/source/.gcloudignore" \
  --tag="$image_tag" --format=json --quiet > "$work_dir/build.json"
image_digest="$(python - "$work_dir/build.json" "$image_tag" <<'PY'
import json, re, sys
build = json.load(open(sys.argv[1]))
if build.get('status') != 'SUCCESS': raise SystemExit('Build did not succeed')
matches = [i['digest'] for i in build.get('results', {}).get('images', []) if i.get('name') == sys.argv[2]]
if len(matches) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', matches[0]):
    raise SystemExit('Build must return exactly one verified image digest')
print(matches[0])
PY
)"
image_ref="${image_tag%:*}@$image_digest"
revision_suffix="g${commit_sha:0:12}-$(date -u +%Y%m%d%H%M%S)"
mkdir -p "$DEPLOY_RECORD_DIR"
record_dir="$DEPLOY_RECORD_DIR/$SERVICE_NAME-$revision_suffix"
mkdir "$record_dir"
cp "$work_dir/build.json" "$record_dir/build.json"
printf '%s\n' "$commit_sha" > "$record_dir/commit.txt"
printf '%s\n' "$image_ref" > "$record_dir/image.txt"
gcloud version > "$record_dir/gcloud-version.txt"
# Native direct IAP is enabled in the deployment itself; there is no public interval.
gcloud run deploy "$SERVICE_NAME" --project="$GCP_PROJECT_ID" --region="$REGION" \
  --image="$image_ref" --service-account="$RUNTIME_SERVICE_ACCOUNT" --port=8080 \
  --cpu=1 --memory=2Gi --min=0 --max=1 --min-instances=0 --max-instances=1 \
  --concurrency=20 --timeout=1800 --cpu-throttling --iap --invoker-iam-check \
  --no-allow-unauthenticated --revision-suffix="$revision_suffix" \
  --labels="managed-by=ccdc-deploy,source-commit=$commit_sha" \
  --env-vars-file="$work_dir/env.json" --quiet
gcloud run services describe "$SERVICE_NAME" --project="$GCP_PROJECT_ID" --region="$REGION" --format=json > "$record_dir/service.json"
gcloud run services get-iam-policy "$SERVICE_NAME" --project="$GCP_PROJECT_ID" --region="$REGION" --format=json > "$record_dir/run-iam.json"
python - "$record_dir" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]); service = json.loads((p/'service.json').read_text())
annotations = service.get('metadata', {}).get('annotations', {})
if annotations.get('run.googleapis.com/iap-enabled') != 'true':
    raise SystemExit('STOP: service does not report IAP enabled. Do not grant users access or ingest data.')
iam = json.loads((p/'run-iam.json').read_text())
if any(m in ('allUsers','allAuthenticatedUsers') for b in iam.get('bindings', []) for m in b.get('members', [])):
    raise SystemExit('STOP: broad Cloud Run IAM membership found. Owner must remove it; do not ingest data.')
print('Revision: ' + service.get('status', {}).get('latestReadyRevisionName', 'UNKNOWN'))
print('Release evidence: ' + str(p))
print('Deployment is pending IAP browser/WebSocket, approved-user, denied-user and staging acceptance checks.')
PY
