# ADR 0005: Portable PKM document model

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decision owner:** VB-110
- **Implementation tasks:** VB-111 through VB-114

## Context

VaultBridge is a client-agnostic, self-hosted knowledge layer for Markdown-based personal knowledge
systems. Portable Markdown and portable metadata are authoritative. Obsidian is an important
supported workflow, but its application model is not the VaultBridge domain model.

### Current implementation

The current implementation is intentionally concrete:

- one configured Markdown root is exposed through the compatibility concepts `VaultService`,
  `VAULT_PATH`, the `/vault` deployment mount, current REST schemas, MCP Resource URIs, the CLI, and
  the dashboard;
- `VaultService` owns contained vault-relative Markdown paths, canonical path verification,
  Markdown-only restrictions, symlink safety, and read/write size boundaries;
- `WikilinkResolver` parses the supported Obsidian-compatible wikilink dialect and resolves only an
  exact, unambiguous target verified through `VaultService`;
- `RelationshipService` derives outgoing wikilinks and backlinks from live Markdown;
- `SemanticSearchService` has its own ATX-heading interpretation for chunking and persists
  rebuildable chunks and embeddings through `SemanticRepository`;
- duplicate candidates, relationship projections, diagnostics, and search scores are derived
  evidence rather than document identity.

There is no generic portable document object, frontmatter reader, alias or tag resolver, standard
Markdown-link relationship parser, or normalized multi-dialect relationship view in the runtime
today. This ADR defines shared semantics for that future work. It does not claim that the conceptual
types or fields below already exist.

## Decision

### Accepted domain semantics

VaultBridge operates on a **Markdown knowledge space**. Its authoritative unit is a contained
Markdown document. The portable domain can conceptually express:

```text
KnowledgeDocument
- canonical identity
- path
- title
- aliases
- headings
- frontmatter metadata
- tags
- links
- relationships
- content
```

This is a domain vocabulary, not a requirement for one giant runtime or persisted object. An
operation should materialize only the fields it needs. A path verifier may produce identity alone;
a metadata operation may add frontmatter fields; relationship work may inspect links without
building a complete document; semantic indexing may retain only its compatible derived chunks.

Future implementations must extend the responsible existing layer or introduce a small
domain-owned component where the new invariant belongs. Adapters must not independently parse or
reinterpret document semantics.

## Document identity

### Canonical identity and path

The canonical identity of a current VaultBridge Markdown document is its verified, vault-relative,
canonical Markdown path, for example:

```text
Projects/VaultBridge.md
Notes/Oracle/APEX.md
```

`VaultService` remains authoritative for deciding whether that path names a valid, live, contained
Markdown file and for returning its canonical vault-relative spelling. An absolute host path is a
private implementation detail and is never document identity.

Canonical identity is not a title, display title, alias, frontmatter `id`, semantic embedding,
wikilink target string, Resource URI, or adapter identifier. A move or rename can therefore change
the current canonical path identity; this ADR does not introduce a path-independent identity or
rename history.

### Titles

The filename stem is the safe default title and current display baseline. Future supported
frontmatter may provide a metadata title for display or lookup, but a metadata title must not
silently replace canonical filesystem identity. Operations and adapters must state whether a value
is a canonical path, filename-derived title, or metadata display title rather than overloading one
field with all three meanings.

### Aliases

Aliases are lookup and resolution metadata, not canonical identities. They may be absent, repeated,
or claimed by multiple documents. Resolution must represent no match, one verified match, or
ambiguity explicitly. It must never select an ambiguous alias by filesystem enumeration, lexical
ordering, semantic similarity, or another arbitrary tie-breaker. Alias lookup cannot bypass
`VaultService` verification or weaken containment.

## Authoritative document data

The authoritative representation is the Markdown file itself, including:

- its contained canonical Markdown path;
- Markdown content;
- supported frontmatter stored in that content;
- explicit headings, aliases, tags, and links expressed in that content.

Parsing does not transfer ownership of these facts into SQLite or an adapter. Unsupported or
malformed syntax remains authoritative file content even when VaultBridge cannot expose a
structured interpretation of it.

## Frontmatter and portable metadata

YAML frontmatter, when VB-111 implements it, is optional authoritative Markdown content. It must be
parsed using a safe, bounded profile. It is data: it is not executed, imported as code, trusted as
application configuration, or permitted to alter containment, authentication, runtime settings,
deployment behavior, or security policy.

### Understood PKM fields

Future capabilities may intentionally understand reserved portable fields such as `title`,
`aliases`, and `tags`. Each understood field needs an explicit type, normalization, malformed-value,
and ambiguity contract in its implementation task. Recognizing a field does not make it canonical
identity or executable behavior.

### Generic portable fields

Other supported safe scalar, list, or map values may later be exposed as generic metadata through a
Knowledge Query Layer. Generic metadata remains inert data. This ADR does not define an unbounded
query language, arbitrary expression evaluation, dynamic configuration, or automatic note
mutation.

### Required VB-111 decisions

VB-111 must select and test the safe parser/profile and specify bounded behavior for:

- opening and closing delimiters and content that only resembles frontmatter;
- malformed YAML and duplicate keys;
- nesting depth, collection sizes, scalar/string sizes, and total metadata size;
- unsupported YAML values, tags, directives, aliases/anchors, merge keys, and other features;
- timestamp/date and other implicit scalar coercion;
- invalid or unsupported text encoding and parser errors;
- the dependency choice, its safe-loading API, maintenance impact, and failure mapping.

Exact numerical limits belong in VB-111, where they can be justified against the existing maximum
note size and parser behavior. They must be finite and tested. Unsafe or general YAML object
deserialization is prohibited.

A metadata failure must be deterministic and explicit to the calling domain operation. It must not
produce a partially trusted identity, guess between duplicate keys, change filesystem access, or
make the underlying Markdown unavailable to operations whose contract does not require valid
metadata. Whether a particular unsupported construct rejects the whole metadata projection or one
field is a VB-111 contract decision, not accidental parser behavior.

## Tags

Tags are portable knowledge metadata. Future work may recognize frontmatter tags and inline
Markdown tags. Supported sources should feed one conceptual tag representation so consumers do not
need independent tag subsystems, while retaining origin/dialect and source value when required for
correct interpretation or diagnostics.

Normalization must be explicit and conservative. Case, Unicode, hierarchy separators, whitespace,
and application-specific conventions must not silently merge materially different values. Obsidian
UI behavior is not authoritative. VB-112 owns the first concrete supported sources, normalization,
duplicate behavior, and malformed-value contract.

## Headings

Source Markdown headings are authoritative content. Heading hierarchy attached to semantic chunks
is a rebuildable interpretation used by the existing semantic index. Future portable document
heading information is another operation-specific projection of the same source, not a second
authoritative heading store.

The current semantic chunker recognizes ATX headings outside fenced code and builds bounded
hierarchy labels. Future PKM work should reuse one clearly specified Markdown interpretation or a
shared parsing component where practical. It must not introduce an incompatible heading parser
merely to populate the conceptual document model. A change to semantic heading/chunk behavior still
requires the existing index-signature compatibility discipline.

## Links and relationships

Wikilinks remain the first supported relationship dialect. Current behavior remains:

```text
WikilinkResolver
        ↓
RelationshipService
        ↓
verified outgoing links / backlinks
```

Future contained standard Markdown links must enter the same broader relationship domain rather
than form an independent subsystem. The normalized concept is approximately:

```text
KnowledgeRelationship
- source path
- raw target reference
- resolved path, when any
- relationship kind
- origin / dialect
- resolution state
- optional source metadata
```

These names are illustrative rather than a persisted schema or required class signature. The
semantics are:

- the source path is a verified canonical Markdown identity;
- the raw target/reference may remain unresolved and is not itself identity;
- a resolved target is always a verified contained Markdown document;
- origin/dialect records how the source expressed the relationship, such as an
  Obsidian-compatible wikilink or future standard Markdown link;
- unresolved and ambiguous states are explicit; adding ambiguity to the broader domain does not
  retroactively change current public responses that expose unresolved wikilinks;
- optional metadata may include a heading fragment, display label, source location, or other
  dialect-specific facts only when the responsible parser can provide them safely;
- source order and occurrence/duplicate behavior remain part of each operation's explicit contract
  rather than being erased by normalization;
- external HTTP(S) URLs are not internal note relationships, and arbitrary filesystem links do not
  become knowledge relationships merely because Markdown can express them.

Relationship projections are derived from authoritative Markdown. A live calculation and a future
rebuildable cache are both derived representations; derived does not mean persisted.

## Dialects and capabilities

VaultBridge may use syntax-specific components for bounded capabilities such as:

- Obsidian-compatible wikilinks;
- future contained standard Markdown internal links;
- future portable frontmatter conventions.

Such components parse their declared syntax and feed shared document or relationship semantics.
They do not own filesystem authority, protocol presentation, or a separate knowledge store. This is
a lightweight capability boundary, not a generic plugin framework.

Markdown remains the product boundary. This ADR does not introduce `StorageProvider`, `PKMPlugin`,
`ObsidianProvider`, `NotionProvider`, `GoogleDocsProvider`, runtime plugin discovery, or a provider
abstraction for non-Markdown sources. Those abstractions require demonstrated product need and a
separate decision.

## Live and derived representations

### Authoritative/live source data

- contained Markdown files and their content;
- canonical Markdown paths;
- frontmatter stored in Markdown;
- explicit supported links, tags, aliases, titles, and headings stored in Markdown.

"Live" describes reading or parsing the current file, not a second category of authority. A live
parsed view is still a representation of authoritative Markdown.

### Derived/rebuildable data

- semantic chunks, heading hierarchy projections, and embeddings;
- semantic index rows and compatibility metadata;
- backlinks and normalized relationship projections;
- duplicate candidates and hygiene diagnostics;
- search, similarity, lexical, and future graph-derived scores;
- caches and other machine-oriented views.

Derived data must remain reconstructible from authoritative Markdown plus application configuration
where feasible. Some derived views are calculated live and never stored. Persistence does not make
a derived value authoritative, and absence of persistence does not make a projection authoritative.

## Persistence

VB-110 adds no persistent portable-document model, metadata database, relationship index, or graph
database. The conceptual model alone is not evidence that one is needed.

Future work should prefer live parsing where it is cheap, operation-specific in-memory values, and
existing derived SQLite structures where their current purpose already justifies them. New
persistence requires measured cost or capability evidence plus an explicit rebuild, compatibility,
invalidation, migration, and ownership strategy. Authoritative PKM semantics must remain
reconstructible from Markdown; a derived store must not become a prerequisite for using the vault.

## Security and containment

`VaultService` remains responsible for:

- containment and canonical vault-relative path verification;
- Markdown-only file restrictions;
- symlink safety;
- note size and read/write boundaries.

A metadata, tag, heading, or link parser is never a filesystem authority. It may produce a target
candidate, but only `VaultService` can verify the resulting contained Markdown identity.
Frontmatter titles, aliases, tags, link destinations, custom values, and dialect markers cannot
escape the knowledge-space root or authorize a file operation. Absolute host paths remain private.

Parser-specific limits supplement rather than replace the whole-note boundary. No metadata value
may change runtime configuration, security policy, authentication, mounts, parser limits, or code
execution merely because it occurs in Markdown. This decision adds no arbitrary filesystem API.

## Compatibility

This ADR changes no runtime behavior and introduces no migration or dependency. It preserves:

- all current REST endpoints, schemas, operation IDs, authentication, and response semantics;
- all current MCP tools, Resources, URI behavior, transports, and write-enable behavior;
- the current CLI and dashboard;
- `VaultService`, `VAULT_PATH`, one configured root, and the `/vault` deployment mount;
- the semantic SQLite schema, model, chunking, ranking, index signature, and rebuild behavior;
- existing Obsidian-compatible wikilink parsing, resolution, ordering, ambiguity safety, outgoing
  relationships, and backlinks;
- current Docker, Compose, TrueNAS package, configuration, and runtime behavior.

Roadmap vocabulary does not rename compatibility identifiers. Later tasks must make any new public
surface or migration an explicit, independently reviewed decision.

## Future implementation sequence

```text
VB-110 portable PKM model / ADR
   ↓
VB-111 bounded YAML frontmatter parsing
   ↓
VB-112 portable aliases and tags
   ↓
VB-113 contained standard Markdown relationships
   ↓
VB-114 normalized relationship view
```

- VB-111 owns the safe bounded YAML profile and metadata parse/failure representation.
- VB-112 owns concrete title/alias/tag field semantics and ambiguity-safe lookup inputs.
- VB-113 owns standard Markdown internal-link parsing and contained resolution through existing
  security and relationship boundaries.
- VB-114 owns the shared multi-dialect relationship view while retaining origin and explicit
  resolution state.

Each task remains independently scoped in `BACKLOG.md` before implementation. This ADR defines
dependencies and invariants, not complete implementation contracts for VB-111 through VB-114.

## Alternatives considered

### Keep Obsidian as the domain model

Rejected. Current functionality is fundamentally Markdown/filesystem based and protocol/client
agnostic. Useful Obsidian-compatible syntax remains supported without owning the core architecture.

### Build a generic PKM/provider plugin framework now

Rejected as premature. There is no demonstrated need for Notion, Google Docs, proprietary stores,
runtime provider discovery, or non-Markdown storage abstractions.

### Persist normalized document metadata immediately

Rejected. No measured need justifies another derived store, compatibility lifecycle, or source of
staleness merely to represent the conceptual model.

### Use a frontmatter field as canonical identity

Rejected. Frontmatter can be absent, duplicated, malformed, edited, or ambiguous and cannot
override contained canonical path identity.

### Treat aliases as unique identifiers

Rejected. Multiple documents may legitimately claim the same alias; pretending uniqueness would
create arbitrary resolution and unsafe client assumptions.

### Make every Markdown link a relationship

Rejected. A future parser must distinguish and verify contained Markdown destinations. External
URLs and arbitrary files are not internal knowledge relationships.

### Build a graph database

Rejected. Current measured relationship workloads do not justify a new service or persistent graph,
and authoritative relationships remain reconstructible from Markdown.

## Consequences

Positive consequences:

- Obsidian-compatible syntax remains useful without making Obsidian the architectural core;
- future metadata, query, relationship, and capture work receives stable portable semantics;
- canonical identity, display titles, and aliases cannot be accidentally conflated;
- wikilinks and future Markdown links can feed one relationship domain instead of duplicate systems;
- adapters can consume shared knowledge semantics while portable Markdown remains authoritative;
- persistence stays evidence-driven and derived state stays rebuildable.

Tradeoffs and limitations:

- canonical paths remain important even when metadata offers richer display and lookup concepts;
- malformed or ambiguous metadata must be surfaced conservatively instead of guessed;
- some application-specific PKM semantics remain intentionally unsupported;
- live parsing can cost more than a persisted projection;
- VB-111 and later tasks still need concrete parser, resource-limit, normalization, and public-surface
  decisions.

## Explicit non-goals

VB-110 does not implement:

- runtime document-model code or a YAML, tag, alias, or Markdown-link parser;
- alias resolution or runtime relationship normalization;
- REST, MCP, CLI, dashboard, semantic-ranking, or index-signature changes;
- SQLite schema changes, relationship persistence, or a graph database;
- multiple knowledge spaces, storage-provider plugins, Notion, or Google Docs support;
- arbitrary-file access or automatic note mutation;
- VB-034 backlink writes or VB-032/VB-033 section mutation;
- knowledge capture/memory or hygiene diagnostics.
