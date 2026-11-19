# MCP integration

Both transports use the same domain services as REST. For named mode, read the [scope and lifecycle guide](KNOWLEDGE_SPACES.md#mcp-transports).



VaultBridge can be launched as a local MCP server for MCP-capable clients:

```text
python -m app.mcp_server
```

The stdio transport exposes `list_notes`, `read_note`, `search_notes`, `related_notes`,
`duplicate_candidates`, `note_links`, `note_backlinks`, `knowledge_hygiene_scan`, and `knowledge_query` (nine read-only
tools), plus contained Markdown Resources such as
`vaultbridge://note/Projects%2FLaunch%20plan.md`. The process reuses `VAULT_PATH`,
`SEMANTIC_DATA_PATH`, model, size, and rate-limit settings. It does not require `API_KEY`; the local
spawning process and filesystem permissions are the trust boundary.

In legacy single-vault mode with `MCP_WRITE_ENABLED=false`, the adapter never starts FastAPI, a network listener, a
background indexer, or the filesystem watcher. Literal list/read/search operations work
independently of semantic storage. Semantic tools read only an existing compatible ready index and
never synchronize, rebuild, or modify it; if a safe immutable index view is unavailable, those
tools return a retryable error.

Named stdio instead composes the shared named runtime according to configured policy/indexing;
enabled spaces participate in startup reconciliation and the optional watcher. This can write
derived indexes even while the exposed MCP tools are read-only. Do not run it alongside another
writer on the same state. See the named-mode lifecycle guide above.

A generic client entry uses the current Python environment and repository as its working directory:

```yaml
command: python
args:
  - -m
  - app.mcp_server
```

The same nine read-only tools and contained Markdown Resources can be exposed over Streamable HTTP
at `/mcp` on the existing application port. It is disabled by default. Enable it with
`MCP_HTTP_ENABLED=true`, keep using `Authorization: Bearer <token>`, and configure
`MCP_HTTP_ALLOWED_HOSTS` plus `MCP_HTTP_ALLOWED_ORIGINS` for the actual deployment. The defaults
allow loopback Host/Origin values only; a request without `Origin` remains valid for non-browser
clients when its Host is allowed. The HTTP transport accepts the current `API_KEY` and optional
`API_KEY_PREVIOUS`, and shares the normal process-local peer rate limit and live application
services/index lifecycle.

Set `MCP_WRITE_ENABLED=true` to add exactly `create_note` and `append_note` to either transport
(eleven tools total).
They reuse the protected REST write behavior: create never overwrites, append supports an optional
dedupe key, and only committed changes queue targeted semantic refresh. In legacy mode, stdio owns
and shuts down a targeted index worker only with writes enabled; it starts no unnecessary full sync. For remote
read/write MCP, use all four explicit settings and terminate TLS at a trusted reverse proxy or use a
private VPN:

```env
MCP_HTTP_ENABLED=true
MCP_WRITE_ENABLED=true
MCP_HTTP_ALLOWED_HOSTS=vaultbridge.example.test:*
MCP_HTTP_ALLOWED_ORIGINS=https://vaultbridge.example.test
```

Do not use wildcard Host or Origin values. Streamable HTTP remains schema-hidden and adds no port,
service, container, OAuth flow, Prompt, subscription feature, or standalone HTTP+SSE endpoint.
