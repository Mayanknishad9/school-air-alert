#!/usr/bin/env bash
# publish_site.sh - publishes frontend/index.html on AWS Amplify Hosting.
# First run creates the Amplify app; later runs just upload a new version.
#   bash scripts/publish_site.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Git Bash on Windows may not see the AWS CLI until VS Code restarts
command -v aws >/dev/null 2>&1 || export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2"

APP_NAME="school-air-alert"
REGION="ap-south-1"
BRANCH="main"
PY=$(command -v python || command -v python3)

# aws.exe on Windows ends lines with \r, which breaks URLs and IDs: strip it
awsq() { aws "$@" | tr -d '\r'; }

APP_ID=$(awsq amplify list-apps --region "$REGION" \
  --query "apps[?name=='$APP_NAME'].appId | [0]" --output text)
if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  echo "Creating Amplify app..."
  APP_ID=$(awsq amplify create-app --name "$APP_NAME" --region "$REGION" \
    --query app.appId --output text)
fi
if ! aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH" \
     --region "$REGION" > /dev/null 2>&1; then
  aws amplify create-branch --app-id "$APP_ID" --branch-name "$BRANCH" \
    --region "$REGION" > /dev/null
fi

# cancel uploads left unfinished by an earlier interrupted run
for J in $(awsq amplify list-jobs --app-id "$APP_ID" --branch-name "$BRANCH" --region "$REGION" \
             --query "jobSummaries[?status=='PENDING' || status=='RUNNING'].jobId" --output text); do
  aws amplify stop-job --app-id "$APP_ID" --branch-name "$BRANCH" --job-id "$J" \
    --region "$REGION" > /dev/null 2>&1 || true
done

"$PY" -c "import zipfile; z = zipfile.ZipFile('site.zip', 'w', zipfile.ZIP_DEFLATED); z.write('frontend/index.html', 'index.html'); z.close()"

read -r JOB_ID UPLOAD_URL < <(awsq amplify create-deployment --app-id "$APP_ID" \
  --branch-name "$BRANCH" --region "$REGION" \
  --query "[jobId, zipUploadUrl]" --output text)
curl -sS --fail -T site.zip "$UPLOAD_URL" > /dev/null
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" \
  --job-id "$JOB_ID" --region "$REGION" > /dev/null
rm -f site.zip

echo -n "Publishing"
for _ in $(seq 1 30); do
  STATUS=$(awsq amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" \
    --job-id "$JOB_ID" --region "$REGION" --query job.summary.status --output text)
  case "$STATUS" in
    SUCCEED) echo; echo "Live at: https://$BRANCH.$APP_ID.amplifyapp.com"; exit 0 ;;
    FAILED|CANCELLED) echo; echo "Amplify deployment $STATUS"; exit 1 ;;
  esac
  echo -n "."; sleep 3
done
echo; echo "Still publishing. Check https://$BRANCH.$APP_ID.amplifyapp.com in a minute."
