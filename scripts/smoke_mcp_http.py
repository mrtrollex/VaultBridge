#!/usr/bin/env python3
"""Exercise MCP Streamable HTTP against a built VaultBridge image."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

TOOLS = {
    "list_notes",
    "read_note",
    "search_notes",
    "related_notes",
    "duplicate_candidates",
    "note_links",
    "note_backlinks",
}
WRITE_TOOLS = {"create_note", "append_note"}
API_KEY = "vb093-current-placeholder"
PREVIOUS_API_KEY = "vb093-previous-placeholder"
MCP_SERVER_ALIAS = "vb093-server"
DISPOSABLE_ROOT_PREFIX = "vaultbridge-vb093-"
CLEANUP_MOUNT = "/cleanup"
CLEANUP_COMMAND = """\
import shutil
from pathlib import Path

root = Path("/cleanup")
for child in root.iterdir():
    if child.is_symlink() or child.is_file():
        child.unlink()
    else:
        shutil.rmtree(child)
"""


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"{Path(args[0]).name} command failed with exit code {error.returncode}"
        ) from None


def request_status(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes]:
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}", data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_until_live(base_url: str, container_name: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            status, body = request_status(base_url, "/health/live")
            if status == 200 and json.loads(body) == {"ok": True}:
                return
        except (OSError, ValueError):
            pass
        state = run(
            "docker", "inspect", "--format", "{{.State.Running}}", container_name, capture=True
        ).stdout.strip()
        if state != "true":
            raise RuntimeError(f"container {container_name} exited before becoming live")
        time.sleep(1)
    raise RuntimeError(f"container {container_name} did not become live")


def published_url(container_name: str) -> str:
    mapping = run(
        "docker", "port", container_name, "8000/tcp", capture=True
    ).stdout.strip()
    port = mapping.rsplit(":", 1)[-1]
    if not port.isdigit():
        raise RuntimeError("Docker did not report a numeric loopback port")
    return f"http://127.0.0.1:{port}"


def start_container(
    image: str,
    name: str,
    vault: Path,
    data: Path,
    *,
    enabled: bool,
    writes: bool = False,
) -> str:
    args = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--user",
        "568:568",
        "--cap-drop",
        "ALL",
        "--publish",
        "127.0.0.1::8000",
        "--env",
        f"API_KEY={API_KEY}",
        "--env",
        f"API_KEY_PREVIOUS={PREVIOUS_API_KEY}",
        "--env",
        "VAULT_PATH=/vault",
        "--env",
        "SEMANTIC_DATA_PATH=/data",
        "--env",
        "HF_HOME=/data/huggingface",
        "--env",
        "SEMANTIC_WATCH_ENABLED=false",
        "--env",
        f"MCP_HTTP_ENABLED={'true' if enabled else 'false'}",
        "--env",
        f"MCP_WRITE_ENABLED={'true' if writes else 'false'}",
    ]
    if enabled:
        args += [
            "--env",
            f"MCP_HTTP_ALLOWED_HOSTS={MCP_SERVER_ALIAS}:8000,127.0.0.1:*",
            "--env",
            "MCP_HTTP_ALLOWED_ORIGINS=https://vb093-client.invalid",
        ]
    args += [
        "--volume",
        f"{vault.resolve()}:/vault:rw",
        "--volume",
        f"{data.resolve()}:/data:rw",
        image,
    ]
    run(*args)
    return published_url(name)


def assert_rest(base_url: str) -> None:
    status, body = request_status(
        base_url, "/api/v1/notes/list?limit=10", token=API_KEY
    )
    assert status == 200, f"authenticated REST returned {status}"
    assert isinstance(json.loads(body)["notes"], list)


def stop_cleanly(name: str) -> None:
    run("docker", "stop", "--time", "30", name)
    exit_code = run(
        "docker", "inspect", "--format", "{{.State.ExitCode}}", name, capture=True
    ).stdout.strip()
    assert exit_code == "0", f"container {name} stopped with exit code {exit_code}"
    run("docker", "rm", name)


def invalid_host_status(base_url: str) -> int:
    parsed = urllib.parse.urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request(
            "POST",
            "/mcp",
            body=b"{}",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "Host": "invalid.vb093.test",
            },
        )
        return connection.getresponse().status
    finally:
        connection.close()


def run_official_client(
    image: str,
    server_name: str,
    network: str,
    script: Path,
    *,
    token_kind: str = "current",
    writes: bool = False,
) -> None:
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--volume",
        f"{script.resolve()}:/tmp/smoke_mcp_http.py:ro",
        "--entrypoint",
        "python",
        image,
        "/tmp/smoke_mcp_http.py",
        "--client",
        f"http://{server_name}:8000/mcp",
        "--token-kind",
        token_kind,
    ]
    if writes:
        args.append("--writes")
    run(*args)


def _retry_removal_after_chmod(
    function: Callable[[str], object], path: str, _: BaseException
) -> None:
    candidate = Path(path)
    candidate.chmod(
        candidate.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    )
    function(path)


def cleanup_disposable_root(root: Path, *, expected_root: Path, image: str) -> None:
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    resolved_root = root.resolve()
    if (
        resolved_root != expected_root
        or resolved_root.parent != temporary_parent
        or not resolved_root.name.startswith(DISPOSABLE_ROOT_PREFIX)
        or resolved_root.name == DISPOSABLE_ROOT_PREFIX
    ):
        raise ValueError(f"refusing unsafe disposable cleanup target: {root}")

    if os.path.lexists(resolved_root):
        try:
            shutil.rmtree(resolved_root, onexc=_retry_removal_after_chmod)
        except PermissionError:
            run(
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--volume",
                f"{resolved_root}:{CLEANUP_MOUNT}:rw",
                "--entrypoint",
                "python",
                image,
                "-c",
                CLEANUP_COMMAND,
            )
            resolved_root.rmdir()
    assert not os.path.lexists(resolved_root), (
        f"disposable cleanup left temporary root behind: {resolved_root}"
    )


def orchestrate(image: str) -> None:
    run("docker", "image", "inspect", image)
    suffix = uuid.uuid4().hex[:12]
    network = f"vaultbridge-vb093-{suffix}"
    containers: set[str] = set()
    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_ROOT_PREFIX))
    expected_root = root.resolve()
    print(f"Disposable root: {expected_root}")
    try:
        run("docker", "network", "create", network)
        directories = (
            root / "disabled-vault",
            root / "disabled-data",
            root / "enabled-vault",
            root / "enabled-data",
            root / "write-vault",
            root / "write-data",
        )
        for directory in directories:
            directory.mkdir()
            directory.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

        disabled = f"vb093-disabled-{suffix}"
        containers.add(disabled)
        disabled_url = start_container(
            image, disabled, root / "disabled-vault", root / "disabled-data", enabled=False
        )
        wait_until_live(disabled_url, disabled)
        assert_rest(disabled_url)
        status, _ = request_status(disabled_url, "/mcp", method="POST", token=API_KEY)
        assert status == 404, f"disabled /mcp returned {status}"
        stop_cleanly(disabled)
        containers.remove(disabled)

        # Keep the note discoverable by list_notes while making the background indexer skip its
        # content. This prevents the smoke from downloading an embedding model at runtime.
        note = root / "enabled-vault" / "Smoke.md"
        note.write_text("# Harmless container smoke note\n", encoding="utf-8")
        note.chmod(0)
        enabled = f"vb093-server-{suffix}"
        containers.add(enabled)
        enabled_url = start_container(
            image, enabled, root / "enabled-vault", root / "enabled-data", enabled=True
        )
        run("docker", "network", "connect", "--alias", MCP_SERVER_ALIAS, network, enabled)
        wait_until_live(enabled_url, enabled)
        assert_rest(enabled_url)

        for token in (None, "invalid-vb093-key"):
            status, _ = request_status(enabled_url, "/mcp", method="POST", token=token)
            assert status == 401, f"MCP authentication negative case returned {status}"
        assert invalid_host_status(enabled_url) == 421
        status, _ = request_status(
            enabled_url,
            "/mcp",
            method="POST",
            token=API_KEY,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://invalid.vb093.test",
            },
            body=b"{}",
        )
        assert status == 403, f"invalid Origin returned {status}"
        status, _ = request_status(
            enabled_url, "/api/v1/notes/list?limit=10", token=PREVIOUS_API_KEY
        )
        assert status == 200, f"previous API key returned {status}"

        # The client URL host must match both the Docker alias and MCP_HTTP_ALLOWED_HOSTS.
        run_official_client(image, MCP_SERVER_ALIAS, network, Path(__file__))
        run_official_client(
            image,
            MCP_SERVER_ALIAS,
            network,
            Path(__file__),
            token_kind="previous",
        )
        stop_cleanly(enabled)
        containers.remove(enabled)

        write_enabled = f"vb106-write-server-{suffix}"
        containers.add(write_enabled)
        write_url = start_container(
            image,
            write_enabled,
            root / "write-vault",
            root / "write-data",
            enabled=True,
            writes=True,
        )
        run("docker", "network", "connect", "--alias", MCP_SERVER_ALIAS, network, write_enabled)
        wait_until_live(write_url, write_enabled)
        run_official_client(
            image,
            MCP_SERVER_ALIAS,
            network,
            Path(__file__),
            writes=True,
        )
        created_note = root / "write-vault" / "MCP Smoke" / "Created.md"
        assert created_note.is_file(), "write-enabled MCP smoke did not create the synthetic note"
        assert "deduped append" in created_note.read_text(encoding="utf-8")
        stop_cleanly(write_enabled)
        containers.remove(write_enabled)
    finally:
        for container in containers:
            subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        cleanup_disposable_root(root, expected_root=expected_root, image=image)
        print(f"Disposable cleanup: PASS ({expected_root})")


async def client_smoke(
    url: str, *, token_kind: str = "current", writes: bool = False
) -> None:
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    token = PREVIOUS_API_KEY if token_kind == "previous" else API_KEY
    async with httpx2.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://vb093-client.invalid",
        }
    ) as http_client:
        transport = streamable_http_client(
            url, http_client=http_client, terminate_on_close=False
        )
        async with Client(transport, mode="2026-07-28") as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            expected_tools = TOOLS | WRITE_TOOLS if writes else TOOLS
            assert names == expected_tools, f"unexpected MCP tools: {sorted(names)}"
            result = await client.call_tool("list_notes", {})
            paths = {note["path"] for note in result.structured_content["notes"]}
            if writes:
                target = await client.call_tool(
                    "create_note",
                    {
                        "title": "Relationship Target",
                        "folder": "MCP Smoke",
                        "content": "# Relationship target\n\nSanitized synthetic target.",
                    },
                )
                source = await client.call_tool(
                    "create_note",
                    {
                        "title": "Relationship Source",
                        "folder": "MCP Smoke",
                        "content": (
                            "# Relationship source\n\n"
                            "[[MCP Smoke/Relationship Target#Verified Section|Synthetic target]]"
                        ),
                    },
                )
                links = await client.call_tool(
                    "note_links",
                    {"path": "MCP Smoke/Relationship Source.md"},
                )
                backlinks = await client.call_tool(
                    "note_backlinks",
                    {"path": "MCP Smoke/Relationship Target.md"},
                )
                created = await client.call_tool(
                    "create_note",
                    {
                        "title": "Created",
                        "folder": "MCP Smoke",
                        "content": "synthetic body",
                        "tags": ["smoke"],
                    },
                )
                appended = await client.call_tool(
                    "append_note",
                    {
                        "path": "MCP Smoke/Created.md",
                        "content": "deduped append",
                        "dedupe_key": "vb106-smoke",
                    },
                )
                deduped = await client.call_tool(
                    "append_note",
                    {
                        "path": "MCP Smoke/Created.md",
                        "content": "deduped append",
                        "dedupe_key": "vb106-smoke",
                    },
                )
                read = await client.call_tool(
                    "read_note",
                    {"path": "MCP Smoke/Created.md"},
                )
                assert target.structured_content["status"] == "created"
                assert source.structured_content["status"] == "created"
                assert links.structured_content == {
                    "links": [
                        {
                            "target": "MCP Smoke/Relationship Target",
                            "heading": "Verified Section",
                            "alias": "Synthetic target",
                            "state": "resolved",
                            "resolved_path": "MCP Smoke/Relationship Target.md",
                        }
                    ]
                }
                assert backlinks.structured_content == {
                    "backlinks": [
                        {
                            "source_path": "MCP Smoke/Relationship Source.md",
                            "target": "MCP Smoke/Relationship Target",
                            "heading": "Verified Section",
                            "alias": "Synthetic target",
                        }
                    ]
                }
                assert created.structured_content["status"] == "created"
                assert appended.structured_content["status"] == "appended"
                assert deduped.structured_content["status"] == "already_applied"
                assert read.structured_content["content"].count("deduped append") == 1
            else:
                assert "Smoke.md" in paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="vaultbridge:ci")
    parser.add_argument("--client")
    parser.add_argument("--token-kind", choices=("current", "previous"), default="current")
    parser.add_argument("--writes", action="store_true")
    args = parser.parse_args()
    if args.client:
        asyncio.run(
            client_smoke(args.client, token_kind=args.token_kind, writes=args.writes)
        )
        return
    orchestrate(args.image)
    print("VaultBridge MCP HTTP read-only and write-enabled container smoke: PASS")


if __name__ == "__main__":
    main()
