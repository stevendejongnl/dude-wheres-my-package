#!/bin/bash
# Send Telegram notification for CI events on dude-wheres-my-package, via
# Apprise. Requires env var APPRISE_URL — silently no-ops if missing so
# CI never fails because of this script.
#
# Usage:
#   send-telegram-notification.sh --type release_success --version 1.2.3 \
#       --branch main --commit <sha> --run-url <url>
#   send-telegram-notification.sh --type release_skip \
#       --branch main --commit <sha> --run-url <url>

set -e

REPO="stevendejongnl/dude-wheres-my-package"

NOTIFICATION_TYPE=""
VERSION=""
BRANCH=""
RUN_URL=""
COMMIT=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --type)     NOTIFICATION_TYPE="$2"; shift 2 ;;
    --version)  VERSION="$2"; shift 2 ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    --run-url)  RUN_URL="$2"; shift 2 ;;
    --commit)   COMMIT="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

escape_html() {
  local text="$1"
  text="${text//&/&amp;}"
  text="${text//</&lt;}"
  text="${text//>/&gt;}"
  text="${text//\"/&quot;}"
  text="${text//\'/&#x27;}"
  echo "$text"
}

short_sha="${COMMIT:0:7}"
escaped_branch=$(escape_html "$BRANCH")

case "$NOTIFICATION_TYPE" in
  release_success)
    if [[ -z "$VERSION" ]]; then
      echo "Error: --version required for release_success" >&2
      exit 1
    fi
    MESSAGE=$(cat <<EOF
<b>📦 DWMP v${VERSION} Released</b>

<b>Status:</b> ✅ Success
<b>Branch:</b> ${escaped_branch}
<b>Commit:</b> <code>${short_sha}</code>

<a href="https://github.com/${REPO}/releases/tag/v${VERSION}">Release notes</a> · <a href="${RUN_URL}">Workflow run</a>
EOF
)
    ;;
  release_skip)
    MESSAGE=$(cat <<EOF
<b>ℹ️ DWMP release check</b>

<b>Status:</b> No release needed
<b>Branch:</b> ${escaped_branch}
<b>Commit:</b> <code>${short_sha}</code>

<a href="${RUN_URL}">Workflow run</a>
EOF
)
    ;;
  *)
    echo "Error: Unknown notification type '${NOTIFICATION_TYPE}'" >&2
    exit 1
    ;;
esac

if [[ "$DRY_RUN" == "true" ]]; then
  echo "==== DRY RUN ===="
  echo "$MESSAGE"
  echo "================="
  exit 0
fi

if [[ -z "$APPRISE_URL" ]]; then
  echo "⚠️  APPRISE_URL not set — skipping notification"
  exit 0
fi

# Telegram cap is 4096 chars.
if [[ ${#MESSAGE} -gt 4000 ]]; then
  MESSAGE="${MESSAGE:0:4000}..."
fi

payload=$(python3 -c 'import json,sys; print(json.dumps({"title":"dwmp","body":sys.argv[1],"format":"html"}))' "$MESSAGE" 2>/dev/null)
if [[ -z "$payload" ]]; then
  echo "⚠️  failed to build notification payload, skipping"
  exit 0
fi

set +e
response=$(curl -sf -X POST "$APPRISE_URL" -H 'Content-Type: application/json' -d "$payload")
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo "⚠️  curl failed (exit $rc), response: $response"
  exit 0
fi

echo "✓ Apprise notification sent"
exit 0
