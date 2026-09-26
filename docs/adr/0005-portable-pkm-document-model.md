# ADR 0005: Portable PKM document model

- **Status:** Accepted
- **Date:** 2026-09-26
- **Task:** VB-110

## Context

VaultBridge currently treats contained Markdown files as authoritative, identifies notes by verified
vault-relative paths, derives semantic chunks into rebuildable SQLite state, and derives
Obsidian-compatible wikilink relationships live through `RelationshipService` and
`WikilinkResolver`. It does not yet have one domain representation for portable metadata, headings,
or multiple relationship dialects.

Milestone 12 adds that representation incrementally. The design must guide bounded frontmatter,
aliases, tags, standard Markdown links, and normalized relationships without turning SQLite into a
second knowledge store, weakening `VaultService`, or prematurely publishing a new protocol schema.

## Decision

### Authority and identity

One contained Markdown file is one portable PKM document. Its authoritative identity is its
`VaultService`-verified canonical vault-relative `.md` path. A title, alias, heading, link label,
database row, semantic chunk, inode, or absolute host path is never note identity.

The authoritative knowledge is the Markdown byte stream, including portable metadata written in
that file. Parsed objects, normalized relationships, indexes, embeddings, caches, and query views
are interpretations or projections. They must be reproducible from the current contained Markdown
and may not be the only copy of user-authored knowledge.

`VaultService` continues to own root containment, traversal and symlink-escape protection,
Markdown-only path validation, configured document-size enforcement, canonical relative paths, and
note reads. Parsers consume a verified read result; they do not resolve host paths or read files
independently.

### Conceptual document shape

The domain may expose an immutable in-memory document snapshot with this conceptual shape:

```text
PortableDocument
- canonical_path: verified vault-relative Markdown path
- content: authoritative decoded Markdown
- title: derived display title plus its source
- headings: ordered heading occurrences
- frontmatter: absent, valid bounded metadata, or invalid with safe diagnostics
- aliases: ordered portable alias values
- tags: ordered portable tag values
- relationships: ordered normalized relationship occurrences
```

This is a domain contract, not a required Python class, database table, REST/MCP schema, or promise
that every consumer will receive every field. VB-111 through VB-114 may introduce the smallest
types needed while preserving this meaning.

### Title semantics

The portable display title is the first usable value in this order:

1. a non-empty string in the valid top-level frontmatter `title` field;
2. the text of the first non-empty level-one Markdown heading in source order;
3. the canonical path's filename stem.

Whitespace at the ends of a candidate is ignored when deciding whether it is usable. The chosen
text otherwise remains user-authored text; title selection does not case-fold, slugify, or make the
title unique. Invalid frontmatter or an invalid `title` value falls through deterministically.
Aliases never override the title and neither titles nor aliases override path identity.

Current REST, MCP, CLI, dashboard, duplicate-candidate, and semantic-search title behavior remains
unchanged until a separately scoped task deliberately adopts this title projection and addresses
compatibility.

### Headings

A heading is an occurrence, not a unique key. Its portable representation contains:

- level `1` through `6`;
- user-visible heading text;
- zero-based source order among headings in the document;
- optional written fragment/identifier metadata when a supported dialect provides it.

Headings remain in source order and repeated identical headings remain repeated. A future derived
lookup may calculate dialect-specific slugs or disambiguators, but those are rebuildable resolution
aids, not canonical note or heading identity. Supporting a heading syntax or fragment algorithm
requires an explicit parser contract; this ADR does not silently equate application-specific slug
rules.

### Frontmatter profile and bounds

VB-111 must parse only a deliberately bounded, safe YAML frontmatter profile. It must not use a
general object constructor or an unsafe loader.

Frontmatter is recognized only at the start of the decoded document, after an optional UTF-8 byte
order mark. The opening line must be exactly `---`; the closing line must be exactly `---` or `...`.
Delimiter line endings may be LF or CRLF. An opening delimiter without a closing delimiter within
the frontmatter bound is malformed, not an instruction to hide the rest of the note. Text that does
not meet this envelope is ordinary Markdown.

The accepted YAML value is one document whose root is a mapping. It may contain nested mappings and
sequences within these limits:

- the existing configured `VaultService.max_note_bytes` remains the whole-document byte limit;
- frontmatter, including delimiters, is at most `65,536` UTF-8 bytes;
- a scalar's source representation is at most `8,192` UTF-8 bytes;
- a mapping key must be a string of at most `256` UTF-8 bytes;
- maximum container depth is `8`, counting the root mapping as depth `1`;
- the aggregate count of mapping entries and sequence items is at most `1,024`.

Limits are checked without unbounded expansion. Later tasks may choose lower bounds for a specific
portable field but may not raise these document-profile bounds without revisiting this decision.

The portable scalar set is JSON-like: string, null, boolean, integer, and finite floating-point
number. Mapping keys are strings. Timestamp/date objects, binary values, sets, non-finite numbers,
and other language-specific values are unsupported unless written as strings.

Implicit scalar resolution follows the YAML 1.2 Core Schema exactly. In particular:

- `true` and `false` resolve as booleans, and `null` and `~` resolve as null;
- ordinary YAML 1.2 Core numeric forms may resolve as integers or finite floating-point numbers;
- YAML 1.1 boolean-like words such as `yes`, `no`, `on`, and `off` remain strings;
- date-like plain scalars such as `2026-09-26` remain strings; timestamp/date typing requires a
  later separately approved capability, and the current profile rejects explicit tags;
- a leading-zero value such as `0123` never receives YAML 1.1 octal semantics: when resolved as an
  integer under the Core Schema its value is decimal `123`; authors must quote it when the lexical
  leading zero is significant.

Typed portable fields validate the resolved scalar type and never silently coerce an incompatible
value. In particular, a metadata `title` must resolve as a string. A boolean, number, or null
`title` is invalid for the title projection and falls through to the first usable level-one heading
and then the canonical filename stem under the title-precedence rule above; it is never stringified.
Parser or library defaults must be configured or validated against this contract and may not change
scalar interpretation across platforms, dependency versions, or implementations. This ADR fixes
the semantics but does not select a concrete YAML dependency for VB-111.

The following make the frontmatter invalid as a whole:

- invalid YAML or a non-mapping root;
- a duplicate key at any mapping depth, including duplicates introduced by merge behavior;
- a second YAML document;
- anchors, aliases, merge keys, explicit/custom tags, or executable/language-specific constructors;
- a non-string mapping key, unsupported scalar/container value, or any exceeded bound.

Unknown keys are not errors when their values fit the safe profile. They are retained in the
bounded generic metadata mapping so future clients do not require VaultBridge-specific YAML.
Preservation means preserving the parsed portable value and mapping/sequence order; it does not
promise comments, quoting style, anchors, or byte-for-byte YAML round-tripping.

Malformed delimiters, profile violations, and parse failures produce an invalid frontmatter state
with bounded diagnostics identified by stable reason and, where safely available, source location.
Diagnostics must not contain metadata values or note content in logs. No parsed metadata is exposed
from an invalid block. The note's Markdown content remains readable unless the existing document
boundary already rejects it for path, containment, size, I/O, or encoding safety. Parsing never
rewrites, truncates, repairs, or otherwise alters the Markdown.

### Portable aliases and tags

For the later VB-112 contract, `aliases` and `tags` are recognized only from valid frontmatter.
Each accepts either one string or a sequence of strings. A non-string member invalidates that
portable field, not the otherwise safe generic metadata mapping. Empty values are omitted from the
portable field with a diagnostic.

Each field is limited to `256` source values and each usable value to `1,024` UTF-8 bytes. Source
order and repeated values are preserved in the occurrence view. Consumers that need a set-like
view may stable-deduplicate exact strings after trimming surrounding whitespace; the first
occurrence wins. No case-folding, Unicode compatibility folding, hierarchy expansion, or automatic
`#` insertion/removal is part of the portable value.

Inline Markdown tags are not added by VB-110 or VB-111. Supporting them requires a later explicit
syntax contract. An alias is a lookup candidate only; if it identifies zero or more than one live
canonical path, resolution stays unresolved. Aliases never become an alternate canonical identity.

### Normalized relationships

All supported link dialects may feed an immutable normalized occurrence with this conceptual shape:

```text
RelationshipOccurrence
- source_path: verified canonical source path
- written_target: target exactly as represented by the dialect after delimiter parsing
- resolved_path: one verified canonical target path, or null
- resolution: resolved or an explicit unresolved reason
- origin: supported syntax/dialect, such as obsidian_wikilink or markdown_link
- relationship_type: portable semantic type, initially note_link
- fragment: optional written heading/fragment
- label: optional written display alias/label
- source_order: zero-based occurrence order in the source document
- origin_metadata: bounded dialect-specific facts not represented above
```

The normalized shape does not erase source semantics. For example, a wikilink display alias and a
Markdown link label share the portable `label` role while `origin` and bounded `origin_metadata`
retain distinctions required to reproduce or explain resolution. `written_target`, fragment, and
label remain user-authored occurrence data; `resolved_path` is verified derived data.

Relationship occurrences are returned in document source order and duplicates are preserved. A
consumer may request a normalized deduplicated view, but deduplication is stable-first-occurrence
and exact across source path, written target, resolved path or unresolved reason, origin,
relationship type, fragment, label, and origin metadata. Dialect-specific parsers must define their
own bounded syntax exclusions before feeding this shape.

Resolution is conservative and deterministic:

- containment and existence are verified through `VaultService`;
- path-qualified targets require exact discovered spelling under the existing policy;
- a title, alias, filename, or fragment that has multiple valid candidates is explicitly
  unresolved as ambiguous;
- missing, unsafe, external, unsupported, or malformed targets remain unresolved with a reason;
- no ranking, filesystem enumeration accident, or first-match rule may choose among candidates.

The existing VB-100 through VB-104 wikilink behavior remains the implemented contract. VB-113 may
add contained standard Markdown note links; VB-114 may introduce the shared normalized view. Both
must reuse `RelationshipService`, `WikilinkResolver` where applicable, and `VaultService` rather
than creating another containment or relationship owner.

### Live parsing and derived projections

The initial portable document representation is live and read-only:

- canonical path and content come from the verified current `VaultService` read;
- frontmatter, title, headings, aliases, tags, and relationships are parsed or derived from that
  same snapshot when the owning capability needs them;
- relationship resolution uses a bounded, immutable live candidate snapshot where a complete scan
  requires consistency.

The following may later be cached or projected when measurements justify it: parsed frontmatter,
heading lookup data, alias/tag lookup data, normalized relationships, and query-oriented metadata.
Every such projection is derived state, must carry enough source fingerprint/version information to
reject stale or incompatible data, and must be safely rebuildable from Markdown. This ADR does not
select persistence, a migration, a graph store, or an index format. Existing semantic SQLite data
and its signature remain unchanged.

### Encoding and deterministic failure behavior

Portable documents are UTF-8. A UTF-8 byte order mark is tolerated only at the beginning and is
ignored for frontmatter-envelope detection; it is not an instruction to rewrite the file. Invalid
UTF-8 follows the existing explicit note-read failure boundary and is never decoded with a
locale-dependent or replacement-character fallback. Parsers preserve authoritative line endings
and content and operate deterministically across supported platforms.

Metadata unavailability must not masquerade as an empty authoritative value. Domain consumers must
be able to distinguish absent frontmatter, valid empty metadata, invalid metadata, and an unavailable
document. Unsupported metadata disables only the affected derived capability unless continuing
would violate an existing safety boundary.

### Compatibility and ownership

VB-110 changes design documentation only. It does not change application code, dependencies,
OpenAPI, REST operation IDs, MCP tools/resources/transports, CLI or dashboard behavior,
`VAULT_PATH`, resource URIs, authentication, note writes, class names, semantic ranking, SQLite or
index formats, Docker/TrueNAS packaging, or release artifacts.

The existing names remain accurate compatibility boundaries. No generic `DocumentService`,
`MetadataService`, or `KnowledgeSpace` runtime abstraction is introduced merely to mirror roadmap
terminology. A later task may extend the responsible existing layer or add a small domain type when
the implemented behavior needs it.

## Consequences

### Positive

- Later metadata and relationship work has one conservative identity, authority, ordering,
  ambiguity, and failure contract.
- Safe unknown metadata can remain portable without making arbitrary YAML objects executable.
- Live Markdown stays usable when metadata is malformed, and all optional projections remain
  rebuildable.
- Existing containment and relationship ownership is preserved while allowing additional dialects.

### Costs and limitations

- The bounded YAML profile intentionally rejects valid but non-portable YAML features.
- Title and alias ambiguity requires explicit unresolved states and may produce fewer automatic
  matches than application-specific heuristics.
- Live parsing and relationship resolution may be costly; persistence or caching needs separate
  measurement, invalidation, migration, and compatibility work.
- This conceptual contract does not itself expose metadata to any client or implement VB-111
  through VB-114.

## Rejected alternatives

- **Make a database or graph store authoritative:** rejected because Markdown must remain usable
  independently and derived state must remain rebuildable.
- **Use titles or aliases as identity:** rejected because they are mutable and non-unique.
- **Accept unrestricted YAML:** rejected because constructors, aliases, deep structures, and large
  values violate the trust and resource boundary.
- **Drop malformed metadata or unreadable fields silently:** rejected because absent, invalid, and
  empty have different meanings.
- **Deduplicate relationships during parsing:** rejected because duplicate occurrences and source
  order are meaningful source facts.
- **Rename the current vault/domain services now:** rejected because it adds compatibility cost
  without implementing multiple knowledge spaces or resolving a current ownership problem.

## Implementation sequence

ADR acceptance completes VB-110. The next task is VB-111, limited to bounded safe frontmatter
parsing and its domain result. VB-112 may then add portable alias/tag projections, VB-113 contained
standard Markdown note relationships, and VB-114 the shared normalized relationship view. Each task
requires its own authoritative backlog definition before implementation and must not infer new
public APIs or persistence from this ADR.
