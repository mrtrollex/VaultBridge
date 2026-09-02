# Dashboard release-readiness checklist

This document records VB-074 evidence without assigning a release version or claiming that a
dashboard-capable image has been published. It distinguishes local source/browser verification from
the required normal-production-image runtime gate.

## Current status

```text
Branch: feat/vb-074-dashboard-hardening
Baseline commit: 5c1e04ea7b6b127bf810fbfc50e6796b7440cc19
Local implementation and automated checks: PASS
Real Chrome functional/accessibility/privacy audit: PASS
Sanitized screenshot artifact capture: BLOCKED
Normal production-image container gate: PASS on disposable TrueNAS
VB-074 overall: COMPLETE
```

The screenshot limitation is an evidence-capture failure, not a rendered-page failure: four
attempts through the connected real-Chrome screenshot command timed out. No partial or fabricated
image was added. The dashboard was still inspected through its rendered DOM, computed layout, focus
state, CSSOM, network-driven state transitions, and console log stream.

## Completed TrueNAS Phase B evidence

The normal Dockerfile image built from the current VB-074 source passed the required real TrueNAS
gate using only a disposable container, unused loopback port, generated disposable API key,
synthetic vault, and disposable derived-data directory. No production VaultBridge container, vault,
API key, port, or derived-data dataset was used or modified.

Sanitized gate output:

```text
Docker build: PASS
PASS liveness
PASS safe health contract
PASS dashboard assets, media types, headers, and key absence
PASS authenticated list and literal search
PASS real semantic startup and search
PASS CLI present in the same image
PASS log-safety checks
PASS restart liveness
PASS restart with persistent disposable derived data
PASS clean container stop
PASS disposable container, image, vault, and data cleanup
```

Transient curl connection resets occurred during initial startup and restart. The bounded liveness
and rich-health retry logic handled them while continuing to require the disposable container to
exist and remain running; every health, authentication, semantic, persistence, log-safety, stop, and
cleanup assertion passed.

## Local automated evidence

Executed on native Windows with Python 3.12:

| Check | Result |
|---|---|
| `pytest -q -ra` | PASS: 426 passed, 15 privilege-dependent symlink tests skipped |
| focused `tests/test_ui.py` | PASS: 28 passed |
| `python -m compileall -q app` | PASS |
| `ruff check .` | PASS |
| `python -m app.cli --help` | PASS |
| exact API method/path/operation-ID matrix | PASS through `tests/test_ui.py` |
| `git diff --check` | PASS; only normal Windows LF-to-CRLF notices were emitted |
| Phase B code block through `bash -n` | PASS |

The UI regression tests are static/HTTP tests. They do not substitute for browser execution.

Focused hardening coverage verifies:

- explicit UI routes, complete asset inventory, media types, CSP, `nosniff`, and no-referrer;
- no remote resources, inline scripts/event handlers, unsafe dynamic DOM APIs, local storage,
  IndexedDB, cookies, service workers, console logging, or persistent query/result storage;
- the unchanged `vaultbridge.ui.apiKey` `sessionStorage` namespace and same-origin Bearer wrapper;
- no new API route, operation ID, note/index mutation control, or TrueNAS-specific dashboard UI;
- one H1, landmarks, labels, instruction associations, live status regions, and native radio inputs;
- long-content wrapping, narrow navigation reflow, focus indicators, palette contrast, and the
  explicit reduced-motion rule.

## Real Chrome evidence

The connected real Chrome session used a disposable three-note synthetic vault, deterministic local
fake embeddings, a disposable API key, and loopback-only local serving. No personal note, private
hostname/IP, production credential, production vault, or TrueNAS dataset was used.

Executed and observed:

- locked initial state, invalid key, successful unlock, reload restoration without focus theft,
  explicit logout, and rotated-key `401` invalidation;
- public Overview Ready state, manual refresh, and network-unavailable error state;
- Literal and Semantic search, empty results, native numeric validation, rate limiting with a
  textual positive `Retry-After`, controlled semantic `503`, and network failure;
- deliberately slow search followed by a newer search, plus logout during a slow request; late
  responses did not repaint protected data;
- long Unicode/Slovak title, nested path, emoji, long heading/snippet, a no-space token,
  quotes/ampersands, and literal `<script>`/HTML-like content;
- zero injected `script` or `img` result descendants, no JavaScript dialog, and text-only output;
- native radio Arrow-key selection and logical reachable focus targets in locked, Overview, and
  Search states; no positive `tabindex` or keyboard trap;
- visible focused-control outline in Chrome (`3px` solid with `3px` offset in the active dark
  palette); status meaning remained textual rather than color-only;
- desktop, approximately 768 px, 390 px, and 320 px CSS viewports. At every measured viewport,
  document `scrollWidth == clientWidth`; navigation stayed inside the viewport, result cards had no
  internal horizontal overflow, and mobile navigation wrapped to two rows;
- the 320 px viewport also supplies the WCAG-style 400% reflow equivalent for a 1280 px reference
  viewport. The browser-control surface did not expose a reliable independent zoom-level reading;
- the active environment reported `prefers-reduced-motion: no-preference`. Chrome parsed the
  explicit `prefers-reduced-motion: reduce` rule with transitions disabled; no information or
  function depends on animation;
- Chrome console warnings/errors after the exercised flows: none.

The local test server was not the production container gate. Its fake embedder changed only model
execution for disposable browser QA; the production UI, FastAPI routes, schemas, authentication,
rate limiter, vault search, semantic service/repository, and background indexing paths remained in
use.

## Contrast audit

Declared palette pairs were calculated with the WCAG relative-luminance formula and guarded by a
test. Normal text pairs are at least 4.5:1 and focus-indicator pairs are at least 3:1. Representative
ratios include:

| Pair | Light | Dark |
|---|---:|---:|
| primary text / page background | 14.98:1 | 16.16:1 |
| muted text / page background | 5.64:1 | 9.35:1 |
| accent text / surface | 6.29:1 | 7.90:1 |
| error text / surface | 6.60:1 | 7.93:1 |
| focus indicator / surface | 4.72:1 | 7.86:1 |

## Browser support statement

The bundled dashboard targets modern evergreen browsers that support ES modules, `fetch`,
`AbortController`, `sessionStorage`, `URL`, and `Intl.NumberFormat`. This standards-based target is
broader than the actual QA evidence. The current VB-074 rendered acceptance was run in the connected
real Chrome browser only; no untested browser/version is claimed.

## Phase B procedure: disposable TrueNAS production-image gate

Docker is not installed on the local Windows host. The commands below must run on the TrueNAS host
from a shell with `docker`, `curl`, `jq`, `openssl`, `ss`, and permission to build/run disposable
containers. They assume the **current VB-074 working tree, including its uncommitted hardening diff,**
has already been transferred to a separate TrueNAS source directory. Do not point `VB074_SOURCE` at
the production VaultBridge checkout while it is being updated or mutated.

The procedure creates a uniquely named temporary root under `/mnt/Apps/AppsData`, binds only its own
synthetic vault/data directories, selects an unused loopback port, and uses a disposable key. It does
not stop, restart, inspect, mount, or reuse the production container, vault, derived data, port, or
credential.

```bash
set -eu

for command_name in docker curl jq openssl ss mktemp realpath; do
  command -v "$command_name" >/dev/null
done

printf 'Absolute path to transferred current VB-074 source: '
read -r VB074_SOURCE
VB074_SOURCE=$(realpath "$VB074_SOURCE")
test -f "$VB074_SOURCE/Dockerfile"
test -f "$VB074_SOURCE/app/ui/assets/app.js"
test -f "$VB074_SOURCE/app/ui/assets/app.css"
grep -Fq 'focusAfterSuccess' "$VB074_SOURCE/app/ui/assets/app.js"
grep -Fq '@media (prefers-reduced-motion: reduce)' "$VB074_SOURCE/app/ui/assets/app.css"

printf 'Source branch: '
git -C "$VB074_SOURCE" branch --show-current
test "$(git -C "$VB074_SOURCE" branch --show-current)" = 'feat/vb-074-dashboard-hardening'
printf 'Source commit: '
git -C "$VB074_SOURCE" rev-parse HEAD
printf 'Expected VB-074 working-tree changes:\n'
git -C "$VB074_SOURCE" status --short

VB074_ROOT=$(mktemp -d /mnt/Apps/AppsData/vb074-gate.XXXXXX)
VB074_ROOT=$(realpath "$VB074_ROOT")
case "$VB074_ROOT" in
  /mnt/Apps/AppsData/vb074-gate.*) ;;
  *) printf 'Unsafe temporary root: %s\n' "$VB074_ROOT" >&2; exit 1 ;;
esac

VB074_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
VB074_IMAGE="vaultbridge:vb074-gate-$VB074_STAMP"
VB074_CONTAINER="vaultbridge-vb074-gate-$VB074_STAMP"
VB074_KEY=$(openssl rand -hex 32)
test "${#VB074_KEY}" -eq 64

VB074_PORT=
for candidate in $(seq 18874 18924); do
  if ! ss -H -ltn "sport = :$candidate" | grep -q .; then
    VB074_PORT=$candidate
    break
  fi
done
test -n "$VB074_PORT"

cleanup_vb074_gate() {
  docker rm -f "$VB074_CONTAINER" >/dev/null 2>&1 || true
  docker image rm "$VB074_IMAGE" >/dev/null 2>&1 || true
  case "$VB074_ROOT" in
    /mnt/Apps/AppsData/vb074-gate.*) rm -rf -- "$VB074_ROOT" ;;
    *) printf 'Refusing unsafe cleanup target: %s\n' "$VB074_ROOT" >&2; return 1 ;;
  esac
}
trap cleanup_vb074_gate EXIT INT TERM

mkdir -p "$VB074_ROOT/vault/Synthetic" "$VB074_ROOT/data"
cat >"$VB074_ROOT/vault/Synthetic/Container Gate.md" <<'EOF'
# Disposable container gate

VB074_DISPOSABLE_MARKER confirms literal search in an isolated synthetic vault.

Local backup recovery and storage verification provide a semantic-search fixture.
EOF
chown -R 568:568 "$VB074_ROOT/vault" "$VB074_ROOT/data"

docker build --tag "$VB074_IMAGE" "$VB074_SOURCE"
docker run -d \
  --name "$VB074_CONTAINER" \
  --user 568:568 \
  --publish "127.0.0.1:$VB074_PORT:8000" \
  --env "API_KEY=$VB074_KEY" \
  --env 'API_KEY_PREVIOUS=' \
  --env 'RATE_LIMIT_ENABLED=true' \
  --env 'RATE_LIMIT_REQUESTS=1000' \
  --env 'RATE_LIMIT_WINDOW_SECONDS=60' \
  --env 'RATE_LIMIT_MAX_CLIENTS=16' \
  --env 'VAULT_PATH=/vault' \
  --env 'SEMANTIC_DATA_PATH=/data' \
  --env 'HF_HOME=/data/huggingface' \
  --env 'SEMANTIC_WATCH_ENABLED=false' \
  --volume "$VB074_ROOT/vault:/vault" \
  --volume "$VB074_ROOT/data:/data" \
  "$VB074_IMAGE" >/dev/null

for attempt in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$VB074_PORT/health/live" \
    | jq -e '.ok == true' >/dev/null; then
    break
  fi
  test "$attempt" -lt 60
  sleep 2
done
printf 'PASS liveness\n'

HEALTH_JSON=
for attempt in $(seq 1 30); do
  if HEALTH_JSON=$(curl -fsS "http://127.0.0.1:$VB074_PORT/health"); then
    break
  fi

  if ! docker container inspect "$VB074_CONTAINER" >/dev/null 2>&1; then
    printf 'Container disappeared while waiting for /health\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  if test "$(docker inspect --format '{{.State.Running}}' "$VB074_CONTAINER")" != "true"; then
    printf 'Container stopped while waiting for /health\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  if test "$attempt" -eq 30; then
    printf 'Timed out waiting for /health after 30 attempts\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  sleep 2
done

test -n "$HEALTH_JSON"
printf '%s' "$HEALTH_JSON" | jq -e '
  (.ok | type == "boolean") and
  (.vault_exists | type == "boolean") and
  (.semantic_index_ready | type == "boolean") and
  (.semantic_index_state | IN("uninitialized", "indexing", "ready", "error")) and
  (.semantic_search_available | type == "boolean") and
  (.semantic_indexer_running | type == "boolean") and
  (.full_sync_required | type == "boolean") and
  (.indexed_notes | type == "number") and
  (.semantic_chunks | type == "number") and
  (.vault_notes | type == "number") and
  ((.last_successful_sync == null) or (.last_successful_sync | type == "string"))
' >/dev/null
printf 'PASS safe health contract\n'

curl -fsS -D "$VB074_ROOT/ui.headers" \
  "http://127.0.0.1:$VB074_PORT/ui/" -o "$VB074_ROOT/ui.html"
grep -Eiq '^content-type: text/html' "$VB074_ROOT/ui.headers"
grep -Eiq "^content-security-policy: .*default-src 'self'.*script-src 'self'.*style-src 'self'.*connect-src 'self'.*object-src 'none'.*frame-ancestors 'none'" "$VB074_ROOT/ui.headers"
grep -Eiq '^x-content-type-options: nosniff' "$VB074_ROOT/ui.headers"
grep -Eiq '^referrer-policy: no-referrer' "$VB074_ROOT/ui.headers"
! grep -Eiq "unsafe-inline|unsafe-eval" "$VB074_ROOT/ui.headers"

for asset in app.css app.js overview.js search.js; do
  curl -fsS -D "$VB074_ROOT/$asset.headers" \
    "http://127.0.0.1:$VB074_PORT/ui/assets/$asset" \
    -o "$VB074_ROOT/$asset"
done
grep -Eiq '^content-type: text/css' "$VB074_ROOT/app.css.headers"
for asset in app.js overview.js search.js; do
  grep -Eiq '^content-type: text/javascript' "$VB074_ROOT/$asset.headers"
done
if grep -Fq -- "$VB074_KEY" \
  "$VB074_ROOT/ui.html" "$VB074_ROOT/app.css" "$VB074_ROOT/app.js" \
  "$VB074_ROOT/overview.js" "$VB074_ROOT/search.js"; then
  printf 'Disposable API key leaked into UI resources\n' >&2
  exit 1
fi
printf 'PASS dashboard assets, media types, headers, and key absence\n'

LIST_JSON=$(curl -fsS \
  -H "Authorization: Bearer $VB074_KEY" \
  "http://127.0.0.1:$VB074_PORT/api/v1/notes/list?limit=1")
printf '%s' "$LIST_JSON" | jq -e '.notes | type == "array"' >/dev/null

LITERAL_JSON=$(curl -fsS -X POST \
  -H "Authorization: Bearer $VB074_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"query":"VB074_DISPOSABLE_MARKER","limit":5}' \
  "http://127.0.0.1:$VB074_PORT/api/v1/notes/search")
printf '%s' "$LITERAL_JSON" | jq -e \
  '.results | any(.title == "Container Gate")' >/dev/null
printf 'PASS authenticated list and literal search\n'

for attempt in $(seq 1 180); do
  if curl -fsS "http://127.0.0.1:$VB074_PORT/health" \
    | jq -e '.semantic_search_available == true' >/dev/null; then
    break
  fi
  test "$attempt" -lt 180
  sleep 10
done

SEMANTIC_JSON=$(curl -fsS -X POST \
  -H "Authorization: Bearer $VB074_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"text":"backup recovery storage","limit":5,"min_score":-1}' \
  "http://127.0.0.1:$VB074_PORT/api/v1/notes/related")
printf '%s' "$SEMANTIC_JSON" | jq -e \
  '.results | any(.title == "Container Gate")' >/dev/null
printf 'PASS real semantic startup and search\n'

docker run --rm --entrypoint python "$VB074_IMAGE" -m app.cli --help \
  | grep -Fq 'Local VaultBridge vault and semantic-index operations'
printf 'PASS CLI present in the same image\n'

docker logs "$VB074_CONTAINER" >"$VB074_ROOT/container.log" 2>&1
if grep -Fq -- "$VB074_KEY" "$VB074_ROOT/container.log"; then
  printf 'Disposable API key appeared in logs\n' >&2
  exit 1
fi
if grep -Fq -- "$VB074_ROOT" "$VB074_ROOT/container.log"; then
  printf 'Disposable absolute host path appeared in logs\n' >&2
  exit 1
fi
printf 'PASS log-safety checks\n'

INDEXED_BEFORE=$(curl -fsS "http://127.0.0.1:$VB074_PORT/health" | jq -r '.indexed_notes')
test -s "$VB074_ROOT/data/semantic-index.sqlite3"
docker restart "$VB074_CONTAINER" >/dev/null

for attempt in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$VB074_PORT/health/live" \
    | jq -e '.ok == true' >/dev/null; then
    break
  fi

  if ! docker container inspect "$VB074_CONTAINER" >/dev/null 2>&1; then
    printf 'Container disappeared while waiting for restart liveness\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  if test "$(docker inspect --format '{{.State.Running}}' "$VB074_CONTAINER")" != "true"; then
    printf 'Container stopped while waiting for restart liveness\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  if test "$attempt" -eq 60; then
    printf 'Timed out waiting for restart liveness after 60 attempts\n' >&2
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  sleep 2
done
printf 'PASS restart liveness\n'

RESTART_HEALTH_JSON=
for attempt in $(seq 1 180); do
  if CURRENT_RESTART_HEALTH_JSON=$(curl -fsS "http://127.0.0.1:$VB074_PORT/health"); then
    RESTART_HEALTH_JSON=$CURRENT_RESTART_HEALTH_JSON
    if printf '%s' "$RESTART_HEALTH_JSON" \
      | jq -e '.semantic_search_available == true' >/dev/null; then
      break
    fi
  else
    if ! docker container inspect "$VB074_CONTAINER" >/dev/null 2>&1; then
      printf 'Container disappeared while waiting for semantic search after restart\n' >&2
      docker logs "$VB074_CONTAINER" 2>&1 || true
      exit 1
    fi

    if test "$(docker inspect --format '{{.State.Running}}' "$VB074_CONTAINER")" != "true"; then
      printf 'Container stopped while waiting for semantic search after restart\n' >&2
      docker logs "$VB074_CONTAINER" 2>&1 || true
      exit 1
    fi
  fi

  if test "$attempt" -eq 180; then
    printf 'Semantic search did not become available after restart\n' >&2
    if test -n "$RESTART_HEALTH_JSON"; then
      printf '%s' "$RESTART_HEALTH_JSON" | jq '{
        ok,
        vault_exists,
        semantic_index_ready,
        semantic_index_state,
        semantic_search_available,
        semantic_indexer_running,
        full_sync_required,
        indexed_notes,
        semantic_chunks,
        vault_notes,
        last_successful_sync
      }' || printf 'Latest restart health response was not valid JSON\n' >&2
    else
      printf 'No successful /health response was obtained after restart\n' >&2
    fi
    docker logs "$VB074_CONTAINER" 2>&1 || true
    exit 1
  fi

  sleep 2
done

INDEXED_AFTER=$(printf '%s' "$RESTART_HEALTH_JSON" | jq -r '.indexed_notes')
test "$INDEXED_AFTER" = "$INDEXED_BEFORE"
test -s "$VB074_ROOT/data/semantic-index.sqlite3"
printf 'PASS restart with persistent disposable derived data\n'

docker stop --time 30 "$VB074_CONTAINER" >/dev/null
test "$(docker inspect --format '{{.State.ExitCode}}' "$VB074_CONTAINER")" = '0'
printf 'PASS clean container stop\n'

cleanup_vb074_gate
trap - EXIT INT TERM
test ! -e "$VB074_ROOT"
! docker container inspect "$VB074_CONTAINER" >/dev/null 2>&1
! docker image inspect "$VB074_IMAGE" >/dev/null 2>&1
printf 'PASS disposable container, image, vault, and data cleanup\n'
```

Record the command output without shell tracing and provide it back for Phase B review. Do not paste
the disposable key, resolved container environment, or a resolved Compose configuration.

## Known limitations and completion rule

- Static tests are not browser execution; the real-Chrome run above is recorded separately.
- Reduced-motion CSS was parsed in Chrome, but the connected environment exposed only the
  `no-preference` OS setting and no safe media-emulation control.
- Sanitized screenshots are not checked in because Chrome screenshot capture was unavailable during
  this run.
- Docker/Compose is unavailable locally, so no local build or runtime success is claimed; the
  separate real TrueNAS production-image gate above passed.
- VB-074 and Milestone 8 are complete based on the combined automated, real-Chrome, and disposable
  TrueNAS evidence recorded here.
- VaultBridge `v1.1.0` is published and its exact OCI digest is anonymously pullable; VB-081 now pins
  exact tag `1.1.0`. The full published-image functional gate below remains unevidenced. VB-082 is
  separately in progress / partial validation, with a custom-YAML core runtime/API/UI PASS record and
  required lifecycle/negative/upgrade/uninstall gates still open. This checklist does not create or
  publish a release.

## VB-075 open functional gate: exact published `v1.1.0` dashboard image

The VB-074 evidence above remains an immutable historical record of the source-built Phase B gate.
It must not be rewritten as proof that the later final UI or published `v1.1.0` image existed during
that run. The release image now exists. The supplied evidence proves its source, workflow, aliases,
digests, `linux/amd64` platform, required OCI labels, and anonymous exact-digest pull. It does not
prove the functional checks below, which remain **REQUIRES LIVE VERIFICATION**.

Record these exact values from the successful release and workflow before running the gate:

```text
release commit SHA: e39ed91db75f912f390c7ec915dea73369bb9252
Git tag: v1.1.0
GitHub Release: v1.1.0
GHCR workflow run: 33641163374
OCI index digest: sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c
linux/amd64 runtime-manifest digest: sha256:62ddad69cf3e4af632d40c3bcdb8f9e601fa7c04009616350f9ab706a4171e92
BuildKit attestation: sha256:744fe630075b91cc576a18b37012c4d37d0b551b17a74cab8088a1448e5fc4a3
exact runtime reference: ghcr.io/mrtrollex/vaultbridge@sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c
anonymous pull: PASS
runtime platform inspection: linux/amd64
OCI source/revision/version/license labels: PASS
```

### Published bundle and delivery contract

Use `GET` and `HEAD` where applicable. Require the exact pulled digest to serve:

| Resource | Required media type | Required checks |
|---|---|---|
| `/ui` | redirect | HTTP `307` to canonical `/ui/`; no credential in location or body |
| `/ui/` | `text/html` | Current dashboard shell; CSP, `nosniff`, and `no-referrer` headers |
| `/ui/assets/app.css` | `text/css` | Present in image; security headers; no disposable key |
| `/ui/assets/app.js` | `text/javascript` | Present in image; security headers; no disposable key |
| `/ui/assets/overview.js` | `text/javascript` | Present in image; security headers; no disposable key |
| `/ui/assets/search.js` | `text/javascript` | Present in image; security headers; no disposable key |
| `/ui/assets/vaultbridge-logo.webp` | `image/webp` | Present in image; security headers; no disposable key |

Require the CSP to retain self-only script/style/connect delivery, deny objects and framing, and omit
`unsafe-inline`/`unsafe-eval`. Unknown `/ui` paths must remain `404`, and UI routes must remain absent
from OpenAPI. Search the downloaded shell/assets and container logs for the generated disposable key;
the search must return no matches.

### Current dashboard behavior gate

Using only disposable synthetic notes and a generated key, verify in the published image:

- the protected-access flow starts locked, rejects an invalid key, validates through the existing
  protected API, keeps the validated key only in namespaced `sessionStorage`, restores the session on
  reload, and clears it plus protected results/note content on logout or `401`;
- Overview loads the public `/health` contract without a Bearer header and renders responding,
  indexing/degraded, malformed, and unavailable states without inventing progress;
- Literal Search calls `POST /api/v1/notes/search`, preserves server order, and renders synthetic
  results as text;
- Semantic Search calls `POST /api/v1/notes/related`, waits for actual semantic availability,
  preserves server order/scores, and renders synthetic results as text;
- opening a result calls protected `GET /api/v1/notes/read`, displays the complete Markdown source as
  text without executing HTML, returns focus to the originating result, suppresses stale reads, and
  clears the reader when protected access ends;
- late Overview, search, and note-reader responses cannot repaint stale or logged-out data;
- no browser console error, remote resource, dashboard-specific API, note mutation, or live index
  mutation is introduced.

The existing local/static UI tests support this audit but do not prove these behaviors in the
published image.

### Exact-image API, CLI, persistence, and privacy gate

Run the anonymously pulled digest with a new disposable root, synthetic vault, persistent derived
data, unique container name, unused loopback port, and new random key. Do not inspect, stop, mount,
or reuse any production container, vault, data, port, or credential. Require:

1. container remains running and `GET /health/live` returns HTTP 200 with `ok: true`;
2. `GET /health` returns the safe rich health contract while initial indexing progresses;
3. protected `GET /api/v1/notes/list?limit=1` succeeds;
4. Literal and real Semantic Search find only the expected synthetic note;
5. protected complete note read returns the expected synthetic Markdown;
6. `python -m app.cli --help` succeeds from the same exact image;
7. derived semantic data exists, survives container restart, and semantic retrieval becomes
   available again without replacing the disposable `/data` mount;
8. application logs omit the key, previous key, authorization headers, query text, note content,
   private/resolved host paths, and complete environment;
9. clean stop returns exit code zero;
10. the uniquely named container, temporary Docker auth directory, and disposable vault/data root are
    removed after resolving and validating their exact paths.

Record the TrueNAS version, Docker version, UTC time, release/workflow URLs, exact release commit,
tag-to-index-digest mapping, runtime-manifest digest, platform, OCI source/revision/version/license
labels, anonymous-pull result, readiness time, restart/persistence result, safe-log result, and cleanup
result. Never record the generated key, previous key, resolved container environment, synthetic query
or note content, or absolute host path.

Passing this VB-075 exact-image gate completes the remaining functional release evidence. VB-081's
source image/app metadata is finalized, but official generated/deployable validation remains open.
VB-082 separately records a partial TrueNAS `25.10.6` custom-YAML core runtime/API/UI validation in
[`TRUENAS_COMMUNITY_APP_DESIGN.md`](TRUENAS_COMMUNITY_APP_DESIGN.md#vb-082-partial-validation-record--2026-09-02).
That evidence does not prove catalog form/Portal behavior and does not complete the required
install/edit/upgrade/rollback/uninstall lifecycle. VB-083 remains blocked, and no upstream submission
has been performed.
