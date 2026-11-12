#!/usr/bin/env python3
"""Bounded, semantic-disabled named-image smoke; see docs/V150_RC_RUNBOOK.md.

Only synthetic temporary mounts are accepted. This complements the legacy image
gate; a PASS never certifies a model, semantic index, watcher or deployment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from . import smoke_mcp_http as smoke
    from . import verify_release_image as release
else:
    import smoke_mcp_http as smoke
    import verify_release_image as release

PREFIX = "vaultbridge-named-"
CAPABILITIES = ["read_note", "list_notes", "literal_search", "relationships", "knowledge_query",
                "capture", "promotion", "create_note", "append_note", "hygiene"]
FIXTURES = {sid: f"# {sid}\n\nIndependent {sid} only.\n".encode() for sid in ("default", "research", "denied")}
CAPTURE_ID = "12345678-1234-4123-8123-123456789abc"
PROMOTION_ID = "abcdefab-cdef-4abc-8abc-abcdefabcdef"
# Exact deterministic artifacts for the fixed approval/capture requests below.
PROMOTED_HASHES = {"default": "b63e7cd5f6e799999d2ed38fdae534244f8beab47e45e2d49163d32fc6bb7e42",
                   "research": "8edaa24a7d9a2b06438dba313c38b20386f048074d119e361855daf7c13f51e3"}


def configuration():
    return {"version": 1, "spaces": [dict(
        space_id=sid, root=f"/qualification/{sid}", semantic_data_path=f"/qualification/{sid}-data",
        read="deny" if sid == "denied" else "allow", write="deny" if sid == "denied" else "allow",
        indexing="disabled", dialects=["obsidian_wikilink", "markdown_link"], capabilities=CAPABILITIES,
    ) for sid in FIXTURES]}


def docker(*args, input_text=None, expected=0, timeout=180):
    result = subprocess.run(["docker", *args], input=input_text, capture_output=True,
                            text=True, encoding="utf-8", timeout=timeout)
    # Commands/env may contain synthetic credentials; never echo arguments or stderr.
    if result.returncode != expected:
        raise RuntimeError(f"Docker {args[0]} failed: exit {result.returncode}, expected {expected}")
    return result.stdout + result.stderr if args[0] == "logs" else result.stdout


def identities(result):
    return {(row["identity"]["space_id"], row["identity"]["path"]) for row in result["items"]}


def frame(number, method, **params):
    return {"jsonrpc": "2.0", "id": number, "method": method, "params": {
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  "io.modelcontextprotocol/clientCapabilities": {}}, **params}}


class Containers:
    """Register before dispatch; release mounts only after checked daemon absence."""

    LABEL = "vaultbridge.qualification.run"

    def __init__(self, root):
        self.root = root
        self.prefix = PREFIX + uuid.uuid4().hex
        self.names = set()
        self.failed = {}
        self.sequence = 0

    def command(self, role, *args):
        self.sequence += 1
        name = f"{self.prefix}-{role}-{self.sequence}"
        self.names.add(name)  # Even Popen/creation failure or a killed client must retain ownership.
        return name, ("run", "--name", name, "--label", f"{self.LABEL}={self.prefix}", *args)

    def present(self, identifier):
        output = docker("container", "ls", "--all", "--no-trunc", "--format", "{{.ID}} {{.Names}}", timeout=15)
        rows = [line.split() for line in output.splitlines()]
        if any(len(row) != 2 or not re.fullmatch(r"[0-9a-f]{64}", row[0]) for row in rows):
            raise RuntimeError("Invalid Docker container inventory")
        return any(identifier in row for row in rows)

    def owned(self, name):
        record = json.loads(docker("inspect", name, timeout=15))[0]
        if (not re.fullmatch(r"[0-9a-f]{64}", record["Id"])
                or (record["Config"].get("Labels") or {}).get(self.LABEL) != self.prefix):
            raise RuntimeError("Container ownership mismatch; refusing to stop it")
        return record

    def stop(self, name):
        identifier = self.owned(name)["Id"]
        docker("stop", "--timeout", "30", identifier, timeout=45)
        return identifier

    def remove(self, name):
        if name not in self.names:
            return  # A previous call already confirmed this identity absent.
        stage = "inventory"
        try:
            if self.present(name):
                stage = "ownership inspection"
                try:
                    record = self.owned(name)
                except Exception:
                    if self.present(name):
                        raise
                    record = None  # Concurrent --rm/removal, confirmed by the daemon inventory.
                if record is not None:
                    identifier = record["Id"]
                    # Address the verified ID, never a name that could be rebound between commands.
                    if record["State"]["Running"]:
                        stage = "stop"
                        try:
                            docker("stop", "--timeout", "30", identifier, timeout=45)
                        except Exception:
                            if self.present(identifier):
                                raise
                    stage = "removal"
                    try:
                        docker("rm", "--force", identifier, timeout=45)
                    except Exception:
                        if self.present(identifier):
                            raise
                    stage = "absence check"
                    if self.present(identifier):
                        raise RuntimeError("Container remains after removal")
            self.names.remove(name)
            self.failed.pop(name, None)
        except Exception as error:
            self.failed[name] = stage
            raise RuntimeError(f"Container cleanup unconfirmed: {name} ({stage}, {type(error).__name__}); "
                               f"preserved mount: {self.root}") from None

    def cleanup(self):
        for name in sorted(self.names - self.failed.keys()):
            try:
                self.remove(name)
            except RuntimeError:
                pass  # Attempt other owned containers, but retain this mount and failure below.
        if self.names:
            residual = ", ".join(f"{name} ({self.failed.get(name, 'unconfirmed')})" for name in sorted(self.names))
            raise RuntimeError(f"Container cleanup failed; residual: {residual}; preserved mount: {self.root}")

    def run(self, role, *args, **kwargs):
        name, command = self.command(role, *args)
        try:
            return docker(*command, **kwargs)
        finally:
            self.remove(name)


class Qualification:
    def __init__(self, image, root, version):
        self.image, self.root, self.version = image, root, version
        self.lifecycle = Containers(root)
        self.containers = self.lifecycle.names
        self.expected = {sid: {"same.md": content} for sid, content in FIXTURES.items()}

    def arguments(self, *, writes=True):
        env = {"API_KEY": smoke.API_KEY, "API_KEY_PREVIOUS": smoke.PREVIOUS_API_KEY,
               "VAULT_PATH": "/qualification/default", "SEMANTIC_DATA_PATH": "/qualification/default-data",
               "KNOWLEDGE_SPACES_JSON": json.dumps(configuration(), separators=(",", ":")),
               "HF_HOME": "/qualification/cache", "SEMANTIC_WATCH_ENABLED": "false",
               "MCP_HTTP_ENABLED": "true", "MCP_WRITE_ENABLED": str(writes).lower(),
               "MCP_HTTP_ALLOWED_HOSTS": "127.0.0.1:*", "MCP_HTTP_ALLOWED_ORIGINS": "https://named.invalid"}
        args = ["--user", "568:568", "--cap-drop", "ALL", "--mount",
                f"type=bind,src={self.root},dst=/qualification"]
        for key, value in env.items():
            args += ["--env", f"{key}={value}"]
        return args

    def unchanged(self):
        for sid, expected in self.expected.items():
            assert smoke.vault_snapshot(self.root / sid) == expected, f"unexpected {sid} file/byte delta"
        assert not any((self.root / f"{sid}-data").exists() for sid in FIXTURES), "disabled index touched data"
        assert not (self.root / "cache").exists(), "model-free smoke touched model cache"

    def start(self, *, writes=True):
        assert not self.containers, "serving/stdio/CLI overlap"
        self.name, command = self.lifecycle.command("http", "-d", "--publish", "127.0.0.1::8000",
                                                    *self.arguments(writes=writes), self.image)
        docker(*command)
        self.url = smoke.published_url(self.name)
        smoke.wait_until_live(self.url, self.name)

    def stop(self):
        identifier = self.lifecycle.stop(self.name)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", identifier).strip() == "0", "unclean shutdown"
        logs = docker("logs", identifier)
        assert smoke.API_KEY not in logs and smoke.PREVIOUS_API_KEY not in logs, "credential log leak"
        assert str(self.root) not in logs and "Traceback" not in logs, "unsafe shutdown/log outcome"
        assert all(content.decode().strip() not in logs for content in FIXTURES.values()), "content log leak"
        self.lifecycle.remove(self.name)
        self.unchanged()

    def http(self, path, body=None, *, token=smoke.API_KEY, expected=200, headers=None, method=None):
        req_headers = {"Content-Type": "application/json", **(headers or {})}
        if token is not None:
            req_headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url + path, headers=req_headers,
            data=None if body is None else json.dumps(body).encode(), method=method)
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
            assert response.code == expected, f"HTTP status {response.code}, expected {expected}"
            if "space" in path or (body and ("space" in body or "spaces" in body)) or path.startswith("/ui"):
                assert response.headers.get("Cache-Control") == "no-store"
            if path.startswith("/ui"):
                assert not response.headers.get("Set-Cookie"), "named UI issued a cookie"
            if content and response.headers.get_content_type() == "application/json":
                return json.loads(content)
            return content.decode() if content else None

    def rest(self):
        for token in (None, "invalid"):
            self.http("/api/v1/notes/list?spaces=research", token=token, expected=401)
        self.http("/api/v1/notes/list?spaces=research", token=smoke.PREVIOUS_API_KEY)
        for sid in ("default", "research"):
            result = self.http(f"/api/v1/notes/read?space={sid}&path=same.md")
            assert result == {"space_id": sid, "result": {"path": "same.md", "content": FIXTURES[sid].decode()}}
        assert self.http("/api/v1/notes/read?path=same.md")["content"] == FIXTURES["default"].decode()
        assert len(self.http("/api/v1/notes/list")["notes"]) == 1
        both = {("default", "same.md"), ("research", "same.md")}
        assert identities(self.http("/api/v1/notes/list?spaces=default&spaces=research")) == both
        for path, body in (("/api/v1/notes/search", {"query": "Independent"}),
                           ("/api/v1/knowledge/query", {"literal_text": "Independent"})):
            assert identities(self.http(path, {**body, "spaces": ["default", "research"]})) == both
        omitted = self.http("/api/v1/knowledge/query", {"literal_text": "Independent"})
        assert identities(omitted) == {("default", "same.md")}
        unknown = self.http("/api/v1/notes/list?spaces=unknown", expected=403)
        assert self.http("/api/v1/notes/list?spaces=denied", expected=403) == unknown
        assert self.http("/api/v1/notes/list?spaces=default&spaces=denied", expected=403) == unknown
        self.http("/api/v1/notes", {"space": "denied", "title": "Forbidden", "content": "No"}, expected=403)
        self.http("/api/v1/notes/related", {"spaces": ["research"], "text": "Independent"}, expected=409)
        for path in ("/ui", "/ui/", "/ui/assets/app.js", "/ui/session"):
            for method in ("GET", "POST", "DELETE"):
                self.http(path, expected=404, method=method)
        assert self.http("/health") == {"ok": True}
        assert self.http("/health/ready", expected=503) == {"ready": False}
        self.http("/mcp", {}, headers={"Origin": "https://denied.invalid"}, expected=403)
        self.http("/mcp", {}, headers={"Host": "denied.invalid"}, expected=421)
        for token in (None, "invalid"):
            self.http("/mcp", {}, token=token, expected=401)
        self.unchanged()
        result = self.http("/api/v1/notes", {"space": "research", "title": "REST", "content": "REST body"})
        path = result["result"]["path"]
        assert path == "Inbox/REST.md"
        actual = (self.root / "research" / path).read_bytes()
        self.expected["research"][path] = smoke.expected_created_bytes(actual, "REST body", [])
        body = {"space": "research", "path": path, "content": "REST addition", "dedupe_key": "vb154-rest"}
        assert self.http("/api/v1/notes/append", body)["result"]["status"] == "appended"
        self.expected["research"][path] += b"\n\nREST addition\n<!-- chatgpt-append:vb154-rest -->\n"
        assert self.http("/api/v1/notes/append", body)["result"]["status"] == "already_applied"
        self.unchanged()

    async def mcp(self, writes):
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        for token in (smoke.API_KEY, smoke.PREVIOUS_API_KEY):
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}",
                                                   "Origin": "https://named.invalid"}) as http:
                async with Client(streamable_http_client(self.url + "/mcp", http_client=http,
                                  terminate_on_close=False), mode="legacy") as client:
                    assert client.server_info.version == self.version
                async with Client(streamable_http_client(self.url + "/mcp", http_client=http,
                                  terminate_on_close=False), mode="2026-07-28") as client:
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    assert tools == smoke.TOOLS | (smoke.WRITE_TOOLS if writes else set())
                    read = await client.call_tool("read_note", {"space": "research", "path": "same.md"})
                    assert not read.is_error
                    assert read.structured_content["result"]["content"] == FIXTURES["research"].decode()
                    query = await client.call_tool("knowledge_query", {
                        "spaces": ["default", "research"], "literal_text": "Independent"})
                    assert not query.is_error and identities(query.structured_content) == {
                        ("default", "same.md"), ("research", "same.md")}
                    resource = await client.read_resource("vaultbridge://space/research/note/same.md")
                    assert resource.contents[0].text == FIXTURES["research"].decode()
                    denied = await client.call_tool("read_note", {"space": "denied", "path": "same.md"})
                    assert denied.is_error
                    if writes and token == smoke.API_KEY:
                        created = await client.call_tool("create_note", {
                            "space": "default", "title": "MCP", "content": "MCP body"})
                        assert not created.is_error
                        path = created.structured_content["result"]["path"]
                        assert path == "Inbox/MCP.md"
                        actual = (self.root / "default" / path).read_bytes()
                        self.expected["default"][path] = smoke.expected_created_bytes(actual, "MCP body", [])
                        appended = await client.call_tool("append_note", {
                            "space": "default", "path": path, "content": "MCP addition", "dedupe_key": "vb154-mcp"})
                        assert not appended.is_error and appended.structured_content["result"]["status"] == "appended"
                        self.expected["default"][path] += b"\n\nMCP addition\n<!-- chatgpt-append:vb154-mcp -->\n"
        self.unchanged()

    def stdio(self):
        assert not self.containers, "serving/stdio overlap"
        frames = [frame(1, "tools/call", name="read_note", arguments={"space": "research", "path": "same.md"}),
                  frame(2, "resources/read", uri="vaultbridge://space/research/note/same.md"),
                  frame(3, "tools/call", name="knowledge_query", arguments={
                      "spaces": ["default", "research"], "literal_text": "Independent"}),
                  frame(4, "tools/call", name="create_note", arguments={"space": "research", "title": "Blocked"})]
        # Read-only registration, actual module entry point and EOF-driven shutdown.
        name, command = self.lifecycle.command("stdio", "-i", *self.arguments(writes=False),
                                               "--entrypoint", "python", self.image, "-m", "app.mcp_server")
        process = subprocess.Popen(["docker", *command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        rows = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                for request in frames:
                    process.stdin.write(json.dumps(request) + "\n")
                    process.stdin.flush()
                    line = executor.submit(process.stdout.readline).result(30)
                    assert line, "stdio response missing"
                    response = json.loads(line)
                    rows[response["id"]] = response
            finally:
                process.stdin.close()
                try:
                    process.wait(30)
                except subprocess.TimeoutExpired:
                    try:
                        self.lifecycle.remove(name)
                    finally:
                        process.kill()
                        process.wait(10)
                    raise
        assert process.returncode == 0, "stdio process shutdown failed"
        assert docker("inspect", "--format", "{{.State.ExitCode}}", name).strip() == "0"
        self.lifecycle.remove(name)
        assert rows[1]["result"]["structuredContent"]["result"]["content"] == FIXTURES["research"].decode()
        assert rows[2]["result"]["contents"][0]["text"] == FIXTURES["research"].decode()
        assert identities(rows[3]["result"]["structuredContent"]) == {("default", "same.md"), ("research", "same.md")}
        assert "error" in rows[4] or rows[4]["result"]["isError"]
        self.unchanged()

    def cli(self, *args, body=None, expected=0):
        assert not self.containers, "serving/CLI overlap"
        output = self.lifecycle.run("cli", "-i", *self.arguments(), "--entrypoint", "python",
                                    self.image, "-m", "app.cli", *args,
                                    input_text=None if body is None else json.dumps(body), expected=expected)
        return json.loads(output) if output.strip() else None

    def metadata(self):
        return self.lifecycle.run("metadata", *self.arguments(), "--entrypoint", "python", self.image, "-c",
            "from app.main import app; from app.mcp_server import MCP_SERVER_VERSION; "
            "import json; print(json.dumps([app.version,MCP_SERVER_VERSION]))")

    def cleanup(self):
        self.lifecycle.cleanup()  # Failure exits before any mount deletion.
        root = self.root
        assert root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith(PREFIX)
        assert root.name != PREFIX and not root.is_symlink(), "unsafe cleanup root"
        if root.exists():
            try:
                shutil.rmtree(root, onexc=smoke._retry_removal_after_chmod)
            except PermissionError:
                # Native Linux bind files can be owned by UID 568. This cleanup worker is
                # also registered, bounded, and confirmed absent before removing its mount.
                self.lifecycle.run("cleanup", "--user", "0:0", "--volume", f"{root}:{smoke.CLEANUP_MOUNT}:rw",
                                   "--entrypoint", "python", self.image, "-c", smoke.CLEANUP_COMMAND)
                root.rmdir()
        assert not root.exists(), "cleanup incomplete"

    def cli_profiles(self):
        for flags, spaces in (([], {"default"}), (["--space", "research"], {"research"}),
                              (["--space", "default", "--space", "research"], {"default", "research"})):
            result = self.cli("query", "--literal-text", "Independent", "--json", *flags)
            if flags:
                assert {sid for sid, path in identities(result)} == spaces
            else:
                assert {row["canonical_path"] for row in result["matches"]} == {"same.md"}
        self.cli("index", "check", expected=2)
        self.unchanged()
        for sid, flags in (("default", []), ("research", ["--space", "research"])):
            capture = {"content": "Original body", "state": "draft", "capture_id": CAPTURE_ID,
                       "captured_at": "2026-10-03T12:00:00+00:00", "title": "Title", "source": "declared<source"}
            captured = self.cli("capture", *flags, body=capture)
            result = captured["result"] if flags else captured
            assert result["committed"]
            path = result["path"]
            actual = (self.root / sid / path).read_bytes()
            metadata = dict(capture_id=CAPTURE_ID, capture_state="draft", captured_at="2026-10-03T12:00:00Z")
            if flags:
                metadata.update(capture_provenance_version=2, capture_space_id="research")
            metadata.update(title="Title", source="declared<source")
            expected = ("---\n" + json.dumps(metadata) + "\n---\n\nOriginal body").encode()
            assert actual == expected, "capture bytes/provenance mismatch"
            self.expected[sid][path] = expected
            before = smoke.vault_snapshot(self.root / sid)
            self.cli("capture", *flags, body=capture)
            assert smoke.vault_snapshot(self.root / sid) == before, "capture replay changed bytes"
            reviewed = self.cli("promote", "review", "--source-path", path, "--capture-id", CAPTURE_ID,
                                *(["--destination", "Notes/Promoted.md"] if flags else []), *flags)
            review = reviewed["result"] if flags else reviewed
            assert ("provenance_version" in review) == bool(flags)
            decision = dict(source_path=path, capture_id=CAPTURE_ID, expected_source_sha256=review["source_sha256"],
                            approved_content="Approved body", action="create", destination="Notes/Promoted.md",
                            promotion_id=PROMOTION_ID, approved_at="2026-10-03T13:00:00Z", transfer_fields=["title"],
                            expected_destination="absent")
            promoted = self.cli("promote", "apply", *flags, body=decision)
            assert (promoted["result"] if flags else promoted)["committed"]
            dest = (self.root / sid / "Notes/Promoted.md").read_bytes()
            assert hashlib.sha256(dest).hexdigest() == PROMOTED_HASHES[sid], "promotion bytes/digest mismatch"
            assert dest.endswith(b"---\n\nApproved body")
            meta = json.loads(dest.split(b"\n---\n", 1)[0].removeprefix(b"---\n"))
            assert meta["promotion_id"] == PROMOTION_ID
            assert meta["promoted_from_sha256"] == review["source_sha256"]
            assert (meta.get("promotion_provenance_version") == 2) == bool(flags)
            if flags:
                assert meta["promotion_space_id"] == meta["promoted_from_capture_space_id"] == sid
            self.expected[sid]["Notes/Promoted.md"] = dest
            self.unchanged()
            self.cli("promote", "apply", *flags, body=decision)
            self.unchanged()


def orchestrate(args):
    release.validate_inputs(args)
    reference = args.image or args.local_image
    inspected = json.loads(docker("image", "inspect", reference))[0]
    assert (inspected["Os"], inspected["Architecture"]) == ("linux", "amd64"), "qualification platform mismatch"
    if args.image:
        release.verify_identity(inspected, args.image, args.expected_revision, args.expected_version)
    image = inspected["Id"]  # Pin all execution to the inspected local content, even in local mode.
    root = Path(tempfile.mkdtemp(prefix=PREFIX)).resolve()
    qualification = Qualification(image, root, args.expected_version)
    try:
        root.chmod(0o777)
        for sid, content in FIXTURES.items():
            (root / sid).mkdir(mode=0o777)
            (root / sid).chmod(0o777)
            (root / sid / "Notes").mkdir(mode=0o777)
            (root / sid / "Notes").chmod(0o777)
            (root / sid / "same.md").write_bytes(content)
        versions = qualification.metadata()
        assert json.loads(versions) == [args.expected_version] * 2, "runtime metadata mismatch"
        qualification.start(writes=False)
        asyncio.run(qualification.mcp(False))
        qualification.stop()
        qualification.start()
        qualification.rest()
        asyncio.run(qualification.mcp(True))
        qualification.stop()
        qualification.stdio()
        qualification.cli_profiles()
        qualification.start()  # Persistent authoritative-byte reuse, no semantic readiness claim.
        qualification.http("/health/live")
        qualification.unchanged()
        qualification.stop()
        receipt = {"mode": "published-digest" if args.image else "local-mechanics-only",
                   "input": reference, "image_id": image, "expected_revision": args.expected_revision,
                   "version": args.expected_version, "semantic_model_index_watcher": "NOT EXERCISED",
                   "vault_hashes": {sid: {path: hashlib.sha256(data).hexdigest() for path, data in rows.items()}
                                    for sid, rows in qualification.expected.items()}}
    finally:
        # Never remove mounts while a writer might still be alive. A forced kill is not clean shutdown.
        qualification.cleanup()
    receipt["cleanup"] = "removed containers and disposable root"
    print(json.dumps(receipt, sort_keys=True))
    print("Named semantic-disabled smoke = PASS; model/index/watcher/deployment qualification remains open")


if __name__ == "__main__":
    parser = release.parser()
    parser.description = __doc__
    orchestrate(parser.parse_args())
