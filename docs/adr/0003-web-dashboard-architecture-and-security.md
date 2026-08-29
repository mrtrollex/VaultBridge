# ADR 0003: VaultBridge Web Dashboard architecture and security

- **Status:** Accepted
- **Date:** 2026-08-29

## Context

VaultBridge `v1.0.0` is a small self-hosted FastAPI application with a stable `/api/v1` interface,
compatibility aliases, a local CLI, Markdown source files, and a derived SQLite semantic index. The
post-v1 roadmap calls for a small first-party browser dashboard before future TrueNAS Community App
packaging. The dashboard must improve operator usability without becoming a second implementation of
VaultBridge or weakening the current API, authentication, privacy, containment, and index-ownership
boundaries.

Current facts that constrain this decision:

- `app/main.py` creates one FastAPI application and includes the health, note, and search routers;
- `/health`, `/health/live`, `/health/ready`, and schema-hidden `/privacy` are public;
- protected legacy and `/api/v1` note/search routes run the process-local rate-limit dependency
  before the shared Bearer-auth dependency;
- `API_KEY` is required for protected routes and `API_KEY_PREVIOUS` is an optional rotation key;
- `/docs`, `/redoc`, and `/openapi.json` are disabled;
- the request-observability boundary logs an internally generated request ID, method, matched route,
  status, and latency, but not raw paths, query strings, headers, request bodies, or responses;
- the rich public `/health` response already contains the operational fields listed later in this
  ADR, but no HTTP response currently exposes whether the optional filesystem watcher is enabled or
  running;
- literal search and semantic related-note search already own their validation, containment,
  ranking, availability, and live-path-verification behavior;
- explicit semantic-index synchronization/rebuild commands require the serving process to be
  stopped because there is no cross-process index lock;
- the root Dockerfile is the one production image definition and currently copies the Python `app`
  package into that image;
- current TrueNAS support is the existing source-built Custom App/Compose path, not an upstream
  catalog application.

The dashboard introduces a browser security boundary because the browser will temporarily hold a
Bearer credential while rendering note-derived values. VB-070 therefore resolves the fundamental
routing, credential, rendering, security-header, information-architecture, and platform decisions
before any runtime route or frontend asset is added.

## Decision

VaultBridge will add a platform-neutral, optional, lightweight Web Dashboard as a first-party client
of the existing application interfaces. It will be served by the existing FastAPI application from
the same repository, origin, process, container, and port as the API.

The accepted initial design is:

```text
Browser
   |
   | GET /ui/
   v
bundled static HTML + CSS + vanilla JavaScript modules
   |
   | same-origin fetch using existing contracts
   v
public /health interfaces + protected /api/v1 note/search interfaces
   |
   +-- existing rate limit -> Bearer auth -> domain operation
```

The initial dashboard:

- uses `/ui/` as its canonical entry route and `/ui/assets/` for bundled assets;
- stores an operator-supplied existing API key in `sessionStorage` for the current tab/session and
  mirrors it in JavaScript memory only while needed;
- sends that value only in the existing `Authorization: Bearer <key>` header;
- has Overview, Search, API / Integration, and About areas in one entry document without a
  client-side URL router;
- uses current HTTP/domain behavior rather than implementing health or search logic in JavaScript;
- renders all server- and note-derived values as untrusted text;
- contains no analytics, third-party scripts, remote fonts, CDN assets, or cloud frontend service;
- exposes no note write, index mutation, account management, filesystem management, or TrueNAS
  administration control.

No part of this decision changes current runtime behavior. Routes, files, headers, and assets are the
implementation contract for later tasks, starting with VB-071.

## Routing model

### Canonical routes

VB-071 will implement this public UI route family:

| Request | Planned behavior |
|---|---|
| `GET /ui` | `307 Temporary Redirect` to `/ui/` |
| `HEAD /ui` | same canonical redirect semantics as `GET` |
| `GET /ui/` | return the dashboard entry document |
| `HEAD /ui/` | return the entry-document headers without a body |
| `GET /ui/assets/<known-asset>` | return a bundled static asset with its correct media type |
| unknown `/ui/assets/...` path | return `404`; never fall back to the entry document |
| any unregistered `/ui/...` path | return `404`; no generic SPA catch-all |

`/ui/` is canonical because relative asset and API URL resolution has stable directory semantics.
The redirect is temporary so deployment prefixes and future routing decisions are not permanently
cached by browsers or intermediaries. Only `GET` and `HEAD` are planned for the UI shell and assets.

The planned repository/package layout is:

```text
app/
  ui/
    index.html
    assets/
      app.css
      app.js
```

The public URL layout is stable; later implementation may split the JavaScript into additional
modules under `/ui/assets/` without changing this ADR. Assets are bundled into the existing Python
package/image. VB-071 should use FastAPI/Starlette's existing response/static-file facilities and add
no frontend or runtime dependency.

### URL resolution and reverse proxies

The entry document uses relative asset references such as `assets/app.css` and `assets/app.js`.
JavaScript derives application URLs from the canonical document base, for example with
`new URL("../health", document.baseURI)` and
`new URL("../api/v1/notes/search", document.baseURI)`. It does not hard-code a scheme, hostname,
port, or origin-root path.

This supports both a normal root deployment (`/ui/` -> `/health`) and a reverse proxy that exposes
the whole application under a consistent prefix (`/vaultbridge/ui/` -> `/vaultbridge/health`). The
proxy remains responsible for forwarding that prefix consistently. The application does not add
CORS for the normal dashboard path.

Direct navigation and refresh target `/ui/` and return the same entry document. The four dashboard
areas are in-page states controlled by accessible navigation controls; they do not create routes
such as `/ui/search` and do not require history fallback. Search queries and credentials never enter
the path, query string, or URL fragment.

The explicit `/ui` prefix cannot shadow `/api/v1`, legacy `/notes`, `/health`, `/privacy`, or future
registered non-UI routes. A catch-all route is prohibited because it could turn API mistakes into
HTML responses or conceal endpoint collisions.

### Same-origin rationale

One origin is preferred because it:

- lets the browser call existing interfaces without a CORS policy or preflight-specific auth path;
- keeps the Bearer credential scoped to one operator-selected VaultBridge origin;
- preserves one reverse-proxy and TLS configuration rather than creating a second public service;
- keeps deployment to one application process, port, container, and image;
- allows one strict CSP `connect-src 'self'` policy;
- gives a future TrueNAS Web Portal one stable target, `/ui/` on the normal VaultBridge port;
- avoids duplicated health, authentication, error, and release behavior.

External/CDN-hosted assets are not part of the design.

## Authentication and session model

### Selected model: `sessionStorage`

The initial dashboard will use the operator's existing VaultBridge API key. After successful
validation, the exact value is stored under one namespaced key in browser `sessionStorage` for the
current origin and tab/session. Runtime code reads it into memory only when preparing authenticated
requests. Explicit logout removes it.

This model is accepted for VaultBridge's personal/self-hosted operator threat model because it:

- survives a normal page reload without creating a second authentication system;
- normally ends with the tab/session rather than persisting like `localStorage`;
- never requires the server to emit, store, exchange, or transform the configured secret;
- retains the existing Bearer contract and its rate-limit/auth dependency order;
- adds no cookie, CSRF token, server session database, expiry worker, or dependency;
- keeps the credential out of HTML, static JavaScript configuration, URLs, browser history, and
  normal backend logs.

`sessionStorage` is not an XSS defense. Malicious JavaScript executing in the dashboard origin could
read it, just as it could intercept a memory-only key or an authenticated request. The selected
model is acceptable only together with the strict CSP, bundled first-party scripts, no
`innerHTML`-based data rendering, no Markdown rendering, no third-party code, and safe backend
logging required by this ADR.

Browser profiles, extensions, debugging tools, operating-system compromise, and a malicious
same-origin reverse proxy remain outside the protection this model can provide. Operators must not
use the dashboard from an untrusted browser profile or device.

### Alternatives considered

| Consideration | JavaScript memory only | `sessionStorage` — selected | New server-side browser session |
|---|---|---|---|
| XSS consequence | running script can steal/intercept the key | running script can read/steal the key | running script can act as the user even if an HttpOnly token hides the credential |
| Accidental persistence | lowest; lost on reload | tab/session scoped; longer than memory but shorter than `localStorage` | server/cookie state persists until expiry/revocation policy |
| Reload UX | poor; key must be re-entered | good; key can be revalidated after reload | good |
| Browser history / URL leakage | none if headers are used | none if headers are used | none if correctly implemented |
| Backend logs | existing header exclusion applies | existing header exclusion applies | new login/session endpoints and cookie handling create more audit surface |
| CSRF | Bearer header is not ambient authority | Bearer header is not ambient authority | cookie sessions require explicit SameSite/CSRF/origin design |
| Server complexity/state | none | none | new session creation, validation, expiry, logout, storage, and cleanup |
| Secret lifecycle | ends on reload/navigation | ends on logout or tab/session end; revalidated after reload | requires independent session lifetime and API-key rotation interaction |
| Existing-auth compatibility | direct | direct | translation layer between cookie/session and Bearer auth |
| New dependencies/services | none | none | likely new state/persistence decisions even if implemented in memory |

Memory-only storage was rejected for the initial dashboard because forced re-entry after every
reload is unnecessarily poor operator UX while providing no protection once an XSS payload is
already executing. It may remain an optional stricter mode only under a future explicit decision;
VB-071 does not need to implement two modes.

A server-side browser session was rejected because it creates a second authentication lifecycle,
ambient cookie authority and CSRF work, expiry/revocation semantics, server state, and new endpoints
without a demonstrated need. It would make the dashboard an auth subsystem rather than a thin client
of the existing API.

The following approaches are explicitly prohibited:

- embedding either configured API key in generated HTML;
- including a key in static JavaScript or runtime configuration returned to the browser;
- placing a key in a URL path, query string, or fragment;
- storing a key in `localStorage`, IndexedDB, Cache Storage, or a service worker;
- putting the raw API key in a cookie;
- adding an endpoint that returns `API_KEY` or `API_KEY_PREVIOUS`;
- persisting the key server-side solely for dashboard convenience;
- logging or reflecting the key in UI messages, DOM text, examples, or diagnostics.

## Unlock, authenticated requests, and logout

### Initial load

```text
GET /ui/
   |
   v
public static shell; no credential in response
   |
   +-- no session key -> locked state
   |
   +-- session key -> validate existing key before enabling protected features
```

The shell may fetch and display the already-public `/health` response while locked. Protected Search
and any protected data controls remain disabled until credential validation succeeds. A key found in
`sessionStorage` is not treated as valid merely because it exists.

### Unlock

The operator enters the existing API key into a labelled `<input type="password">` with browser
autocomplete behavior appropriate for a reusable current-password-like secret. The HTML/server never
prepopulates it. Submission is handled in JavaScript and never performs a URL-encoded form
navigation.

VB-071 validates the submitted key with the non-destructive existing request:

```http
GET /api/v1/notes/list?limit=1
Authorization: Bearer <operator-supplied-key>
```

The response body is discarded during validation and is not rendered or persisted. The submitted
key remains only in a local runtime value until a `2xx` response proves it is accepted. Then the key
is written to the namespaced `sessionStorage` entry, the password field is cleared, the UI becomes
unlocked, and protected requests use the same header.

If validation returns `401`, the pending value and any stored value are removed, the password field
is cleared, protected state is cleared, and the UI remains locked with a generic authentication
message. If validation returns `429`, the UI does not classify the key as invalid or store a newly
entered key; it shows a rate-limit message and, when the header is a positive decimal integer, its
`Retry-After` delay in seconds, then requires the operator to retry. A missing or invalid header
produces a generic retry-later message. Network or server failure likewise does not store a newly
submitted key.

On reload, a stored session key is revalidated through the same endpoint. A reload-time `429` keeps
the already stored key but leaves protected features temporarily unavailable until validation is
retried after `Retry-After`. A reload-time network/server failure also keeps the session key but does
not present the session as validated. Any `401` always clears it and returns to the locked state.

### Authenticated fetch behavior

One small fetch wrapper owns protected requests. It:

- constructs URLs relative to `document.baseURI`;
- reads the session key immediately before the request;
- sets exactly `Authorization: Bearer <key>` for protected calls;
- does not add the key to error objects, DOM attributes, analytics, console output, or logs;
- uses existing JSON request/response contracts and rate-limit/authentication ordering;
- clears temporary references after request completion where practical, acknowledging that
  JavaScript cannot guarantee physical memory zeroization.

### Logout

Logout is always visible and keyboard accessible while unlocked. It:

1. removes the namespaced key from `sessionStorage`;
2. clears in-memory credential references where practical;
3. clears protected search inputs, results, snippets, paths, error details, and pending request state;
4. aborts in-flight protected requests where practical and ignores late results;
5. returns Search and other protected controls to the locked state;
6. requires manual key entry before another protected request.

Logout is entirely local because no server session exists.

### HTTP and network outcomes

| Outcome | Dashboard behavior |
|---|---|
| `2xx` | use the response according to the current contract |
| `400` / `422` | show concise field/request feedback; never render arbitrary response HTML |
| `401` | clear credential and protected state, lock the UI, request the key again |
| `429` | keep an already validated session, show rate limiting, display a valid positive-integer `Retry-After` in seconds (otherwise a generic retry-later message), and never start an automatic retry loop |
| semantic `503` | keep the authenticated session; show semantic search as temporarily unavailable and leave literal search usable |
| other `5xx` | show a fixed generic server-error message; do not expose raw exception/detail text |
| network failure/timeout | show an unavailable/retry state without clearing a previously validated key or automatically resubmitting a mutation |

UI code must not expose raw exception internals. It may use allowlisted validation information where
the existing response contract makes that safe, but general `detail` strings are not inserted into
the DOM verbatim.

## Security and threat model

### Protected assets

- the current and optional previous API keys;
- note titles, paths, headings, snippets, content, search queries, and future metadata;
- the operator's ability to invoke protected APIs;
- operational facts that are already public only to the extent of their current contract.

### Trust boundaries

- static dashboard code bundled and reviewed with VaultBridge is trusted application code;
- every browser input and every API value derived from a vault/note is untrusted text;
- browser extensions, developer tools, local malware, injected reverse-proxy content, and a
  compromised VaultBridge origin can defeat browser-side secret handling;
- TrueNAS is a future packaging/portal launcher, not a trusted dashboard business-logic provider;
- third-party frontend code is absent, so no external script receives application data.

### Primary threats and controls

| Threat | Required control |
|---|---|
| XSS steals Bearer key or invokes protected APIs | strict CSP, external self-hosted scripts/styles, `textContent`, no note Markdown rendering, no arbitrary `innerHTML`, no inline handlers, no third-party scripts |
| credential leaks through URL/history/referrer | header-only Bearer use, no key/query/hash credential, `Referrer-Policy: no-referrer` |
| credential persists longer than intended | `sessionStorage` only, no `localStorage`/IndexedDB/cookie/service worker, explicit logout |
| API key is disclosed by server/UI | no secret-return endpoint, no server injection, no DOM reflection, placeholders in documentation/examples |
| current auth/rate limits are bypassed | dashboard calls existing protected `/api/v1` endpoints; no UI-specific proxy or data endpoints |
| note content executes as HTML/Markdown | initial dashboard renders plain text only; no Markdown-to-HTML pipeline |
| malicious API errors reveal internals | fixed status-based messages; no raw arbitrary `detail`/exception rendering |
| UI route shadows APIs | explicit `/ui`, `/ui/`, and `/ui/assets/` only; no catch-all |
| queries/results become a browser knowledge cache | no persistence by default; clear protected state on logout |
| remote frontend service observes data | all required assets and fetches are same-origin; no analytics/CDN/remote font/script |

XSS is especially consequential here because executing script could read `sessionStorage` and use
the Bearer key until logout or tab/session end. Rendering safety and CSP are therefore part of the
authentication design, not optional frontend hardening.

## Rendering and content safety

VB-071 through VB-074 must follow these rules:

- assign note titles, paths, headings, snippets, queries, metadata, and server messages through
  `textContent` or equivalent DOM text-node APIs;
- never pass server/note data to `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, or
  string-built event-handler attributes;
- do not render Markdown as HTML in the initial dashboard and do not execute HTML found in notes;
- use `addEventListener` from bundled JavaScript modules; no inline event handlers;
- validate any future link destination against an explicit allowlist before setting `href`;
- do not dynamically evaluate code (`eval`, `Function`, string timers, or injected modules);
- do not place credentials or protected data in DOM attributes, hidden inputs, page source, or
  `data-*` fields;
- clear protected result nodes on logout and replace them with the locked state.

Static, reviewed interface markup may be present in the bundled HTML. The prohibition applies to
interpreting runtime data as markup.

## Browser security-header contract

VB-071 will apply this policy to the `/ui/` entry response without changing current API response
contracts:

```text
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self';
  img-src 'self' data:;
  connect-src 'self';
  object-src 'none';
  base-uri 'none';
  form-action 'self';
  frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
```

The implementation must serialize CSP as one valid header value. It must not add `unsafe-inline`,
`unsafe-eval`, wildcard sources, external origins, or CDN exceptions. CSS and JavaScript are
separate bundled files so those relaxations are unnecessary. Static assets must use correct media
types and `nosniff`.

`frame-ancestors 'none'` is accepted. Current TrueNAS documentation models a Web Portal as a
protocol/host/port/path URL, and the current upstream `truenas/apps` contribution guide describes
portals as clickable Web UI links, not embedded application frames. See the
[TrueNAS portal configuration reference](https://www.truenas.com/docs/scale/apps/installcustomappscreens/#portal-configuration-settings)
and [`truenas/apps` contribution guide](https://github.com/truenas/apps/blob/master/CONTRIBUTIONS.md#portals).
VB-080/VB-081 must revalidate this current upstream behavior. If a future supported TrueNAS portal
demonstrably requires framing, a separate security review may narrow `frame-ancestors` to the exact
trusted origin; VB-071 must not pre-emptively weaken it.

No security headers are added by VB-070.

## Public and protected information boundary

The dashboard does not redefine endpoint visibility:

| Resource/action | Current endpoint | Current boundary | Planned dashboard use | API change needed? |
|---|---|---|---|---|
| Rich operator health | `GET /health` | Public | Overview, including locked state | No |
| Process liveness | `GET /health/live` | Public | optional connection/liveness diagnosis; not required for primary cards | No |
| Workload readiness | `GET /health/ready` | Public | optional ready/unavailable summary | No |
| Privacy statement | `GET /privacy` | Public, schema-hidden | informational link/text where useful | No |
| Literal search | `POST /api/v1/notes/search` | Protected: rate limit then Bearer auth | Search / Literal | No |
| Semantic related search | `POST /api/v1/notes/related` | Protected: rate limit then Bearer auth | Search / Semantic | No |
| Note listing | `GET /api/v1/notes/list` | Protected: rate limit then Bearer auth | credential validation only in VB-071; response discarded | No |
| Read full note | `GET /api/v1/notes/read` | Protected: rate limit then Bearer auth | out of initial dashboard scope | No |
| Duplicate candidates | `POST /api/v1/notes/duplicates` | Protected: rate limit then Bearer auth | out of initial dashboard scope | No |
| Create note | `POST /api/v1/notes` | Protected: rate limit then Bearer auth | out of scope; no control | No |
| Append note | `POST /api/v1/notes/append` | Protected: rate limit then Bearer auth | out of scope; no control | No |

The public shell does not make protected resources public. It can show only facts already returned by
public routes until authentication succeeds.

## Information architecture

The first dashboard is one responsive document with four top-level areas.

### Overview

```text
+------------------------------------------------------+
| VaultBridge                     [Locked] [Unlock]     |
| Overview | Search | API / Integration | About        |
+------------------------------------------------------+
| Overall status: Ready / Indexing / Error / Unavailable|
|                                                      |
| Vault                    Semantic Index              |
| available: yes/no        state: ready/indexing/...   |
| eligible notes: 842      search available: yes/no    |
|                          indexed notes: 734          |
|                          chunks: 4610                |
|                          last full sync: ...         |
|                          full sync required: yes/no  |
|                          indexer running: yes/no     |
|                                                      |
| Filesystem watcher: not reported by current HTTP API |
+------------------------------------------------------+
```

Overview uses `/health` as its primary source. It must distinguish `ok`, vault availability,
semantic lifecycle state, and semantic search availability rather than collapsing them into one
boolean. It must represent `null` last-sync values and unknown/unavailable states honestly.

Facts available today from `/health`:

- `ok`;
- `vault_exists` (the existing field name; it is not the stronger readiness predicate);
- `semantic_index_ready`;
- `semantic_index_state`;
- `semantic_search_available`;
- `semantic_indexer_running`;
- `full_sync_required`;
- `indexed_notes`;
- `semantic_chunks`;
- `vault_notes`;
- `last_successful_sync`.

Facts available through separate current public probes are liveness (`/health/live`) and workload
readiness (`/health/ready`). No current HTTP contract exposes watcher enabled/running state, queue
depth/paths, current note, percentage, batch, ETA, model download progress, database size, NAS
metrics, or TrueNAS state. The first dashboard must label watcher state as unavailable or omit it.
Adding any such fact requires a separately reviewed API task; VB-070 and VB-071 do not invent fields.

### Search

```text
+------------------------------------------------------+
| Search                                               |
| [ Literal ] [ Semantic ]                             |
| Query: [................................]            |
| Folder: [optional vault-relative folder] Limit: [ ]  |
| Semantic only: minimum score [0.28]                  |
| [Search]                                             |
|------------------------------------------------------|
| title                                                |
| path                                                 |
| snippet as plain text                                |
| semantic mode: score / semantic / lexical / heading  |
+------------------------------------------------------+
```

Literal mode sends the current JSON contract to `POST /api/v1/notes/search` and displays only its
current `path`, `title`, and `snippet` result fields. Semantic mode sends the current JSON contract
to `POST /api/v1/notes/related` and displays only current `path`, `title`, `score`,
`semantic_score`, `lexical_score`, `snippet`, and `heading` fields.

The backend remains authoritative for folder validation, limits, semantic thresholds, ranking,
overfetch, relative-floor filtering, per-note aggregation, containment, canonical paths, and live
Markdown verification. JavaScript does not rerank, rescore, reconstruct hidden candidates, search
the vault, or build another index. It may format existing numeric fields for display without
changing ordering or meaning.

Queries default to memory/DOM state only, are not put in URL parameters/fragments or browser
storage, and are cleared on logout. Full-note reading, note editing, duplicate handling, and opening
arbitrary filesystem paths are not required.

### API / Integration

```text
+------------------------------------------------------+
| API / Integration                                    |
| Base URL: derived from this document's application URL|
| Preferred namespace: /api/v1                         |
| Authentication: Authorization: Bearer YOUR_API_KEY   |
|                                                      |
| curl ... -H "Authorization: Bearer YOUR_API_KEY"     |
| [Repository] [API docs in repository] [TrueNAS guide]|
+------------------------------------------------------+
```

This area is static/informational. The base URL is derived from the current document/application
base; examples always use `YOUR_API_KEY`. It must not display either configured key, interpolate the
operator-supplied key, enable runtime FastAPI documentation/OpenAPI endpoints, or imply that the
future TrueNAS catalog app exists.

Safe documentation examples use a placeholder origin and credential, never the operator's submitted
value, for example:

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  "https://vaultbridge.example/api/v1/notes/list?limit=1"

curl -X POST \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"query":"example","limit":5}' \
  "https://vaultbridge.example/api/v1/notes/search"
```

The rendered dashboard substitutes its derived application base URL in explanatory text only; it
does not substitute the stored key into examples or copyable DOM content.

### About

```text
+------------------------------------------------------+
| About VaultBridge                                    |
| Small self-hosted REST + semantic search bridge      |
| Markdown is authoritative; semantic data is derived  |
| [GitHub repository] [License] [Roadmap] [Release docs]|
+------------------------------------------------------+
```

About uses checked-in project identity and documentation links. It does not invent a runtime version
endpoint or query a TrueNAS middleware API.

## Backend and API reuse

The UI is a client, not a domain layer:

- Overview reads existing public health interfaces without recreating state calculations;
- Literal Search calls the stable v1 literal-search route;
- Semantic Search calls the stable v1 related-note route and preserves result order/fields;
- unlock validation uses the existing protected list route and discards its minimal response;
- the existing protected dependency order, request validation, rate limiting, Bearer comparison,
  vault containment, safe errors, semantic availability, and request observability remain
  authoritative;
- no UI-specific data proxy, auth bypass, alternate search endpoint, health endpoint, OpenAPI
  exposure, or domain service is introduced by the design.

The legacy note/search aliases remain for compatibility, but the new dashboard uses `/api/v1`.

## Operational and index safety

The dashboard may display current public index facts. It must not provide controls named or
equivalent to:

```text
Rebuild Index
Force Sync
Delete Index
Repair Index
```

`python -m app.cli index`, `reindex`, `index check`, and `index rebuild` retain their documented
stopped-service ownership. No command becomes an HTTP request, no UI route calls
`SemanticSearchService.sync()`/`rebuild()`, and no browser action mutates SQLite.

If live maintenance becomes a demonstrated need, a separate backlog/design task must cover serving-
process ownership, cross-process exclusion, authorization, rate limiting, cancellation, shutdown,
failure recovery, progress, and audit/privacy behavior before an endpoint or control is added.

## Lightweight frontend technology

The accepted first implementation uses:

```text
one static HTML entry document
one static CSS file (or a similarly small bundled split)
small vanilla JavaScript ES modules
FastAPI/Starlette file responses from the existing application
```

This is sufficient for four in-page areas, accessible navigation, status cards, unlock/logout state,
two fetch-based search forms, result lists, and responsive layout. It keeps the repository and image
small, works with strict self-only CSP, and requires no build step.

The initial dashboard has no mandatory React, Vue, Svelte, npm, Node, bundler, frontend package
manager, second web server, second process, second container, or external asset host.

Reconsideration requires a separate documented decision and measured evidence such as substantial
client-side routing, complex editor state, a large reusable component system, or demonstrated
maintainability/testability problems that small modules cannot address. A desire for fashionable
tooling is not sufficient.

## Accessibility and UX contract

VB-071 through VB-074 must provide:

- semantic HTML landmarks, headings, lists, forms, buttons, and status regions;
- complete keyboard navigation with logical focus order and no keyboard trap;
- clearly visible focus indicators;
- programmatic labels, instructions, and error association for every input;
- an API-key control using password-input behavior and an accessible show/hide control only if later
  implemented without DOM/log reflection;
- status conveyed by text/icon plus color, never color alone;
- sufficient text, component, and focus contrast;
- reflow/responsive behavior at normal mobile widths and browser zoom;
- explicit loading, locked, empty, validation, rate-limited, unavailable, and unexpected-error states;
- accessible unlock and logout controls;
- reduced-motion support, no autoplay animation, and no interaction available only on hover;
- focus placement/announcement when authentication state or search results materially change.

VB-070 does not select a visual style or polished component system.

## Privacy contract

The initial dashboard has no analytics, tracking, telemetry, remote fonts, remote JavaScript, CDN
assets, service worker, or cloud frontend dependency. Application fetches go only to the current
VaultBridge origin.

By default the UI does not persist:

- search queries;
- search results, snippets, headings, paths, or note contents;
- error response bodies;
- operational snapshots;
- the API key beyond the selected `sessionStorage` tab/session model.

`localStorage` and IndexedDB are not used as a knowledge cache. No browser request body,
Authorization header, query, note-derived value, or credential is intentionally logged by frontend
code. Existing safe backend request/application logging remains authoritative and unchanged.

## Deployment and platform consequences

The dashboard ships in the same normal VaultBridge production image and listens on the same
application port. API-only and CLI users do not need to visit or use it; its presence must not alter
API/CLI behavior. There is no second service hostname, port, container, database, or deployment
procedure.

The planned future relationship is:

```text
TrueNAS Apps
    |
    | Web Portal clickable URL
    v
http(s)://<VaultBridge-host>:<port>/ui/
    |
    v
the same platform-neutral VaultBridge image and API
```

Future TrueNAS packaging may configure the published image, mounts, environment, port, health
checks, and portal path. The dashboard contains no TrueNAS-specific navigation, dataset management,
NAS metrics, middleware API calls, user model, or domain logic. Docker/Compose and other platforms
use the same UI. VB-070 adds no TrueNAS catalog files.

## Consequences

### Positive

- VB-071 has one routing, auth/session, CSP, rendering, and error-handling contract;
- the dashboard reuses current production behavior and stays operationally small;
- reload-friendly authentication requires no new backend auth state;
- same-origin deployment fits the current image and future TrueNAS Web Portal;
- strong content/rendering rules directly protect the in-browser Bearer credential;
- API and CLI remain independent, stable interfaces.

### Costs and residual risks

- a successful same-origin XSS can steal/use the session key; CSP and safe rendering reduce but do
  not eliminate this risk;
- `sessionStorage` behavior depends on browser tab/session semantics and is not secure against a
  compromised device/profile/extension;
- every new dynamic value and future frontend dependency requires security review;
- a strict CSP and no build system favor a deliberately small interface over a rich client app;
- public `/health` information remains publicly visible because that is the existing contract, not a
  new dashboard exposure;
- watcher state and richer progress cannot be displayed without a separate API decision;
- `frame-ancestors 'none'` requires future TrueNAS packaging to keep using link-based portals unless
  a narrowly scoped security decision proves framing necessary.

## Explicit non-goals

- implementing any route, HTML, CSS, JavaScript, image, test, or runtime header in VB-070;
- a general Obsidian replacement, Markdown renderer/editor, WYSIWYG editor, graph view, file manager,
  or full-note reader;
- create/append/update/delete/merge/backlink actions;
- index sync/rebuild/repair/delete controls or live maintenance endpoints;
- usernames/passwords, OAuth, a user database, multi-user administration, or a new auth service;
- returning, embedding, reflecting, logging, or server-persisting configured API keys;
- CORS changes, a second UI origin, service, container, process, server, or port;
- React/Vue/Svelte, npm/Node, a bundler, remote scripts/fonts/assets, analytics, or telemetry;
- enabling `/docs`, `/redoc`, or `/openapi.json`;
- TrueNAS-specific business logic, NAS/dataset administration, metrics, middleware calls, or catalog
  implementation;
- changing current API paths, operation IDs, schemas, authentication, rate limiting, logging,
  semantic behavior, CLI behavior, Docker/Compose behavior, or release/version identity.

## Implementation contract for VB-071 through VB-074

### VB-071 — shell and authenticated session

VB-071 must implement exactly these architectural decisions:

- public `GET`/`HEAD /ui` -> `307` canonical redirect to `/ui/`;
- public `GET`/`HEAD /ui/` entry document and explicit `/ui/assets/` static asset path;
- planned `app/ui/index.html` and `app/ui/assets/` package layout, included in the existing image;
- relative assets and `document.baseURI`-relative same-origin API URLs;
- no SPA catch-all, CORS change, API contract change, or second service;
- static HTML/CSS and small vanilla JavaScript modules without inline code or new dependencies;
- `sessionStorage` only after successful `GET /api/v1/notes/list?limit=1` validation;
- one protected fetch wrapper using the existing Bearer header;
- locked initial state, reload revalidation, explicit logout, and `401` credential clearing;
- status-specific `429`/`Retry-After`, semantic `503`, other `5xx`, and network handling from this ADR;
- the exact CSP, `nosniff`, and no-referrer response-header contract;
- text-only dynamic rendering and the prohibited DOM/code APIs from this ADR;
- accessible Overview/Search/API/About navigation shell and unlock/logout controls;
- no note/index mutation and no UI-specific data/auth endpoint.

VB-071 may use public `/health` to establish a real locked-state shell, but completing all Overview
cards and both search modes remains VB-072/VB-073 work. Its focused tests must prove canonical
routing, no route shadowing, correct headers/media types, auth success/failure/logout, secret
non-disclosure, safe text rendering, and stable existing API operation IDs.

### VB-072 — overview and health visibility

VB-072 must render the existing `/health` fields and optional current probes exactly as documented
here, distinguish lifecycle from search availability, handle null/unknown/unavailable states, and
omit or label watcher state unavailable. It adds no health calculation, field, auth change, or index
mutation without a separate approved task.

### VB-073 — search interface

VB-073 must map Literal and Semantic modes directly to the two protected `/api/v1` endpoints and
current schemas/fields documented here. It must preserve server ordering and constraints, keep
queries out of URLs/storage/logs, render results as text, clear them on logout, and add no second
search/ranking/index implementation or write behavior.

### VB-074 — usability, accessibility, and release hardening

VB-074 must verify the complete accessibility/privacy contract, responsive and reduced-motion
behavior, CSP/rendering safety, status/error states, browser smoke coverage, documentation and
sanitized screenshots, and the normal production-image path. It must verify API/CLI compatibility
and release readiness without assigning a release version or expanding dashboard scope.

Fundamental routing, browser credential storage, unlock/logout, protected fetch, security-header,
rendering, navigation, API-reuse, and no-mutation decisions are closed by this ADR. Reopening one
requires a new documented architecture/security decision rather than an implicit VB-071 change.
