#!/usr/bin/env python3
"""Exercise MCP Streamable HTTP against a built VaultBridge image."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TOOLS = {
    "list_notes",
    "read_note",
    "search_notes",
    "related_notes",
    "duplicate_candidates",
}
API_KEY = "vb093-current-placeholder"
PREVIOUS_API_KEY = "vb093-previous-placeholder"


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
    ]
    if enabled:
        args += [
            "--env",
            "MCP_HTTP_ALLOWED_HOSTS=vb093-server:8000,127.0.0.1:*",
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


def run_official_client(image: str, server_name: str, network: str, script: Path) -> None:
    run(
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
    )


def orchestrate(image: str) -> None:
    run("docker", "image", "inspect", image)
    suffix = uuid.uuid4().hex[:12]
    network = f"vaultbridge-vb093-{suffix}"
    containers: set[str] = set()
    root = Path(tempfile.mkdtemp(prefix="vaultbridge-vb093-"))
    try:
        run("docker", "network", "create", network)
        directories = (
            root / "disabled-vault",
            root / "disabled-data",
            root / "enabled-vault",
            root / "enabled-data",
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
        run("docker", "network", "connect", "--alias", "vb093-server", network, enabled)
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

        run_official_client(image, enabled, network, Path(__file__))
        stop_cleanly(enabled)
        containers.remove(enabled)
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
        shutil.rmtree(root, ignore_errors=True)


async def client_smoke(url: str) -> None:
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Origin": "https://vb093-client.invalid",
        }
    ) as http_client:
        transport = streamable_http_client(
            url, http_client=http_client, terminate_on_close=False
        )
        async with Client(transport, mode="2026-07-28") as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == TOOLS, f"unexpected MCP tools: {sorted(names)}"
            result = await client.call_tool("list_notes", {})
            paths = {note["path"] for note in result.structured_content["notes"]}
            assert "Smoke.md" in paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="vaultbridge:ci")
    parser.add_argument("--client")
    args = parser.parse_args()
    if args.client:
        asyncio.run(client_smoke(args.client))
        return
    orchestrate(args.image)
    print("VB-093 MCP HTTP container smoke: PASS")


if __name__ == "__main__":
    main()
