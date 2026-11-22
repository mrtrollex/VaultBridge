# Sanitization and extraction boundary

All payloads are selected from recorded evidence or are explicitly authored indexes/
verification recipes. `manifest.json` records destination, original relative identity,
original-byte SHA-256, transformation method, sanitized-byte SHA-256 and source relation.
An original ignored identity is provenance only; required extracted content is in this
directory. No qualification test result is manufactured by an authored summary.

- JUnit is converted to JSON, retaining suite counts/timestamps, every final testcase
  identity/outcome/time, skip/failure reasons and native properties. The machine hostname
  is removed. Diagnostic-only extracts deliberately retain failures/skips and original
  suite totals, labelling omitted passes and their intermediate source separately.
  A small number of backend parameter IDs contain static fixture API credentials:
  their values receive distinct aliases. Original full identity SHA-256 and pinned
  source AST literal locations preserve exact, independently reconstructable identities
  without republishing credential values. The verifier checks that round trip locally.
- Native event JSON is retained as one record per line. Personal executable/user paths
  and Web Lock client IDs are removed. Request method/path/status/cache policy, native
  errors, sequence/timing, native `persisted` flags, DOM/controller-erasure booleans and
  journal phase observations are retained. Public journal key names are not credentials;
  no nonce, cookie, bearer or private storage value is added.
- Mutation/layout/receiver observations are parsed from the exact printed JSON lines.
  Their log line number, original log hash and case identities remain recorded. Numeric
  measurements and cookie-presence booleans are not replaced by success prose.
- Trace ZIPs are not shipped. Only allowlisted HAR method/URL structure, statuses,
  header names, cache/content/fetch metadata, cookie flags/expiry/presence, technical
  failure and timing fields leave the archive. Cookie values, Authorization and all
  request bodies are omitted. Only a recorded session response body with exactly the
  nonsecret boolean `authenticated` is allowed; other response bodies are omitted.
- Trace API arguments and ordinary results, DOM snapshots, screenshots, sources and
  browser storage are omitted. Cookie-jar results retain only native attribute/expiry/
  presence metadata, never values. API class/method/call/timing/error-presence and
  recorded lifecycle console booleans are retained. No missing event, cookie effect
  or body is reconstructed.
- Windows/Linux executable and workspace roots become explicit `<WINDOWS_PYTHON>`,
  `<LINUX_PYTHON>`, `<SOURCE>`, `<LINUX_SOURCE>` and temporary/home aliases. Exact other
  command arguments and execution dates are retained. The original-byte hash refers
  to the private original, while the indexed hash refers to this sanitized payload.
- Local receiver ports use stable opaque aliases; only reserved synthetic `.test`
  hostnames survive. Loopback addresses become aliases. Original native request errors,
  HTTP status codes, file/test identities and technical build/image checksums survive.
- Known synthetic authentication strings are also redacted where present. No actual
  session/bearer/cookie value, private hostname, username, absolute user path or real
  vault content is intentionally included. The verifier rejects common leak patterns;
  generation additionally scanned the actual recorded private user/path identifiers
  and fixture credential literals before handoff.

The 40 history/public-control native traces and six identical-source focused Linux
complete-DELETE traces are extracted. Standard final passing contexts used
`retain-on-failure`; raw pass traces were intentionally not retained there. Native
events, printed mutation observations, exact passing JUnit assertions and the pinned
test source are the available final-source evidence for those cases. Raw traces
containing credentials/screenshots cannot safely be copied wholesale. These omissions
limit forensic detail; they do not create a claim of authenticated BFCache admission.

All four ADR 0012 alternative H classes remain linked. Historical failed suspension,
Windows Strict-cookie and original Chromium bootstrap records retain their historical
source/review limitations and are not attributed to this final fingerprint. The
separate full owner review transcript was not supplied; only its explicitly reported
finding/scope is transcribed. No new independent review was performed.

Artifact bytes are UTF-8 and protected from Git checkout conversion by a scoped
`.gitattributes` rule. Manifest and checksum are outside their own payload hash set.
The code fingerprint itself is independently reproducible from implementation files;
explicit recorded-line-ending reconstruction is permitted only after matching each
LF-content hash. Documentation/evidence additions do not extend qualification to
different executable/test content.
