#!/usr/bin/env bash
# Smoke-test a hosted Agere deployment.
#   deploy/smoke_hosted.sh https://agere.example.com
#   AGERE_SMOKE_KEY=agk_... deploy/smoke_hosted.sh https://agere.example.com   # also checks an API key
# Exits non-zero on the first failed check.
set -euo pipefail

BASE="${1:-${AGERE_SMOKE_URL:-}}"
if [[ -z "$BASE" ]]; then
  echo "usage: $0 <base-url>   (e.g. https://agere.example.com)" >&2
  exit 2
fi
BASE="${BASE%/}"
CURL=(curl -sS --max-time 15 -o /dev/null)
fail=0

check() {  # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then
    printf 'ok    %-40s %s\n' "$1" "$3"
  else
    printf 'FAIL  %-40s expected %s, got %s\n' "$1" "$2" "$3"
    fail=1
  fi
}

# 1. /healthz -> 200 with a JSON body
body="$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/healthz")" || { echo "FAIL  /healthz unreachable"; exit 1; }
code="${body##*$'\n'}"; json="${body%$'\n'*}"
check "GET /healthz status" 200 "$code"
if python3 -c 'import json,sys; json.loads(sys.argv[1])' "$json" 2>/dev/null; then
  printf 'ok    %-40s %s\n' "GET /healthz body is JSON" "$json"
else
  printf 'FAIL  %-40s %s\n' "GET /healthz body is JSON" "$json"; fail=1
fi

# 2. /app logged out -> redirect (3xx) to /login
read -r code location < <("${CURL[@]}" -w '%{http_code} %{redirect_url}\n' "$BASE/app")
case "$code" in 30[1278]) check "GET /app redirects (logged out)" 3xx 3xx ;; *) check "GET /app redirects (logged out)" 3xx "$code" ;; esac
if [[ "${location:-}" == *"/login"* ]]; then
  printf 'ok    %-40s %s\n' "GET /app -> /login" "$location"
else
  printf 'FAIL  %-40s got "%s"\n' "GET /app -> /login" "${location:-}"; fail=1
fi

# 3. /api/workspaces without auth -> 401
code="$("${CURL[@]}" -w '%{http_code}' "$BASE/api/workspaces")"
check "GET /api/workspaces (no auth)" 401 "$code"

# 4. optional: API key lists workspaces
if [[ -n "${AGERE_SMOKE_KEY:-}" ]]; then
  body="$(curl -sS --max-time 30 -w '\n%{http_code}' -H "Authorization: Bearer ${AGERE_SMOKE_KEY}" "$BASE/api/workspaces")"
  code="${body##*$'\n'}"; json="${body%$'\n'*}"
  check "GET /api/workspaces (API key)" 200 "$code"
  if python3 -c 'import json,sys; json.loads(sys.argv[1])' "$json" 2>/dev/null; then
    printf 'ok    %-40s\n' "API key response is JSON"
  else
    printf 'FAIL  %-40s\n' "API key response is JSON"; fail=1
  fi
else
  echo "skip  API key check (set AGERE_SMOKE_KEY=agk_... to run it)"
fi

if [[ "$fail" -ne 0 ]]; then echo "smoke: FAILED"; exit 1; fi
echo "smoke: all checks passed"
