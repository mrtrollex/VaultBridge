# Legacy Web Dashboard

The bundled Dashboard is available only when `KNOWLEDGE_SPACES_JSON` is omitted. Named mode deliberately returns 404 for Dashboard, assets and session routes.



- bundled same-origin `/ui/` entry point with no second frontend service
- accessible Overview, Search, Hygiene, API / Integration, and About navigation
- read-only Overview using the existing public `/health` facts, with manual refresh and explicit
  loading/unavailable states
- API-key unlock through the existing dashboard session boundary and signed HttpOnly cookie
- strict browser security headers, local assets, and text-only dynamic rendering
- protected literal and semantic Search using existing backend ranking and read-only result fields,
  with no query/history persistence
- bounded outgoing-link and backlink facts for a successfully read note, using the existing
  authenticated versioned endpoints without client-side relationship parsing or persistence
- read-only Knowledge Hygiene scans on explicit Run scan / Refresh scan, with returned finding
  counts, canonical note paths, safe evidence, and separate scan/candidate/index coverage;
  partial scans and truncated findings are explicitly labelled
- retrieval-only operator visibility: no note editor, index-maintenance controls, account system,
  or TrueNAS-specific behavior

Open the dashboard from the same VaultBridge origin at `/ui/`. The browser session uses an
operator-supplied existing API key once through `/ui/session`; the key is not stored by browser
JavaScript. The signed HttpOnly cookie restores access across reloads and browser reopen for up to seven days;
successful restoration refreshes that lifetime. It uses `SameSite=Strict`, path `/`, and `Secure`
when the application sees HTTPS. Unlock sends the key in a JSON POST body, never a URL. Protected
requests use the cookie plus `X-VaultBridge-UI-Request: 1`. Changing the current API key invalidates
existing sessions, even while the previous key remains accepted for Bearer clients and new unlocks.
Logout deletes the browser cookie and clears protected form/results; `401` also clears that page
state. There is no server-side revocation list: a copied signed token remains valid until expiry or
current-key rotation. The public Overview remains available.

The dashboard targets current evergreen browsers with support for ES modules, `fetch`,
`AbortController`, `URL`, and `Intl.NumberFormat`. This is a modest
standards-based compatibility statement, not a claim that every browser/version has been tested.
Actual browser and production-image evidence is tracked separately in
[`docs/DASHBOARD_RELEASE_CHECKLIST.md`](DASHBOARD_RELEASE_CHECKLIST.md).

<p align="center">
  <img src="assets/vaultbridge-dashboard-overview.png" alt="VaultBridge dashboard Overview showing a ready local semantic index" width="720">
</p>

<p align="center">
  <img src="assets/vaultbridge-dashboard-about.png" alt="VaultBridge dashboard About view describing its local-first, API-first design" width="720">
</p>

These screenshots were captured from the running TrueNAS canary dashboard without browser chrome,
API keys, note content, paths, or private host details. The Overview exposes only the same public
health facts returned by `/health`. Their repository URLs are non-authoritative source/preview
material; they are not evidence that the accepted TrueNAS catalog already displays them.
