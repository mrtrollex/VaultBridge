# Named Knowledge Spaces

Named Knowledge Spaces ship in published stable **v1.5.0**. Start with legacy single-vault
[Docker setup](DOCKER.md) unless you need independent roots and explicit policies.
This guide owns named configuration, selection and operational limits. See the
[release ledger](RELEASE_CHECKLIST.md#v150-release-evidence) for exact identities and evidence,
and [release notes](V150_RELEASE_NOTES.md) for recovery boundaries. Historical slice verification
is retained in [VB152_D_VERIFICATION.md](VB152_D_VERIFICATION.md).

## Configuration and composition

Without `KNOWLEDGE_SPACES_JSON`, VaultBridge retains legacy single-vault startup,
the current Dashboard and stopped-service maintenance commands. `VAULT_PATH` and
`SEMANTIC_DATA_PATH` keep their existing defaults and meanings.

Supplying `KNOWLEDGE_SPACES_JSON` selects strict named composition. It is a JSON object
with `version: 1` and one to eight `spaces`. Every record explicitly supplies `space_id`,
`root`, `semantic_data_path`, `read`, `write`, `indexing`, `dialects` and `capabilities`;
`label` is optional and private. IDs follow `[a-z][a-z0-9_-]{0,63}`. There must be exactly
one `default`. No unknown fields, duplicate keys, null selectors or wildcard scopes are accepted.

For example, provision two ordinary, disjoint Markdown roots and derived-data locations,
then supply these existing environment settings to the chosen process:

```bash
export VAULT_PATH=/vault/default
export SEMANTIC_DATA_PATH=/data/default
export KNOWLEDGE_SPACES_JSON='{"version":1,"spaces":[{"space_id":"default","root":"/vault/default","semantic_data_path":"/data/default","read":"allow","write":"allow","indexing":"disabled","dialects":["obsidian_wikilink","markdown_link"],"capabilities":["read_note","list_notes","literal_search","relationships","knowledge_query","duplicate_candidates","capture","promotion","create_note","append_note","hygiene"]},{"space_id":"research","root":"/vault/research","semantic_data_path":"/data/research","read":"allow","write":"allow","indexing":"disabled","dialects":["obsidian_wikilink","markdown_link"],"capabilities":["read_note","list_notes","literal_search","relationships","knowledge_query","duplicate_candidates","capture","promotion","create_note","append_note","hygiene"]}]}'
```

These are placeholders inside the process filesystem. Operators must provide the appropriate
existing mount/access configuration; paths are never client request parameters. On Windows,
use absolute native paths and JSON escaping. The example disables indexing in both spaces,
so no model, scheduler or watcher starts and semantic requests are unavailable. For semantic
retrieval, explicitly enable `indexing` and include `semantic_retrieval` in that space's
capabilities. `read: deny` requires both `write: deny` and `indexing: disabled`.

The named default must match the resolved `VAULT_PATH` and `SEMANTIC_DATA_PATH`. Named JSON
does not override conflicting legacy settings. Settings are loaded at process startup;
explicit factory settings take precedence over environment loading. Invalid configuration,
overlapping/redirected roots or incompatible raw-owner injection fails startup safely as
`invalid_configuration`; there is no legacy fallback or extra activation switch.

One immutable registry/resolver pins the roots and owners for the process. The runtime starts
the shared watcher through native readiness when enabled, schedules startup reconciliation,
then opens operation admission. Semantic indexing completes asynchronously. Read/index policy
controls background work; disabled indexing never processes old derived rows. Markdown remains
authoritative, derived SQLite indexes remain independent, and embeddings stay local/CPU-only.

## Docker configuration

Generic Compose forwards an explicit environment allowlist and mounts one root. Adding named JSON
only to `.env` has no effect. Use explicit environment injection and independent mounts.
Provision disjoint host directories and deliberate ownership/ACLs for the chosen non-root UID/GID.
For an isolated synthetic evaluation, replace the placeholder paths and use a private `named.env`:

```bash
IMAGE=ghcr.io/mrtrollex/vaultbridge:1.5.0
docker run --detach --name vaultbridge-named --user 568:568 --cap-drop ALL \
  --publish 127.0.0.1:18765:8000 --env-file /absolute/disposable/named.env \
  --mount type=bind,src=/absolute/disposable/default,dst=/vault/default \
  --mount type=bind,src=/absolute/disposable/research,dst=/vault/research \
  --mount type=bind,src=/absolute/disposable/default-data,dst=/data/default \
  --mount type=bind,src=/absolute/disposable/research-data,dst=/data/research \
  --mount type=bind,src=/absolute/disposable/cache,dst=/cache \
  "$IMAGE"
```

For immutable evaluation replace `IMAGE` with the [verified digest](RELEASE_CHECKLIST.md#v150-release-evidence).
`named.env` must contain `API_KEY=<synthetic-key>`, `VAULT_PATH=/vault/default`,
`SEMANTIC_DATA_PATH=/data/default`, `HF_HOME=/cache`, `SEMANTIC_WATCH_ENABLED=false` and the one-line
`KNOWLEDGE_SPACES_JSON` value from the example above. Do not include shell `export` or surrounding
shell quotes in env-file values. Add explicit MCP settings only when needed; protect the file.
The indexing-disabled example has no semantic readiness; enable indexing/capability deliberately
for semantic evaluation. Stop/remove the serving instance before CLI/stdio uses the same writable
mounts. The Community App form does not imply this named layout.

## REST and explicit selection

Use existing `/api/v1` routes. Existing note/search aliases retain their paths and operation IDs.
Omitting scope always selects default only and retains the old response shape and limits.
Explicit `default` selects the strict scoped contract just like another configured ID.

| Operations | Selection | Result |
| --- | --- | --- |
| Read, duplicates, links, backlinks, hygiene scan, create and append | Singular `space` | `{space_id,result}` around the operation payload |
| List, literal search, related search | Plural `spaces` | Qualified `items`, selected-space `coverage`, `state`, `ordering`, `result_limited` |
| POST `/api/v1/knowledge/query` | Optional plural `spaces` | Always scoped output; omission selects default only |

GET list uses repeated parameters, for example
`/api/v1/notes/list?spaces=default&spaces=research`. POST search uses
`{"query":"backup","spaces":["default","research"]}`. Read uses
`/api/v1/notes/read?space=research&path=Notes%2FExample.md`. Create and append keep their
existing request fields with an optional singular `space`. Capture/promotion remain CLI-only.

Bearer clients retain the existing current/previous key behavior and direct-peer HTTP limiter.
Credentials authenticate the deployment and confer no space permission. The VB-156B source adds
guarded Dashboard cookies only on ADR 0011 section 5's exact read-method/path allowlist;
cookies never authenticate writes, legacy aliases, MCP, Hygiene, Capture/Promotion or maintenance.
Unknown and denied selections have
the same safe public error. A mixed allowed/denied selection fails wholly before owner work;
availability coverage applies only after full authorization. Labels remain private. Policy-filtered
readable-ID discovery is defined below; there is no anonymous registry,
automatic scope expansion or fallback to another space. Equal paths in two spaces remain distinct.

Scoped content responses use `Cache-Control: no-store`. GET path/scope parameters can appear
in separately operated reverse-proxy/access logs; application redaction does not control those
external logs. Treat result content, selected IDs and capture/promotion review output as private.
See the [complete request/error contract](../BACKLOG.md#vb-152--permission-aware-query-and-write-adapter-integration--p1-complete-ad-accepted-and-merged)
for strict bounds, Query predicates and legacy/scoped error distinctions.

## MCP transports

Named stdio uses the existing `python -m app.mcp_server` entry point. It owns one runtime,
including policy-authorized startup indexing and optional shared watching, and drains that
same generation on EOF, cancellation or shutdown. It retains local trusted-process access and
the process-local operation limiter; it has no Bearer requirement.

**Run named stdio instead of the serving application against the same writable derived state.**
Do not run both concurrently. There is no cross-process index lock, daemon coordination or
automatic stopped-service detector. Legacy stdio retains persisted reads and write opt-in lifecycle.

Streamable HTTP remains opt-in with `MCP_HTTP_ENABLED=true` at `/mcp` in the serving application.
It uses the parent's exact operations and HTTP limiter, creates no child runtime, and remains
stateless. Preserve the existing `MCP_HTTP_ALLOWED_HOSTS` and `MCP_HTTP_ALLOWED_ORIGINS` settings,
current/previous Bearer credentials on every request and supported protocol negotiation. When
HTTP MCP is disabled, no SDK server/session manager is constructed for it.

Both transports expose the existing read tools and `knowledge_query`. `MCP_WRITE_ENABLED=false`
omits `create_note` and `append_note`; enabling registration still requires the selected space's
read/write policy and operation capability. Tools accept the same singular/plural scope fields
and qualified outcomes as REST. The old `vaultbridge://note/{path}` resource remains default-only.
Explicit results use `vaultbridge://space/{space_id}/note/{path}`, with the entire canonical
UTF-8 relative path encoded as one URI component, including `%2F` for slash. A resource locator
grants no permission; invalid/unknown/denied/missing/unavailable resources share a static error.

## Stopped-service CLI

Stop the serving application and stdio process before local commands that could touch the same
derived state. Existing command names/positional arguments stay valid:

```bash
python -m app.cli search backup --space default --space research --json
python -m app.cli related 'backup strategy' --space research --json
python -m app.cli query --space research --request-json < query-request.json
python -m app.cli hygiene scan --space research --group frontmatter --json
python -m app.cli capture --space research < capture-request.json
python -m app.cli promote review --space research --source-path Inbox/Captures/EXAMPLE.md --capture-id CAPTURE_UUID --destination Notes/Target.md
python -m app.cli promote apply --space research < approved-decision.json
```

Replace the example IDs and paths with the exact capture evidence. Structured Query JSON is the
REST/MCP request; command-line selection is allowed only if that JSON omits `spaces`. Use
`--qualified-path` for exact qualified paths, or bare `--path` with one effective selected space.
Capture/review/apply accept exactly one `--space`. Explicit selection, including default, uses
v2 provenance; omitted selection retains default-only v1 bytes/digests and outputs. Apply
reconstructs review and the exact decision in the current process. It never trusts serialized
owner bindings or infers a destination/scope from artifact metadata.

Reads use inspection-only or persisted immutable views without startup sync, storage creation,
watching, model download or cache repair. Missing/incompatible persisted semantics fails or
reports unavailable; it does not trigger a rebuild. Writes use the full runtime and wait for
qualified indexing work before exit, including startup reconciliation of unselected enabled
spaces where operator policy authorizes it. Successful Markdown commits survive enqueue failures.

Named `status`, `index`, `index check`, `index rebuild` and `reindex` are deliberately unsupported.
They fail `invalid_configuration` with exit 2 before registry/owner/filesystem work, including
direct exported function invocation. No force/all/discovery maintenance mode is available.
Legacy maintenance remains unchanged. General exits remain 0 success, 1 operational inability
or uncertain commit, and 2 invalid/denied/capability/configuration/programming failure.

## Dashboard, health and recovery

The accepted VB-156B foundation serves an isolated named `/ui/` with explicit bundled routes and
safe 404/405/no-store boundaries. The VB-156C working source adds Browse, raw Markdown reading and
federated Literal and Semantic Search. It reuses the explicit scope and qualified results of the
existing REST routes. Knowledge Query, Relationships and named Hygiene UI remain deferred.
Published v1.5.0 has the legacy-only Dashboard. See
[Dashboard sessions and entire-hostname trust](DASHBOARD.md#named-access--spaces--status)
for secure-context/Web Locks/shared-storage requirements, nonrenewing restore, orphan quarantine,
fixed expiry, recovery and copied-token limits. All ports/routes of a dedicated hostname must be
trusted, with one canonical issuer and drained legacy handover; cookies have no port isolation.

### Named discovery and selected status

Both routes are named-only, authenticated, direct-peer limited and no-store on success/failure/method
responses. They add no MCP tools or CLI commands. Current/previous Bearer and guarded named cookies
are accepted. Legacy composition retains 404. No labels, roots, derived paths, write permissions,
denied IDs/counts, operational internals or raw errors are returned.

`GET /api/v1/knowledge/spaces` (`listKnowledgeSpacesV1`) accepts no query/body. It returns exactly
`{"spaces":[{"space_id":"default","read_capabilities":["list_notes"],"relationship_dialects":[],"indexing":"disabled"}]}`.
All row fields are required; there are 0–8 ASCII-ID-sorted entries. Capabilities are sorted subsets
of `read_note`, `list_notes`, `literal_search`, `semantic_retrieval`, `knowledge_query`, `relationships`.
Dialects are sorted `markdown_link`/`obsidian_wikilink` subsets only with relationships permission.
Indexing is `enabled|disabled`. Projection is pure read-policy metadata through SpaceOperations and
SpacePolicyResolver, with zero owner/filesystem/model/index probes. Unavailable readable spaces stay
listed; all-read-denied returns `{"spaces":[]}`. Unexpected query/body is safe 422 `invalid_request`;
unexpected failure is static 500 `internal_error`. Discovery is never an authorization grant.

`GET /api/v1/knowledge/spaces/status` (`getKnowledgeSpaceStatusV1`) accepts only repeated `spaces`
parameters, raw 1–8 valid IDs before deduplication. Omission retains default-only semantics. It returns
exactly `{"spaces":[{"space_id":"default","index_state":"disabled","search_available":false,"refresh_required":false}]}`,
one ASCII-sorted row per selected ID, all fields required. Complete read authorization precedes any
owner inspection; unknown/denied mixtures fail wholly as 403 `scope_not_allowed`. Invalid selectors
are 422 `invalid_scope`; other unexpected parameters/body are 422 `invalid_request`.

Index state is `disabled|not_permitted|indexing|ready|unavailable`. Disabled indexing and disallowed
semantic retrieval yield false/false with zero operational probes. Otherwise scheduler booleans are
observed before cancellable admission, then guarded root/storage metadata and chunk existence are
read without counts, walks, model initialization, schema writes, refresh or scheduler mutations.
Ready requires searchable compatible ready lifecycle without debt; runnable work is indexing unless
paused/failed. Recovery debt alone is unavailable. A compatible previous index can stay searchable
while refresh is required. Observation/root/binding failure is unavailable/false/true. WAL storage
currently receives that conservative row to avoid creating coordination files; a read-only open
is not inherently filesystem-immutable. No immutable live-WAL shortcut is used. Stopped/saturated
admission and cancellation retain safe 503 semantics. Observations are non-atomic and not promises
of future search success. Selection/status never trigger search, repair or fallback to another space.

`/health/live` remains public and dependency-free `{"ok":true}`. Named `/health` exposes only
`{"ok":boolean}` for runtime admission. `/health/ready` keeps `{"ready":boolean}` and 200/503:
it checks only default, after read/semantic/index policy authorization. Disabled or denied default
returns false without owner probes, even if another space is usable. It never represents overall
registry availability or forces indexing. `/privacy` remains static and public. Legacy health
diagnostics are unchanged.

Startup failures fence admission and roll back the same generation. Shutdown fences operations
and indexing, closes MCP ingress/sessions, stops the watcher, drains scheduler and operations,
then releases shared resources. Admission remains one active operation and at most 64 waiters.
Cancellation is cooperative: native/model calls can drain to their safe boundary. Cancelled reads
return no successful partial result. Confirmed and uncertain write evidence takes precedence
over late cancellation; `committed:false` with `commit_unknown` does not prove no write occurred.
Preserve exact IDs/requests and reconcile the authorized artifact before any further write;
never automatically retry an uncertain commit.

[ADR 0010](adr/0010-safety-first-e6-cleanup-outcomes.md) permits bounded ambiguous native ownership
to remain stopping indefinitely. Such a generation is retained and cannot be replaced/restarted
in-process; process exit may reclaim resources but is not successful in-process shutdown.
Do not infer cleanup from completed futures or numeric handles. This integration adds no
cross-process locking, root rebind/artifact migration, hardlink/hostile-writer isolation,
atomic multi-root snapshot or universal cleanup/latency guarantee.
