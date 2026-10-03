# ADR 0007: Knowledge Capture and provenance model

- **Status:** Accepted
- **Date:** 2026-09-28
- **Task:** VB-130

## Context

ADR 0005 makes each contained Markdown file and its portable metadata authoritative, with a
verified vault-relative path as identity. ADR 0006 composes live Markdown facts for bounded
Knowledge Query; it does not decide whether a note is curated knowledge. At this ADR's acceptance,
`VaultService` offered create and append boundaries and duplicate/related-note services offered
advisory evidence; none was a capture or promotion workflow. VB-131 subsequently added capture.

Milestone 14 calls for portable intake, provenance, conservative related-note analysis, and
explicit review before promotion. A capture may be useful to keep without asserting that it is
correct, complete, or ready to join an authoritative note.

## Decision

### Authority, identity, and state

A capture artifact is one contained, inspectable UTF-8 Markdown file. Its bytes, including bounded
portable frontmatter, are authoritative under ADR 0005; its identity is its `VaultService`-verified
canonical vault-relative `.md` path. No database row, conversation ID, title, source URL, timestamp,
or embedding is capture identity. A capture request or in-memory proposal is not persistent
knowledge. There is no hidden memory store.

`inbox` means captured material awaiting triage. `draft` means material deliberately retained for
review or editing but not yet promoted. These are workflow states of a Markdown capture artifact,
not claims about its truth or quality. A future intake writer must record its state visibly in
portable Markdown metadata; a folder may organize intake but is not the sole state authority.
Unknown, absent, or invalid state must not be silently interpreted as promoted. The exact folder
layout and public representation belong to VB-131.

Promotion is a separate, explicit decision that creates a new destination Markdown note or appends
to one verified existing destination. The resulting destination is authoritative Markdown. The
source capture remains a separately inspectable intake artifact until a separately authorized
retention action is defined; promotion does not imply a rewrite, rename, move, or delete of it.
An existing note without capture-state metadata is not reclassified by this ADR. Knowledge Query
and semantic search keep their current scope and behavior; they do not automatically filter intake.
Future consumers that need a curated-only view must define that scope explicitly.

The conceptual domain input and outcome are immutable facts, not a required Python class or public
schema:

```text
CaptureProposal
- content: authored Markdown text
- title: optional proposed display title
- source: optional author-supplied provenance description
- captured_at: capture-event timestamp supplied or established by the trusted capture boundary
- tags: ordered portable tag values, optional
- capture_type: optional short descriptive type
- state: inbox | draft

CaptureArtifact
- path: verified canonical vault-relative Markdown path
- state: inbox | draft, read from valid portable metadata
- provenance: bounded portable capture facts, including unknown/absent distinctions

PromotionDecision
- source_path: verified capture path
- action: create | append
- destination: explicit proposed new note or verified existing canonical note path
- approved content: exact Markdown content to commit
```

These shapes do not prescribe a protocol, class name, or serialized field spelling. A promotion
decision is not durable permission for later automatic writes; the human/operator must approve the
specific action, destination, and content at the write boundary. An agent may prepare proposals
and evidence but may not infer approval from a match, query result, previous approval, or silence.

### Portable provenance

The capture event records when material entered the intake workflow, not when its asserted facts
became true or when a source was published. `captured_at` is a UTC RFC 3339 string with seconds and
an explicit offset (`Z` or `+00:00`); it is not filesystem creation/modification time or a YAML date
object. A missing/untrusted caller timestamp may be established at the trusted capture boundary;
an invalid supplied timestamp is rejected, not silently replaced. Later writes must not invent an
earlier event time. `created`, when already present in a note, retains its existing meaning and is
not silently equated with `captured_at`.

`source` describes the declared origin, such as a citation, URL, document label, or an explicit
`unknown` value. It is an untrusted attribution, not proof of authenticity, permission to fetch
the URL, or an instruction to ingest an entire conversation. Unknown or omitted source remains
distinguishable from a verified external source; the model makes no verification claim. Portable
`tags` follow ADR 0005/VB-112 ordering and value semantics. `capture_type` is an optional
human-readable descriptor, not an executable instruction or a fixed taxonomy. These facts must be
visible in the capture Markdown, using simple YAML 1.2 Core strings or string sequences under ADR
0005's bounded profile. The future writer may choose exact field names but must publish one stable
portable mapping in its own contract and preserve unknown safe metadata. No proprietary metadata
schema, Obsidian-only feature, or external lookup is required to inspect a capture.

Promotion must keep provenance inspectable in the destination Markdown when it transfers capture
content. A newly created destination may carry appropriate portable frontmatter; an append must
carry an attributable Markdown block because the current append boundary does not edit existing
frontmatter. The destination must not misstate a capture's declared source as verified. It must
remain possible to trace the transferred text to the source capture by a portable, vault-relative
reference where the capture still exists. An absent or invalid source record is not silently filled
from a title, URL-shaped text, or semantic match.

The `create_note` implementation stamps `source: chatgpt` and `created` independently of
caller-supplied provenance; `append_note` appends content and an optional idempotency marker without
editing frontmatter. This ADR does not claim those methods already serialize the above fields or
authorize a behavior change to them. VB-131/VB-132 must contract and prove any additive capture
composition needed while keeping the existing public create/append semantics intact.

### Bounds and privacy

The future domain boundary rejects malformed or oversized proposals before enumeration, related
analysis, or a write. Capture content is at most 65,536 UTF-8 bytes and must also fit the existing
configured `VaultService.max_note_bytes` after all Markdown and metadata are composed. Proposed
title is at most 256 UTF-8 bytes; source is at most 2,048; capture type is at most 64. At most 16
tags may be supplied, each at most 1,024 UTF-8 bytes, before any deduplication. Tags retain their
source order and duplicates as ADR 0005 specifies. The whole frontmatter also remains within ADR
0005's 65,536-byte profile and its scalar/depth/item bounds. Empty required content, invalid
UTF-8, unsupported scalar types, invalid timestamp/state, and exceeded bounds fail closed; no
coercion, truncation, or partial capture is accepted.

Capture is explicitly initiated for selected content. No ambient chat-history ingestion, automatic
transcript scraping, remote URL fetching, mandatory LLM, cloud API, external embedding provider, or
content transfer to an external service is authorized. Proposed content, source strings, URLs,
titles, tags, private paths, and exception details do not enter logs or routine diagnostics.
Expected failures use stable, bounded reason categories; user-facing inspection of the Markdown
is a separate, authenticated read governed by existing access controls.

### Advisory duplicate and related-note interaction

Existing duplicate-candidate, related-note, relationship, and Knowledge Query capabilities may
offer bounded evidence during review. A candidate is a suggestion, never proof of identity or
permission to mutate. A missing index, stale candidate, ambiguous relationship, or no candidate
leaves intake intact and must not cause an automatic create, append, merge, or discard. Candidate
sets must have an explicit finite limit and deterministic ordering supplied by their owning
service; the capture model does not alter their ranking or treat similarity as a threshold for
promotion. Before append, the selected destination must be reverified through `VaultService` for
current existence, canonical path, containment, size, and write safety. A proposed new destination
must use the existing create compatibility boundary with the atomic conflict protection required
below and surface conflicts for a new decision. No broad scan or semantic lookup is required for a
valid capture.

The existing `create_note` may return `unchanged` when an existing note merely ends with the
proposed content; that result does not verify the earlier text or capture provenance. VB-132 must
treat `unchanged` as an unresolved create attempt, not successful promotion: leave the capture in
intake, report that no new destination was created, and require a fresh human/operator decision
about the destination and action. It must not mark the capture promoted or silently retry as an
append, even when the existing note appears related. A lost response to an earlier successful
create also requires this conservative re-review; `unchanged` alone is never proof of attribution.

### Writes, failures, and ownership

`VaultService` remains the sole owner of path containment, exact spelling, symlink and traversal
safety, Markdown-only writes, configured size/UTF-8 limits, and the existing create/append
compatibility behavior. At ADR acceptance, `create_note` existence check and `write_text`, and
`append_note` marker check and append, were separate uncoordinated operations. VB-131 subsequently
added shared cross-process/thread coordination and an atomic create-if-absent capture operation,
without changing the existing public create/append signatures or their content semantics. The
legacy methods still do not encode VB-132's exact promotion attribution or retry decision.

VB-131 established its separately scoped intake create guarantee. Before VB-132 relies on
create/append for promotion, it must establish and test the additional exact-path
atomicity/concurrency mechanism across cooperating VaultBridge-mediated writers to the same
destination, including supported processes and threads and existing create/append callers. It
must preserve contained Markdown-only writes and existing caller compatibility. Create-if-absent must never overwrite a
conflicting destination for a cooperating writer; append deduplication must commit at most once
for one idempotency context among cooperating writers. This ADR selects no locking, filesystem,
or storage implementation. Pre-write verification alone cannot satisfy the guarantee. A retry
must remain tied to an explicit approved action and idempotency context. When a concurrent write
or retry leaves the committed result uncertain, promotion remains unresolved until the destination
and attribution are safely established or a new human/operator decision is made.

This strong concurrency guarantee assumes a stable destination namespace during the commit.
Ordinary external Markdown editing remains supported. Active rename, replacement, or relocation
of the capture destination or staging directory chain by a non-cooperating filesystem writer is
outside it: advisory locks constrain only participants, directory descriptors can remain attached
after rename, and a namespace check cannot be atomic with a later pathname commit. VaultBridge
does not require exclusive host filesystem ownership, privileged mount namespaces, immutable
directory flags, deployment-specific ACLs, a global filesystem scan, or hidden idempotency storage.
`VaultService` still verifies contained canonical paths and fails closed on ordinary traversal or
symlink attacks through VaultBridge inputs. When it detects unsafe namespace state before any
possible commit, the outcome is `unsafe_destination`. If a commit may have occurred but canonical
placement with the expected complete bytes cannot be proved, the outcome is `commit_unknown`,
never confirmed creation. A known `commit_unknown` caused by possible out-of-band relocation
remains unresolved even when the canonical path is absent; the caller must retain that outcome,
and an operator must resolve the possible relocated artifact and explicitly authorize any further
write. If absence of another same-ID artifact cannot be established, no further write is
authorized. These intake rules do not implement or authorize VB-132 promotion.

The existing application indexing path remains post-commit derived work, not a condition that can
roll back committed Markdown. The future workflow must report whether Markdown was committed even
if post-commit indexing fails. This ADR introduces no transaction across two Markdown files and no
claim of atomic review-plus-write behavior.

Invalid input or unsafe path fails before mutation. A safe create conflict, missing/changed append
destination, invalid capture metadata, or stale review evidence returns an explicit safe failure or
requests a new decision; it never selects another target or silently broadens scope. After a
confirmed write, failure to update a separate review/disposition view cannot erase or repeat that
write. Programming errors are not hidden as expected input failures. Diagnostic messages and logs
must not contain note content, metadata values, absolute host paths, symlink destinations, source
URLs, credentials, or raw exception strings.

The ADR 0005 frontmatter/portable-field layer owns parsing and typed metadata semantics.
`RelationshipService` owns relationship resolution, duplicate occurrences, and ambiguity;
`KnowledgeQueryService` owns its current read-only query composition; duplicate/related services
own their current advisory evidence. A later small capture domain boundary may compose these
owners and the approved action, but adapters must not invent separate provenance, state, or
promotion rules. None of these readers becomes a write authority.

### Later task boundary

VB-131 may implement a separately accepted, bounded intake writer that creates inspectable
inbox/draft Markdown and records the portable capture event. It must define its concrete metadata
mapping, idempotency and failure tests, and adapter scope before runtime work. It does not get
promotion authority from this ADR.

VB-132 may implement separately accepted read/review and explicit create-or-append promotion over
the same capture model, with current-destination verification, provenance transfer, retry rules,
and tests for conflicts and post-commit failure. It may consume existing duplicate/related evidence
as advisory input. This ADR commits no runtime class, public endpoint/tool/command/UI, persistence,
or automatic disposition operation for either task.

## Consequences

- Intake remains portable and inspectable even when it is not curated knowledge.
- Provenance is an attributed capture fact, not a truth or source-verification guarantee.
- Promotion requires a deliberate write decision and may leave a source capture and a destination
  note containing related material; no automatic cleanup or merge follows.
- Existing search/query behavior continues to see any eligible Markdown, including intake, until
  a separately accepted scope contract changes that behavior.
- The present create/append methods do not encode arbitrary capture provenance; later tasks must
  address that mismatch explicitly without changing current caller behavior.

## Rejected alternatives

- **Use a hidden vector-memory or database record as the capture authority:** it would make captured
  knowledge inaccessible without VaultBridge and weaken Markdown portability.
- **Treat every capture as immediately curated knowledge:** it would erase the review distinction.
- **Automatically merge or append to the closest related note:** similarity and links are advisory,
  potentially stale, and cannot express operator intent.
- **Use filesystem timestamps or inferred source as provenance:** neither reliably records the
  capture event or a truthful attribution.
- **Change existing create/append behavior in VB-130:** this is a design task and existing callers
  depend on their current write contract.
