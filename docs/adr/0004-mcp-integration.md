# ADR 0004: MCP integration architecture

- **Status:** Accepted
- **Date:** 2026-09-07
- **Decision owner:** VB-090
- **Implementation task:** VB-091

## Context

VaultBridge currently exposes one set of vault and retrieval capabilities through parallel client
surfaces: stable REST `/api/v1`, legacy REST compatibility aliases, the bundled Web Dashboard, the
local CLI, and a ChatGPT Action example. Model Context Protocol (MCP) should become another
first-class integration surface without replacing REST or creating a second implementation of vault,
search, indexing, or write behavior.

The existing ownership boundaries constrain the design:

- Markdown is authoritative and remains usable without VaultBridge;
- semantic data is local, derived, disposable, and rebuildable;
- `VaultService` owns contained vault-relative Markdown operations;
- `SemanticSearchService` owns semantic search, ranking, index compatibility, and lifecycle;
- `DuplicateCandidateService` composes live-title and verified semantic duplicate evidence;
- `BackgroundSemanticIndexer` is the one in-process owner that queues post-write semantic refresh;
- HTTP routers, the Web Dashboard, and the CLI are adapters rather than domain owners;
- the current API application and offline CLI deliberately do not provide cross-process semantic-index
  write coordination;
- the root Dockerfile remains the one production image definition;
- existing Bearer authentication, safe logging, request IDs, and process-local rate limiting must not
  be weakened or overstated.

MCP evolves independently of VaultBridge. The transport, authorization, tool, Resource, error, and SDK
decisions below were checked against the current authoritative sources on 2026-09-07. The current MCP
specification revision was `2026-07-28`.

## Decision

VaultBridge will implement MCP as a thin protocol adapter over the existing application and domain
services. It will not call VaultBridge through HTTP loopback, move protocol concepts into domain
services, or duplicate containment, ranking, persistence, or write behavior.

VB-091 will be a bounded **read-only stdio-first MVP**:

- an explicit `python -m app.mcp_server` entry point;
- the standard MCP stdio transport only;
- five stable tools: `list_notes`, `read_note`, `search_notes`, `related_notes`, and
  `duplicate_candidates`;
- one read-only note Resource template using a VaultBridge-specific URI;
- no Prompts;
- no `create_note`, `append_note`, delete, overwrite, section update, backlink mutation, or index
  mutation;
- no FastAPI route, new listening socket, always-on process, background indexer, or filesystem watcher.

The selected future network transport is **Streamable HTTP**, mounted at `/mcp` in the existing
FastAPI application, on the existing application port, and disabled by default. It is not part of
VB-091. Legacy HTTP+SSE and custom transports are not selected.

This split is intentional. A spawned stdio process is useful to local desktop MCP clients without
requiring the API server. Keeping it read-only avoids a second process writing the shared semantic
index. A future in-process Streamable HTTP adapter can safely share the running application's
`VaultService`, `SemanticSearchService`, `DuplicateCandidateService`, and
`BackgroundSemanticIndexer`, including post-write refresh ownership.

No existing REST, dashboard, CLI, ChatGPT Action, Docker, or TrueNAS client needs to change.

## Architecture

```text
                          clients
          +----------+-------+-------+-----------+
          |          |               |           |
          v          v               v           v
       REST API   Web Dashboard     CLI      MCP clients
          |          |               |       /         \
          |          |               |   stdio MVP   future HTTP
          |          |               |       |        /mcp, opt-in
          |          |               |       v           |
          |          |               |   MCP adapter <----+
          |          |               |       |
          +----------+---------------+-------+
                                             |
                         +-------------------+-------------------+
                         |                                       |
                         v                                       v
                  VaultService                    SemanticSearchService
                         |                                       |
                         v                                       v
              Markdown vault (truth)                SQLite / FastEmbed

Future authenticated writes also use the running application's
BackgroundSemanticIndexer after the Markdown commit.
```

The MCP adapter owns only:

- MCP server and capability registration;
- transport startup/shutdown;
- MCP input/output schema conversion;
- safe Resource URI encoding/decoding;
- transport authentication and Origin checks where applicable;
- protocol error/result mapping;
- protocol-specific rate-limit invocation and safe correlation logging.

It does not own filesystem policy, Markdown I/O, search ranking, semantic lifecycle, embeddings,
duplicate logic, or index persistence.

## Transport decision

### Current standardized choices

MCP revision `2026-07-28` defines stdio and Streamable HTTP as the standard transport bindings.
Custom transports are possible, but reduce interoperability and require their own framing and
lifecycle contract.

The former HTTP+SSE transport from revision `2024-11-05` has been deprecated since `2025-03-26`.
New implementations should not adopt it. Streamable HTTP may use an SSE response for one POST when
the server needs to stream request-related notifications, but that is not the deprecated standalone
HTTP+SSE transport.

Revision `2026-07-28` also changed Streamable HTTP relative to revisions through `2025-11-25`: it
removed the standalone GET stream and protocol-level sessions. Each client JSON-RPC message is sent
as its own POST to one MCP endpoint; a request receives either one JSON response or a request-scoped
SSE response. VaultBridge implementation work must use the official SDK's current protocol behavior
rather than copying older session/GET examples.

### stdio: selected for VB-091

Under stdio, the MCP client launches VaultBridge as a subprocess. JSON-RPC messages use stdin/stdout,
one message per line. stdout is reserved exclusively for valid MCP messages; VaultBridge logs remain
UTF-8 JSON on stderr.

The explicit entry point is preferable to always-on MCP settings:

```text
python -m app.mcp_server
```

The entry point loads the existing `Settings`, constructs only the services needed by the read-only
surface, and starts no FastAPI application, `BackgroundSemanticIndexer`, or filesystem watcher. It
calls `SemanticSearchService.use_persisted_index_for_read_only_search()` before semantic operations
so a compatible ready index can be queried without lifecycle writes. It never synchronizes or
rebuilds the index.

If the persisted index is missing, incompatible, rebuilding, or otherwise unavailable, literal
list/read/search tools continue to work and semantic tools return a safe retryable execution error.

### Streamable HTTP: selected future network transport

A later task may add an explicitly enabled `/mcp` endpoint to the existing FastAPI application. It
must:

- use the existing application process and port rather than a second service or port;
- share the already wired service and indexer instances;
- support POST according to the then-current Streamable HTTP specification and official SDK;
- require the current `MCP-Protocol-Version` and `Mcp-Method` headers plus `Mcp-Name` where
  applicable, validate their values against the JSON-RPC body, and reject mismatches before dispatch;
- support both `application/json` and request-scoped `text/event-stream` responses as required by
  negotiated protocol behavior, without adding a standalone GET event stream;
- validate `Origin` on every request and reject an invalid present Origin with HTTP `403`;
- retain localhost-only deployment defaults and require the existing reverse-proxy/VPN/TLS guidance
  for remote access;
- require authentication on every request;
- stay out of the REST OpenAPI document;
- be absent when not explicitly enabled.

Streamable HTTP is deferred from VB-091 because it adds a network trust boundary, Origin policy,
MCP-specific HTTP header validation, authentication interoperability decisions, and mounted-ASGI
lifecycle tests that are unnecessary for a useful local MVP.

### Rejected transports

- **Legacy HTTP+SSE:** deprecated and not appropriate for a new server.
- **WebSocket or raw TCP:** custom transports add client and security work without a measured need.
- **Both standard transports in VB-091:** useful eventually, but not the smallest safe first
  implementation.

## Process architecture

Three process models were evaluated:

| Model | Decision | Lifecycle consequences |
|---|---|---|
| MCP inside the existing application | Selected for future Streamable HTTP | Shares services, authentication boundary, request observability, indexer, image, process, and port. |
| Separate MCP entry point using the same package/image | Selected for stdio | The client owns subprocess lifecycle. The MVP is read-only and starts no index writer. |
| Separate permanent service/container | Rejected | Adds another deployment, socket/port, health lifecycle, secret boundary, and potential semantic-index writer without a measured need. |

The two selected entry shapes share one MCP registration/adapter implementation. They are transport
composition roots, not separate domain implementations or product forks.

The normal API container cannot expose its stdin as a practical network integration. Local clients
may spawn the Python entry point directly or run the same normal image interactively with explicit
vault/semantic-data mounts. The main API container is not required for stdio. Operators must not run
offline index maintenance or a second index writer against the same semantic-data directory.

## Authentication decision

### Local stdio

VB-091 does not require `API_KEY` for stdio. The MCP client deliberately spawning a local process,
the operating-system account, inherited environment, and filesystem permissions form the trust
boundary. Requiring the HTTP API key would not protect the subprocess from the user that already has
permission to launch it and read the configured vault.

The client should pass only required configuration such as `VAULT_PATH` and `SEMANTIC_DATA_PATH`.
Credentials, if any are needed by future integrations, must come from the process environment rather
than MCP arguments. The adapter never returns or logs configured secrets.

This matches the current MCP authorization guidance: the HTTP authorization framework is optional,
stdio implementations should not run that HTTP flow, and credentials for stdio should be obtained
from the environment when needed.

### Future network transport

Every Streamable HTTP request must require `Authorization: Bearer <token>`. The first VaultBridge
network implementation will reuse the current `API_KEY` plus optional `API_KEY_PREVIOUS` rotation
window, constant-time comparison, generic `401` response, and secret-safe handling. It will not add
accounts, cookies, sessions, a user database, or an authorization service.

The MCP HTTP authorization specification recommends its OAuth 2.1 discovery and audience-bound
access-token profile when HTTP authorization is supported. VaultBridge's pre-shared operator API key
is a deliberate local-first deployment profile, not a claim of full MCP OAuth discovery compliance.
Some generic MCP clients that require automatic OAuth discovery may therefore need manual Bearer
configuration or may not interoperate with this first network profile. OAuth support requires a
separate architecture/security decision and is not silently introduced here.

Transport authentication failures are HTTP `401` boundaries, not tool execution results. Tokens
never appear in URLs, JSON-RPC bodies, tool arguments, Resources, logs, or error messages.

## Tool surface

Tool names are lower snake case and are treated as stable protocol identifiers. Each success returns
structured content matching the described fields and a JSON text content block for clients that need
the compatibility representation. Note-bearing results may also include a Resource link using the
canonical URI scheme defined below. No raw embeddings are returned.

### Initial read-only tools

| Tool | Purpose and arguments | Response fields | Service operation | Expected execution errors | Initial |
|---|---|---|---|---|---|
| `list_notes` | List recent Markdown notes. `folder?: string = ""`, `limit?: integer = 50` (`1..200`). | `folder`, `notes[] {path,title,modified,resource_uri}` | `VaultService.list_notes` | `invalid_path`, `validation_error`, `rate_limited`, sanitized internal failure | Yes |
| `read_note` | Read one known Markdown note. `path: string` (`1..700`). | `path`, `content`, `mime_type`, `resource_uri` | `VaultService.read_note` | `invalid_path`, `note_not_found`, `validation_error`, `rate_limited`, sanitized internal failure | Yes |
| `search_notes` | Literal title/content search. `query: string` (`1..300`), `folder?: string = ""`, `limit?: integer = 10` (`1..50`). | `query`, `results[] {path,title,snippet,resource_uri}` | `VaultService.search_notes` | `invalid_path`, `validation_error`, `rate_limited`, sanitized internal failure | Yes |
| `related_notes` | Find semantically related live notes. `text: string` (`2..4000`), `folder?: string = ""`, `limit?: integer = 5` (`1..20`), `min_score?: number = 0.28` (`-1..1`). | `text`, `results[] {path,title,score,semantic_score,lexical_score,snippet,heading,resource_uri}` | `SemanticSearchService.search`, with folder canonicalization and result verification through `VaultService` | `invalid_path`, `semantic_index_unavailable`, `semantic_index_rebuilding`, `validation_error`, `rate_limited`, sanitized internal failure | Yes |
| `duplicate_candidates` | Advisory duplicate check before a future create. `title: string` (`1..180`), `text?: string = ""` (`0..4000`), `folder?: string = ""`, `limit?: integer = 5` (`1..20`), `min_score?: number = 0.28` (`-1..1`). | `title`, `results[] {path,title,match_type,score,semantic_score,lexical_score,snippet,heading,resource_uri}` | `DuplicateCandidateService.find_candidates` | `invalid_path`, `semantic_index_unavailable`, `semantic_index_rebuilding`, `validation_error`, `rate_limited`, sanitized internal failure | Yes |

`related_notes` is the one semantic-retrieval tool. A second `semantic_search` name is not included
because it would expose the same operation with competing names and schemas. The tool description
will state clearly that `related_notes` performs local hybrid semantic retrieval.

All MCP `path` values use canonical vault-relative POSIX separators (`/`) regardless of the host
operating system. The adapter normalizes service-returned separators before producing tool results or
Resource links; it never exposes a host-native absolute path. Input remains subject to
`VaultService` normalization and containment, and response ordering and score values are unchanged.

### Deferred write tools

| Tool | Purpose and arguments | Response fields | Service operation | Expected execution errors | Initial |
|---|---|---|---|---|---|
| `create_note` | Create one non-overwriting Markdown note. `title: string`, `folder?: string = "Inbox"`, `content?: string = ""`, `tags?: string[] = []`. Limits match the REST contract. | `success`, `path`, `status`, `resource_uri` | `VaultService.create_note`, then running application's `BackgroundSemanticIndexer.enqueue` only after a committed create | `invalid_path`, `note_conflict`, `validation_error`, `rate_limited`, sanitized internal failure | No |
| `append_note` | Idempotently append Markdown. `path: string`, `content: string`, `dedupe_key?: string`. Limits match the REST contract. | `success`, `path`, `status`, `resource_uri` | `VaultService.append_note`, then running application's `BackgroundSemanticIndexer.enqueue` only after a committed append | `invalid_path`, `note_not_found`, `validation_error`, `rate_limited`, sanitized internal failure | No |

Writes are deferred beyond VB-091. Their first safe transport is the future in-process network
adapter because it can reuse the application-owned indexer. A standalone stdio writer would need an
exclusive-process contract or explicit cross-process coordination before it could promise the same
post-write semantic refresh behavior. HTTP loopback from stdio is rejected as the default because it
would require the API server and API key, add a network failure path, and turn the adapter into a
client of another adapter.

No MCP tool will expose arbitrary overwrite, delete, section update, upsert, rename, backlink
mutation, filesystem enumeration beyond Markdown notes, index sync/rebuild, or raw SQLite access.
VB-032 and VB-033 remain deferred and cannot be bypassed through MCP.

## Resources

VB-091 includes a read-only note Resource because Resources are application-driven context objects,
whereas tools are model-controlled operations. This gives clients a stable way to attach a known
note returned by list/search tools without exposing host filesystem paths. `read_note` remains useful
for clients and model flows that do not support Resource selection.

The canonical URI form is:

```text
vaultbridge://note/{percent-encoded-vault-relative-path}
```

Example:

```text
vaultbridge://note/Projects%2FLaunch%20plan.md
```

The complete canonical POSIX-style vault-relative path is UTF-8 percent-encoded as one URI path
value, including `/` as `%2F`. The URI has exactly:

- scheme `vaultbridge`;
- authority `note`;
- one encoded path value;
- no user information, port, query, or fragment.

The server exposes a `vaultbridge://note/{path}` Resource template with MIME type
`text/markdown`. `resources/read` decodes exactly once, rejects malformed UTF-8 or non-canonical
encodings, and passes the resulting path to `VaultService.read_note`. `VaultService` remains the
authority for Markdown-only enforcement, traversal rejection, canonical vault-relative identity,
size limits, and symlink containment. Absolute host paths are never accepted or returned.

The MVP does not advertise subscriptions, list-changed notifications, or a duplicate unbounded
resource catalogue. `resources/list` may be empty; the template and Resource links returned by the
five tools provide discovery. A missing note Resource is a JSON-RPC invalid-params error as required
by the current MCP Resource specification, never an ambiguous empty contents array.

## Prompts

VaultBridge will not bundle MCP Prompts in VB-091. No concrete product need justifies prescribing
how clients summarize, search, link, or reason over the vault. Client applications remain responsible
for prompting; VaultBridge exposes knowledge operations and content.

## Writes and safety

When write tools are separately approved, they must preserve all current rules:

- only `VaultService.create_note` and `VaultService.append_note` perform Markdown writes;
- paths remain vault-relative and contained after symlink resolution;
- only `.md` notes are accepted;
- configured size limits and create-conflict behavior remain unchanged;
- create remains non-overwriting and append retains `dedupe_key` idempotency;
- the semantic path is queued only after a committed Markdown mutation;
- queue failure cannot roll back or repeat a committed Markdown write; startup full synchronization
  remains recovery;
- no delete, arbitrary overwrite, section mutation, backlink insertion, or invented wikilink target is
  introduced;
- MCP clients should provide human confirmation for write invocations, but server safety cannot rely
  on client UI behavior.

No write tool is registered in VB-091, so there is no hidden write-enable setting in that task.

## Error mapping

Unknown tool names, malformed MCP envelopes, and inputs that fail the published tool schema are
protocol errors handled by the official SDK. Expected failures reached during a valid tool call are
tool execution results with `isError: true`, stable machine-readable error content, and a short safe
message. Internal failures use sanitized protocol/internal errors without exception text.

| Existing condition | MCP mapping |
|---|---|
| Invalid/traversing/non-Markdown path or invalid folder | Tool execution error `invalid_path`; Resource URI failures use JSON-RPC `-32602` (`Invalid Params`). |
| Note not found | Tool execution error `note_not_found`; missing Resource uses JSON-RPC `-32602` and no empty contents result. |
| Size or other domain validation failure | Tool execution error `validation_error`; no submitted content is echoed. |
| No compatible ready persisted semantic index | Retryable tool execution error `semantic_index_unavailable`. |
| Persisted index is rebuilding/indexing in another process | Retryable tool execution error `semantic_index_rebuilding`; stdio does not wait, sync, or write. |
| Duplicate create conflict (future) | Tool execution error `note_conflict`; existing note content is not returned. |
| Rate limit exceeded | stdio: tool execution error `rate_limited` with bounded retry guidance; HTTP: `429` plus `Retry-After` at the transport boundary. |
| Missing/invalid HTTP Bearer credential (future) | HTTP `401` before MCP tool dispatch; generic message only. |
| Invalid HTTP Origin (future) | HTTP `403` before MCP tool dispatch. |
| Unexpected internal error | Sanitized internal error with stable wording and `error_type` only in safe server logs. |

Error data must not contain API keys, Authorization headers, note/query/content arguments, absolute
paths, exception text, raw embedding vectors, SQLite details, model-cache paths, or private host data.

## Rate limiting and observability

The current protected-route dependency and its ASGI peer identity do **not** automatically protect
stdio. VB-091 must not claim otherwise.

For stdio, the adapter directly reuses the standard-library `FixedWindowRateLimiter` primitive with
one constant local-process/client identity. The existing rate settings define the process-wide MCP
operation budget. This is not peer identification, distributed quota, authentication, or protection
against the operating-system user that launched the process. Restarts clear it. Every tool call and
Resource read consumes one operation; published schema limits still bound query/content sizes and
result counts.

Future Streamable HTTP explicitly applies Origin validation, the shared application
`FixedWindowRateLimiter`, and Bearer authentication before MCP dispatch. It uses the direct ASGI peer
and the existing dependency order principle; forwarded client-address headers remain untrusted.
Mounting an SDK ASGI application does not by itself prove those VaultBridge controls are active, so
VB-091 must not claim network protection and a future HTTP task must test it explicitly.

MCP-owned logs use `app/core/logging.py`, stderr, static messages, and allowlisted fields only:

- internally generated correlation ID;
- transport (`stdio` or `streamable_http`);
- stable tool/protocol operation name;
- success/failure class;
- non-negative duration;
- safe error type where needed.

Tool arguments, Resource URIs, note paths, queries, content, snippets, results, credentials, headers,
client-provided JSON-RPC IDs, and exception messages are not logged. stdout remains protocol-only for
stdio. Future HTTP requests also retain the outer ASGI-generated `X-Request-ID`; the adapter may use
that existing context but does not trust an incoming ID or invent a second HTTP correlation header.

## Configuration

VB-091 will add no MCP environment setting. The planned explicit module selects stdio, while
existing settings continue to own vault, semantic data, model, size, and rate-limit configuration.

For a future HTTP task, one disabled-by-default boolean is sufficient conceptually:

```text
MCP_HTTP_ENABLED=false
```

When true, the existing FastAPI host and port expose the fixed `/mcp` path. `MCP_TRANSPORT`,
`MCP_HOST`, `MCP_PORT`, and a configurable MCP path are not justified. The stdio command selects its
transport explicitly; network MCP must not open a second port.

No setting is added by this ADR.

## SDK and dependency decision

VB-091 should use the official Python package `mcp`, stable major version 2. The checked official
repository states that v2 is the current stable line, supports MCP revision `2026-07-28` and every
earlier revision, implements stdio and Streamable HTTP, requires Python 3.10+, and is MIT licensed.
VaultBridge already requires Python 3.12, so the SDK does not lower or raise the project's Python
floor.

The implementation should use a bounded requirement compatible with the tested stable major, for
example `mcp>=2,<3`, following the repository's existing dependency-range policy. The exact lower
minor/patch must be selected and tested when VB-091 begins because the SDK is actively maintained and
the protocol evolves.

The SDK will be a normal required runtime dependency in VB-091, not a separate optional image extra.
That modestly increases the base installation/image dependency set, but it keeps one supported image
and avoids maintaining handwritten JSON-RPC framing, capability negotiation, schemas, transports,
and compatibility behavior. It adds no external service or runtime network dependency by itself.

VB-091 must review the selected release's transitive dependencies, security advisories, license
metadata, and image-size delta, and must test protocol behavior. Future SDK major or MCP revision
changes require compatibility review rather than an unbounded upgrade.

## Packaging and deployment

### Normal Docker image

MCP remains in the normal VaultBridge Python package and production image. VB-091 does not need a
second Dockerfile, image, service, or exposed port. The existing container command remains the
FastAPI application. stdio is an explicit alternate invocation of the same image/package.

### Generic Docker

The normal API/dashboard deployment is unchanged. A local MCP host may spawn the Python entry point
directly, or explicitly run the same image interactively with stdin plus the existing vault and
semantic-data mounts. Containerized stdio is an operator/client launch choice, not an always-on
Compose service. The MVP never writes the semantic index.

Future Streamable HTTP uses the existing API port and `/mcp`, remains disabled by default, and follows
the same host-loopback, reverse-proxy/VPN, TLS, Bearer-key, and mount guidance as the API.

### TrueNAS

The current TrueNAS deployment and staged Community App definition remain unchanged. There is no
VaultBridge-TrueNAS fork and no second MCP container. stdio is mainly a local client integration and
does not require the TrueNAS application to expose container stdin. Future opt-in Streamable HTTP can
share the normal application port after separate implementation and live validation; no new port is
opened by default.

### Web Dashboard

The dashboard remains a REST client. It does not call MCP, store MCP credentials, list MCP tools, or
become an MCP administration UI.

## Compatibility

MCP is additive. This ADR changes no runtime behavior. In particular:

- REST `/api/v1` paths, operation IDs, schemas, authentication, errors, and response semantics remain
  stable;
- legacy REST aliases remain compatibility routes;
- the ChatGPT Action schema and configuration do not change;
- the Web Dashboard remains on its accepted REST architecture;
- CLI commands and stopped-service maintenance rules remain unchanged;
- model, chunking, ranking, index signature/schema, and persistence remain unchanged;
- Docker, Compose, TrueNAS, ports, mounts, runtime identities, and version metadata remain unchanged.

## Alternatives considered

### Expose every REST endpoint mechanically

Rejected. Protocol names should represent a small, coherent knowledge-operation surface. Health,
privacy, and index maintenance are not model tools; `semantic_search` would duplicate
`related_notes`; REST compatibility aliases are not separate capabilities.

### Use HTTP loopback for all MCP operations

Rejected. It would require the API server and Bearer key even for local stdio, add serialization and
network failures, duplicate adapter boundaries, and make MCP unavailable when only the local package
and vault are present.

### Implement stdio and Streamable HTTP together in VB-091

Rejected for initial scope. It combines local process lifecycle with an independently significant
network authentication/Origin/header/mount lifecycle. The shared adapter design keeps later addition
straightforward without making the first task broad.

### Streamable HTTP only

Rejected for the first task. It gives poor local desktop ergonomics, requires the server to be
running, and introduces network security work before validating the tool/resource surface.

### Separate MCP service/container

Rejected. It creates a permanent runtime fork and risks competing semantic-index ownership.

### Handwritten MCP protocol support

Rejected. The official maintained SDK is compatible with VaultBridge's Python floor and avoids
owning fast-changing protocol framing, schemas, negotiation, and transport compatibility.

### Optional SDK extra or MCP-specific image

Rejected for VB-091. It would make the advertised first-class integration absent from the normal
runtime or create another artifact. A single required SDK dependency has lower total lifecycle cost.

### Include writes in the stdio MVP

Rejected. It would either leave semantic refresh stale, start a competing index writer, require the
HTTP server through loopback, or need new cross-process coordination. None is acceptable implicitly.

### No Resources

Rejected. A safe custom Resource URI provides a clear application-driven context path for a known
Markdown note. It reuses `VaultService.read_note` and exposes no host filesystem identity.

### Bundled Prompts

Rejected. VaultBridge has no concrete product-specific reasoning workflow that belongs in the
server.

## Consequences

Positive consequences:

- local MCP clients gain useful list/read/literal/semantic/duplicate access without an API server;
- all vault and search invariants remain owned by existing services;
- the MVP cannot mutate Markdown or the semantic index;
- REST, dashboard, CLI, Docker, and TrueNAS remain independent and compatible;
- the same adapter can later be mounted for Streamable HTTP;
- no new service, port, account system, or cloud dependency is introduced.

Tradeoffs and limitations:

- VB-091 is local stdio only; remote MCP clients wait for a later task;
- semantic tools require an already compatible ready persisted index and can be temporarily
  unavailable while another process is rebuilding it;
- `create_note` and `append_note` are not MCP tools initially;
- some clients may not support custom Resource schemes or may use only tools, so `read_note` remains;
- the official SDK increases normal runtime dependencies and must be maintained with the protocol;
- the future pre-shared-key HTTP profile may require manual client configuration and is not claimed
  as full OAuth discovery compliance.

## Deferred work

- Streamable HTTP `/mcp` implementation and mounted-ASGI lifecycle tests;
- explicit `MCP_HTTP_ENABLED` configuration;
- current-spec HTTP header, Origin, Bearer, rate-limit, and reverse-proxy validation;
- OAuth 2.1 discovery/audience support if client interoperability demonstrates a need;
- `create_note` and `append_note` after transport/process ownership and human-confirmation UX are
  approved;
- any stdio write mode, which requires exclusive ownership or cross-process coordination;
- Resource catalogue pagination, subscriptions, and change notifications;
- MCP Prompts;
- any section update, upsert, delete, backlink mutation, index maintenance, or other new domain
  capability.

## VB-091 implementation contract

VB-091 is complete only when all of the following are true:

1. Add the official `mcp` v2 SDK as one bounded normal runtime dependency after recording its exact
   tested version, transitive/license review, and image-size impact.
2. Add an explicit `python -m app.mcp_server` stdio entry point; do not add an HTTP route, listener,
   daemon, second service, or alternate image.
3. Construct/inject existing `Settings`, `VaultService`, `SemanticSearchService`, and
   `DuplicateCandidateService` without importing the FastAPI application as a side effect.
4. Do not construct/start `BackgroundSemanticIndexer`, `SemanticFilesystemWatcher`, synchronization,
   rebuild, or any semantic-index write path.
5. Register exactly the five initial read-only tools and the note Resource template defined above, in
   deterministic order. Do not register `semantic_search`, write tools, health/index tools, or
   Prompts.
6. Preserve the exact argument limits/defaults, service calls, result fields, live-path verification,
   score values/order, and Resource URI rules from this ADR.
7. Before semantic calls, enable only a compatible persisted ready index through the existing
   read-only service boundary; never wait for or trigger synchronization.
8. Return structured success content plus a JSON text compatibility block. Map expected errors
   safely and never expose exception text, host paths, secrets, raw vectors, note/query content, or
   private storage details.
9. Keep stdout protocol-only. Add safe stderr operation logging and direct constant-client use of the
   existing fixed-window limiter without claiming ASGI peer protection.
10. Add focused fake-service tests for every tool schema/result and failure class, Resource URI
    canonicalization/traversal/symlink/Markdown/not-found handling, rate limiting, stdout cleanliness,
    no-secret logging, no semantic persistence writes, and process shutdown.
11. Run the full Python/compile checks, verify all protected REST operation IDs and existing clients
    remain unchanged, and verify Docker configuration/build because the dependency changes the normal
    image.
12. Update user documentation only after the command exists. Do not implement the deferred network or
    write phases as part of VB-091.

## Authoritative references

Retrieved 2026-09-07:

- MCP specification `2026-07-28`, transport overview:
  <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports>
- MCP stdio transport:
  <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio>
- MCP Streamable HTTP transport and legacy HTTP+SSE deprecation:
  <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- MCP authorization:
  <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization>
- MCP tools and error handling:
  <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- MCP Resources:
  <https://modelcontextprotocol.io/specification/2026-07-28/server/resources>
- Official Python MCP SDK repository and v2 documentation links:
  <https://github.com/modelcontextprotocol/python-sdk>
