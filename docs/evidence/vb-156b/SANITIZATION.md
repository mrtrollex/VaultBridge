# Sanitization and trace extraction

This bundle is an evidence transformation, not a new test execution. All selected inputs were read
from the implementation worktree or original review/session records without importing project
modules or changing that worktree. Every exported text/JSON file is inspected by the export/validation
pipeline; mandatory result/source records are additionally checked for semantic consistency.

## Text, source and JUnit

- Complete selected logs retain every progress, request and error line. UTF-16 PowerShell and UTF-8
  inputs are decoded by BOM; output is UTF-8/LF. ANSI formatting and trailing whitespace are removed.
- Personal source roots, interpreter locations, home/temporary directories and local host/user names are replaced with fixed
  aliases. Original artifact paths are preserved relative to `VB156B_SOURCE/` or `CODEX_SESSION/`;
  the private absolute roots are intentionally omitted.
- Fixture API-key literals, session tokens, Authorization/Cookie/Set-Cookie literal values, cookie
  values and browser coordination nonces/revisions are redacted, including synthetic values.
  Header/attribute names, HTTP status, cookie-presence facts, paths, request chronology and errors
  remain. Program identifiers and expressions in source are not secret values.
- All suites, cases, outcomes, failure/error text and diagnostic properties from every selected
  JUnit file are retained in JSON. Machine `hostname` is omitted; nested snapshot JSON is parsed to
  remove lock client identities. Failure counts are never rewritten as passes. Partial logs without
  complete JUnit remain partial executions, not passes or invented counts.
- Included source text is explicitly a current sanitized capture or a named retained prior artifact.
  Raw source hash maps and sanitized-content hashes are distinct. Historical review source links
  are retargeted to included source text while the original labels/verdict/limitations are retained.
- The review verification record retains the hash-check script prefix and its complete tool output.
  The temporary in-memory probe program is omitted; its 21-case outcome and exact review scope
  remain in both the recorded output and full verdict. No new review is implied.

## Native event documents

`native-events.json` includes all events in each selected document. The selection is bounded to the
two bootstrap barrier families plus the exact traced history/public-control runs. The latter require
both a matching embedded trace wall-time and exact raw JUnit snapshot-state equality. There are
24 matched history/control documents per platform. Other bootstrap documents are labelled
supplementary/intermediate; no final-source attribution is manufactured.

Executable paths and lock client IDs are omitted. Only fixed storage-key names and lifecycle phases
are retained, with no journal/intent values. Event sequence/time, module request/response/finished/
failed events, HTTP/cache facts, errors, page-close events, native persisted/visibility flags,
erasure booleans, lock names/modes and navigation rejection reasons remain. Document URLs are
synthetic reserved test destinations. No protected response bodies are included.

## Raw trace replacement

No raw ZIP, screenshot, DOM snapshot, storage snapshot or request/response body is included. Native
traces can contain credentials and protected state even when generated with synthetic fixtures.
Instead, `traces/*.json` contains:

1. Original archive SHA-256 and original relative path.
2. Each processed `.network`/`.trace` member name and SHA-256.
3. Every network row, with its original one-based member line number, timestamp, method,
   scheme/host/port/path and test-case query label; **other query values are omitted**.
4. Cookie header presence and cookie names only; Sec-Fetch-Site and Origin presence; response status;
   Set-Cookie names and attribute facts, including Strict, Secure, HttpOnly, Path, Max-Age and
   host-only/no Domain. No Cookie, Authorization, Origin or Set-Cookie credential/header value is copied.
5. Trace context engine/platform/SDK/wall-time metadata and after-call errors. Error text receives
   the same redaction as logs. Native lifecycle observations are preserved separately in the event
   documents and JUnit properties rather than extracting raw DOM or arbitrary JavaScript results.

All network rows are retained, not just the failing cross-site requests. The separately exported
receiver observations are the test's complete structured output for all nine scenarios per Strict
case, not inferred from cookie metadata. This preserves contradictory/passing scenarios too.
Member/line/hash identities allow comparison with originals by an authorized holder; hashes alone
are not offered as a substitute for the included event content. An independent reader can inspect
all retained security facts without accessing raw credentials or another local worktree.

## What is unavailable

The original failing bootstrap context had no native trace/browser-side error or evaluation recording.
No such recording is invented. Git/task/review records also do not recover complete contemporaneous
source trees for original/intermediate runs. Those linkages remain UNVERIFIED and block independent
acceptance. Only the recorded production/final-repair hashes are verified; current source is never
presented as a reconstructed historical snapshot. Linux distro/kernel identity is not in the preserved
run manifests; the OS claim stays restricted to the recorded actual WSL/Linux environment.

Validation scans every durable artifact for personal roots/user/host names, credential/token/header
values, forbidden raw binaries and unnecessary coordination identifiers. It also checks all manifest
hashes/paths, source fingerprints, browser identities, JUnit counts, original failures, trace/event
content and unchanged status/security boundaries. Its result certifies those checks, not the missing
historical provenance or runtime qualification.
