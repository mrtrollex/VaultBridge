# P2: TrueNAS MCP delivery preparation

This is a local, statically validated preparation, dated 2026-10-10. It is not a
catalog submission, published package, application release, or live upgrade result.
Live candidate migration remains pending O1; catalog delivery remains pending P5/O3.

## Source identity and preservation

The read-only inspection of [truenas/apps master](https://github.com/truenas/apps/tree/bb96ef6fd8c2708765012a4a3ca3739370a4f92c/ix-dev/community/vaultbridge)
used commit `bb96ef6fd8c2708765012a4a3ca3739370a4f92c`. The development package is
`1.0.3`, application/image `1.3.0`, library `2.3.11`.

| Baseline role at the pinned evidence point | Package | Application image |
|---|---|---|
| Current principal baseline | `1.0.3` | `1.3.0` |
| Historical retained baseline | `1.0.2` | `1.3.0` |

Historical retained baseline coverage must not substitute for current-package coverage.

`upstream/` contains the five unmodified source inputs needed by the preparation.
`upstream-lock.json` records their LF-normalized SHA-256 hashes and the matching
library Python hashes. The generator verifies these before creating any output.
The historical library is reused only after verifying its content matches upstream.
No historical package metadata or template is used as the candidate baseline.

The historical package metadata and templates at `ix-dev/community/vaultbridge/` remain untouched: package
`1.0.0`, image `1.1.0`, old port `30486`. The prepared candidate retains upstream's
port `30491`, CDN icon, `/ui/` portal, image `1.3.0`, API-key fields, watcher fields,
storage/Host Path/ixVolume choices, identity/constraints, resources and library.
An actual rendered baseline comparison with an explicit debounce value proves only
four MCP environment mappings are added. The candidate also hardens the existing
watcher debounce mapping as described below.

`1.0.4` is a **disposable test candidate identity**, derived from inspected `1.0.3`.
Its migration has `from.max_version: 1.0.3` and `target.min_version: 1.0.4`, covering
both historical `1.0.2` and current `1.0.3` saved state. P5 must refresh upstream and choose actual
revision/bounds and an already-published stable image. Do not submit this fixture
identity or claim it selects v1.4.0.

## Live watcher finding and candidate hardening

The task's live reproduction on TrueNAS `25.10.7`, Community package `1.0.3` /
image `1.3.0`, found that disabling Filesystem Watcher could leave the hidden
`watch_debounce_seconds` absent or empty. The container then received
`SEMANTIC_WATCH_ENABLED=false` and an empty `SEMANTIC_WATCH_DEBOUNCE_SECONDS`,
which failed runtime Settings float validation. Enabling the watcher and saving
debounce `1` restored healthy startup.

The generator now applies Jinja `default(1, true)` to the candidate's existing
debounce environment mapping. Missing and empty values render as `1`; explicit
valid values such as `2` or `0.5` are preserved. The watcher form retains its
conditional visibility, runtime Settings parsing remains strict, and pinned
upstream inputs and historical package files remain unchanged. Focused tests
render both watcher states through the upstream library and parse the resulting
debounce with unchanged runtime Settings.

The task reports that the TrueNAS RC runtime canary passed. This is separate from
verification of this candidate template repair and does not prove real catalog
package migration. Real candidate-package migration and actual catalog package
migration remain pending; no catalog migration pass is claimed.

## Supported mechanism and provenance

The inspected [middleware upgrade implementation](https://github.com/truenas/middleware/blob/3a454c842718edfd9feef09847fbeb906f1d1eb4/src/middlewared/middlewared/plugins/apps/upgrade.py)
calls `upgrade_values` before `normalize_and_validate_values`, then adds context
and invokes `update_app_config` to render/persist. `get_data_for_upgrade_values`
reads `get_current_app_config` for the old installed version. The
[lifecycle implementation](https://github.com/truenas/middleware/blob/3a454c842718edfd9feef09847fbeb906f1d1eb4/src/middlewared/middlewared/plugins/apps/ix_apps/lifecycle.py)
loads that saved YAML without applying target defaults. Thus presence of a
dedicated field at this point is saved provenance; an absent field has no target
default yet. Do not call the migration after injecting target defaults.

The [migration selector](https://github.com/truenas/middleware/blob/3a454c842718edfd9feef09847fbeb906f1d1eb4/src/middlewared/middlewared/plugins/apps/migration_utils.py)
validates `app_migrations.yaml`, applies inclusive source/target version bounds,
and requires executable `migrations/<file>`. Middleware executes the script with
one saved-values YAML filename, consumes stdout as replacement YAML, and raises
on nonzero exit using stderr before normalization/render. The current upstream
[Immich migrations](https://github.com/truenas/apps/tree/bb96ef6fd8c2708765012a4a3ca3739370a4f92c/ix-dev/community/immich/migrations)
and its manifest provide real examples of this same interface and version policy.
The upgrade API merges explicit request overrides after migration; target schema
validation rejects any reserved name reintroduced by those overrides before render.

## Carry-forward contract

Exactly one mapping in `migrations/carry_forward_mcp` owns these four legacy names:

| Legacy additional_env name | Dedicated field |
|---|---|
| MCP_HTTP_ENABLED | mcp.http_enabled |
| MCP_WRITE_ENABLED | mcp.write_enabled |
| MCP_HTTP_ALLOWED_HOSTS | mcp.allowed_hosts |
| MCP_HTTP_ALLOWED_ORIGINS | mcp.allowed_origins |

For each field: carry legacy-only values, retain dedicated-only values, and apply
runtime defaults only when both are absent. Reject dual representation even when
equal. Reject duplicate legacy names and invalid legacy boolean strings. Work on
a deep copy; failure never changes the supplied state or input file. Errors name
settings/representations without their values. Only consumed reserved entries
are removed; unrelated entries, values and order remain unchanged. Repeat migration
is idempotent.

Boolean conversion matches the existing Pydantic runtime grammar, case-insensitive:
true tokens `1,on,t,true,y,yes`; false tokens `0,off,f,false,n,no`. No trimming,
Python string truthiness, or runtime grammar change is introduced. Host/Origin
strings are copied exactly, including empty strings and whitespace, leaving their
interpretation to unchanged runtime parsing. Disabled HTTP and writes plus the
runtime loopback allowlists remain fresh-install defaults.

The four dedicated fields render each reserved name once on the existing Web Port.
No port, auth, mount, capability, or application runtime change is made. Fields
are unconditional because current middleware's `show_if` handling can omit hidden
defaults. This preserves writes while HTTP is disabled and keeps rendering total.

Future installs/edits reject reserved names using the supported `valid_chars`
constraint on the additional_env name, with an explanatory description/error.
The [current schema constructor](https://github.com/truenas/middleware/blob/3a454c842718edfd9feef09847fbeb906f1d1eb4/src/middlewared/middlewared/plugins/apps/schema_construction_utils.py)
uses `AfterValidator(match_validator(re.compile(...)))` for this constraint.
The generic environment collision guard is unchanged and remains a fallback.

## Reproduce preparation and static checks

From the VaultBridge root, using development dependencies:

```text
.venv/Scripts/python.exe scripts/prepare_truenas_mcp.py --output <new-disposable-directory>
.venv/Scripts/python.exe -m pytest -q tests/test_truenas_mcp_preparation.py tests/test_truenas_package.py tests/test_config.py
```

Output must be a new disposable directory. The generator copies pinned inputs,
adds MCP form/environment fields and migration files, and sets executable mode.
It also adds the watcher debounce fallback to the generated template.
The generator normalizes output text to LF, including the migration shebang and
library; preserve its executable permission when transferring to POSIX. Historical package sources
and external checkouts are never overwritten.

The Jinja2, Docker Python client and bcrypt development dependencies exercise the
unchanged upstream library; its imports need these packages even for rendering.
The standard library cannot execute Jinja templates or satisfy those imports.
They add no application runtime dependency, service, or automatic network call.

Official components were inspected at [apps_validation commit e08605c9a0ab97efa835bd98eff00943768c846c](https://github.com/truenas/apps_validation/tree/e08605c9a0ab97efa835bd98eff00943768c846c).
On Linux, run its `validate_questions_yaml`, `validate_migration_config`,
`validate_migration_file`, `validate_templates`, and metadata JSON schema against
the materialized directory. Use `catalog_templating.render.render_templates` for
both test values and `docker compose -f <rendered-file> config --quiet`.
These component checks passed in WSL, as did execution of actual upstream
migration selection and `upgrade_values` functions with disposable saved-state
dependencies. The fixture script used the test virtualenv interpreter in place
of `/usr/bin/python3`; no migration body or middleware algorithm was changed.
This is component/static proof, not a running middleware/NAS test. Full devbox
catalog CI and a deployed candidate were NOT RUN. Full catalog CI remains a
separate prerequisite before submission.

The fixture suite covers cases 1–7, all four equal/differing dual-representation
conflicts (case 8), explicit false and supported boolean tokens, invalid values,
unrelated entries/order, dedicated-only state, absent-vs-saved defaults,
idempotence, subprocess success/failure, future-edit rejection and actual library
rendering/collision protection. See the separate review packet for executed totals.

## O1/O3 package migration: NOT YET VERIFIED

### Mandatory refresh immediately before O1 and again immediately before O3

Refresh the actual current truenas/apps/catalog VaultBridge package state separately
before each execution. Record the upstream commit / catalog revision inspected,
package version, application image/tag, and relevant migration range. Resolve any
upstream/catalog discrepancy before proceeding; do not silently use a stale baseline.

If the current package has advanced beyond the P2-pinned `1.0.3`, the newest current
package becomes the principal baseline and `1.0.3` becomes historical compatibility
evidence. Check migration bounds and fixtures include the newer version before
proceeding. If they do not, stop for a scoped preparation update; do not invent a
published target revision or proceed outside the validated range.

### O1 current-baseline candidate procedure

At the pinned evidence point, start from a disposable real current package `1.0.3` /
image `1.3.0` install with copied vault/data, subject to the mandatory refresh above.
The historical retained baseline `1.0.2` / image `1.3.0` may also be tested as a
compatibility case; it cannot replace the current-baseline case. Keep
its API key private; never capture auth headers, cookies or full environments.
Save these legacy additional_env entries through the old form:

```text
MCP_HTTP_ENABLED=true
MCP_WRITE_ENABLED=false
MCP_HTTP_ALLOWED_HOSTS=mcp-canary.example.test:*
MCP_HTTP_ALLOWED_ORIGINS=https://mcp-canary.example.test
CANARY_MARKER=preserve-this
```

Route the canary Host to the disposable NAS Web Port. Before upgrade, use an
authenticated MCP client with the accepted Origin above: initialize and perform
a read. Repeat with `https://mcp-denied.example.test`: require HTTP 403. Retain
sanitized status-only evidence. Upgrade through a supported candidate-package
workflow; if unavailable, record that limitation and keep the live gate pending.
Verify dedicated values, absence of reserved additional_env entries, marker
preservation, and each reserved generated environment name exactly once without
dumping other environment values. Repeat the accepted read and denied 403 pair.
Save/reopen Edit App and verify persistence. Test ambiguous dual representations
only on a disposable copy; require rejection before render without saved changes.
Restore explicitly through the supported recovery path if needed.

### O3 refresh-at-execution catalog procedure

Start the actual Community App upgrade from the package current immediately before
the new package is delivered. At this pinned P2 evidence point that is `1.0.3` /
image `1.3.0`; this is not a permanent version requirement. Perform the mandatory
refresh immediately before O3, recording the current upstream/catalog package and
selected application image/tag and verifying migration bounds/fixtures. Repeat the
full legacy configuration and before/after assertions above on the actual catalog
upgrade after P5. Historical retained cases cannot substitute for this current-package case.

Each of the following remains **NOT YET VERIFIED**: real candidate upgrade from
the refreshed current principal baseline (pinned `1.0.3` / image `1.3.0`);
non-default Host carry-forward; non-default Origin carry-forward; accepted
Origin initialization/read; denied Origin HTTP 403; Edit App persistence; actual
generated-catalog upgrade from the refreshed current package. Historical retained
`1.0.2` compatibility is also unverified live. Static tests do not close VB-082 or authorize publication.
