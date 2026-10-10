"""Candidate identity and safety-critical release workflow contracts."""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from scripts import verify_release_image as gate

ROOT = Path(__file__).resolve().parents[1]


def constant(path: str, name: str) -> str:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


def test_candidate_versions_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert version == constant("app/main.py", "APP_VERSION") == constant(
        "app/mcp_server.py", "MCP_SERVER_VERSION") == "1.4.0-rc.2"


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/publish-ghcr.yml").read_text())


def test_release_semver_and_prerelease_contract():
    steps = workflow()["jobs"]["verify"]["steps"]
    script = steps[0]["run"]
    pattern = re.search(r"semver_pattern='([^']+)'", script)[1]
    # The workflow's Bash ERE is also valid for these Python regex cases.
    assert re.fullmatch(pattern, "v1.4.0-rc.1")
    assert re.fullmatch(pattern, "v1.4.0-rc.2")
    assert re.fullmatch(pattern, "v1.4.0")
    assert not re.fullmatch(pattern, "1.4.0-rc.1")
    assert not re.fullmatch(pattern, "v1.4.0+local")
    assert '"$release_tag" == *-* && "$expected_prerelease" != "true"' in script
    assert '"$release_is_prerelease" != "$expected_prerelease"' in script
    assert '"$release_is_draft" != "false" || -z "$published_at"' in script
    assert '"$expected_source_sha" =~ ^[0-9a-f]{40}$' in script


@pytest.mark.parametrize(("prerelease", "should_run"), [("true", False), ("false", True)])
def test_stable_aliases_excluded_for_rc(prerelease, should_run):
    jobs = workflow()["jobs"]
    condition = jobs["stable-aliases"]["if"]
    assert condition == "needs.verify.outputs.release_is_prerelease == 'false'"
    assert (prerelease == condition.rsplit("'", 2)[1]) is should_run
    metadata = next(step for step in jobs["publish"]["steps"] if step.get("id") == "meta")["with"]
    assert metadata["flavor"] == "latest=false"
    assert metadata["tags"].strip().splitlines() == [
        "type=semver,pattern={{version}},value=${{ needs.verify.outputs.release_tag }},priority=900"]
    assert "org.opencontainers.image.version=${{ needs.verify.outputs.release_tag }}" in metadata["labels"]
    assert "org.opencontainers.image.revision=${{ needs.verify.outputs.release_source_sha }}" in metadata["labels"]


def test_recovery_and_exact_source_contract():
    data = workflow()
    trigger = data.get("on", data.get(True))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"release_tag", "expected_source_sha", "expected_prerelease"}
    assert all(value["required"] for value in inputs.values())
    assert inputs["expected_prerelease"]["type"] == "boolean"
    verify = data["jobs"]["verify"]["steps"]
    assert "releases/tags/${release_tag}" in verify[0]["run"]
    source = next(step for step in verify if step.get("id") == "source")["run"]
    assert '${RELEASE_TAG}^{commit}' in source
    assert '"$release_source_sha" != "$tag_source_sha"' in source
    assert '"$release_source_sha" != "$EXPECTED_SOURCE_SHA"' in source
    publish = data["jobs"]["publish"]["steps"]
    assert publish[0]["with"]["ref"] == "${{ needs.verify.outputs.release_source_sha }}"
    assert '"$(git rev-parse HEAD)" != "$EXPECTED_SOURCE_SHA"' in publish[1]["run"]


@pytest.mark.parametrize(("tag", "expected", "actual", "draft", "published", "success"), [
    ("v1.4.0-rc.1", "true", "true", "false", "2026-01-01", True),
    ("v1.4.0-rc.1", "false", "false", "false", "2026-01-01", False),
    ("v1.4.0-rc.1", "true", "false", "false", "2026-01-01", False),
    ("v1.4.0-rc.1", "true", "true", "true", "", False),
    ("v1.4.0-rc.2", "true", "true", "false", "2026-01-01", True),
    ("v1.4.0-rc.2", "false", "false", "false", "2026-01-01", False),
    ("v1.4.0-rc.2", "true", "false", "false", "2026-01-01", False),
    ("v1.4.0-rc.2", "true", "true", "true", "", False),
    ("v1.4.0", "false", "false", "false", "2026-01-01", True),
])
def test_execute_release_validation_without_network(tmp_path, tag, expected, actual, draft, published, success):
    # Execute the real safety-critical Bash step, substituting only the GitHub API.
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable; this contract must also run on POSIX CI")
    output = tmp_path / "outputs"
    script = workflow()["jobs"]["verify"]["steps"][0]["run"]
    fake_gh = 'gh() { printf "%s\\n" "$FAKE_RELEASE_FIELDS"; }\n'
    environment = {**os.environ, "EVENT_NAME": "workflow_dispatch", "RECOVERY_RELEASE_TAG": tag,
                   "RECOVERY_EXPECTED_SOURCE_SHA": "a" * 40, "RECOVERY_EXPECTED_PRERELEASE": expected,
                   "GITHUB_REPOSITORY": "example/VaultBridge", "GITHUB_OUTPUT": output.as_posix(),
                   "FAKE_RELEASE_FIELDS": "\t".join((tag, draft, actual, published))}
    completed = subprocess.run([bash, "-c", fake_gh + script], env=environment, capture_output=True, text=True)
    assert (completed.returncode == 0) is success, completed.stderr
    if success:
        assert f"release_is_prerelease={expected}" in output.read_text()


def arguments(*extra):
    return gate.parser().parse_args([
        "--image", "ghcr.io/mrtrollex/vaultbridge@sha256:" + "a" * 64,
        "--expected-revision", "b" * 40, "--expected-version", "1.4.0-rc.1", *extra])


def test_image_input_required():
    with pytest.raises(SystemExit):
        gate.parser().parse_args(["--expected-revision", "b" * 40, "--expected-version", "1.4.0-rc.1"])


@pytest.mark.parametrize(("field", "value"), [
    ("image", "ghcr.io/mrtrollex/vaultbridge:1.4.0-rc.1"),
    ("image", "ghcr.io/mrtrollex/vaultbridge@sha256:bad"),
    ("expected_revision", "main"), ("expected_revision", "B" * 40),
    ("expected_version", "v1.4.0-rc.1"), ("expected_version", "1.4"),
    ("expected_version", "1.4.0-rc.01"), ("expected_version", "1.4.0;false"),
])
def test_bad_identity_inputs_rejected(field, value):
    args = arguments()
    setattr(args, field, value)
    with pytest.raises(ValueError):
        gate.validate_inputs(args)


@pytest.mark.parametrize("version", ["1.4.0-rc.1", "1.4.0-rc.2", "1.3.0"])
def test_identity_labels_must_match(version):
    args = arguments("--expected-version", version)
    gate.validate_inputs(args)
    image = {"Os": "linux", "Architecture": "amd64", "RepoDigests": [args.image], "Config": {"Labels": {
        "org.opencontainers.image.source": gate.SOURCE,
        "org.opencontainers.image.revision": args.expected_revision,
        "org.opencontainers.image.version": f"v{version}",
        "org.opencontainers.image.licenses": "MIT"}}}
    gate.verify_identity(image, args.image, args.expected_revision, args.expected_version)
    for key in ("source", "revision", "version", "licenses"):
        labels = image["Config"]["Labels"]
        old = labels[f"org.opencontainers.image.{key}"]
        labels[f"org.opencontainers.image.{key}"] = "wrong"
        with pytest.raises(AssertionError, match=f"OCI {key} mismatch"):
            gate.verify_identity(image, args.image, args.expected_revision, args.expected_version)
        labels[f"org.opencontainers.image.{key}"] = old
    with pytest.raises(AssertionError, match="RepoDigest"):
        gate.verify_identity(image, args.image + "wrong", args.expected_revision, args.expected_version)
    image["Architecture"] = "arm64"
    with pytest.raises(AssertionError, match="platform"):
        gate.verify_identity(image, args.image, args.expected_revision, args.expected_version)


def test_cleanup_rejects_unrelated_root(tmp_path):
    with pytest.raises(ValueError, match="unsafe disposable cleanup"):
        gate.validate_cleanup(tmp_path, tmp_path.resolve())
    assert tmp_path.is_dir()


def test_identity_failure_prevents_functional_execution_and_cleans_root(monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    roots = []
    commands = []
    original_mkdtemp = gate.tempfile.mkdtemp

    def mkdtemp(**kwargs):
        root = original_mkdtemp(**kwargs)
        roots.append(Path(root))
        return root

    def run(*args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout=json.dumps([{"Os": "linux", "Architecture": "amd64",
                                                 "RepoDigests": [], "Config": {}}]))

    monkeypatch.setattr(gate.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(gate.smoke, "run", run)
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    with pytest.raises(AssertionError, match="RepoDigest"):
        gate.orchestrate(arguments())
    assert roots and all(not root.exists() for root in roots)
    assert not any(command[1] == "run" for command in commands)
    assert "PASS" not in capsys.readouterr().out


def test_local_mode_cannot_claim_release_identity():
    args = gate.parser().parse_args(["--local-image", "vaultbridge:v140-rc-prep",
                                    "--expected-revision", "b" * 40, "--expected-version", "1.4.0-rc.1"])
    gate.validate_inputs(args)
    assert args.image is None


def test_compatible_restart_requires_reuse_evidence():
    import json

    initial = {"event": "semantic_sync_completed", "indexed_notes": 2, "unchanged_notes": 0}
    reused = {"event": "semantic_sync_completed", "indexed_notes": 0, "unchanged_notes": 2}
    gate.verify_index_reuse("uvicorn message\n" + json.dumps(initial) + "\n" + json.dumps(reused))
    with pytest.raises(AssertionError, match="unexpectedly rebuilt"):
        gate.verify_index_reuse(json.dumps(initial) + "\n" + json.dumps(initial))
    with pytest.raises(AssertionError, match="evidence missing"):
        gate.verify_index_reuse(json.dumps(initial))


def test_documented_cli_examples_parse():
    from app.cli import _parser

    for command in (
        ["hygiene", "scan", "--json"],
        ["hygiene", "scan", "--group", "relationships", "--finding-limit", "100"],
        ["query", "--semantic-text", "backup", "--folder", "Projects", "--tag", "active", "--limit", "10"],
        ["promote", "review", "--source-path", "Inbox/Captures/<capture-id>.md", "--capture-id", "<capture-id>"],
        ["promote", "apply"], ["capture"],
    ):
        assert _parser().parse_args(command)


def test_current_documentation_local_links_exist():
    for name in ("README.md", "README_TRUENAS.md", "ARCHITECTURE.md", "PROJECT_STATE.md", "ROADMAP.md",
                 "docs/RELEASE_CHECKLIST.md", "docs/DASHBOARD_RELEASE_CHECKLIST.md", "docs/V140_RC_RUNBOOK.md"):
        path = ROOT / name
        content = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        content = re.sub(r"`[^`\n]+`", "", content)
        for target in re.findall(r"\]\(([^)]+)\)", content):
            if "://" in target or target.startswith("#"):
                continue
            local = target.split("#", 1)[0]
            assert (path.parent / local).exists(), f"{name}: missing local link {local}"


def publication_blocks():
    doc = (ROOT / "docs/V140_RC_RUNBOOK.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```powershell\n(.*?)```", doc, re.S)
    assert blocks, "publication procedure must use checked PowerShell"
    freeze = next(block for block in blocks if "function Invoke-Checked" in block)
    publication = next(block for block in blocks if "'release', 'create'" in block)
    return blocks, freeze, publication


def test_runbook_fails_closed_and_pins_all_source_checks():
    blocks, freeze, publication = publication_blocks()
    assert "$ErrorActionPreference = 'Stop'" in freeze
    assert "$output = & $Command @Arguments" in freeze
    assert "if ($LASTEXITCODE -ne 0) { throw" in freeze
    assert freeze.index("if ($LASTEXITCODE -ne 0)") < freeze.index("return $output")
    assert "$reviewedSha = (Invoke-Checked git" in freeze
    assert "'HEAD^{commit}'" in freeze and "'^[0-9a-f]{40}$'" in freeze
    assert "@('status', '--porcelain')).Count -ne 0" in freeze
    assert "$headSha -cne $reviewedSha" in freeze
    for block in blocks:
        assert "1.4.0-rc.1" not in block, "historical rc.1 must not be the next execution target"
        assert not re.search(r"(?m)^\s*(git|gh|python|docker)\s", block), "unchecked native command"
    for required in (
        "$tag = 'v1.4.0-rc.2'", "'for-each-ref'", "$localRefs.Count -ne 0",
        "'ls-remote', '--tags'", "'fetch', '--no-tags'", "'FETCH_HEAD^{commit}'",
        "$remoteBefore -cne $reviewedSha", "$localCommit -cne $reviewedSha",
        "$remoteAfter -cne $reviewedSha", "$publishedCommit -cne $reviewedSha",
        "'--verify-tag', '--prerelease'", "$release.tagName -cne $tag",
        "$release.isPrerelease -ne $true", "$release.isDraft -ne $false",
        "IsNullOrWhiteSpace($release.publishedAt)", "'--repo', $releaseRepository",
    ):
        assert required in publication
    phases = ("$remoteBefore =", "Assert-ReviewedCheckout #", "@('tag', '-a'",
              '"$tag^{commit}"', "@('push'", "$remoteAfter =", "$remoteAfter -cne $reviewedSha",
              "@('release', 'create'", "@('release', 'view'", "$publishedCommit =",
              "$publishedCommit -cne $reviewedSha")
    positions = [publication.index(phase) for phase in phases]
    assert positions == sorted(positions), "source/prerelease checks must guard publication in order"


def powershell():
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell execution tested on the native operator platform")
    return executable


def test_checked_runbook_helper_stops_on_native_failure(tmp_path):
    _, freeze, _ = publication_blocks()
    helper = re.search(r"(?ms)^function Invoke-Checked \{.*?^\}", freeze)[0]
    shell = powershell()
    script = tmp_path / "failure.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n" + helper + "\n" +
                      "Invoke-Checked $env:TEST_POWERSHELL -Arguments @('-NoProfile', '-NonInteractive', "
                      "'-Command', 'exit 9')\nWrite-Output 'UNSAFE_CONTINUATION'\n", encoding="utf-8")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                            env={**os.environ, "TEST_POWERSHELL": shell}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "UNSAFE_CONTINUATION" not in result.stdout
    assert "failed with exit code 9" in result.stderr


def test_runbook_powershell_blocks_parse_without_execution(tmp_path):
    blocks, _, _ = publication_blocks()
    source = tmp_path / "runbook.ps1"
    source.write_text("\n".join(blocks), encoding="utf-8")
    parser_script = (
        "$tokens = $null; $errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile($env:TEST_RUNBOOK, "
        "[ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count) { $errors; exit 1 }"
    )
    result = subprocess.run([powershell(), "-NoProfile", "-NonInteractive", "-Command", parser_script],
                            env={**os.environ, "TEST_RUNBOOK": str(source)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_project_state_current_mcp_and_dashboard_contract():
    from scripts.smoke_mcp_http import TOOLS, WRITE_TOOLS

    current = (ROOT / "PROJECT_STATE.md").read_text(encoding="utf-8").split(
        "## Working production characteristics", 1)[1]
    assert "exactly eight default read-only tools" in current and "(ten total)" in current
    for name in TOOLS | WRITE_TOOLS:
        assert f"`{name}`" in current
    assert "with five MCP tools" not in current and "with seven MCP tools" not in current
    assert "operator-supplied Bearer-key validation" not in current
    for description in ("`/ui/session`", "signed HttpOnly session cookie", "`X-VaultBridge-UI-Request: 1`",
                        "no raw request credential in sessionStorage/localStorage", "Logout", "`401`"):
        assert description in current
