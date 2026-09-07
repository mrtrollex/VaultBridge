#!/usr/bin/env bash

set -Eeuo pipefail

readonly IMAGE_REF="ghcr.io/mrtrollex/vaultbridge@sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c"
readonly RELEASE_COMMIT="e39ed91db75f912f390c7ec915dea73369bb9252"
readonly RUNTIME_MANIFEST="sha256:62ddad69cf3e4af632d40c3bcdb8f9e601fa7c04009616350f9ab706a4171e92"
readonly RELEASE_WORKFLOW="33641163374"
readonly RELEASE_URL="https://github.com/mrtrollex/VaultBridge/releases/tag/v1.1.0"
readonly RELEASE_WORKFLOW_URL="https://github.com/mrtrollex/VaultBridge/actions/runs/33641163374"
readonly TMP_BASE="${TMPDIR:-/tmp}"

test_root=""
container_name=""
container_created=false

fail() {
  printf 'VB-075 exact-image gate: FAIL (%s)\n' "$1" >&2
  exit 1
}

on_error() {
  local line_number="$1"
  local line_stack="${BASH_LINENO[*]}"
  trap - ERR
  fail "unexpected command failure at line $line_number (stack $line_stack)"
}
trap 'on_error "$LINENO"' ERR

safe_remove_test_root() {
  [[ -n "$test_root" && -d "$test_root" ]] || return 0
  local resolved_root resolved_base
  resolved_root="$(realpath "$test_root")"
  resolved_base="$(realpath "$TMP_BASE")"
  [[ "$(dirname "$resolved_root")" == "$resolved_base" ]] || return 1
  [[ "$(basename "$resolved_root")" == vaultbridge-vb075.* ]] || return 1
  rm -rf -- "$resolved_root"
}

cleanup() {
  local original_status=$?
  set +e
  if [[ "$container_created" == true && -n "$container_name" ]]; then
    docker rm --force "$container_name" >/dev/null 2>&1
  fi
  safe_remove_test_root
  return "$original_status"
}
trap cleanup EXIT INT TERM

for command_name in docker curl python3 realpath; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing required command: $command_name"
done
docker info >/dev/null 2>&1 || fail "Docker daemon unavailable"

test_root="$(mktemp -d "$TMP_BASE/vaultbridge-vb075.XXXXXX")"
auth_dir="$test_root/docker-auth"
vault_dir="$test_root/vault"
data_dir="$test_root/data"
evidence_dir="$test_root/evidence"
mkdir -p "$auth_dir" "$vault_dir" "$data_dir" "$evidence_dir"
chmod 0777 "$vault_dir" "$data_dir"

container_name="vaultbridge-vb075-$(python3 - <<'PY'
import secrets
print(secrets.token_hex(6))
PY
)"
api_key="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(36))
PY
)"
previous_api_key="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(36))
PY
)"
literal_marker="vb075literal$(python3 - <<'PY'
import secrets
print(secrets.token_hex(8))
PY
)"

export VB075_VAULT_DIR="$vault_dir"
export VB075_LITERAL_MARKER="$literal_marker"
python3 - <<'PY'
import os
from pathlib import Path

vault = Path(os.environ["VB075_VAULT_DIR"])
marker = os.environ["VB075_LITERAL_MARKER"]
(vault / "alpha.md").write_text(
    "# Transaction notes\n\nDatabase isolation and relational query planning.\n\n" + marker + "\n",
    encoding="utf-8",
)
(vault / "beta.md").write_text(
    "# Deep sky observing\n\nOrbital telescopes observe distant galaxies and nebulae beyond the solar system.\n",
    encoding="utf-8",
)
PY

DOCKER_CONFIG="$auth_dir" docker pull "$IMAGE_REF" >/dev/null 2>&1 || fail "anonymous immutable pull"
docker image inspect "$IMAGE_REF" >"$evidence_dir/image.json"
python3 - "$evidence_dir/image.json" "$IMAGE_REF" "$RELEASE_COMMIT" <<'PY'
import json
import sys

image = json.load(open(sys.argv[1], encoding="utf-8"))[0]
expected_ref, expected_revision = sys.argv[2:]
assert image["Os"] == "linux"
assert image["Architecture"] == "amd64"
assert expected_ref in image.get("RepoDigests", [])
labels = image["Config"].get("Labels") or {}
assert labels.get("org.opencontainers.image.source") == "https://github.com/mrtrollex/VaultBridge"
assert labels.get("org.opencontainers.image.revision") == expected_revision
assert labels.get("org.opencontainers.image.version") == "v1.1.0"
assert labels.get("org.opencontainers.image.licenses") == "MIT"
PY

host_port="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
base_url="http://127.0.0.1:$host_port"

network_args=(--publish "127.0.0.1:$host_port:8000")
command_args=()
if [[ "${VB075_HOST_NETWORK:-0}" == 1 ]]; then
  network_args=(--network host)
  command_args=(uvicorn app.main:app --host 127.0.0.1 --port "$host_port")
fi

docker run --detach \
  --name "$container_name" \
  --user 568:568 \
  --cap-drop ALL \
  "${network_args[@]}" \
  --env API_KEY="$api_key" \
  --env API_KEY_PREVIOUS="$previous_api_key" \
  --env VAULT_PATH=/vault \
  --env SEMANTIC_DATA_PATH=/data \
  --env HF_HOME=/data/huggingface \
  --env SEMANTIC_WATCH_ENABLED=false \
  --volume "$vault_dir:/vault:rw" \
  --volume "$data_dir:/data:rw" \
  "$IMAGE_REF" \
  "${command_args[@]}" >/dev/null
container_created=true

request() {
  curl --silent --show-error --fail --connect-timeout 5 --max-time 30 "$@"
}

wait_for_live() {
  local deadline=$((SECONDS + 120))
  until request "$base_url/health/live" >"$evidence_dir/live.json" 2>/dev/null; do
    (( SECONDS < deadline )) || fail "liveness timeout"
    sleep 2
  done
  python3 - "$evidence_dir/live.json" <<'PY'
import json
import sys
assert json.load(open(sys.argv[1], encoding="utf-8")) == {"ok": True}
PY
}

wait_for_ready() {
  local deadline=$((SECONDS + 1200))
  until request "$base_url/health/ready" >"$evidence_dir/ready.json" 2>/dev/null && \
    python3 - "$evidence_dir/ready.json" <<'PY' >/dev/null 2>&1
import json
import sys
assert json.load(open(sys.argv[1], encoding="utf-8")) == {"ready": True}
PY
  do
    (( SECONDS < deadline )) || fail "semantic readiness timeout"
    sleep 5
  done
}

wait_for_live
request "$base_url/health" >"$evidence_dir/health.json"
python3 - "$evidence_dir/health.json" <<'PY'
import json
import sys
health = json.load(open(sys.argv[1], encoding="utf-8"))
required = {
    "ok", "vault_exists", "semantic_index_ready", "semantic_index_state",
    "semantic_search_available", "semantic_indexer_running", "full_sync_required",
    "indexed_notes", "semantic_chunks", "vault_notes", "last_successful_sync",
}
assert set(health) == required
assert health["ok"] is True and health["vault_exists"] is True
PY

status="$(curl --silent --output "$evidence_dir/ui-redirect.txt" --dump-header "$evidence_dir/ui-redirect.headers" --write-out '%{http_code}' "$base_url/ui")"
[[ "$status" == 307 ]] || fail "canonical dashboard redirect"
grep -Eiq '^location: /ui/[[:space:]]*$' "$evidence_dir/ui-redirect.headers" || fail "dashboard redirect location"

request --dump-header "$evidence_dir/ui.headers" "$base_url/ui/" >"$evidence_dir/ui.html"
grep -Eiq '^content-type: text/html([;[:space:]]|$)' "$evidence_dir/ui.headers" || fail "dashboard media type"
grep -Eiq '^x-content-type-options: nosniff[[:space:]]*$' "$evidence_dir/ui.headers" || fail "nosniff header"
grep -Eiq '^referrer-policy: no-referrer[[:space:]]*$' "$evidence_dir/ui.headers" || fail "referrer policy"
csp="$(grep -Ei '^content-security-policy:' "$evidence_dir/ui.headers" | tr -d '\r' || true)"
[[ "$csp" == *"default-src 'self'"* && "$csp" == *"script-src 'self'"* && \
   "$csp" == *"style-src 'self'"* && "$csp" == *"connect-src 'self'"* && \
   "$csp" == *"object-src 'none'"* && "$csp" == *"frame-ancestors 'none'"* ]] || fail "dashboard CSP"
[[ "$csp" != *"unsafe-inline"* && "$csp" != *"unsafe-eval"* ]] || fail "unsafe dashboard CSP"

for asset in app.css app.js overview.js search.js vaultbridge-logo.webp; do
  request --dump-header "$evidence_dir/$asset.headers" "$base_url/ui/assets/$asset" >"$evidence_dir/$asset"
  grep -Eiq '^x-content-type-options: nosniff[[:space:]]*$' "$evidence_dir/$asset.headers" || fail "asset security headers"
done
status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$base_url/ui/not-a-route")"
[[ "$status" == 404 ]] || fail "unknown dashboard route"
status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$base_url/openapi.json")"
[[ "$status" == 404 ]] || fail "runtime OpenAPI remains disabled"
docker run --rm --entrypoint python "$IMAGE_REF" -c \
  'from app.main import app; assert not any(path == "/ui" or path.startswith("/ui/") for path in app.openapi()["paths"])'

status="$(curl --silent --output /dev/null --write-out '%{http_code}' --header 'Authorization: Bearer invalid-vb075-key' "$base_url/api/v1/notes/list?limit=1")"
[[ "$status" == 401 ]] || fail "invalid authentication rejection"
request --header "Authorization: Bearer $api_key" "$base_url/api/v1/notes/list?limit=2" >"$evidence_dir/list.json"
python3 - "$evidence_dir/list.json" <<'PY'
import json
import sys
assert {note["path"] for note in json.load(open(sys.argv[1], encoding="utf-8"))["notes"]} == {"alpha.md", "beta.md"}
PY

export VB075_EVIDENCE_DIR="$evidence_dir"
python3 - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ["VB075_EVIDENCE_DIR"])
(root / "literal-request.json").write_text(
    json.dumps({"query": os.environ["VB075_LITERAL_MARKER"], "limit": 2}), encoding="utf-8"
)
(root / "semantic-request.json").write_text(
    json.dumps({"text": "astronomy telescope galaxies", "limit": 2, "min_score": -1}), encoding="utf-8"
)
PY
request --header "Authorization: Bearer $api_key" --header 'Content-Type: application/json' \
  --data-binary "@$evidence_dir/literal-request.json" "$base_url/api/v1/notes/search" >"$evidence_dir/literal.json"
python3 - "$evidence_dir/literal.json" <<'PY'
import json
import sys
assert [item["path"] for item in json.load(open(sys.argv[1], encoding="utf-8"))["results"]] == ["alpha.md"]
PY

initial_ready_started=$SECONDS
wait_for_ready
initial_ready_seconds=$((SECONDS - initial_ready_started))
request --header "Authorization: Bearer $api_key" --header 'Content-Type: application/json' \
  --data-binary "@$evidence_dir/semantic-request.json" "$base_url/api/v1/notes/related" >"$evidence_dir/semantic.json"
python3 - "$evidence_dir/semantic.json" <<'PY'
import json
import sys
results = json.load(open(sys.argv[1], encoding="utf-8"))["results"]
assert results and results[0]["path"] == "beta.md"
PY

request --get --header "Authorization: Bearer $api_key" --data-urlencode 'path=alpha.md' \
  "$base_url/api/v1/notes/read" >"$evidence_dir/read.json"
python3 - "$evidence_dir/read.json" "$literal_marker" <<'PY'
import json
import sys
note = json.load(open(sys.argv[1], encoding="utf-8"))
assert note["path"] == "alpha.md" and sys.argv[2] in note["content"]
PY

docker run --rm --entrypoint python "$IMAGE_REF" -m app.cli --help >/dev/null
find "$data_dir" -type f -size +0c -print -quit | grep -q . || fail "derived semantic data persistence"

docker stop --time 60 "$container_name" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$container_name")" == 0 ]] || fail "clean pre-restart stop"
docker start "$container_name" >/dev/null
restart_ready_started=$SECONDS
wait_for_live
wait_for_ready
restart_ready_seconds=$((SECONDS - restart_ready_started))
request --header "Authorization: Bearer $api_key" --header 'Content-Type: application/json' \
  --data-binary "@$evidence_dir/semantic-request.json" "$base_url/api/v1/notes/related" >"$evidence_dir/semantic-after-restart.json"
python3 - "$evidence_dir/semantic-after-restart.json" <<'PY'
import json
import sys
results = json.load(open(sys.argv[1], encoding="utf-8"))["results"]
assert results and results[0]["path"] == "beta.md"
PY

docker logs "$container_name" >"$evidence_dir/container.log" 2>&1
for forbidden in "$api_key" "$previous_api_key" "$literal_marker" "astronomy telescope galaxies" \
  "Orbital telescopes observe distant galaxies" "$test_root" 'Authorization:' 'API_KEY=' \
  'API_KEY_PREVIOUS=' 'VAULT_PATH=' 'SEMANTIC_DATA_PATH='; do
  if grep -Fq -- "$forbidden" "$evidence_dir/container.log"; then
    fail "privacy-safe log scan"
  fi
done
for artifact in "$evidence_dir/ui.html" "$evidence_dir/app.css" "$evidence_dir/app.js" \
  "$evidence_dir/overview.js" "$evidence_dir/search.js"; do
  grep -Fq -- "$api_key" "$artifact" && fail "dashboard secret scan"
done

docker stop --time 60 "$container_name" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$container_name")" == 0 ]] || fail "clean final stop"
docker rm "$container_name" >/dev/null
container_created=false

docker_version="$(docker version --format '{{.Server.Version}}')"
verified_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
safe_remove_test_root || fail "disposable path validation and cleanup"
[[ ! -e "$test_root" ]] || fail "disposable cleanup"
trap - EXIT INT TERM

printf '%s\n' \
  'VB-075 exact-image gate: PASS' \
  "verified UTC: $verified_at" \
  "Docker: $docker_version" \
  "release commit: $RELEASE_COMMIT" \
  "release workflow: $RELEASE_WORKFLOW" \
  "release URL: $RELEASE_URL" \
  "release workflow URL: $RELEASE_WORKFLOW_URL" \
  "image: $IMAGE_REF" \
  "runtime manifest: $RUNTIME_MANIFEST" \
  'platform: linux/amd64' \
  "initial readiness seconds: $initial_ready_seconds" \
  "restart readiness seconds: $restart_ready_seconds" \
  'dashboard/assets/security headers: PASS' \
  'authenticated API/literal/semantic/read: PASS' \
  'CLI availability: PASS' \
  'restart/persistence: PASS' \
  'privacy-safe logs: PASS' \
  'clean stop: PASS' \
  'disposable cleanup: PASS'
