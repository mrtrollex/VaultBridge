# VaultBridge

[VaultBridge](https://github.com/mrtrollex/VaultBridge) is a self-hosted REST and semantic search
bridge for Obsidian Markdown vaults. It is licensed under the MIT License. Project documentation is
available in the [repository README](https://github.com/mrtrollex/VaultBridge#readme) and the
[TrueNAS deployment guide](https://github.com/mrtrollex/VaultBridge/blob/main/README_TRUENAS.md).

This directory is a pre-submission development copy of the future TrueNAS Community App definition.
It does not make VaultBridge available in TrueNAS Discover and is not an official TrueNAS app. The
accepted upstream definition will be owned by `truenas/apps`; this copy exists only so VB-081 can be
developed and statically reviewed before release and submission gates are satisfied.

## Release gate

No dashboard-capable VaultBridge release is selected by VB-081. `ix_values.yaml` therefore does not
contain a production `images` map. Render fixtures use the clearly synthetic
`0.0.0-vb081-development-placeholder` tag only to exercise the template. Before upstream submission,
VB-075/VB-082 must provide and verify a public, versioned, anonymously pullable image and the source
must pin:

```text
ghcr.io/mrtrollex/vaultbridge:<released-version>
```

The release tag, matching `app_version`, final TrueNAS CDN icon URL, factual upstream `date_added`,
generated library/hash, and generated catalog metadata remain release/submission finalization work.
Test-only image values must not be copied into the contribution.

## Storage and permissions

The selected Obsidian vault is mounted read/write at `/vault` by default and remains authoritative.
VaultBridge does not create, copy, recursively change, or automatically repair ownership or ACLs on
that path. The selected non-root UID/GID must already have traverse and read/write access, unless the
operator deliberately enables and configures the TrueNAS ACL form. A read-only mount is supported
for retrieval-only use; note create and append operations then fail normally.

Persistent derived semantic data and model caches are mounted at `/data`. The default is a
TrueNAS-managed ixVolume; an existing host path is also supported. The standard permissions helper
may prepare app-owned `/data`, but it is never applied to `/vault`.

If the application cannot access `/vault`, grant the configured UID/GID the minimum necessary access
using the dataset's existing POSIX or ACL model, then restart the application. Do not use broad
world-writable permissions or recursively replace an existing vault ACL.

The Web Portal opens `/ui/` on the configured Web Port. It never includes an API key. API keys are
masked in the install form, but this is UI masking rather than an encrypted secret-store guarantee;
a privileged TrueNAS or Docker administrator can inspect deployed container configuration.
