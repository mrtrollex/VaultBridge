# Retained independent review: 401-repair

Timestamp: 2026-11-21T15:16:09.003Z

No actionable implementation defects found. Approval applies to the HTTP 401 repair; overall VB-156B qualification remains incomplete.

- **A. Confirmed implementation defects:** None found in the repair or its integration.
- **B. Repair verification:** [scoped-client.js:15](../source/implementation-changes.txt) handles 401 before parsing, preserving epoch and shared-record checks. [named-session.js:206](../source/implementation-changes.txt) synchronously clears protected state, then revalidates under the existing mutation lock before publishing intent and deleting the cookie. Stale responses cannot delete a newer session. Failed DELETE remains fail-closed; non-401 semantics are unchanged. No legacy Dashboard or REST/MCP regression was found.

  The [regression tests:364](../source/implementation-changes.txt) exercise five body forms, actual HTTP truncation, same-tab and cross-tab reauthentication, concurrent logout, and failed deletion. Observable barriers control ordering without sleeps. Independent in-memory checks passed **21 cases**, including reproduction against the old code. Inspected retained native evidence records **300 passes per platform**, with zero failures/skips; browser suites were not rerun during this review.
- **C. External browser defects:** The documented Windows WebKit SameSite failure remains an external browser defect, not a VaultBridge implementation finding.
- **D. Qualification gaps:** Corrected Windows WebKit qualification, the unexplained historical Chromium bootstrap failure, and authenticated BFCache admission/suspended-logout evidence remain outstanding. [Qualification record:306](../source/implementation-changes.txt)
- **E. Required owner decisions:** No new architecture decision is required for this repair. Overall acceptance remains gated on the outstanding qualification evidence and deployment trust acknowledgements. This review does not waive or amend ADR 0011.

No files were modified.

APPROVE
