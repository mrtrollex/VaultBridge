# ADR 0009: Knowledge Spaces and Scope Policies

- **Status:** Accepted
- **Date:** 2026-10-07
- **Task:** VB-150 (design only)

## Context and decision authority

Milestone 15 is complete. Today VaultBridge supports exactly one authoritative contained
Markdown root. ADRs 0005–0008 govern portable documents, Knowledge Query, capture/promotion,
and read-only hygiene. This proposal extends their identity and scope boundary for a future
multi-space implementation; it does not supersede current single-space runtime contracts.
This accepted ADR is the Milestone 16 design boundary. Acceptance alone does not authorize
VB-151 runtime or VB-152 adapter implementation; each needs its own accepted BACKLOG contract.
Milestone 16 remains PLANNED after design acceptance.

The following decisions are accepted by the project.
Current configuration, API semantics, resource URIs, authentication, Markdown bytes, and
derived-index formats remain unchanged by VB-150.

### Evidence from current owners

The design follows inspected code rather than introducing a replacement domain framework:

| Current owner | Observed boundary | Future consequence |
| --- | --- | --- |
| `app/core/config.py: Settings` | One resolved `vault_path` from `VAULT_PATH`, one `semantic_data_path`, global note-size/semantic/MCP settings | Normalize operator configuration into a bounded registry; keep legacy settings valid |
| `app/services/vault.py: VaultService` | One resolved root, traversal/symlink containment, verified canonical relative paths, reads/writes, coordinated capture/promotion primitives, bounded hygiene snapshots | Keep one containment owner per space; never use a union filesystem root |
| `app/services/relationships.py: RelationshipService` | One vault, dialect-specific and normalized live resolution, verified backlink scans | Retain local resolution and local candidate snapshots |
| `app/services/knowledge_query.py: KnowledgeQueryService` | One vault/relationship/semantic owner set; live eligibility precedes unchanged ranking; paths are identity | Compose qualified results above per-space owners without reimplementing predicates |
| `app/services/capture.py` and `promotion.py` | One vault; fixed intake path, explicit destination and source/destination hashes; portable provenance and decision digest | Bind scope before review/commit and preserve existing uncertainty/retry protections |
| `app/services/knowledge_hygiene.py: KnowledgeHygieneService` | One vault; bounded snapshot, local alias/relationship/candidate evidence, read-only index inspection | Select one space, retaining ADR 0008 semantics and bounds |
| `app/services/semantic_search.py` and `app/repositories/semantic.py` | One root and path-keyed SQLite repository; per-note aggregation and local relative floor; public scores rounded after ordering | Prefer separate derived indexes; do not concatenate path-keyed rows or merge rounded scores as if exact |
| `app/services/indexer.py` | One in-process synchronization worker, coalesced relative-path jobs | Future jobs must carry their space binding; lifecycle belongs to the semantic owner |
| `app/main.py`, `app/api/dependencies.py` | Shared single-vault services injected into REST and MCP HTTP | Inject one policy boundary; adapters must not independently authorize spaces |
| `app/mcp_server.py`, `app/mcp_http.py` | Shared tools/resources, single-vault composition; default-off create/append writes | Resource reads must use the same policy boundary as tools; write enablement remains an additional gate |
| `app/cli.py` | Local composition of query, capture, promotion, hygiene and offline index owners | Local CLI also passes through policy; it cannot bypass read/write restrictions |

Current Knowledge Query is wired only to its accepted CLI subset. Capture and promotion have
local CLI adapters. Hygiene has accepted REST/MCP/CLI adapters. This proposal does not infer
additional public adapters from the existence of a domain service. Current semantic search loads
repository chunks before filtering `eligible_paths`; that helper is not proof of a bounded
cross-space retrieval primitive. Current query request/result bounds do not impose a hard
whole-vault enumeration work ceiling. VB-151 must address these facts explicitly for new scopes.

## Selected design

### 1. KnowledgeSpace and logical identity

One KnowledgeSpace is an operator-configured logical namespace over one contained Markdown root,
with a finite policy and the existing domain owners bound to that root. Its conceptual value is:

```text
KnowledgeSpace
- space_id: stable logical identifier
- root_binding: private filesystem root, available only to internal owners
- label: optional bounded operator display label
- read: allow | deny
- write: allow | deny
- indexing: enabled | disabled
- dialects: finite subset of accepted relationship dialects
- capabilities: finite subset of implemented domain operations
```

Use exact lowercase ASCII identifiers of 1–64 characters matching
`[a-z][a-z0-9_-]{0,63}`. IDs are operator assigned and stable across restart and root relocation;
they are not titles, directory names, host paths, hashes of roots, array positions, or credentials.
`default` is the reserved legacy ID. Labels are optional display text, at most 128 UTF-8 bytes,
never lookup keys or permission inputs. Only approved labels may be returned to authorized callers.

Root binding is internal configuration. Callers supply IDs and relative paths only; they cannot
supply, register, or override roots. A registry holds at most eight spaces. Resolve and validate
roots using containment owners at startup: reject identical or ancestor/descendant resolved roots
and root aliases that would expose the same namespace under different policies. A symlink from
one space into another remains a containment escape even when both spaces are selected. Operator
configuration must not intentionally alias shared writable files across policy boundaries; this
proposal does not claim inode identity or OS-level isolation from the operator/process itself.

Policies are deployment-wide operator restrictions for the existing authenticated service and
trusted local operations. They are not per-user permissions. Authentication remains its current
owner. A caller cannot raise configured permission or capability. Effective permission is the
intersection of selected scope, configured policy, implemented capability, existing operation
enablement (including `MCP_WRITE_ENABLED`), and containment/size rules. Write allow requires read
allow, since accepted writes inspect existing state. Indexing enabled also requires read allow.
Invalid policy combinations are configuration errors, never silently weakened restrictions.

Dialects initially name only `obsidian_wikilink` and `markdown_link`; capability names denote only
already implemented operations (note read/list/literal search, relationships, semantic retrieval,
Knowledge Query, capture, promotion, hygiene, create/append). They gate operations rather than
choosing alternate parsers or rankers. Requesting an unsupported operation or required dialect
fails before evaluation; do not silently drop predicates. Dialect exclusions do not hide raw
Markdown or claim that text uses a different syntax. Legacy policy retains every current domain
capability/dialect and the existing narrower adapter exposure.

### 2. Note identity and compatibility

The future domain identity is logically `(space_id, canonical_relative_path)`. Within a space,
`VaultService` still supplies the exact verified canonical forward-slash Markdown path. Titles,
aliases, fragments, capture IDs, database rows, and host paths never replace this pair. Thus
`(default, Notes/Plan.md)` and `(research, Notes/Plan.md)` are different notes. Symlink aliases
within one root still resolve to one canonical note; equal content across roots is not identity.

Existing APIs keep path-only identity in the default space and their existing response shapes.
No new prefix is inserted into old paths. Future scope-aware results must carry logical space
identity alongside each path (or an unambiguous one-space result envelope); neither filenames
nor Markdown are automatically rewritten. Any future cache, queue, candidate set, review token,
provenance decision, or derived result spanning spaces must preserve the pair. A space ID rename
changes logical identity and requires an explicit operator migration contract; no alias registry
or hidden persistent identity store is introduced here.

### 3. Legacy single-root and conceptual configuration

Existing `VAULT_PATH`, including its current default, maps to implicit `default`. Existing
`SEMANTIC_DATA_PATH`, size limits, semantic settings and opt-in write settings retain their
current meanings. Single-root deployments need no mandatory migration, extra metadata, or new
configuration merely to continue operating.

The conceptual future named collection contains at most eight records with the fields above and
a private derived-data binding per space. This is not an environment-variable, YAML-file, or
public schema specification. Exact configuration transport belongs to a later contract.

When adding spaces, preserve `default` bound to the existing legacy root; require an explicit
`default` record in named-collection mode. There is no first-record, sole-readable-space, or
alphabetical fallback. If both legacy settings and a named default are supplied, their root and
derived-data bindings must agree or startup fails safely; do not choose precedence silently.
In named mode, read/write/index/dialect/capability choices must be explicit. Legacy mode inherits
current behavior rather than new restrictive defaults. Removing/rebinding `default` is an explicit
compatibility migration outside this proposal. Moving the same logical space's root requires
operator confirmation that the namespace is retained and derived state is revalidated/rebuilt;
never reuse unrelated root content just because paths match.

Omitted scope continues to address only `default`, even with multiple spaces configured. A denied
or unavailable default causes an error; it never selects another space. No call gains broader
scope from adding configuration records. An intentional policy denial can disable old operations,
but merely adopting the future model must not do so.

### 4. Scope types and validation

Use two small conceptual types, not an expression language:

```text
ReadScope: one space ID or a bounded nonempty set of space IDs
WriteScope: exactly one space ID
Omitted legacy scope: default
```

Explicit read selections contain 1–8 IDs, with the input cardinality checked before exact
deduplication. Evaluation uses sorted ASCII ID order; caller order and registry order do not affect
results. Empty explicit scope is invalid, not default, all spaces, or a meaningful empty search.
Wildcards, automatic discovery, nested scopes, and both conflicting singular/plural inputs are
invalid. Unknown/disallowed selections invalidate the whole request before any note/index work;
do not return partial results for authorization failures. Configuration discovery, if ever exposed,
must enumerate only readable authorized spaces and safe labels, never roots or inaccessible counts.

Single-note reads, relationship/backlink calls, hygiene scans, capture, promotion and create/append
select exactly one space. List/literal search/semantic retrieval/Knowledge Query may eventually
use ReadScope. Multi-space writes are invalid, including batches with one path per space. New
scope-aware writes require one explicit destination ID even if only one space is configured.
Legacy calls may safely default to `default`; a reviewed promotion binds that choice before commit.
A future CLI `--space` concept is singular; plural CLI syntax is deferred with VB-152.

For multi-space Query, path scopes and relationship predicate identities must be qualified pairs.
Do not apply a bare note path to every selected space or resolve it by first match. A folder filter,
if offered as a common relative filter, must explicitly mean the same contained folder independently
within each selected space; a missing folder contributes no candidates there and never broadens
scope. Text/tag/metadata predicates can apply identically per selected space. Space selection alone
does not make the otherwise empty ADR 0006 query valid. Qualified relationship predicates still
describe local relationships: the other endpoint must be in the candidate's space. Cross-space
edge predicates are unsupported and cannot be inferred from selecting both endpoint spaces.

### 5. One policy boundary, existing containment owners

Introduce only the conceptual domain boundary needed to resolve a request to authorized immutable
space bindings. Its concrete class/module is VB-151's responsibility. It owns registry lookup,
scope cardinality/defaulting, read/write decisions, indexing eligibility, and capability/dialect
compatibility. It validates the entire scope before handing any selected owners to orchestration.
Bindings retain one registry/policy generation for an operation; configuration is startup-owned,
with no hot-reload, durable grant, or persistent policy store in this design.

| Responsibility | Authoritative owner |
| --- | --- |
| Parse operator configuration / construct registry | Typed configuration and composition roots |
| Scope resolution and all space policy decisions | One shared domain/policy boundary |
| Root containment, canonicalization, verified reads/writes | Per-space `VaultService` |
| Metadata / dialect parsing and relationship resolution | Existing parsers and per-space `RelationshipService` |
| Query constraints and bounded composition | Knowledge Query orchestration over selected owners |
| Capture/promotion review, provenance, uncertainty and commit | Existing services and contained write primitives |
| Index storage, compatibility, ranking, lifecycle | Per-space semantic service/repository; existing synchronization owner |
| Transport shape and error mapping | Thin REST/MCP/CLI adapters using the same domain validation |

Adapters may reject malformed wire types before dispatch, but cannot make independent permission
decisions, infer defaults, enumerate other roots, or rank/filter policy results themselves. Background
indexing, stopped-service index commands, MCP resources and local CLI operations obey the same
policy. Internal owner injection is not a public bypass. The registry is operator configuration;
parsed metadata cannot change it.

### 6. Read and privacy semantics

Authorize selected spaces before containment, existence tests, reads, candidate discovery or index
inspection. A result must derive only from selected readable spaces:

- `read_note` reads one qualified note; missing-note detail is available only after space permission.
- List/search/query operate only through selected owners; limits and counts apply only to their
  authorized candidate universe. There are no global private-space totals.
- Outgoing relationships and backlinks are local to the selected source/target space. Internal
  resolution cannot search an unselected space, including as a fallback for a missing local target.
- Duplicate/related evidence is advisory and local by default. Any later multi-space candidate
  operation needs explicit ReadScope and qualified pairs; it cannot choose a write destination.
- Hygiene uses only the selected space, including related paths, peer counts and index categories.
- Semantic retrieval opens only selected eligible indexes, filters live eligibility before ranking,
  and never consults denied/unselected indexes for hits, scores, relative floors or availability.
- MCP resource lookup, templates, resource references and resource reads must authorize the same
  logical binding. Existing path-only URIs remain default-only. A future qualified URI is deferred;
  clients must not be able to fabricate a URI that bypasses tool policy.

Do not leak inaccessible notes through existence errors, relationship targets, duplicate candidates,
snippets, counts, index signatures, lifecycle metadata or timing caused by note work in denied spaces.
Raw Markdown can itself contain authored references; returning an authorized source's bytes is not
permission to resolve another space or claim that its target exists. No new cross-space authored
target syntax is interpreted by this proposal. Logs/failures use stable categories and safe bounded
counts, never note/query text, arbitrary metadata, credentials, host paths, symlink destinations,
raw exceptions, SQL, or embeddings. Public health/diagnostics must not acquire cross-space listings
or counts implicitly; any expanded operational exposure needs its own privacy contract.

### 7. Writes, capture and promotion

Every write binds exactly one permitted space before path inspection or mutation. Existing create,
append, atomic capture, coordinated promotion, size limits, conflict checks, and commit-unknown
semantics remain in their owners. Configuration allow is necessary but does not replace explicit
promotion approval of the exact destination/action/content or existing MCP opt-in enablement.
Read-only policy must prevent every write entry point, including local CLI and recovery attempts.

Capture selects one intake space explicitly on new interfaces or defaults safely on legacy ones;
its `Inbox/Captures/<capture_id>.md` is relative to that selected root. Equal capture IDs in different
spaces are not the same artifact. Provenance retains logical space and path context; an external
declared `source` string remains untrusted attribution, not a space selector.

Promotion source and destination must be in the same selected space. An explicit different target
space is rejected; this proposal does not authorize even an operator-requested cross-space
promotion. Review evidence, expected hashes, decision digest, idempotency proof and returned paths
must be bound to logical identity so approval/retry cannot replay against an equal path in another
space. Candidate evidence never changes that destination. Preserve the source capture and current
explicit create/append behavior; no automatic move, merge, repair, backlink insertion or migration.

Future portable provenance must carry enough logical identity to disambiguate source and destination
without roots, using bounded inspectable Markdown fields/manifests. Exact field/version mapping and
digest migration are deferred. Existing v1 provenance without space IDs is interpreted within the
legacy default context and is not rewritten or rehashed automatically. Copying old artifacts into
another namespace must not silently establish their provenance or retry authorization there; that
needs a separately approved migration. New multi-space promotion remains blocked until the portable
format and replay-safety contract are accepted. Index enqueue after commit carries the space binding;
index failure cannot repeat or negate a durable Markdown commit.

### 8. Indexing choice

Indexing is permission to maintain derived semantic state for a configured space, independent of
permission to read Markdown or mutate notes. Disabling it forbids synchronization and semantic
retrieval for that space, even if old rows exist; ordinary readable Markdown/query remains usable.
Policy changes do not authorize deleting old index files or rewriting Markdown. Hygiene may inspect
existing derived state only through its accepted strictly read-only owner, with a safe disabled or
unavailable category as appropriate. It must not initialize/rebuild an index.

| Model | Privacy/isolation | Rebuild and compatibility | Query composition / risk / complexity |
| --- | --- | --- | --- |
| A: independent derived SQLite index per space | A selected owner never loads another space's chunks; failures isolated by binding | Rebuild one space; preserve legacy database location and path-keyed schema | Bounded federated merge needed; more lifecycle bindings, but smallest change to current owners |
| B: shared derived index with mandatory space identity on every row/key | Possible with mandatory pre-load scoping in every read/write/stats path; omission risks disclosure | Requires compound keys, schema/signature migration and scoped cleanup/rebuild | Unified selection possible, but greater migration and query/maintenance auditing burden |
| C: one active index switched between roots | No simultaneous composition; stale root bindings are hazardous | Repeated rebuilds or explicit storage switching | Avoids federation only by abandoning the multi-space requirement |

Select A as the future baseline. This is isolation inside one process, not tenant or OS security.
No new database, persistent policy store, index format or migration is implemented in VB-150.
Future additional instances use the existing derived SQLite storage engine. Preserve the legacy
default index and its compatible format/location; never import it into another space on path match.
Additional private derived-data bindings must be distinct and must not expose or index another
space's data. Existing exclusion rules remain authoritative. Derived data may be rebuilt from the
bound Markdown; root rebindings require explicit compatibility validation rather than assuming the
current signature proves logical namespace identity. Shared immutable model assets may eventually
be reused under the semantic owner without shared note-content storage or cloud embeddings.

Worker scheduling, storage-layout validation, root-binding validation and resource/model reuse
require a VB-151 contract before implementation. Do not instantiate one unbounded worker/model per
configured space by inference. SQLite, one container and CPU/local embeddings remain defaults.

### 9. Bounded cross-space Query and read composition

Allow future opt-in cross-space reads/Knowledge Query over 1–8 readable spaces. One-space legacy
queries retain ADR 0006 exactly, including errors and bounds. For new multi-space operations:

- Validate every requested ID/policy/capability before evaluating any space. Qualified candidate
  identity is the pair; deduplicate only equal pairs, never equal paths/titles across spaces.
- Reuse per-space live constraint evaluation and verification before semantic ranking. For
  nonsemantic results sort `(space_id, path.casefold(), path)`. For semantic composition sort
  descending final, semantic and lexical score, then ascending `space_id`, `path.casefold()`, `path`.
  Ordering must be independent of caller order, registry order and completion order.
- Keep ADR 0006's visible default 20 / maximum 100 global, 64 path entries global and 16 entries
  per predicate collection global, existing text/byte limits, one-read reuse and finite retries.
  New multi-space candidate discovery selects at most 10,000 canonical paths per space, hence
  at most 80,000 overall, with explicit partial coverage on overflow. Owner/parser/note-size
  bounds remain in force. This does not retrofit a new cap onto legacy requests.
- Keep each semantic owner's score calculation, threshold, aggregation and relative floor.
  Compose the union of its bounded local results, not a claim of globally rescoring all chunks.
  Per-space semantic window remains `min(500, max(limit * 5, limit))`, hence at most 4,000
  candidate results overall. Merge once, reverify live qualified paths, truncate globally, and
  never widen/retry until full. Fewer than the requested limit is permitted.
- Per-space relative floors are deliberate: a weaker local space winner can remain in the union.
  This is federated retrieval, not a new global relative-floor ranking contract. A different global
  floor would need an explicit ranking decision and evaluation.
- Semantic composition requires mutually compatible scoring configuration (model/fingerprint,
  chunk/embedding representation, weights/thresholds) and owner-provided ordering precision.
  Existing public rounded scores are insufficient to preserve exact ordering. An incompatible
  selection fails before ranking; do not compare incomparable scores or drop a space silently.
  The precise internal merge interface is deferred; weights and public scores are not redesigned.
- Report per-selected-space semantic basis and complete/partial coverage; no atomic multi-root
  snapshot or Markdown freshness claim. Nonsemantic reads remain index-independent. Authorized
  root/index unavailability can produce an explicitly partial multi-space result; do not substitute
  a nonsemantic mode for missing semantic retrieval. If no selected space can execute the requested
  mode, fail unavailable, not successful empty. An available empty contribution remains successful.

These are candidate/result ceilings, not proof of finite filesystem-entry or loaded-chunk work.
Before exposing multi-space queries, VB-151 must specify and test fixed owner discovery/entry,
chunk-loading and cancellation budgets (including symlink verification), with deterministic partial
selection and no repeated full scans per predicate/source. Bounded post-load/output filtering alone
does not satisfy that gate. No cross-space implementation is authorized until those execution bounds,
precise-score merge and partial-result contract exist. List/literal/semantic read composition follows
the same selected-space privacy, identity, ordering, finite global output and no-fallback principles;
its operation-specific request/work ceilings belong to that contract, not inferred new endpoints.

### 10. Relationships and hygiene

All currently supported unqualified links resolve only inside their source KnowledgeSpace, as do
ordinary source-relative Markdown links. Identical paths/titles in other spaces have no effect on
local resolution. Local ambiguity remains unresolved; missing locally never triggers a global
title/path/alias search. Resolution snapshots and any caches must be space-bound.

Cross-space relationships are deferred, not supported by this proposal. Any later support must
encode an explicit logical target ID plus relative identity, authorize both selected readable ends,
preserve ambiguity-safe dialect semantics and define backlinks without private-source leakage.
No final Markdown syntax is invented here. A client-supplied target ID cannot turn current link
syntax into cross-space resolution. Backlinks scan only the target's selected space under current
semantics; they do not discover sources in other spaces.

Hygiene remains one-space-per-scan. Its 10,000-path, 500-finding, 10-related-path and optional
20-source/5-candidate bounds and complete/partial semantics remain ADR 0008's contract. Isolation,
alias collision and duplicates are local facts, never global uniqueness/connectivity claims.
Intake exclusions, no-write inspection and unavailable evidence retain Milestone 15 meanings.
Future scope-aware hygiene results can carry one space ID on the scan envelope; primary/related
paths inherit it, including pathless index findings. If a caller combines separate scans, it must
retain those envelopes. Existing path-only default results stay unchanged; no host root is emitted.
There is no cross-space hygiene aggregation, repair, automatic index rebuild or new semantic
candidate retrieval authorization.

### 11. Errors and adapter-visible scope

These are conceptual domain classes, not final HTTP statuses, JSON schemas, tool names or exit codes:

| Internal class | Public behavior for future scope-aware calls |
| --- | --- |
| Unknown space | Generic `scope_not_allowed`; no registry/existence detail |
| Configured but read/write-disallowed space | Same `scope_not_allowed`, before note/index inspection |
| Unavailable authorized space | Safe `space_unavailable` (or existing one-space operational category); no root, note existence or raw I/O error |
| Invalid multi-space request | `invalid_scope` for empty/oversized/malformed selections, conflicting inputs or any multi-space write |
| Unsupported capability/dialect or incompatible semantic composition | Safe `capability_unavailable`; reject rather than silently discard requested semantics |
| Partial multi-space read/query | Structured successful partial coverage for authorized selections only, with bounded availability categories and deterministic usable results |

Unknown and disallowed spaces remain distinct internally for policy decisions but deliberately
indistinguishable publicly. Failures never say whether an inaccessible space contains a requested
note. Do not echo untrusted IDs/paths in errors. Partial metadata identifies only already-authorized
selected IDs, not unknown/denied spaces or their counts. A mixture containing one denied/unknown
space fails wholly before data reads; partial availability is not partial authorization. Existing
default-only adapter errors stay compatible until an explicitly scoped adoption task changes them.

Future REST may express logical `space` / `spaces`, MCP analogous arguments, and CLI a singular
`--space`. Adapters map these into the same conceptual types; no host path parameter is allowed.
Public qualified identities/resources must be disambiguated without changing legacy operation IDs,
paths, tools or resource semantics. Exact endpoints, arguments, schemas, URI encoding, default-only
resource handling and error mappings belong to VB-152. This ADR selects no public surface.

## Alternatives, impacts and implementation gates

| Choice | Alternatives considered | Compatibility impact | Security/privacy impact | Future implementation consequence |
| --- | --- | --- | --- | --- |
| Stable operator ID with private root | Host path, title, automatic root hash | Old paths unchanged; explicit ID rename/rebinding contract | Prevents host disclosure and root injection | Validate finite IDs/registry and reject overlapping roots |
| Qualified note pair | Globally unique filenames, title/alias identity, rewriting old paths | Legacy identity remains default-relative | Equal relative paths cannot misroute reads/writes | Qualify candidates, jobs, review and derived results |
| Reserved legacy default | First configured/first readable/sole remaining space | No mandatory migration or accidental scope expansion | No fallback to a private alternate space | Normalize legacy/named configuration; fail conflicts |
| Finite deployment-wide policy boundary | Per-adapter checks, arbitrary ACL/DSL, user RBAC | Existing auth and global write opt-ins preserved | One decision before any owner access | Shared immutable authorized bindings; no policy DB |
| Up to eight read spaces, one write space | All-space wildcard, single-space-only reads, multi-space writes | Only explicit future calls gain multi-space scope | Bounded fan-out; no guessed destination | New bounds and composition contract before runtime |
| Independent derived indexes | Shared compound-key DB, switched-root index | Legacy DB stays in place and compatible | Avoids loading non-selected chunks | Bounded federation and storage/lifecycle validation gates |
| Local links; defer cross-space syntax | Global name search, guessed space-qualified syntax | Existing link semantics preserved | No existence/backlink leakage across roots | Separate explicit cross-space-link contract if required |
| Same-space promotion and logical provenance | Automatic move, approved cross-space promotion now | Legacy provenance/decision digests untouched | Approval cannot replay in another namespace | Portable format/version and replay-safety contract needed |
| One-space hygiene | Multi-space scan/global alias or isolation analysis | ADR 0008 semantics unchanged | No hidden global comparisons or private peer counts | One logical scan envelope in future adapters |
| Explicit partial availability, fail authorization | Silent fallback/drop, all-or-nothing availability | Old one-space behavior remains intact | Denied/unknown selection never contributes data | Typed coverage and deterministic merge; no full-result guarantee |

The design trades a small bounded registry and federated composition for explicit identity and
policy checks at every existing entry point. It adds no service, runtime dependency, authentication
scheme, distributed coordinator, or authoritative store. Separate derived indexes reduce migration
risk but need bounded resource scheduling and cannot promise transactional cross-root consistency.

Deferred questions are gates rather than invitations to implement: operator configuration encoding;
precise owner discovery/chunk budgets and cancellation; semantic merge precision and compatibility
validation; worker/model resource reuse; derived-data/root-binding validation; portable provenance
version and old-artifact migration; final scope-aware adapter/resource/error contracts; and any
explicit cross-space link syntax. VB-151/VB-152 remain unscoped future tasks pending authoritative
contracts. No new performance or live-runtime evidence is claimed by this documentation.

## Non-goals

- Multi-tenancy, SaaS tenant control plane, user/group administration, RBAC dashboard.
- Arbitrary roots or caller-supplied host paths, public filesystem administration.
- Per-folder policy language, arbitrary expressions, hot-reloaded grants or persistent policy store.
- Hidden proprietary knowledge storage or a database becoming authoritative over Markdown.
- Unbounded cross-space search, new graph ranking, cloud embeddings or new model/index formats.
- Automatic note moves, note migration, cross-space promotion/repair, backlink insertion, mutation
  from advisory query/duplicate/hygiene evidence, or new retention/delete operations.
- VB-151 runtime, VB-152 adapters, changes to current configuration/runtime behavior or tests.
- Release, deployment, commit, reviewer launch, or completion of Milestone 16.

## Acceptance and sequence

VB-150 delivered this accepted design and consistent design/task/status documentation with docs-only
verification and a review packet. Independent design review returned APPROVE; a packet alone is not
ADR acceptance. VB-150 is complete; this accepted ADR is authoritative for Milestone 16 design. Define VB-151's
exact domain/runtime and compatibility gates separately before implementation, then VB-152's thin
adapter adoption separately. Do not infer authorization from numbering or roadmap order.
