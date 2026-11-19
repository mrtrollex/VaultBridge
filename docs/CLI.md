# CLI workflows and index maintenance

Run from an installed Python environment or the matching container image. See [Docker maintenance](DOCKER.md) and [named-mode CLI limits](KNOWLEDGE_SPACES.md#stopped-service-cli).

The Compose examples below use the source-build Compose path. For the README's published-image
overlay, add `-f docker-compose.yml -f compose.release.yml` to every Compose command so the CLI and
serving application use the same pinned image/configuration. Do not switch images during maintenance.



The local CLI reuses VaultBridge's existing vault and semantic services:

```bash
python -m app.cli status
python -m app.cli index
python -m app.cli reindex
python -m app.cli search "backup"
python -m app.cli related "how is my backup replicated?"
python -m app.cli query --semantic-text "backup" --folder Projects --tag active --limit 10
```

`status` is the concise persisted vault/index view. `search` is literal title/content search and
does not load the embedding model. `related` uses the existing compatible semantic index without
synchronizing it and live-verifies result paths before display. Both query commands support
`--folder`; `search` supports `--limit`, while `related` supports `--limit` and `--min-score`.
Those commands print bounded snippets and vault-relative paths. `query` calls the bounded
`KnowledgeQueryService` and supports `--semantic-text`, `--literal-text`, `--folder`,
repeatable exact `--path`, repeatable required `--tag`, and `--limit`. It prints only canonical
vault-relative paths and existing semantic score evidence when applicable. Empty results are
successful.

For the shared structured Knowledge Query request, use:

```bash
python -m app.cli query --request-json --json < query-request.json
python -m app.cli query --literal-text "backup" --qualified-path '{"space_id":"default","path":"Projects/Backup.md"}' --json
```

`--request-json` reads one complete UTF-8 object, at most 1,048,576 bytes, with optional `spaces`,
`semantic_text`, `literal_text`, `folder`, `paths`, `tags`, `metadata`, `relationships` and `limit`.
For example, `{"literal_text":"backup","metadata":[{"key":"status","operator":"equals","value":"active"}]}`.
It uses the same request as REST `POST /api/v1/knowledge/query` and MCP `knowledge_query`.
Unknown/duplicate keys, non-finite values and conflicting flags are rejected. Do not combine
`--request-json` with query-content flags; `--space` may supply selection only when JSON omits
`spaces`. Repeatable `--qualified-path` accepts one `{space_id,path}` object per occurrence and
cannot mix with bare `--path`. Neither form expands the selected scope. Bare paths require one
effective selected space. Omitted selection remains default-only.

Structured requests and explicit `--space` use qualified results and selected-space coverage;
REST Query always uses this structure, and MCP adds a canonical `resource_uri` per item. Semantic
text selects semantic score fields; other Query results contain qualified note identities.
CLI reads retain persisted read-only composition: no startup sync, storage initialization, watcher
or model download. Named production composition and its deliberate UI/maintenance exclusions are
described in [Named Knowledge Spaces](KNOWLEDGE_SPACES.md). See the
[exact request and error contract](../BACKLOG.md#vb-152--permission-aware-query-and-write-adapter-integration--p1-complete-ad-accepted-and-merged).

`capture` reads exactly one UTF-8 JSON request from standard input (at most 1,048,576 bytes):

```bash
python -m app.cli capture < capture-request.json
```

The request requires `content`, `state` (`inbox` or `draft`), `capture_id` (a lowercase UUID v4),
and `captured_at` (UTC RFC 3339 at second precision, with `Z` or `+00:00`). Optional fields are
`title`, `source`, ordered `tags`, `capture_type`, and `metadata` for additional safe portable
frontmatter values. Duplicate JSON keys and unknown top-level fields are rejected. For example:

```json
{"content":"Selected Markdown text\n","state":"inbox","capture_id":"9f563c82-f963-4a44-9f36-1f7c6f459a4c","captured_at":"2026-10-03T12:30:00Z","source":"unknown","tags":[]}
```

Capture creates only `Inbox/Captures/<capture_id>.md`; title is display metadata. It retains the
exact authored body and ordered portable frontmatter. Keep the original request, ID, and timestamp
for retries. Exact bytes at that path yield `already_applied`; differences yield `conflict` without
overwriting or choosing another path. Output contains the stable category and `committed` boolean;
committed outcomes also include the ID, canonical path, and separate `index_pending` or
`index_unavailable` evidence. An accepted indexing job may subsequently fail without changing
Markdown success. After `commit_unknown`, retain the original request and inspect the exact
canonical path. Verified matching bytes can resolve the outcome as `already_applied`. If the path
is absent after a possible commit or namespace relocation, do not automatically retry: an operator
must resolve the possible relocated artifact and explicitly authorize another write. If another
same-ID artifact cannot be ruled out, do not write again.
Expected failures exit `1`, confirmed Markdown success exits `0`, and configuration/programming
failures exit `2`. Content and metadata are never echoed in diagnostics.

Capture enqueues existing local indexing after commit and drains its worker before CLI exit.
Keep the serving process stopped while running this CLI command because semantic persistence has
no cross-process index lock. Markdown creation itself coordinates cooperating VaultService writers
across local processes and threads: a POSIX directory lock or Windows kernel mutex, plus a complete
temporary file linked atomically without replacement. Active capture destination or staging
namespace mutation by a non-cooperating filesystem writer is outside that strong guarantee;
detected uncertainty does not confirm creation. Ordinary external Markdown editing remains
supported. The filesystem must support hard links; unsupported filesystems fail safely. This adds
no capture database. Explicit review and promotion are separate CLI operations described below.

`hygiene scan` reads live Markdown and reports relationship (wikilink and inline Markdown link),
isolation, frontmatter, alias and authored-body diagnostics without repairs:

```bash
python -m app.cli hygiene scan --json
python -m app.cli hygiene scan --group relationships --finding-limit 100
```

The same bounded diagnostics are available through protected
`POST /api/v1/knowledge/hygiene/scan`, MCP `knowledge_hygiene_scan`, and the legacy Dashboard Hygiene panel.
Coverage, unavailable notes, truncation and optional duplicate/index evidence are explicit; advice
is not a repair, freshness guarantee or semantic-duplicate promise.

Explicit promotion retains the capture source and requires a human decision:

```bash
python -m app.cli promote review --source-path Inbox/Captures/<capture-id>.md --capture-id <capture-id>
python -m app.cli promote apply < promotion-decision.json
```

Replace `<capture-id>` with the original lowercase UUID v4. Review emits the source bytes/metadata
and `source_sha256`; `--destination Notes/Target.md` additionally inspects an append destination.
Treat review output as private vault content. Apply accepts one bounded JSON decision with
`source_path`, `capture_id`, `expected_source_sha256`, `approved_content`, `action` (`create` or
`append`), `destination`, a stable lowercase UUID v4 `promotion_id`, UTC-second `approved_at`, and
ordered `transfer_fields` (selected existing `title`, `tags`, `capture_type`). Create also requires
`expected_destination: "absent"`; append instead requires `expected_destination_sha256` from review.
Changed source/destination bytes fail closed. Preserve the exact decision for retries; do not change
IDs after uncertain outcomes. `commit_unknown` requires operator reconciliation before another
write. Markdown success and derived-index outcome are separate. Stop the serving process for
Capture and Promotion apply because they own targeted index writes; use the same offline discipline
for review with optional candidate evidence. Focused Capture/Promotion tests cover synthetic writes
separately from the image gate, which only checks their parsers.

Capture and Promotion have no REST/MCP/dashboard adapters. Query has REST and MCP adapters;
its Dashboard integration remains unavailable. Named mode fences the whole Dashboard/session
surface; Hygiene mutations remain unavailable.

The existing create/append REST routes and optional MCP tools accept singular `space` in their
JSON body. Explicit selection, including `"default"`, uses strict validation and returns
`{space_id,result}` around the existing note result. Omission retains the default-only legacy
request and response. `MCP_WRITE_ENABLED` must still be enabled before either write tool exists;
registration grants no space permissions. Slice D connects named production composition to one
runtime; see [configuration, operational boundaries and recovery](KNOWLEDGE_SPACES.md).

CLI capture, promotion review and apply likewise accept one `--space ID`. Explicit selection uses
v2 provenance and `{space_id,result}` evidence; omitted selection retains v1 bytes and outputs.
Scoped review requires `--destination` for the exact chosen create or append path. Scoped apply
reconstructs and reauthorizes the approved request in the current process; client metadata and
hashes do not grant access. V1 and v2 artifacts cannot be exchanged between these flows. Scoped
writes use the existing runtime, including startup reconciliation for enabled spaces and index
drain before exit. Stop the serving application/stdio process first. No new cross-process lock,
approval cache, automatic promotion or uncertain-write retry is provided.

`index` brings derived semantic data up to date through the production incremental/full sync path;
`reindex` first discards and then rebuilds derived semantic data. Markdown remains the source of
truth and neither command changes note files.

VaultBridge has no cross-process index lock. `status`, `index`, and `reindex` are stopped-service
operations; do not run them through `docker exec` in the serving application container. Read-only
`search`, `related`, and `query` do not mutate semantic persistence, but they are local readers and
are not a new remote/concurrent-process coordination layer.

Inspect persisted index integrity without loading the embedding model or changing storage:

```bash
docker compose stop obsidian-api
docker compose run --rm --no-deps obsidian-api python -m app.cli status
docker compose run --rm --no-deps obsidian-api python -m app.cli index check
docker compose up -d obsidian-api
```

The check reports vault/database/schema/signature/lifecycle/searchability/count status. It uses an
immutable SQLite view and refuses inspection while SQLite WAL/SHM sidecars are present. Exit `0`
means healthy, `1` means an integrity/readiness problem, and `2` means a CLI, configuration, or
programming failure. `/health` and `/health/ready` remain authoritative for the running service.

To update a compatible index normally, keep the service stopped and synchronize it without a reset:

```bash
docker compose run --rm --no-deps obsidian-api python -m app.cli index
```

If inspection shows a clean rebuild is appropriate, rebuild only the derived semantic data through
the production synchronization path:

```bash
docker compose run --rm --no-deps obsidian-api python -m app.cli reindex
docker compose run --rm --no-deps obsidian-api python -m app.cli index rebuild
docker compose up -d obsidian-api
```

`reindex` and `index rebuild` are equivalent. Rebuild may download the model if its cache is empty.
It clears/recreates semantic index data but does not modify Markdown. Review a failed check before
deciding to rebuild; do not edit or delete the SQLite files while the service is running.
