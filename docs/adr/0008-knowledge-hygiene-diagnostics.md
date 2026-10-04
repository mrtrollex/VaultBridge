# ADR 0008: Read-only Knowledge Hygiene diagnostics

- **Status:** Accepted
- **Date:** 2026-10-03
- **Task:** VB-140

## Context

ADR 0005 makes contained Markdown and its canonical vault-relative path authoritative. ADR 0006
establishes bounded read-only composition and live verification. ADR 0007 distinguishes inbox/draft
capture from operator-approved promotion. VB-131 and VB-132 are implemented and merged; promotion
leaves the capture intact and does not make a semantic match authoritative. Milestone 15 needs a
precise diagnostic vocabulary without changing those contracts or the one-root Markdown vault.

The current owners already expose normalized relationship reasons, bounded portable frontmatter
and alias/tag projections, advisory duplicate candidates, and semantic lifecycle/compatibility
evidence. Whole-vault editing remains possible outside VaultBridge, so diagnostics cannot promise
one transactional view of all files.

## Decision

### Authority and stages

Markdown bytes remain the source of truth. One `VaultService`-verified, contained canonical
vault-relative `.md` path is one note identity. An alias, title, inode, symlink spelling, database
row, or semantic result is not another identity. `VaultService` owns enumeration, exact spelling,
containment, safe reads, canonicalization, size checks, and symlink safety. `RelationshipService`
owns relationship parsing, normalized occurrence interpretation, and resolution. The ADR 0005
frontmatter and portable-field owners retain metadata semantics. `DuplicateCandidateService`
retains duplicate-candidate evidence; the existing related-note and `SemanticSearchService` owners
retain ranking and derived-index evidence. Hygiene may compose these owners, never create a second
parser, link resolver, filesystem security layer, duplicate detector, or ranking implementation.

Every result separates four stages:

1. **Observed fact:** an owner-observed state of a verified live Markdown snapshot, or an explicit
   compatible derived-index state. Unavailable evidence is not an observed absence.
2. **Diagnostic classification:** a deterministic rule below applied to those facts.
3. **Advisory evidence:** bounded context explaining the classification, with provenance and limits.
4. **Future repair action:** a separate operator decision and contract, never part of a finding.

A finding is informational. It cannot authorize or trigger mutation, even if all evidence agrees.

### Conceptual result and ordering

The future immutable `DiagnosticFinding` has this conceptual shape, not a required class or wire
schema:

```text
DiagnosticFinding
- kind: one stable diagnostic kind below
- primary_path: verified canonical vault-relative Markdown path, except vault-level index state
- related_paths: bounded distinct verified canonical paths, possibly empty
- evidence_source: live_markdown | derived_index
- category: stable owner reason or bounded rule category
- evidence: only the safe fields specified by the corresponding rule
```

Vault-level derived-index findings have no `primary_path`; they are keyed by kind and category.
Relationship evidence may include origin, `source_order`, and resolution reason, but no written
target, label, fragment text, or raw Markdown. Frontmatter evidence may include its stable reason
and bounded one-based line/column; portable-field evidence may include field name, reason, and
source index. Every `colliding_alias` finding includes one zero-based `source_index` from the
portable alias projection; `duplicate_alias_in_note` includes two. Alias evidence may also include
peer paths, total peer count, and a truncation flag, but not alias text or its comparison key.
Candidate findings may include the owner's `match_type` and a verified candidate path; scores,
snippets, headings, query text, and content are omitted.
Index evidence may include only the explicit lifecycle/compatibility category, not signatures,
SQL, model files, or timestamps presented as freshness guarantees. No arbitrary metadata is copied.

For the same completed scan evidence, sort paths by `(path.casefold(), path)` and findings by
`(primary_path is absent, primary_path.casefold(), primary_path, kind, category, evidence_source,
source_order if present else -1, alias_source_index if present else -1,
alias_comparison_key or "", related_paths)`, with the vault-level entry last.
The comparison key is an internal ordering field for alias findings only; it is never emitted or
logged. Related paths use the same path order. Deduplicate exact finding identities after owner
evidence is normalized: one relationship occurrence is keyed by primary path, kind, origin, and
source order; `colliding_alias` by kind, exact alias comparison key, and primary canonical path;
`duplicate_alias_in_note` by kind, exact alias comparison key, and primary canonical path; a
candidate pair by its two canonical paths and owner's match type; a note-level state by path and
kind; and index state by kind/category. Alias occurrence indices are evidence, never cross-note
identity fields. Do not collapse distinct relationship source occurrences into one guessed link.
Bounded output truncation follows sorting and reports that it happened. No severity, confidence, repair
priority, LLM judgment, or inferred canonical winner is part of the model.

### Relationship and isolation rules

Only `RelationshipService`'s verified `RelationshipOccurrence` with `relationship_type=note_link`
and supported origin counts. Its `resolution=missing`, `unsafe`, and `ambiguous` produce distinct
`missing_relationship_target`, `unsafe_relationship_target`, and
`ambiguous_relationship_target` findings at the source path. A resolved occurrence has one verified
canonical `resolved_path`. Raw-text guesses, unsupported/external syntax, labels, and unparsed text
do not create relationship findings. Existing resolution is note-level: a written heading or
fragment does not establish heading existence. No missing-heading diagnostic is authorized.

`isolated_note` means a verified live curated Markdown note with zero resolved normalized outgoing
note relationships **and** zero resolved normalized incoming note relationships across a complete
eligible scan. Count self-links on both sides; repeated occurrences still establish connectivity.
Unresolved-only outgoing links give zero resolved edges, so such a note can be isolated and also
have a relationship finding. Do not call it an orphan or infer that it has no useful content.
This classification is withheld when the scan is incomplete, a relevant source is unreadable or
racing, or relationship resolution is unavailable. For this rule, exclude valid `capture_state`
`inbox` or `draft` artifacts as intake. Do not infer state from `Inbox/Captures` path alone. An
absent `capture_state` is ordinary-note metadata absence and does not exclude isolation. An
explicit but invalid/unknown `capture_state`, or invalid frontmatter that prevents reading a
declared state, cannot prove intake or curated status and is excluded from isolation until
classification is reliable. No other generated/system-note class is defined by the portable
model. Existing `VaultService` exclusions for non-note directories apply. A contained symlink
alias resolves to one canonical note and is not a second node; unsafe symlinks are never nodes.

### Capture and advisory candidates

Valid inbox/draft captures remain eligible for fact-level diagnostics where the current safe read
and parsed state support them: malformed frontmatter, invalid portable fields, empty authored
body, and normalized relationship reasons. They do not receive `isolated_note` merely because
they have not been promoted. Candidate evidence for captures is optional and advisory. A
promoted destination is an ordinary canonical note unless its own valid metadata says it is an
intake artifact. Promotion provenance does not merge source and destination identities. Hygiene
does not change capture state, disposition, review, or promotion.

`duplicate_candidate` and `near_duplicate_candidate` are advisory labels over the existing
`DuplicateCandidateService` `exact_title` and `semantic` match types, respectively, after both
paths are reverified and self-results removed. The service's current exact-title rule concerns
filename stems and its semantic mode retains its existing ranking and availability behavior;
neither proves identical Markdown, identity, or a preferred note. Related-note evidence may be
shown only through its existing owner and finite result limit, never used as a new duplicate
algorithm. Preserve owner ordering as evidence before deterministic finding sort. Index
unavailability is explicit, and an empty candidate result is not a proof of uniqueness. No
automatic merge, delete, rewrite, winner/loser designation, or global mutation follows.

The existing `DuplicateCandidateService.find_candidates()` calls
`VaultService.live_markdown_paths()` for every request, so a limit on hygiene source notes or
returned results alone does not bound candidate work. VB-141 must add one small additive bounded
batch entry point owned by `DuplicateCandidateService`. It accepts at most 10,000 distinct,
`VaultService`-verified canonical candidate paths from the completed bounded hygiene enumeration,
builds its exact-title lookup once over that set, and answers at most 20 verified source notes
without another whole-vault enumeration per source. At most 5 candidates are returned per source.
The same owner retains title normalization, exact-title matching, merge ordering, limits, and
semantic thresholds; hygiene does not reimplement them. Optional semantic candidates may be
included only through an owner path that constrains actual index/chunk retrieval and ranking to
the bounded eligible set before loading or scanning it. Current `SemanticSearchService.search()`
filters `eligible_paths` after `load_chunks()` and is insufficient for this bound. Until a bounded
semantic owner path exists, omit `near_duplicate_candidate` evidence and report that candidate
coverage is partial. No repeated full-vault or full-index search for each source is permitted.

### Portable aliases and metadata

Only usable `aliases` occurrences from valid ADR 0005 frontmatter and a valid VB-112 portable
field participate. Compare `value.strip()` strings by exact Unicode code-point equality. Do not
normalize Unicode (including NFC/NFKC), case-fold, collapse interior whitespace, or fuzzy-match.
The portable field already omits empty values; surrounding whitespace is ignored for equality
only, while source indices remain those of original occurrences. Two equal usable occurrences
in one canonical note produce `duplicate_alias_in_note`, even if no other note has that alias.
For each exact comparison key, collect distinct canonical participating note paths. If at least
two distinct paths participate, emit exactly one `colliding_alias` finding per participating path,
with bounded distinct peer paths and the complete distinct peer count. The required
`source_index` is the smallest zero-based original source index among that note's usable alias
occurrences matching the key. It is one integer in `0..255`, never an occurrence pair or an alias
value. Distinct comparison keys in the same note have distinct source indices, so for `[x, y]`
in both notes, each note's two cross-note findings carry `source_index` 0 and 1 respectively.
Repeated equal occurrences within a note do not multiply cross-note findings: `[x, x]` in note A
and `[x]` in note B form one cross-note group and exactly one cross-note finding for A with
`source_index` 0 and one for B with `source_index` 0. For a key occurring at least twice in one
note, emit exactly one separate `duplicate_alias_in_note` finding for that path and key, whether
or not another note participates. Its evidence contains exactly the two smallest distinct
original zero-based source indices for that key, in ascending order; both are in `0..255`.
Thus each alias finding exposes at most two indices, and each cross-note finding exactly one.
Sort and deduplicate findings only after grouping by comparison key and canonical path;
neither source indices nor occurrence count determine finding identity. Multiple symlink
spellings of one canonical note cannot create a cross-note collision. Aliases do not resolve note
identity or create a link target. Invalid/absent alias fields do not join collision comparisons.

`invalid_frontmatter` applies only to the parser's explicit `invalid` state and stable reason;
`invalid_portable_field` applies only to `aliases` or `tags` with the field owner's explicit
`invalid` reason. An owner's `empty_value` diagnostic can be reported as
`empty_portable_field_value` with field and source index, without emitting the value. Valid
unknown safe metadata is not an error. Do not infer conflicts between title, heading, tags,
capture provenance, or arbitrary unknown keys: there is no general conflict rule in ADR 0005.
Duplicate YAML keys already make the entire frontmatter invalid; do not invent a second conflict
interpretation. An invalid frontmatter block does not become empty metadata.

`empty_authored_body` means that, after the frontmatter owner recognizes **valid** frontmatter
and separates its exact envelope, the remaining authored Markdown body has no non-whitespace
characters. With absent frontmatter, test the whole Markdown body (ignoring only an initial
UTF-8 BOM for this classification). With invalid frontmatter, withhold this finding because the
authored-body boundary is uncertain. Whitespace uses the decoded string's Unicode whitespace
classification; a heading, comment, punctuation, or other non-whitespace text is content for
this rule. The frontmatter owner must supply or expose the valid body boundary for VB-141; hygiene
must not add a second YAML-envelope parser. There is no `suspiciously_empty` rule: valid short
notes are not objectively defective.

### Derived index state

Semantic index evidence is derived, never authoritative Markdown. The existing
`SemanticSearchService.query_basis()` is not a hygiene inspection path: it can reach `state`,
then `_read_persisted_state()`, which persists `error` or `uninitialized` for invalid/missing
metadata. `inspect_index()` delegates to `SemanticRepository.read_status()` using a live SQLite
`mode=ro` connection; it does not establish the strict no-sidecar-write guarantee or expose the
in-memory previous-index basis. `inspect_persisted_index()` uses
`SemanticRepository.read_immutable_status()` for stopped/offline inspection and refuses WAL/SHM
sidecars, so it cannot supply complete live evidence. No existing path meets the full hygiene
contract. Before VB-141 exposes any derived-state hygiene finding, it must add a small strictly
read-only inspection API owned by `SemanticSearchService`, using its existing repository and
compatibility logic rather than another parser or storage implementation.

That API may observe only already-existing persisted and current in-memory state, classifying
measurable facts. It must not initialize persisted state, repair metadata, write error state,
prepare/create/migrate storage, trigger rebuild or refresh, enqueue indexing, or mutate cache,
database, index, or SQLite sidecar files. A live storage read that cannot meet this guarantee must
return a bounded unavailable category rather than fall back to `query_basis()`, `state`, or a
write-capable lifecycle path. Invalid/corrupt persisted metadata likewise yields a bounded
invalid/unavailable category without repair. VB-141 must verify the no-write behavior for missing,
invalid, and corrupt metadata and live storage with sidecars. This inspection remains inside the
semantic/index owner; Knowledge Hygiene only consumes its safe classification.

`compatible_ready` means the owner observed a compatible completed index; it does **not** prove
each indexed note equals current Markdown. The new inspection may expose
`compatible_previous_refresh` or `compatible_previous_error` only when existing compatible
persisted evidence and current in-memory lifecycle evidence together establish a usable previous
result set during refresh or after error. Those categories support `previous_compatible_index`:
the result set may be stale. Report the exact owner category, not per-note staleness or an age.
Missing storage, incompatible signature, storage error, invalid metadata, or unavailable evidence
is `derived_index_unavailable` with the owner's stable bounded category. Do not label it stale.
Ready counts and last-successful-sync time alone cannot prove current Markdown freshness; no
per-note stale finding or automatic rebuild is authorized.

### Scan consistency, bounds, and privacy

A whole-vault scan is a sequence of reads, not an atomic snapshot. VB-141 must enumerate canonical
Markdown notes through `VaultService` with final canonical paths in deterministic path order, with a
hard maximum of 10,000 selected eligible paths per request and an explicit incomplete/limit result.
The owner completes one bounded-memory filesystem discovery pass; its entry order does not affect
selection of the smallest canonical paths when a later symlink alias reveals an earlier canonical
target. VB-141 must add a bounded read-only `VaultService` enumeration operation; hygiene must not
work around the owner with raw filesystem traversal. Scan each path at most once for authored
content in the ordinary pass and use the same verified content for frontmatter and relationship
interpretation. Each selected canonical note retains at most one mandatory owner-valid spelling
for relationship resolution (at most 10,000); an alias-only target uses an eligible alias spelling.
The owner prefers a discovered canonical spelling, otherwise the smallest eligible alias in
canonical spelling order, independent of discovery order. Up to 10,000 additional alias spellings
have a separate allowance; overflow makes resolution evidence incomplete without evicting
selected notes or mandatory spellings. Canonical top-K eviction removes that note's owner path
fact and mandatory spelling evidence. Reverify primary and related paths before return
where practical. Missing, changed, unreadable, oversized, or unsafe paths are omitted from
affected findings or reported as stable unavailable categories without leaking the path on a
failure surface. Do not retry without a fixed bound. Incomplete enumeration or source evidence
must suppress whole-vault absence claims, especially `isolated_note` and cross-note alias
collision completeness. External Markdown editing stays supported; the result carries a
`complete`/`partial` scan indicator and safe counts, never a transactional-consistency claim.

VB-141 may return at most 500 findings and at most 10 related canonical paths per finding.
Optional candidate analysis uses one bounded owner batch over at most 10,000 verified canonical
candidate paths and at most 20 verified source notes per request, in canonical path order. It
returns at most 5 candidates per source and reports partial coverage. These ceilings constrain
the owner's enumeration and matching work, not only the displayed output; an incomplete bounded
enumeration cannot silently stand in for a complete candidate universe. Existing owner bounds,
including frontmatter's 65,536-byte envelope, portable-field 256-value/1,024-byte limit,
configured note-size bound, and candidate owner's limit/overfetch cap remain in force. Never
perform an unbounded all-pairs comparison. The result reports scan and evidence truncation; zero
findings under partial coverage is not a clean bill
of health. These are design ceilings, not a runtime implementation or a public schema.

Routine logs and failures carry only stable categories and safe counts/booleans: no Markdown
content, arbitrary metadata values, alias strings, query text, URLs, host paths, symlink
destinations, credentials, raw exceptions, embeddings, SQL, or semantic snippets. Authorized
results may expose verified canonical vault-relative paths plus only the bounded evidence above.
There is no hidden diagnostic database or cache.

### Repair and future implementation boundary

VB-140 and VB-141 are read-only. No finding may delete, merge, rewrite, rename, move, retag,
repair links, alter frontmatter, rebuild indexes automatically, or promote captures. Any future
repair capability needs its own explicit contract for operator approval, conflict detection,
retries, and concurrency. VB-141 needs a separate authoritative BACKLOG implementation contract
for request/result/failure details, exact owner composition, strictly read-only semantic/index
inspection, bounded candidate-owner work, the bounded enumeration extension, and focused
failure/concurrency tests before code. VB-142 may later supply thin adapters and
optional dashboard views under a separate contract. This ADR selects no REST, MCP, CLI, or
dashboard surface and changes no OpenAPI, dependency, deployment, or release behavior.

## Consequences

- Findings are reproducible classifications over inspected facts, with explicit incomplete or
  unavailable evidence rather than false certainty.
- Captures remain visible as intake without being mislabeled isolated curated notes.
- A future bounded `VaultService` enumeration/body-boundary capability may be needed before
  VB-141 can meet these semantics. VB-141 also requires a strictly read-only semantic/index
  inspection API and a bounded `DuplicateCandidateService` batch entry point; this ADR adds no
  runtime capability.
- Relationship fragments, semantic freshness per note, broad metadata conflicts, and subjective
  short-note quality remain outside the supported evidence.

## Rejected alternatives

- A new parser, resolver, filesystem walker, duplicate algorithm, or semantic ranker would split
  ownership and could disagree with live domain behavior.
- A diagnostic database or automatic repair loop would create a second state/approval authority.
- Inferring note quality from short length, unresolved links, or semantic scores would turn
  advisory evidence into unsupported factual or mutation claims.
