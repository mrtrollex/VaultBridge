# VB-082 TrueNAS lifecycle validation runbook

**Status:** Executed pre-upstream record; VB-082 remains **IN PROGRESS / PARTIAL VALIDATION**.

**Scope:** Reusable procedure, result ledger, and sanitized VB-082 lifecycle evidence for the
generated VaultBridge Community App definition. The earlier captured and operator-confirmed results
remain recorded in
[`TRUENAS_COMMUNITY_APP_DESIGN.md`](TRUENAS_COMMUNITY_APP_DESIGN.md#vb-082-partial-validation-record--2026-09-02).

This runbook does not change the VaultBridge runtime, the released `v1.1.0` image, or any API or UI
contract. All executable pre-upstream gates are complete as of 2026-09-08, which unblocks only the
VB-083 submission/review phase. Neither VB-082 nor VB-083 is complete, and no submission is claimed.

> **Compromised test key:** The disposable API key used in the earlier VB-082 session was exposed.
> It is permanently non-reusable. Generate two new disposable values for this run and never paste
> either value into retained output, screenshots, app notes, portal URLs, issue text, or commits.

## Current gate classification

The PASS rows below are backed by the sanitized 2026-09-08 operator evidence retained in this file.
Classification-only and upstream-only rows are not executed PASS results.

| Remaining gate | Classification | Automation / required surface | Concrete reason |
|---|---|---|---|
| API-key rotation overlap | **PASS** | TrueNAS edit/redeploy plus status-only HTTP checks | Liveness and both disposable keys authenticated during the bounded overlap; no key value is retained. |
| Previous-key removal | **PASS** | TrueNAS edit/redeploy plus status-only HTTP checks | After clearing the previous key, the old key returned `401`, the new key returned `200`, and liveness remained `200`. |
| Web Port change from `30486` | **PASS — runtime behavior** | TrueNAS Custom App edit/redeploy | Port `30500` served live/ready/authenticated API and `/ui/`; `30486` stopped serving this app; vault hashes and derived data persisted. Real catalog edit-form behavior remains upstream-only. |
| Occupied-port negative | **PASS** | Bounded temporary listener plus TrueNAS update/recovery | Port `30501` remained owned by the blocker, the update failed with bounded address-in-use evidence, and the app recovered on `30500` without data loss. |
| Permission/ACL negative | **PASS** | Dedicated disposable POSIX dataset plus TrueNAS restart/redeploy | Readiness rejected the inaccessible vault, direct note read failed, liveness remained available, host permissions were not repaired by the app, and exact restoration recovered normal operation. |
| Supported upgrade | **UNSUPPORTED / NO VALID PRIOR PACKAGE STATE** | Documentation only for this initial package | VaultBridge has one Community App package version (`version: 1.0.0`, `app_version: 1.1.0`) and no accepted earlier package. A source-built/custom-YAML image is not an earlier supported Community App package. Do not invent one. |
| Rollback behavior | **BLOCKED** | Real catalog app with at least two installed catalog revisions | Current TrueNAS supports rollback to a prior catalog revision and can optionally roll back app-pool snapshots, but VaultBridge has no prior catalog revision to select. This is not “unsupported by TrueNAS.” |
| External vault preservation on uninstall | **PASS** | Exact generated host-path mapping through TrueNAS Custom App delete | The external vault remained present with identical hashes and unchanged numeric metadata. |
| Host-path `/data` uninstall behavior | **PASS** | Exact host-path-data render through TrueNAS Custom App delete | External host-path data remained present with an identical before/after manifest and unchanged numeric metadata. |
| ixVolume uninstall behavior | **REQUIRES UPSTREAM CATALOG/PR** | Real catalog-app Delete dialog | TrueNAS documents an explicit **Remove iXVolumes** choice, shown only for apps that own iXVolumes. Custom YAML or ordinary named volumes do not prove that UI/ownership path. |
| Real question form and edit form | **REQUIRES UPSTREAM CATALOG/PR** | Real TrueNAS Apps UI | TrueNAS 24.10 and later do not provide a supported third-party catalog-loading path; Custom App forms/YAML are not the generated catalog question form. |
| Real Web UI / Portal button | **REQUIRES UPSTREAM CATALOG/PR** | Real TrueNAS Apps UI | TrueNAS documents that YAML custom apps do not include the Web UI button. The generated portal metadata must be delivered through the catalog. |

Current authoritative platform references:

- [Managing Installed Apps](https://apps.truenas.com/managing-apps/managing-installed-apps/) documents
  edit/update, rollback, snapshot scope, deletion, the **Remove iXVolumes** choice, and the absence of
  a Web UI button for YAML custom apps.
- [TrueNAS 25.10 Apps UI reference](https://www.truenas.com/docs/scale/25.10/scaleuireference/apps/)
  documents the supported catalog and Custom App surfaces.
- The pinned upstream contributor contract remains
  [`truenas/apps` `906a20a...` `CONTRIBUTIONS.md`](https://github.com/truenas/apps/blob/906a20a22ee885add8c620660eba3d6ed51289da/CONTRIBUTIONS.md).

## Evidence rules

Retain only:

- TrueNAS full version/build, host architecture, and test timestamp;
- Community App package version, app version, image tag, and resolved image digest;
- sanitized UI screenshots with secret fields masked and private addresses/paths cropped or redacted;
- HTTP status codes, state names, relative synthetic filenames, file hashes, and bounded error text;
- before/after numeric ownership and permission modes for disposable paths;
- whether each result is `PASS`, `FAIL`, `UNSUPPORTED / NO VALID PRIOR PACKAGE STATE`,
  `REQUIRES UPSTREAM CATALOG/PR`, or `BLOCKED`.

Never retain:

- either API key, an `Authorization` header, a resolved secret-bearing Compose environment, or shell
  output that contains a key;
- private host paths, hostnames, IP addresses, vault content, unrelated app/container names, or
  complete support bundles;
- container inspection output containing environment variables.

Run credential checks from a non-recorded terminal. Do not enable shell tracing (`set -x`). The
commands below read keys silently so they are not present in shell history. Retain only the printed
labels and HTTP status codes.

## Preconditions

1. Use the disposable TrueNAS validation host only. Record its current full version/build from
   **System Settings > General > System Information** and confirm the platform is `linux/amd64`.
2. Confirm no production VaultBridge app, real vault, or real API key is in scope.
3. Create a disposable dataset/directory containing exactly two synthetic Markdown files. Record a
   relative-name SHA-256 manifest before testing.
4. Create a separate disposable host-path directory for the `/data` alternative.
5. Use the current submission definition with package `version: 1.0.0`, `app_version: 1.1.0`, image
   `ghcr.io/mrtrollex/vaultbridge:1.1.0`, and current Community App default Web Port `30488`.
6. Resolve the running image and require OCI index digest
   `sha256:753e613617d221c3dac311600a36cab3f2727b09f630321664eaa7b7ad6eb48c`.
7. Select one unused disposable port as `<FREE_PORT>` and a different unused port as
   `<OCCUPIED_PORT>`. Do not use `8765`, `30488`, or a port belonging to another application.
8. Take a TrueNAS configuration backup if required by local operator policy. Do not include it in
   repository evidence.

Prepare a non-recorded TrueNAS shell once:

```sh
set -eu
APP_NAME='vaultbridge-vb082'
DEFAULT_PORT='30488'
FREE_PORT='<FREE_PORT>'
OCCUPIED_PORT='<OCCUPIED_PORT>'
VAULT_PATH='<DISPOSABLE_VAULT_HOST_PATH>'
DATA_PATH='<DISPOSABLE_DATA_HOST_PATH>'
read -r -s -p 'Current disposable API key: ' VB082_OLD_KEY; printf '\n'
read -r -s -p 'New disposable API key: ' VB082_NEW_KEY; printf '\n'
test "${#VB082_OLD_KEY}" -ge 32
test "${#VB082_NEW_KEY}" -ge 32
test "$VB082_OLD_KEY" != "$VB082_NEW_KEY"
```

The literal placeholders must be replaced only in the live disposable shell/UI. Do not save a
filled copy of this runbook.

## Stage 1 - API-key rotation overlap

**Preconditions:** The disposable app is healthy on the current default port; both new disposable keys are loaded in
the non-recorded shell; public health and one protected request work with the current key.

**Exact steps:**

1. In the app configuration, set **API Key** to the new key and **Previous API Key** to the old key.
   Save/update and wait for the app to return to Running/healthy.
2. Run this status-only check locally on TrueNAS:

```sh
base="http://127.0.0.1:${DEFAULT_PORT}"
printf 'health_live=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' "$base/health/live")"
printf 'old_key=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $VB082_OLD_KEY" "$base/api/v1/notes/list?limit=1")"
printf 'new_key=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $VB082_NEW_KEY" "$base/api/v1/notes/list?limit=1")"
```

**PASS:** `health_live=200`, `old_key=200`, and `new_key=200`; the UI shows both secret fields
masked; neither key appears in app notes, the Portal URL, retained screenshots, or application logs.

**FAIL:** Either key is printed or persisted in evidence; health changes because of authentication;
either intended key is rejected; an unknown key authenticates; redeploy changes storage mappings.

**Cleanup:** Continue immediately to Stage 2. Do not leave the overlap active longer than needed.

**Retain:** Three labelled status codes, Running/healthy state, timestamp, and one sanitized masked-
field screenshot. Do not retain request commands after substituting actual values.

## Stage 2 - remove the previous key

**Preconditions:** Stage 1 passed.

**Exact steps:**

1. Clear **Previous API Key**, keep **API Key** set to the new key, save/update, and wait for
   Running/healthy.
2. Re-run the Stage 1 status-only block.

**PASS:** `health_live=200`, `old_key=401`, and `new_key=200`. The `401` response remains generic and
does not identify which key was rejected.

**FAIL:** Old key still authenticates, new key fails, public health requires a key, or either key is
recorded.

**Cleanup:** Leave only the new disposable key configured. At the end of all validation, unset both
shell variables with `unset VB082_OLD_KEY VB082_NEW_KEY`.

**Retain:** The three labelled status codes and sanitized app state only.

## Stage 3 - Web Port edit and persistence

**Preconditions:** Stages 1-2 passed; `<FREE_PORT>` is confirmed unused; the vault manifest and a
derived-data marker/count have been recorded without private paths.

**Exact steps:**

1. Edit the exact Custom App YAML/rendered mapping from `<DEFAULT_PORT>:8000` to `<FREE_PORT>:8000`; do not
   change mounts, image, environment, user, capabilities, or health check. Save/update.
2. Wait for Running/healthy and run:

```sh
printf 'new_port_health=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:${FREE_PORT}/health/live")"
printf 'new_port_api=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $VB082_NEW_KEY" \
  "http://127.0.0.1:${FREE_PORT}/api/v1/notes/list?limit=1")"
printf 'old_port=%s\n' "$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:${DEFAULT_PORT}/health/live" || true)"
```

3. Open `http://<TRUENAS_HOST>:<FREE_PORT>/ui/` manually, unlock with the new disposable key, and
   confirm one synthetic note can still be listed/read. Do not retain the host address or content.
4. Recompute the relative-name vault manifest and confirm the derived-data marker/count survives.

**PASS:** New port health/API/UI work; old port does not serve this VaultBridge instance; hashes and
derived persistence evidence are unchanged; only the intended published port is present.

**FAIL:** Both ports still serve this app, another service is mistaken for VaultBridge, the app
publishes an unexpected port, data changes/disappears, or redeploy changes the runtime contract.

**Cleanup:** Leave the app temporarily on `<FREE_PORT>` for Stage 4.

**Retain:** Status codes, sanitized port numbers, app state, and before/after hashes/counts.

## Stage 4 - occupied-port negative

**Preconditions:** Stage 3 passed. Confirm `<OCCUPIED_PORT>` is disposable and unused before starting
the listener.

**Exact steps:**

1. Start a bounded temporary listener and verify it returns `200`:

```sh
nohup python3 -m http.server "$OCCUPIED_PORT" --bind 0.0.0.0 \
  >/tmp/vb082-port-blocker.log 2>&1 &
BLOCKER_PID=$!
printf '%s\n' "$BLOCKER_PID" >/tmp/vb082-port-blocker.pid
curl -sS -o /dev/null -w 'blocker=%{http_code}\n' \
  "http://127.0.0.1:${OCCUPIED_PORT}/"
```

2. Attempt to update the app Web Port to `<OCCUPIED_PORT>` without changing anything else.
3. Capture only the bounded TrueNAS error/state showing address/port allocation failure.
4. Verify the blocker still returns `200`; verify the vault and `/data` evidence remains intact.
5. Stop only the recorded listener, remove its two `/tmp` files, restore `<FREE_PORT>`, and wait for
   the app to become healthy:

```sh
BLOCKER_PID="$(cat /tmp/vb082-port-blocker.pid)"
case "$BLOCKER_PID" in *[!0-9]*|'') printf 'REFUSE: invalid blocker PID\n' >&2; exit 2;; esac
ps -p "$BLOCKER_PID" -o args= | grep -F "python3 -m http.server $OCCUPIED_PORT"
kill "$BLOCKER_PID"
wait "$BLOCKER_PID" 2>/dev/null || true
rm -f /tmp/vb082-port-blocker.pid /tmp/vb082-port-blocker.log
```

**PASS:** Update/deploy fails clearly; the listener is not disrupted; no second VaultBridge instance
starts; data remains intact; restoring `<FREE_PORT>` returns the app to Running/healthy.

**FAIL:** TrueNAS reports success while binding the occupied port, kills/replaces the listener,
corrupts data/configuration, or cannot recover after restoring the prior port.

**Cleanup:** Confirm the blocker PID no longer exists and both `/tmp/vb082-port-blocker*` files are
gone. Keep the app on `<FREE_PORT>`.

**Retain:** Sanitized error category, app state sequence, blocker status before/after, data hashes,
and cleanup confirmation. Do not retain general Docker logs.

## Stage 5 - permission/ACL negative

**Preconditions:** Disposable vault only. Confirm the configured `/vault` source resolves exactly to
`<DISPOSABLE_VAULT_HOST_PATH>`. Stop if it resolves outside the disposable test dataset.

**Exact steps:**

1. Record numeric owner/group/mode and the relative-name file manifest:

```sh
test -d "$VAULT_PATH"
case "$VAULT_PATH" in
  */vb082-*|*/vb082/*) ;;
  *) printf 'REFUSE: path is not visibly disposable\n' >&2; exit 2 ;;
esac
stat -c 'before uid=%u gid=%g mode=%a' "$VAULT_PATH"
(cd "$VAULT_PATH" && find . -type f -name '*.md' -print0 | sort -z | xargs -0 sha256sum)
```

2. Record the original numeric mode in a non-retained shell variable, then remove access without
   changing ownership or ACL entries:

```sh
VB082_ORIGINAL_MODE="$(stat -c '%a' "$VAULT_PATH")"
chmod 000 "$VAULT_PATH"
stat -c 'denied uid=%u gid=%g mode=%a' "$VAULT_PATH"
```

3. Restart/redeploy the app without enabling **Enable ACL**. Check `/health/live`,
   `/health/ready`, richer `/health`, one protected list request, and one direct protected note read.
   Inspect only bounded VaultBridge lifecycle errors; do not retain raw paths or full logs.
4. Confirm the host owner/group remain identical and mode remains `000`; confirm no permissions
   helper action targets `/vault`.
5. Restore the exact original mode and verify normal readiness/listing:

```sh
chmod "$VB082_ORIGINAL_MODE" "$VAULT_PATH"
stat -c 'restored uid=%u gid=%g mode=%a' "$VAULT_PATH"
unset VB082_ORIGINAL_MODE
```

**PASS:** Liveness may remain `200`, but readiness and direct vault reads must not falsely report a
usable vault; the failure is bounded and observable; `/vault` owner/group/ACL are not changed by the
app or helper; restoring permissions returns readiness and direct reads to normal with unchanged
file hashes. The list endpoint may still return `200`; that observation alone is not proof that note
content is readable.

**FAIL:** `/health/ready` or a direct note read claims normal usable-vault behavior while access is
denied; the app/helper chmods, chowns, or rewrites the vault ACL; failure loops damage the host; normal
operation does not recover after exact restoration.

**Cleanup:** Restore the exact original mode even after a failed test. Verify hashes and leave the
disposable vault readable/writable by UID/GID `568:568` for later stages.

**Retain:** Numeric before/denied/restored owner/group/mode, status codes, safe lifecycle event names,
and file hashes. Never retain the private source path or full error stack.

## Stage 6 - upgrade classification

**Preconditions:** Inspect the VaultBridge package history available to TrueNAS.

**Exact steps:**

1. Record that the only defined Community App package is `version: 1.0.0` with
   `app_version: 1.1.0` and image tag `1.1.0`.
2. Confirm no earlier accepted VaultBridge Community App package/revision exists in the catalog.
3. Do not install `v1.0.0` through Custom YAML and call that a Community App upgrade source.

**Expected result:** **UNSUPPORTED / NO VALID PRIOR PACKAGE STATE**. This is an accurate initial-
package limitation, not a PASS execution and not a fabricated upgrade.

**FAIL:** Claiming upgrade from a hand-made earlier package, floating tag, source-built Custom App,
or unverified image/package combination.

**Cleanup:** None.

**Retain:** Package/app/image versions and the absence of a prior supported package state.

## Stage 7 - rollback capability and current blocker

**Preconditions:** Current TrueNAS documentation has been checked.

**Exact steps:**

1. Record that TrueNAS supports catalog-app rollback to a prior catalog revision.
2. Record that **Roll back snapshots** affects app-pool data such as ixVolumes, not mounted host
   paths.
3. Confirm VaultBridge has no earlier catalog revision available to select.

**Expected result:** **BLOCKED** for VaultBridge. TrueNAS supports the lifecycle operation, but there
is no valid prior VaultBridge catalog revision. Do not label this `UNSUPPORTED BY CURRENT TRUENAS`.

When a second real catalog revision exists, validate only with disposable data: capture the selected
revision; hash `/vault`; record `/data` state; run rollback once without and, if separately justified,
once with **Roll back snapshots**; require host-path vault preservation in both cases.

**FAIL:** Claiming rollback from a container restart, manual image-tag edit, dataset snapshot alone,
or Custom YAML conversion.

**Cleanup:** None until the blocked prerequisite exists.

**Retain:** Authoritative documentation URL, TrueNAS version, and `BLOCKED` reason.

## Stage 8 - external host-path uninstall ownership

**Preconditions:** Stages 1-5 passed; the disposable vault and host-path `/data` alternative both have
before manifests. Use the exact host-path-data generated render. This tests the same TrueNAS app
deletion and external bind-mount ownership property; it is not Docker-only evidence and does not
claim ixVolume or catalog-form behavior.

**Exact steps:**

1. Install/update the disposable Custom App with `/vault` and `/data` both bound to verified
   disposable host paths. Reach ready state and create only rebuildable derived data.
2. Stop and delete the Custom App through TrueNAS. Do not run any filesystem cleanup command.
3. Recompute the relative-name SHA-256 manifests and numeric owner/group/mode for both paths.

**PASS:** Both external host paths and all files remain byte-for-byte
intact with unchanged ownership/permissions. No package/app cleanup code targets them.

**FAIL:** TrueNAS/app deletion removes or mutates either external path, or evidence cannot distinguish
app deletion from later manual cleanup.

**Cleanup:** Preserve both paths until evidence is reviewed. Their later deletion is an explicit
operator action outside this runbook.

**Retain:** Before/after relative hashes and numeric metadata only.

## Stage 9 - real catalog question form, Portal, and ixVolume uninstall

**Preconditions:** **REQUIRES UPSTREAM CATALOG/PR.** The accepted/generated VaultBridge entry is
available through the real TrueNAS catalog delivery path. Do not convert a catalog app to Custom
App; TrueNAS documents conversion as permanent.

**Exact steps:**

1. Open the real install form and verify all generated fields: masked current/previous API keys;
   required vault host path and ACL control; ixVolume/host-path data selector; watcher and debounce;
   UID/GID; current Web Port default `30488`; host IPs/networks; CPU `2`; memory `4096` MB; optional labels.
2. Install with the default ixVolume and disposable vault. Require Running/healthy, readiness,
   authenticated API/UI behavior, and **Web UI** opening `/ui/` on the selected port.
3. Open Edit. Require stored secret values to remain masked and the port/watcher/resource fields to
   render correctly. Do not change storage type; current TrueNAS documents storage volume type as
   non-editable.
4. Hash the external vault, then stop and delete the app with **Remove iXVolumes** cleared. Require
   the external vault to remain intact. Record whether the ixVolume is retained.
5. Reinstall with a fresh disposable ixVolume, create derived data, hash the external vault, then
   stop/delete with **Remove iXVolumes** selected. Require the external vault to remain intact and
   record that the app-owned ixVolume is deleted. Never select force-remove for this new app.
6. Repeat one final install/delete using host-path `/data`; require both host paths to remain.

**PASS:** Form values and masking match `questions.yaml`; Web UI opens `/ui/`; edit behavior matches
the generated contract; external vault and host-path data always remain; the ixVolume choice is
explicit and its observed retain/delete behavior matches the selected option.

**FAIL:** Custom YAML is presented as catalog-form proof; secrets appear in Portal/notes/evidence;
the external vault changes; ixVolume behavior differs from the explicit choice; force-remove is
needed for a fresh package; Portal does not target `/ui/`.

**Cleanup:** Delete only the disposable app and, when deliberately selected, its disposable
ixVolume. Preserve host paths until manifests are reviewed. Never delete a real vault.

**Retain:** Sanitized form/Portal/Delete screenshots, selected ixVolume option and outcome, app
versions/digest, and external-path hashes. Do not retain filled secret values or private paths.

## Sanitized execution evidence — 2026-09-08

All evidence below uses disposable credentials and synthetic Markdown. No API key, Authorization
header, private hostname/address, resolved private host path, raw environment, or unrelated
application log is retained. The disposable key exposed in the earlier VB-082 session remains
permanently compromised and non-reusable.

### API-key rotation overlap and removal — PASS

The healthy baseline returned `health_live=200`, and the current disposable key authenticated with
HTTP `200`. During the bounded overlap, `API_KEY` held the new disposable value and
`API_KEY_PREVIOUS` held the old disposable value: liveness returned `200`, and both old and new keys
authenticated with `200`. After `API_KEY_PREVIOUS` was cleared, liveness remained `200`, the old key
returned `401`, and the new key returned `200`. No key value is retained.

### Web Port change and persistence — PASS for runtime behavior

The disposable Custom App moved from port `30486` to `30500`. On `30500`, liveness, readiness, and
an authenticated API request returned `200`; port `30486` returned `000`. The `/ui/` route on
`30500` loaded and showed Ready / 2 indexed notes. The two synthetic vault files retained these
SHA-256 values:

```text
alpha.md  b551001ca83c182986862b9f59839aa49aa28bc3a2e8332d063e325bf1b47bcb
beta.md   e113a2078a99a1cb85bc0193da3a9537f844340dd81205ebfbc5451a4bc2293b
```

Derived `/data` persisted. Its file count changed from `17` to `19` during normal runtime/redeploy
activity, with no data loss observed. This retained evidence does **not** prove the stricter
authenticated UI unlock and note-read subcheck after the port change, so that subcheck is not
upgraded to PASS. It also does not prove the separate real-catalog edit form or generated Portal
button.

### Occupied-port negative and recovery — PASS

A bounded disposable listener on `30501` returned `blocker=200` before the update. The TrueNAS update
to `30501` failed with the bounded lifecycle error `failed to bind host port for 0.0.0.0:30501 ...
address already in use`. The listener still returned `200`; VaultBridge did not steal or replace it;
the vault hashes were unchanged; and `/data` remained present. After restoring port `30500`,
liveness, readiness, and the authenticated API each returned `200`. Blocker cleanup succeeded,
`30501` then returned `000`, and final readiness on `30500` returned `200`.

### Permission/ACL negative and recovery — PASS

The initial plan correctly stopped on a shared NFSv4 ACL dataset because `acltype=nfsv4`,
`aclmode=restricted`, and `chmod 000` was rejected. No shared-dataset ACL property was changed.

The executed negative case instead used a dedicated disposable POSIX dataset with `readonly=off`,
owner/group `568:568`, baseline mode `770`, and the same two synthetic file hashes recorded above.
The healthy baseline returned live `200`, ready `200`, and authenticated list `200`. After
`chmod 000`, owner/group remained `568:568`, the app remained Running, liveness returned `200`,
readiness returned `503`, and `/health` returned `200` while reporting zero vault and indexed notes.
The authenticated list endpoint returned `200`, while a direct read of `alpha.md` returned `500
Internal Server Error`. Mode remained `000` after restart; VaultBridge did not chmod, chown, or
otherwise repair the vault.

Restoring the exact mode `770` returned live `200`, ready `200`, authenticated list `200`, and direct
`alpha.md` read `200`. Owner/group remained `568:568`, final mode was `770`, and both file hashes were
unchanged. The list endpoint's observed `200` during denial is retained as-is and is not
misrepresented as proof that every authenticated operation failed.

### External host-path uninstall ownership — PASS

Before deletion, the exact disposable app `vaultbridge-vb082` was stopped. Its disposable external
vault and external host-path `/data` were both owner/group `568:568`, mode `770`; the vault contained
2 files and `/data` contained 19 files. The vault and data manifests were captured before deleting
the Custom App through TrueNAS.

After deletion, `vaultbridge-vb082` no longer existed, while both external paths remained. Their
owner/group/mode values were unchanged, and each before/after SHA-256 manifest was identical.
External `/vault` preservation and host-path `/data` preservation on uninstall are therefore PASS.
This does not prove ixVolume retain/remove behavior.

### Classification-only and upstream-only results

- Supported Community App upgrade: **UNSUPPORTED / NO VALID PRIOR PACKAGE STATE**. The only package
  is `version: 1.0.0`, `app_version: 1.1.0`, image `1.1.0`; no earlier accepted package exists. This
  is not an executed PASS.
- Rollback: **BLOCKED**. Current TrueNAS supports rollback to prior catalog revisions, but VaultBridge
  has no prior real catalog revision. This is not `UNSUPPORTED BY CURRENT TRUENAS` and not PASS.
- Real generated install/question form, edit form, secret masking, generated Web UI / Portal button
  targeting `/ui/`, catalog-only storage UI behavior, and ixVolume uninstall retain/remove behavior:
  **REQUIRES UPSTREAM CATALOG/PR**.

All executable pre-upstream VB-082 gates are now complete. VB-083's submission/review phase is
therefore unblocked so that the supported real catalog surface can become available. VB-082 remains
**IN PROGRESS / PARTIAL VALIDATION**, VB-083 is neither submitted nor complete, and the remaining
VB-082 catalog-only gates and eventual rollback criterion are unchanged.

## Reusable execution order and operator-action estimate

Execute in this order to minimize redeploys and preserve recovery paths:

1. Preconditions and evidence baseline.
2. Rotation overlap.
3. Previous-key removal.
4. Free-port edit and persistence.
5. Occupied-port negative, cleanup, and port restoration.
6. Permission negative, exact permission restoration, and recovery.
7. Host-path `/data` plus Custom App uninstall ownership check.
8. Record upgrade as **UNSUPPORTED / NO VALID PRIOR PACKAGE STATE**.
9. Record rollback as **BLOCKED** until a prior real catalog revision exists.
10. After the upstream catalog path exists, run the real form/Portal and three storage-delete cases.

Estimated manual operator actions, counting a grouped UI edit, shell block, or evidence checkpoint as
one action rather than individual clicks:

| Phase | Manual actions |
|---|---:|
| Ready-now preflight, rotation, port, collision, permission, and host-path uninstall | 16 |
| Classification-only upgrade/rollback review | 2 |
| Later real catalog form/Portal and storage ownership validation | 9 |
| **Total if all prerequisites become available** | **27** |

No repository helper script is added. Most safe assertions are already copy/paste command blocks;
automating the TrueNAS UI mutations would hide the exact platform behavior under test and would add
risk around app deletion and storage ownership.

## Result ledger

| Gate | Result | TrueNAS/package/image evidence | Sanitized evidence reference |
|---|---|---|---|
| Rotation overlap | PASS | Old/new disposable keys both `200`; live `200`; no values retained | 2026-09-08 sanitized execution evidence above |
| Previous-key removal | PASS | Old key `401`; new key `200`; live `200` | 2026-09-08 sanitized execution evidence above |
| Web Port edit | PASS — runtime behavior | `30486` to `30500`; live/ready/API `200`; old port `000`; hashes/data persisted | 2026-09-08 evidence; authenticated UI note-read subcheck not retained |
| Occupied-port negative | PASS | `30501` blocker retained ownership; address-in-use failure; recovery on `30500` | 2026-09-08 sanitized execution evidence above |
| Permission/ACL negative | PASS | mode `000`; live `200`; ready `503`; direct read `500`; exact `770` recovery | 2026-09-08 sanitized execution evidence above |
| Upgrade | UNSUPPORTED / NO VALID PRIOR PACKAGE STATE | Package `1.0.0`; app/image `1.1.0`; no prior package | Classification only; not PASS |
| Rollback | BLOCKED | Current TrueNAS supports it; no prior VaultBridge catalog revision | Requires a prior real catalog revision |
| External vault / host-path data uninstall | PASS | Both paths and manifests/metadata unchanged after TrueNAS app deletion | 2026-09-08 sanitized execution evidence above |
| ixVolume uninstall | REQUIRES UPSTREAM CATALOG/PR | Real catalog Delete dialog required | Not executed |
| Real question/edit form, secret masking, storage UI, and Portal | REQUIRES UPSTREAM CATALOG/PR | Supported real catalog surface required | Not executed |

VB-082 must remain **IN PROGRESS / PARTIAL VALIDATION** until every required acceptance criterion has
actual retained evidence or a classification explicitly permitted by its contract. Completion of
all executable pre-upstream gates unblocks only the VB-083 submission/review phase; `BLOCKED` and
`REQUIRES UPSTREAM CATALOG/PR` rows still prevent VB-082 completion, and VB-083 completion remains
subject to its own acceptance criteria.
