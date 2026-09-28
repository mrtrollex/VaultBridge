# ADR 0006: Bounded Knowledge Query capability

- **Status:** Accepted
- **Date:** 2026-09-27
- **Task:** VB-120

## Context

ADR 0005 and VB-111 through VB-114 establish one portable, read-only domain foundation over
authoritative Markdown: verified canonical paths, bounded frontmatter, portable aliases and tags,
and live normalized relationships. `SemanticSearchService` separately owns the established
embedding, hybrid-ranking, index-compatibility, and search-availability contract. Clients do not yet
have one domain capability that composes those facts without duplicating their semantics or exposing
storage internals.

Milestone 13 needs that composition boundary before runtime or adapter work begins. The boundary must
remain finite and testable, tolerate a semantic index that legitimately trails current Markdown,
and avoid claiming an atomic vault-wide filesystem snapshot that the current architecture does not
provide.

## Decision

### Authority and ownership

Markdown remains the authoritative knowledge source. Frontmatter objects, portable-field
projections, normalized relationships, semantic chunks and embeddings, query views, caches, and any
later query-oriented projection are derived and rebuildable. None may become the only copy of
user-authored knowledge.

Existing owners remain authoritative:

- `VaultService` owns containment, traversal and symlink protection, exact spelling, verified
  canonical vault-relative paths, Markdown-only checks, configured note-size enforcement, UTF-8
  reads, and deterministic live-note enumeration;
- the existing frontmatter and portable-field code owns VB-111/VB-112 parsing, scalar typing,
  alias/tag projection, bounds, and absent/valid/invalid semantics;
- `RelationshipService` owns normalized relationship composition and the supported dialects'
  resolution, origin, occurrence, ordering, and duplicate semantics;
- `SemanticSearchService` owns semantic availability, embedding, the accepted hybrid-ranking
  calculation, per-note selection, relative floor, and score ordering.

Knowledge Query is one client-agnostic domain orchestration boundary over those owners. It may
coordinate their immutable results and apply the finite predicates below, but it may not reimplement
containment, metadata parsing, relationship resolution, or semantic ranking. This decision does not
require or name a Python class or module; VB-121 must introduce only the smallest runtime boundary
that the implementation needs.

### Immutable conceptual request and result

The conceptual request is one immutable value:

```text
KnowledgeQueryRequest
- semantic_text: optional string
- literal_text: optional string
- folder: optional vault-relative directory scope
- paths: ordered tuple of exact vault-relative Markdown paths
- tags: ordered tuple of required portable tag values
- metadata: ordered tuple of MetadataPredicate
- relationships: ordered tuple of RelationshipPredicate
- limit: positive visible-result limit

MetadataPredicate
- key: exact top-level frontmatter key
- operator: exists | equals | not_equals | sequence_contains
- value: omitted for exists; otherwise one portable scalar

RelationshipPredicate
- direction: outgoing | incoming
- other_path: exact verified canonical Markdown path
- origin: optional obsidian_wikilink | markdown_link
```

Omitted optional text or scope fields and empty predicate tuples add no constraint. A text-free
request is valid when it supplies at least one folder, path, tag, metadata, or relationship
constraint. A request with no effective constraint is invalid; Knowledge Query is not an unbounded
list-all replacement. Both `semantic_text` and `literal_text` may be present.

Creation time, modification time, title, alias, heading, and adapter/lifecycle options are not
request predicates in the initial capability. Filesystem creation time is not a portable Markdown
fact, and adding any of these predicates requires a later explicit contract.

The conceptual immutable result is:

```text
KnowledgeQueryResult
- matches: ordered tuple of KnowledgeQueryMatch
- ordering: semantic | canonical_path
- semantic_index_basis: none | compatible_ready | compatible_previous_refresh |
  compatible_previous_error

KnowledgeQueryMatch
- canonical_path: verified live vault-relative Markdown path
- final_score: optional existing hybrid score
- semantic_score: optional existing semantic score
- lexical_score: optional existing lexical score
```

The three scores are present together only for semantic ordering and retain the existing
`SemanticSearchService` meanings. They are absent for canonical-path ordering. The index-basis fact
is bounded lifecycle information, not a promise that indexed content equals the latest Markdown.
Content, metadata values, relationship details, or excerpts are not required result fields; a later
adapter contract may expose separately approved bounded fields without changing canonical identity.
These shapes are domain concepts, not public JSON, REST, MCP, CLI, or dashboard schemas.

### Text semantics

`semantic_text` selects the existing `SemanticSearchService` retrieval mode. It is embedded exactly
as supplied and uses the current semantic threshold, lexical components, hybrid weights, per-note
aggregation, relative floor, and score ordering. “Semantic” therefore means the accepted hybrid
semantic-search contract, not a new pure-vector or metadata-aware ranking algorithm.

`literal_text` is an exact, case-sensitive Unicode code-point substring constraint over the complete
current decoded Markdown returned by `VaultService`, including valid or invalid frontmatter source.
It performs no case folding, Unicode normalization, tokenization, stemming, regular expression,
glob, Markdown rendering, or ranking. It is always a live filter. When semantic text is also
present, literal matching does not add score or alter ranking weights.

An empty semantic or literal string is invalid when that field is supplied. Whitespace is content;
the request is not silently trimmed into a different query.

### Folder and path scope

Folder and path inputs remain vault-relative and are validated through `VaultService`. Absolute
paths, traversal, unsafe or escaping symlinks, non-Markdown path entries, and over-bound values make
the request invalid without revealing host details. Exact note paths and existing folders require
their discovered spelling and are represented internally by canonical forward-slash vault-relative
paths.

Folder scope is recursive and segment-aware: `Projects` includes contained Markdown below that
directory, not a sibling such as `Projects-old`. `paths` is the one finite disjunction: a note may
match any listed path. When both are supplied, a listed path must also lie in the folder. Duplicate
scope paths have no effect after stable exact deduplication. A safely formed missing folder or note
path matches nothing; it does not broaden the query and is not an unsafe-path error.

The vault root is expressed by omitting `folder`; an explicitly supplied empty folder is invalid and
cannot by itself turn an otherwise empty request into a list-all query.

### Tag predicates

Every supplied tag is required. Matching uses only the VB-112 `tags` projection from valid
frontmatter. The query value and each stored occurrence have surrounding whitespace removed using
the same portable-field usability rule, then compare by exact Unicode code points. Matching is
case-sensitive, performs no Unicode normalization or hierarchy expansion, and gives `#` no special
meaning. An author-written leading `#` therefore matches only the same leading `#` in the request.

Repeated stored tags and repeated identical query tags do not change the existence result; query
duplicates are stable-deduplicated after trimming. An empty-after-trimming request tag is invalid.
Absent, invalid, or unusable tags do not satisfy a tag predicate. Aliases and titles are never
consulted.

### Bounded frontmatter predicates

Metadata predicates address one exact top-level mapping key only. Keys are case-sensitive raw
Unicode strings; dots, brackets, slashes, and other characters have no traversal meaning. Nested
keys, arbitrary YAML paths, and recursive matching are not supported.

The supported predicate behavior is:

- `exists` matches when valid frontmatter contains the exact top-level key, regardless of the
  supported portable value stored there;
- `equals` requires a scalar field and a scalar request value of the same portable type with equal
  value;
- `not_equals` requires a present scalar field of the same portable type and an unequal value;
  absence, a type mismatch, or a container does not match;
- `sequence_contains` requires a top-level sequence and matches when at least one immediate scalar
  member has the same portable type and value as the request scalar.

Request values are limited to the VB-111 portable scalar set: string, null, boolean, integer, or
finite floating-point number. Boolean and integer values do not compare as the same type; nor do
integer and floating-point values. Strings use exact Unicode code-point equality with no trimming or
normalization. Mappings and nested sequences are queryable only by `exists`. Sequence membership is
not recursive. Multiple predicates, including predicates for the same key, all compose with AND.

Request integers are limited to the signed 64-bit range and request floating-point numbers to finite
IEEE 754 binary64 values. Larger stored VB-111 integers remain valid authoritative metadata and may
still satisfy `exists`; the query boundary does not coerce or truncate them.

A generic predicate whose key is `title`, `aliases`, or `tags` still evaluates only the exact
VB-111 top-level value under the operator rules above. It does not invoke title precedence,
VB-112 tag matching, alias lookup, or relationship resolution. The dedicated `tags` request field is
the only portable-tag predicate, and aliases are never resolution keys in Knowledge Query.

Absent or invalid frontmatter satisfies no metadata predicate. Unsupported operators, missing or
extraneous predicate values, unsupported value types, and over-bound keys or values invalidate the
request rather than being ignored. Query evaluation never coerces, stringifies, repairs, or silently
reinterprets a VB-111 value.

### Normalized relationship predicates

Relationship predicates operate only on resolved normalized `note_link` occurrences owned by
`RelationshipService`. `other_path` is verified through `VaultService` and denotes canonical target
identity, never a written target, title, alias, or filename heuristic.

- `outgoing` matches a candidate note when one normalized occurrence from that candidate resolves
  to `other_path`;
- `incoming` matches a candidate note when one normalized occurrence from `other_path` resolves to
  the candidate's canonical path;
- an omitted origin accepts either currently supported origin; a supplied origin requires that
  exact normalized origin.

Occurrences with `missing`, `ambiguous`, or `unsafe` resolution never satisfy a predicate. Duplicate
occurrences do not change the existence result. Fragment, label, written target, source order,
origin metadata, unresolved reason, and future relationship types are not queryable in the initial
capability. Multiple relationship predicates compose with AND. A safely missing `other_path`
matches nothing; an unsafe path invalidates the request.

### Constraint composition and ranking

All non-scope constraints compose with AND. All tags are required, all metadata predicates are
required, and all relationship predicates are required. The path tuple's bounded OR and the
optional relationship-origin choice are the only disjunctions. There is no general NOT, nested
grouping, precedence, or recursive boolean expression.

Without `semantic_text`, qualifying live notes sort by canonical path using `(path.casefold(),
path)`. Literal text remains a filter and never changes this order.

With `semantic_text`, live path, literal, tag, metadata, and relationship facts define the eligible
canonical-path set before semantic selection. `SemanticSearchService` ranks only the intersection
of that live eligible set and compatible indexed notes. Applying the live predicates only after a
globally truncated semantic result is not conforming because it can hide higher-ranked eligible
notes. Within the eligible set, the existing score calculation, thresholds, relative floor,
per-note selection, and tie-break chain remain unchanged: final score, semantic score, lexical
score, then canonical path. ADR 0006 does not change weights, model, chunking, embedding input,
thresholds, index signature, or persistence.

VB-121 may request a bounded semantic candidate window to tolerate notes disappearing during final
verification. It is at most `min(500, max(limit * 5, limit))`, is taken after eligibility filtering,
and is consumed once; there is no unbounded widening or retry loop. A race may therefore yield fewer
than `limit` results. The visible result tuple is always capped by `limit`.

### Live Markdown and semantic-index consistency

Path existence, content, literal matching, frontmatter, portable tags, and normalized relationships
are evaluated from current `VaultService`-verified Markdown. Query evaluation starts from one
deterministically ordered live-path enumeration. Each candidate's content-dependent live predicates
must be derived from one verified read of that candidate where the existing owners permit it;
relationship resolution may use one bounded immutable candidate snapshot as established by ADR
0005. A path is verified again before inclusion.

There is no atomic vault-wide filesystem snapshot. Files may appear, disappear, or change between
enumeration, independent reads, relationship scans, semantic ranking, and final verification.
Unreadable, changed-to-unsafe, missing, oversized, or invalid-UTF-8 candidates are conservatively
omitted. Evaluation does not substitute cached metadata for a failed live read and does not disclose
the filesystem cause.

Semantic facts may be older than those live facts. A live note absent from the compatible index
cannot appear in a semantic query; a stale indexed note that is no longer a verified live eligible
note cannot appear. A modified live note may be filtered using its current Markdown while its score
still reflects a previously committed compatible chunk. The result's semantic index basis reports
that bounded fact without claiming content freshness.

A compatible previously completed index remains usable while refresh is in progress and after a
failed refresh under the existing lifecycle contract. If no compatible searchable index is
available, a semantic query fails with `semantic_unavailable`; it does not silently fall back to
literal or canonical ordering. Queries without semantic text remain independent of semantic-index
availability.

### Bounds and failure behavior

VB-121 must enforce these request and execution bounds before expensive evaluation:

- default visible `limit` `20`; maximum `100`;
- maximum `64` exact path scopes;
- maximum `16` tag constraints;
- maximum `16` metadata predicates;
- maximum `16` relationship predicates;
- maximum semantic text size `4,096` UTF-8 bytes;
- maximum literal text size `8,192` UTF-8 bytes;
- maximum folder, path, or relationship-path value size `1,024` UTF-8 bytes;
- maximum metadata key size `256` UTF-8 bytes;
- maximum tag or string predicate value size `1,024` UTF-8 bytes;
- metadata request integers in the signed 64-bit range and finite binary64 floating-point values;
- maximum semantic candidate window `500`, further limited by the formula above.

The existing `VaultService.max_note_bytes`, VB-111 frontmatter limits, VB-112 portable-field limits,
and relationship-parser limits remain authoritative and are not raised here. Request collections
are immutable and bounded before stable deduplication; duplicates cannot be used to evade cardinality
limits.

Malformed shapes, unknown fields or operators, incompatible operator/value combinations, non-finite
numbers, empty supplied text, an empty query, and exceeded bounds are invalid requests. Unsafe scope
is a distinct safe domain failure. Semantic unavailability is a distinct retryable capability
failure. Safely missing scopes or relationship paths produce no matches. Invalid/absent metadata or
unresolved relationships simply fail the applicable predicate. Per-note read, parse, race, or safety
failures omit that note and may contribute only bounded reason codes or counts.

Diagnostics and logs must not contain absolute host paths, symlink destinations, exception strings,
query or literal text, tag or metadata values, note content, SQL fragments, embeddings, or storage
implementation details. Stable reason codes and canonical vault-relative paths may be used only
where the existing privacy boundary permits them.

### Query-language boundary

Knowledge Query accepts only the finite typed request above. It explicitly rejects:

- arbitrary SQL or SQLite/FTS syntax passthrough;
- arbitrary filesystem predicates, host-path globs, or regular expressions;
- YAML-path, JMESPath, JSONPath, XPath, or similar arbitrary path execution;
- user-defined functions or expressions;
- an unbounded or recursively nested boolean DSL;
- adapter-specific independent query semantics.

Extending the predicate or operator set requires a later explicit domain-contract change with new
bounds and compatibility tests.

### Adapter boundary

VB-120 adds no REST/OpenAPI endpoint or schema, operation ID, MCP tool/resource, CLI command, or
dashboard behavior. Future adapters must call the one domain capability and must not independently
parse metadata, resolve relationships, filter notes, or rank results. Public adoption belongs to
VB-122 or another separately accepted BACKLOG contract. This ADR does not commit a JSON field name,
endpoint path, MCP name, CLI syntax, or dashboard interaction.

### Persistence and infrastructure

VB-120 authorizes no database or schema change, stored graph, metadata index, vector database,
Qdrant, Redis, Celery, task queue, external service, background worker, mandatory cache, or query
parser dependency. It also adds no write behavior.

If VB-121 measurements show that a derived query projection is necessary, that projection must be
fully rebuildable from Markdown, carry an explicit source/version fingerprint and safe
invalidation/compatibility contract, and be separately justified by repeatable measurements. It
cannot become authoritative or silently alter the accepted semantic index signature.

### VB-121 implementation and evaluation gate

VB-121 is the next proposed task, but it requires its own authoritative BACKLOG contract. That
contract must implement this decision without reopening its core semantics and must use fake/local
embedders in tests rather than downloading a model.

Evaluation must cover, independently and in combinations:

- semantic-only and literal-only queries;
- recursive folder scope and exact path scopes, including missing and unsafe inputs;
- exact tags, whitespace, case, Unicode, `#`, and duplicates;
- every metadata operator and supported scalar type, sequences, absent/invalid frontmatter,
  unsupported values, and top-level-only keys;
- incoming/outgoing normalized relationships, both origins, canonical identity, unresolved
  occurrences, duplicates, and excluded fragments/labels;
- mixed AND constraints and text-free queries;
- deterministic non-semantic ordering and semantic tie-breaking under reversed source/repository
  order;
- unreadable, missing, changed, oversized, invalid-UTF-8, and unsafe notes without private-detail
  leakage;
- ready, initial-unavailable, refreshing-with-previous, and failed-with-previous semantic states,
  including live metadata/relationship changes newer than the index;
- candidate/result/cardinality and text-size bounds;
- representative English and Slovak semantic queries where relevant.

The evaluation must run the existing deterministic retrieval suite and show no regression from the
accepted VB-022/VB-024 baseline unless a separately approved ranking decision explicitly justifies
and records one. It must also prove that public REST/OpenAPI, MCP, CLI, dashboard, persistence,
index-signature, and write behavior remain unchanged.

## Consequences

### Positive

- One bounded domain contract composes current owners without making an adapter or database the
  source of query semantics.
- Live structural correctness is not lost behind a globally truncated semantic candidate list.
- Stale-but-compatible semantic operation is explicit and conservative while current Markdown
  remains authoritative.
- Finite predicates, exact bounds, and deterministic ordering make VB-121 implementable and
  testable without a general query language.

### Costs and limitations

- Semantic queries can omit new live notes that are not yet in the compatible index, and modified
  notes can temporarily carry an older semantic score.
- Live metadata and relationship evaluation can require bounded parsing and vault scans; performance
  must be measured before adding a projection.
- Filesystem races can produce fewer results than requested because VaultBridge does not claim an
  atomic vault snapshot or perform unbounded retries.
- The initial predicate set intentionally excludes nested metadata, unresolved relationship search,
  fragment/label search, OR groups, regex, and application-specific tag interpretation.

## Rejected alternatives

- **Filter only a truncated semantic result:** rejected because eligible higher-ranked notes can be
  lost before live constraints are applied.
- **Silently fall back when semantic search is unavailable:** rejected because it changes query
  meaning and ordering without caller consent.
- **Use SQLite or a graph store as the knowledge authority:** rejected because Markdown must remain
  independently usable and all projections rebuildable.
- **Expose a generic expression language:** rejected because it weakens bounds, portability,
  privacy, and adapter consistency.
- **Let each adapter compose its own query:** rejected because filtering, relationship, metadata,
  and ranking semantics would drift.
- **Add a runtime `KnowledgeQueryService` in the ADR task:** rejected because VB-120 decides the
  contract only; runtime shape and measured implementation belong to VB-121.

## Implementation sequence

ADR acceptance completes VB-120. VB-121 may next receive an authoritative BACKLOG contract for the
bounded domain runtime and evaluation described here. VB-122 or another separately approved task may
then adopt that single domain capability in selected adapters. Neither later task is authorized by
roadmap sequence alone.
