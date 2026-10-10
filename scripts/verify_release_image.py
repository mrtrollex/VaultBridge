#!/usr/bin/env python3
"""Disposable functional gate for an immutable published release image.

Python is intentional: validation/cleanup and HTTP assertions are unit-testable on
Windows and POSIX. --local-image tests mechanics only, never published identity.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if __package__:
    from . import smoke_mcp_http as smoke
else:
    import smoke_mcp_http as smoke

SOURCE = "https://github.com/mrtrollex/VaultBridge"
PREFIX = "vaultbridge-release-"
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    images = result.add_mutually_exclusive_group(required=True)
    images.add_argument("--image", help="ghcr.io/mrtrollex/vaultbridge@sha256:<64 lowercase hex>")
    images.add_argument("--local-image", help="local mechanics only; skips pull/OCI identity")
    result.add_argument("--expected-revision", required=True)
    result.add_argument("--expected-version", required=True, help="application version without v prefix")
    return result


def validate_inputs(args: argparse.Namespace) -> None:
    if args.image and not re.fullmatch(r"ghcr\.io/mrtrollex/vaultbridge@sha256:[0-9a-f]{64}", args.image):
        raise ValueError("image must be the exact repository with a lowercase sha256 digest")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_revision):
        raise ValueError("expected revision must be a full lowercase source SHA")
    if not re.fullmatch(VERSION, args.expected_version):
        raise ValueError("expected version must be release SemVer without v or build metadata")
    prerelease = args.expected_version.partition("-")[2]
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
        raise ValueError("numeric prerelease identifiers must not have leading zeros")
    if args.local_image and not re.fullmatch(r"vaultbridge:[a-z0-9][a-z0-9_.-]*", args.local_image):
        raise ValueError("local mechanics image must use an explicit vaultbridge:<local-tag>")


def verify_identity(image: dict, reference: str, revision: str, version: str) -> None:
    assert (image["Os"], image["Architecture"]) == ("linux", "amd64"), "release platform mismatch"
    assert reference in image.get("RepoDigests", []), "exact RepoDigest mismatch"
    labels = image["Config"].get("Labels") or {}
    for key, expected in {
        "source": SOURCE, "revision": revision, "version": f"v{version}", "licenses": "MIT",
    }.items():
        assert labels.get(f"org.opencontainers.image.{key}") == expected, f"OCI {key} mismatch"


def validate_cleanup(root: Path, expected: Path) -> None:
    assert not root.is_symlink(), "unsafe disposable cleanup target"
    resolved = root.resolve()
    if (resolved != expected or resolved.parent != Path(tempfile.gettempdir()).resolve()
            or not resolved.name.startswith(PREFIX) or resolved.name == PREFIX):
        raise ValueError("unsafe disposable cleanup target")


def hashes(vault: Path) -> dict[str, str]:
    return {p.relative_to(vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in vault.rglob("*") if p.is_file()}


def verify_index_reuse(log_text: str) -> None:
    synchronizations = []
    for line in log_text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == "semantic_sync_completed":
            synchronizations.append(record)
    assert len(synchronizations) >= 2, "initial/restart sync evidence missing"
    assert synchronizations[0]["indexed_notes"] == 2, "initial synthetic index missing"
    assert synchronizations[-1]["indexed_notes"] == 0, "compatible index unexpectedly rebuilt"
    assert synchronizations[-1]["unchanged_notes"] == 2, "compatible notes were not reused"


class HTTP:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path: str, *, method: str = "GET", body=None, headers=None):
        request = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def json(self, path: str, **kwargs):
        status, _, body = self.request(path, **kwargs)
        assert status == 200, f"HTTP gate failed: {path.split('?')[0]} ({status})"
        return json.loads(body)


def wait_ready(http: HTTP) -> None:
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        try:
            status, _, body = http.request("/health/ready")
            if status == 200 and json.loads(body) == {"ready": True}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(3)
    raise RuntimeError("bounded semantic readiness timeout")


def dashboard(http: HTTP, key: str, original: dict, vault: Path) -> list[str]:
    # Disable redirect following for the canonical-path assertion.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        urllib.request.build_opener(NoRedirect()).open(http.base + "/ui", timeout=30)
    except urllib.error.HTTPError as error:
        assert error.code == 307 and error.headers["Location"] == "/ui/"
    else:
        raise AssertionError("canonical UI redirect missing")
    status, headers, body = http.request("/ui/")
    assert status == 200 and "text/html" in headers["Content-Type"]
    assert key.encode() not in body
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    csp = headers["Content-Security-Policy"]
    for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                      "connect-src 'self'", "object-src 'none'", "frame-ancestors 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert b"Hygiene" in body and b"Overview" in body
    for asset in ("app.js", "app.css", "overview.js", "search.js", "hygiene.js", "vaultbridge-logo.webp"):
        status, headers, content = http.request("/ui/assets/" + asset)
        assert status == 200 and content and headers["X-Content-Type-Options"] == "nosniff"
        assert key.encode() not in content
    assert http.request("/ui/not-a-route")[0] == 404
    assert http.request("/openapi.json")[0] == 404
    assert http.json("/health")["ok"] is True  # Overview's public contract.
    assert http.request("/ui/session")[0] == 401
    assert http.request("/ui/session", method="POST", body={"api_key": "wrong"})[0] == 401
    status, headers, body = http.request("/ui/session", method="POST", body={"api_key": key})
    assert status == 200 and json.loads(body) == {"authenticated": True}
    assert headers["Cache-Control"] == "no-store"
    cookie_header = headers["Set-Cookie"]
    assert "HttpOnly" in cookie_header and "SameSite=strict" in cookie_header
    assert "Max-Age=604800" in cookie_header and "Path=/" in cookie_header
    assert "Secure" not in cookie_header  # Loopback HTTP; HTTPS is covered by source tests.
    cookies = [cookie.value for cookie in http.jar]
    assert cookies and all(key not in value for value in cookies)
    assert http.json("/ui/session") == {"authenticated": True}
    ui_headers = {"X-VaultBridge-UI-Request": "1"}
    assert http.json("/api/v1/notes/list?limit=10", headers=ui_headers)["notes"]
    result = http.json("/api/v1/knowledge/hygiene/scan", method="POST", body={}, headers=ui_headers)
    assert result["scan"]["state"] == "complete"
    assert any(f["kind"] == "missing_relationship_target" and f["primary_path"] == "alpha.md"
               for f in result["findings"])
    assert hashes(vault) == original, "Hygiene mutated authoritative bytes"
    # A forged/invalidated cookie is rejected; UI cleanup is exercised by Chromium E2E.
    bad = HTTP(http.base)
    assert bad.request("/api/v1/notes/list", headers={**ui_headers,
                       "Cookie": "vaultbridge_ui_session=invalid"})[0] == 401
    assert http.json("/ui/session", method="DELETE") == {"authenticated": False}
    assert not list(http.jar)
    assert http.request("/ui/session")[0] == 401
    assert http.request("/api/v1/notes/list", headers=ui_headers)[0] == 401
    return cookies


def rest(http: HTTP, key: str, previous: str, marker: str) -> None:
    for token in (None, "invalid"):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        assert http.request("/api/v1/notes/list", headers=headers)[0] == 401
    for token in (key, previous):
        headers = {"Authorization": f"Bearer {token}"}
        assert {n["path"] for n in http.json("/api/v1/notes/list?limit=10", headers=headers)["notes"]} == {
            "alpha.md", "beta.md"}
    headers = {"Authorization": f"Bearer {key}"}
    assert marker in http.json("/api/v1/notes/read?path=alpha.md", headers=headers)["content"]
    literal = http.json("/api/v1/notes/search", method="POST", body={"query": marker}, headers=headers)
    assert [n["path"] for n in literal["results"]] == ["alpha.md"]
    semantic = http.json("/api/v1/notes/related", method="POST",
                         body={"text": "astronomy telescope galaxies", "limit": 2, "min_score": -1}, headers=headers)
    assert semantic["results"] and semantic["results"][0]["path"] == "beta.md"
    links = http.json("/api/v1/notes/links?path=alpha.md", headers=headers)["links"]
    assert any(link["resolved_path"] == "beta.md" for link in links)
    backlinks = http.json("/api/v1/notes/backlinks?path=beta.md", headers=headers)["backlinks"]
    assert any(link["source_path"] == "alpha.md" for link in backlinks)


def orchestrate(args: argparse.Namespace) -> None:
    validate_inputs(args)
    image = args.image or args.local_image
    run = smoke.run
    run("docker", "info")
    root = Path(tempfile.mkdtemp(prefix=PREFIX)).resolve()
    expected_root = root
    name = "vaultbridge-release-" + uuid.uuid4().hex[:12]
    created = False
    key, previous, marker = (secrets.token_urlsafe(36) for _ in range(3))
    cookies: list[str] = []
    try:
        if args.image:
            auth = root / "docker-auth"
            auth.mkdir()
            # Empty configuration excludes operator registry credentials/helpers.
            environment = {**os.environ, "DOCKER_CONFIG": str(auth)}
            pulled = subprocess.run(["docker", "pull", image], env=environment, capture_output=True, timeout=1200)
            assert pulled.returncode == 0, "anonymous exact-digest pull failed"
        metadata = json.loads(run("docker", "image", "inspect", image, capture=True).stdout)[0]
        if args.image:
            verify_identity(metadata, image, args.expected_revision, args.expected_version)
        else:
            assert (metadata["Os"], metadata["Architecture"]) == ("linux", "amd64")
        # Runtime version must agree before starting functional assertions.
        run("docker", "run", "--rm", "--user", "568:568", "--cap-drop", "ALL", "--entrypoint", "python",
            image, "-c", "from app.main import APP_VERSION, app; from app.mcp_server import MCP_SERVER_VERSION; "
            f"assert APP_VERSION == MCP_SERVER_VERSION == app.version == {args.expected_version!r}")
        vault, data = root / "vault", root / "data"
        for directory in (vault, data):
            directory.mkdir()
            directory.chmod(0o777)
        (vault / "alpha.md").write_text("# Database notes\n\nRelational transactions.\n" + marker +
                                      "\n[[beta]]\n[[Missing synthetic target]]\n", encoding="utf-8")
        (vault / "beta.md").write_text(
            "# Deep sky observing\n\nOrbital telescopes observe distant galaxies and nebulae.\n", encoding="utf-8")
        original = hashes(vault)
        env_args = []
        for entry in (f"API_KEY={key}", f"API_KEY_PREVIOUS={previous}", "VAULT_PATH=/vault",
                      "SEMANTIC_DATA_PATH=/data", "HF_HOME=/data/huggingface", "SEMANTIC_WATCH_ENABLED=false"):
            env_args.extend(("--env", entry))
        created = True  # Also clean up a partially failed docker run.
        run("docker", "run", "--detach", "--name", name, "--user", "568:568", "--cap-drop", "ALL",
            "--publish", "127.0.0.1::8000", *env_args,
            "--volume", f"{vault}:/vault:rw", "--volume", f"{data}:/data:rw", image)
        http = HTTP(smoke.published_url(name))
        smoke.wait_until_live(http.base, name)
        assert http.json("/health/live") == {"ok": True}
        health = http.json("/health")
        assert set(health) == {
            "ok", "vault_exists", "semantic_index_ready", "semantic_index_state",
            "semantic_search_available", "semantic_indexer_running", "full_sync_required",
            "indexed_notes", "semantic_chunks", "vault_notes", "last_successful_sync",
        }, "rich health contract mismatch"
        assert health["ok"] and health["vault_exists"]
        assert not any(forbidden in json.dumps(health) for forbidden in (key, previous, marker, str(root)))
        wait_ready(http)
        assert http.json("/health")["semantic_search_available"] is True
        cookies = dashboard(http, key, original, vault)
        rest(http, key, previous, marker)
        assert any(p.is_file() and p.stat().st_size for p in data.rglob("*")), "derived data missing"
        run("docker", "stop", "--time", "60", name)
        assert run("docker", "inspect", "--format", "{{.State.ExitCode}}", name, capture=True).stdout.strip() == "0"
        # Every local CLI read uses stopped-service mounts; writes only get parser checks.
        for command in (("--help",), ("status",), ("index", "check"), ("search", marker),
                        ("related", "astronomy telescope galaxies"), ("query", "--literal-text", marker),
                        ("hygiene", "scan", "--json"), ("capture", "--help"),
                        ("promote", "review", "--help"), ("promote", "apply", "--help")):
            run("docker", "run", "--rm", "--user", "568:568", "--cap-drop", "ALL", *env_args,
                "--volume", f"{vault}:/vault:rw", "--volume", f"{data}:/data:rw", "--entrypoint", "python",
                image, "-m", "app.cli", *command)
        run("docker", "start", name)
        http = HTTP(smoke.published_url(name))
        smoke.wait_until_live(http.base, name)
        wait_ready(http)
        rest(http, key, previous, marker)
        assert hashes(vault) == original, "restart/CLI mutated authoritative bytes"
        smoke.wait_until_indexer_idle(http.base)
        run("docker", "stop", "--time", "60", name)
        assert run("docker", "inspect", "--format", "{{.State.ExitCode}}", name, capture=True).stdout.strip() == "0"
        logs = run("docker", "logs", "--tail", "5001", name, capture=True)
        log_text = logs.stdout + logs.stderr
        assert len(log_text.splitlines()) <= 5000, "log history exceeds bounded audit"
        assert len(log_text) <= 2_000_000, "log audit exceeds bounded evidence limit"
        verify_index_reuse(log_text)
        for forbidden in (key, previous, marker, str(root), "API_KEY=", *cookies):
            assert forbidden not in log_text, "privacy-safe log audit failed"
        assert "authorization:" not in log_text.lower() and "cookie:" not in log_text.lower(), "header leak"
        run("docker", "rm", name)
        created = False
        assert not run("docker", "ps", "-aq", "--filter", f"name=^{name}$", capture=True).stdout.strip()
    finally:
        try:
            if created:
                run("docker", "rm", "--force", name)
        finally:
            validate_cleanup(root, expected_root)
            smoke.cleanup_disposable_root(root, expected_root=expected_root, image=image, prefix=PREFIX)
    print("LOCAL MECHANICS: PASS (published identity NOT VERIFIED)" if args.local_image else
          f"Release immutable-image gate: PASS ({image}; revision {args.expected_revision}; {args.expected_version})")


def main() -> None:
    args = parser().parse_args()
    try:
        orchestrate(args)
    except (AssertionError, ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Release image gate: FAIL ({error})") from None


if __name__ == "__main__":
    main()
