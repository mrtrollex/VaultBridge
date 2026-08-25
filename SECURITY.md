# Security Policy

## Scope

VaultBridge can be exposed to the internet through a reverse proxy/tunnel, so authentication and vault path handling are security-sensitive.

## Supported versions

Before `1.0.0`, only the latest release is expected to receive security fixes.

## Reporting a vulnerability

VaultBridge does not currently provide a confirmed dedicated private external vulnerability-reporting
channel. Do not include credentials, API keys, vault contents, exploit details, or other sensitive
data in public GitHub issues.

A private reporting channel may be added in the future. This document will be updated when one is
actually available.

## Security invariants

- Every write/read/search endpoint except health endpoints requires authentication.
- User-supplied paths must remain inside the configured vault root after resolution.
- Only Markdown note operations are exposed.
- API keys must never be written to logs.
- `.env`, semantic databases, model cache and user vault data are excluded from Git.
- HTTPS is expected at the public edge.
- The semantic index is derived data and may be deleted/rebuilt safely.

## Threats to test explicitly

- `../` traversal
- absolute paths
- symlink escape
- malformed/oversized note content
- missing/invalid Authorization header
- accidental API-key logging
- duplicate/retried writes
- hostile Markdown content (treated as data, never executed)
