# Retained independent review: implementation-before-401

Timestamp: 2026-11-21T14:17:20.256Z

Found **one confirmed implementation defect**. Qualification also remains incomplete under unchanged ADR 0011.

**Confirmed implementation defects**

- **MEDIUM — A current 401 can leave protected state visible when its body cannot be parsed.** In [scoped-client.js:13](../source/implementation-changes.txt), `response.json()` precedes the 401 check. A truncated or non-JSON 401 therefore bypasses coordinated cleanup; [spaces.js:132](../source/implementation-changes.txt) handles it as ordinary unavailability and retains selection/capabilities.

  A read-only, in-memory probe using the actual session, client and status-controller code confirmed: broken-body 401 left `authenticated`, visible metadata and no DELETE; valid-JSON 401 cleared state and completed DELETE. Process current 401 status independently of body parsing, preserving revision/epoch protection against stale cleanup. Add broken-body coverage alongside [the existing 401 test](../source/implementation-changes.txt). This violates [ADR 0011:231](../source/implementation-changes.txt).

**External platform defects**

- **HIGH — Windows Playwright WebKit remains unqualified for Strict-cookie delivery.** The retained production/control failures match [Playwright #44141](https://github.com/microsoft/playwright/issues/44141); [WebKit #81431](https://github.com/WebKit/WebKit/pull/81431) remains open. Original assertions remain at [test_named_cookie_delivery.py:136](../source/implementation-changes.txt). This is supported as a browser-port defect, with no evidence identifying a VaultBridge implementation cause. Server guards cannot prevent cookie delivery to another receiving server.

**Inconclusive qualification evidence**

- **MEDIUM — Chromium bootstrap remains unexplained.** The original failure and missing `spaces.js` request are accurately preserved at [VB156B_QUALIFICATION.md:47](../source/implementation-changes.txt). Dependency/lock barriers and successful reruns distinguish observable stages; they establish neither causation nor correction.
- **MEDIUM — Authenticated BFCache admission and suspended logout remain unexercised.** I inspected the retained Windows/Linux history properties: authenticated returns report `persisted=false`. Protected-state erasure and non-BFCache restoration pass. The document accurately preserves this limitation at [VB156B_QUALIFICATION.md:138](../source/implementation-changes.txt). No authenticated admission claim is justified.

**Recommendations requiring explicit owner approval**

A separately reviewed qualification-contract amendment could address these evidence blockers without changing runtime security by:

- Defining supported platform/build profiles and explicitly excluding affected Windows WebKit until unchanged assertions pass.
- Separating demonstrated non-BFCache restoration from authenticated BFCache qualification, with alternative native evidence or explicit support restrictions. Automation-disabled admission cannot establish production non-admission.
- Recording the unexplained bootstrap failure as an explicitly accepted residual availability risk, retaining its failure evidence and strict regression assertions.

Such an amendment must preserve cookie attributes, guards, journal quarantine, state erasure and revalidation. It cannot substitute for correcting the confirmed 401 defect.

I inspected tracked/untracked implementation, tests, ADR and retained evidence. All 100 snapshot hashes matched. Retained results show 246 Windows and 368 Linux affected-suite passes, plus 110 backend passes. Those suites were inspected, not rerun. No additional concrete implementation defect was established. No files were modified.

FIXES REQUIRED
