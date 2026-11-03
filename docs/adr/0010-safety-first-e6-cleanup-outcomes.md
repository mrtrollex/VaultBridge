# ADR 0010: Safety-first E6 cleanup outcomes

- **Status:** Accepted
- **Date:** 2026-11-01
- **Task:** VB-151 E6 R9 cleanup identity and retryability repair
- **Authority:** Explicit approval by the task owner in the R9 safety-first implementation instruction.

## Decision

Allocation safety takes priority over universal cleanup retry. This supplements
ADR 0009's delegated runtime lifecycle contract; its architecture, status and all
unrelated E1–E6 requirements remain unchanged.

Classify each original/proof allocation as owned and open, release in progress,
verified released, verified safe to retry, or ambiguous. Terminal cleanup complete
is an aggregate state requiring verified release of every mandatory resource.
A certified-safe failure MUST remain retryable. An opaque closer exception, a
restored callback, a valid numeric slot, matching file metadata or equality of
kernel objects/open file descriptions does not establish allocation ownership.
A new allocation can reuse the same number for the same underlying object.

Never retry a release whose original allocation ownership is unresolved. Retain
bounded actual owners/records and relevant objects, report incomplete cleanup,
continue independent safe cleanup, fence admission and keep runtime stopping.
This applies to final proof resources and startup rollback too. Ambiguity may
remain permanently unresolved in-process. Never equate consumed callbacks,
collections or completed futures with verified native disposal.

Actual process termination may be required for OS resource reclamation. It is
not successful in-process runtime shutdown, nor authorization for new automatic
termination, supervisors, processes, watchers or recovery workers. No public
API/configuration/schema, deployment or release change is selected by this ADR.

## Evidence and retry

A failure before owner-controlled dispatch to any release/transfer can certify
no effect, provided there is no earlier ambiguous history. Once an opaque closer
was dispatched, hidden before/after effects cannot be inferred from an exception.
Known-owned allocation history and a trustworthy primitive's verified release
can satisfy cleanup; a bare opaque callback return does not suffice. New evidence
may resolve ambiguity, but no timeout, flag reset or object equality authorizes a
numeric retry. Per-resource locks serialize compliant close/transfer actors; they
are not an OS allocation-generation-qualified compare-and-close operation.

## Consequences and acceptance

The original E6 task is explicitly amended only for this cleanup-liveness conflict.
Normal behavior and all isolation, containment, non-destructive storage, lease,
consumer, cancellation, queue, generation and adapter guards remain mandatory.
The existing stream/event guard tests' unconditional eventual-stopped expectation
may change only where their opaque errors have no safe-retry certificate. Their
actual ownership, no-collateral-close and no-false-stopped assertions remain.
Prior regression expectation changes require a specific approved-conflict inventory;
no blanket weakening or skips/xfails are authorized.

Native Windows/Linux verification must cover certified-safe retry, verified release,
opaque before/after failure, same/different-object reuse, final proof disposal,
concurrent cleanup, bounded unresolved retention and permanent fencing. A trusted
native per-allocation receipt protocol remains a separate, unapproved alternative.
Acceptance of this ADR does not certify an implementation or complete VB-151/R9.
