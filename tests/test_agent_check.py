from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import agent_check, agent_validation


@pytest.fixture(autouse=True)
def isolated_validation(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", tmp_path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/CODEX_PLAYBOOK.md", {"docs"}),
        ("app/api/notes.py", {"python", "api", "ui"}),
        ("app/services/semantic_search.py", {"python", "semantic"}),
        ("tests/eval/retrieval_cases.json", {"semantic"}),
        ("app/ui/assets/app.js", {"ui"}),
        ("tests/e2e/test_dashboard.py", {"python", "ui"}),
        ("app/mcp_http.py", {"python", "mcp"}),
        ("app/core/config.py", {"python", "semantic", "api", "mcp"}),
        ("Dockerfile", {"docker"}),
        ("compose.truenas.yml", {"docker", "truenas/deployment"}),
        ("README_TRUENAS.md", {"docs", "truenas/deployment"}),
        ("ix-dev/community/vaultbridge/app.yaml", {"truenas/deployment"}),
    ],
)
def test_classify_file_uses_repository_paths(path, expected):
    assert agent_check.classify_file(path) == expected


def test_discovery_unions_branch_index_worktree_and_untracked(monkeypatch, tmp_path):
    base = "a" * 40
    merge_base = "0123456789abcdef" * 2 + "01234567"
    reference = "refs/remotes/origin/main"
    diff = (
        "diff", "--no-ext-diff", "--no-relative", "--ignore-submodules=none",
        "--find-renames=100%", "--find-copies=100%", "--find-copies-harder", "--name-status", "-z",
    )
    responses = {
        ("rev-parse", "--show-toplevel"): os.fsencode(tmp_path) + b"\n",
        ("ls-files", "--cached", "-v", "-z"): b"H app/main.py\0",
        ("rev-parse", "--verify", "--quiet", "--end-of-options", f"{reference}^{{commit}}"): base.encode(),
        ("rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", reference): reference.encode(),
        ("merge-base", "--all", "HEAD", base): merge_base.encode() + b"\n",
        (*diff, merge_base, "HEAD"): b"M\0app/main.py\0",
        (*diff, "--cached"): b"D\0tests/test_api.py\0",
        diff: b"M\0docs/guide with spaces.md\0",
        ("ls-files", "--others", "--exclude-standard", "-z"): b"scripts/new.py\0",
    }

    def fake_git(root: Path, *args: str, check: bool = True) -> bytes:
        assert root == tmp_path
        return responses[args]

    monkeypatch.setattr(agent_check, "_git", fake_git)
    monkeypatch.setattr(agent_check, "_git_ref_exists", lambda root, ref: ref == "refs/remotes/origin/main")
    monkeypatch.setattr(agent_validation, "require_supported_checkout_attributes", lambda *args: None)
    monkeypatch.setattr(agent_validation, "checkout_eol_settings", lambda root: (b"false", b"lf"))
    monkeypatch.setattr(agent_validation, "checkout_byte_changes", lambda *args: set())

    discovery = agent_check.discover_changed_files(tmp_path)

    assert discovery.files == (
        "app/main.py",
        "docs/guide with spaces.md",
        "scripts/new.py",
        "tests/test_api.py",
    )
    assert discovery.base == "refs/remotes/origin/main (merge base 0123456789ab)"
    assert discovery.base_commit == base
    assert not discovery.workflow_only


WORKFLOW_TESTS = (
    "tests/test_agent_task.py", "tests/test_agent_check.py", "tests/test_agent_finish.py",
    "tests/test_agent_review.py", "tests/test_agent_validation.py", "tests/test_agent_evidence.py",
)
WORKFLOW_SCRIPTS = (
    "scripts/agent_task.py", "scripts/agent_check.py", "scripts/agent_finish.py",
    "scripts/agent_review.py", "scripts/agent_validation.py", "scripts/agent_evidence.py",
    "scripts/agent_evidence_io.py",
)
PLAYBOOK = "docs/CODEX_PLAYBOOK.md"
FULL_CHECKS = ("ruff", "pytest", "compileall")
WORKFLOW_CHECKS = ("ruff", "pytest-workflow", "compileall")


def _checkout_helpers(root):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=root, stderr=subprocess.DEVNULL,
        )

    def write(path, content="changed\n"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return git, write


@pytest.fixture(scope="module")
def workflow_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("workflow-template")
    git, write = _checkout_helpers(root)
    git("init", "-b", "main")
    for path in (*WORKFLOW_SCRIPTS, *WORKFLOW_TESTS, PLAYBOOK, "app/module.py", "docs/guide.md"):
        write(path, f"base: {path}\n")
    git("add", ".")
    git("commit", "-m", "base")
    return root


@pytest.fixture
def workflow_checkout(tmp_path, workflow_template):
    # Each test owns its worktree and all Git metadata, including the object store.
    shutil.copytree(workflow_template, tmp_path, dirs_exist_ok=True)
    return _checkout_helpers(tmp_path)


def _checks_for(discovery):
    return agent_check.select_checks(
        agent_check.classify_files(discovery.files), discovery.files, workflow_only=discovery.workflow_only,
    )


def _materialize_index(root, git):
    git("checkout-index", "--all", "--prefix=.git/materialized/")
    for name in git("ls-files", "-z").decode("utf-8").strip("\0").split("\0"):
        (root / ".git/materialized" / name).replace(root / name)


def test_local_and_managed_scope_ignore_inherited_feature_base(tmp_path, workflow_checkout, monkeypatch):
    git, write = workflow_checkout
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("switch", "-c", "feature")
    write("app/module.py")
    git("commit", "-am", "committed runtime change")
    git("update-ref", "refs/remotes/origin/feature", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature")
    write(WORKFLOW_TESTS[0])
    expected = agent_check.discover_changed_files(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_BASE_REF", "feature")
    assert agent_check.discover_changed_files(tmp_path) == expected
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert agent_check.discover_changed_files(tmp_path, managed=True) == expected
    assert expected.base_ref == "refs/remotes/origin/main"
    assert expected.files == ("app/module.py", WORKFLOW_TESTS[0])
    assert tuple(check.key for check in _checks_for(expected)) == FULL_CHECKS


def test_hosted_ci_and_explicit_comparisons_keep_selected_base_without_narrowing(
    tmp_path, workflow_checkout, monkeypatch,
):
    git, write = workflow_checkout
    git("switch", "-c", "release")
    write("app/module.py")
    git("commit", "-am", "runtime included in release")
    git("update-ref", "refs/remotes/origin/release", "HEAD")
    git("switch", "-c", "feature")
    write(WORKFLOW_TESTS[0])
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_BASE_REF", "release")
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.base_ref == "refs/remotes/origin/release"
    assert discovery.files == (WORKFLOW_TESTS[0],)
    assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS

    explicit = agent_check.discover_changed_files(tmp_path, base_ref="release")
    assert explicit.base_ref == "refs/heads/release"
    assert explicit.files == discovery.files
    assert tuple(check.key for check in _checks_for(explicit)) == FULL_CHECKS
    with pytest.raises(agent_check.DiscoveryError, match="requires the trusted"):
        agent_check.discover_changed_files(tmp_path, base_ref="release", managed=True)


@pytest.mark.parametrize("problem", ("missing", "redirected", "ambiguous"))
def test_invalid_trusted_comparison_blocks_discovery(tmp_path, workflow_checkout, monkeypatch, problem):
    git, _ = workflow_checkout
    git("switch", "-c", "feature")
    if problem == "missing":
        git("branch", "-D", "main")
        message = "trusted main comparison base"
    elif problem == "redirected":
        git("symbolic-ref", "refs/remotes/origin/main", "refs/heads/feature")
        message = "verify the trusted main comparison reference"
    else:
        real_git = agent_check._git

        def git_with_ambiguous_base(root, *args, **kwargs):
            if args[0] == "merge-base":
                assert args[1] == "--all"
                return b"a" * 40 + b"\n" + b"b" * 40 + b"\n"
            return real_git(root, *args, **kwargs)

        monkeypatch.setattr(agent_check, "_git", git_with_ambiguous_base)
        message = "unique merge base"
    with pytest.raises(agent_check.DiscoveryError, match=message):
        agent_check.discover_changed_files(tmp_path, managed=True)


def test_git_index_environment_override_cannot_redefine_checkout(tmp_path, workflow_checkout, monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other-index"))
    with pytest.raises(agent_check.DiscoveryError, match="Git environment overrides"):
        agent_check.discover_changed_files(tmp_path)
    assert agent_validation.source_identity(tmp_path) is None


def test_git_config_environment_cannot_exclude_untracked_pytest_configuration(
    tmp_path, workflow_checkout, monkeypatch,
):
    git, write = workflow_checkout
    write(WORKFLOW_TESTS[0])
    write("conftest.py")
    scope = agent_check.discover_changed_files(tmp_path, managed=True)
    assert scope.files == ("conftest.py", WORKFLOW_TESTS[0])
    assert tuple(check.key for check in _checks_for(scope)) == FULL_CHECKS

    exclude = tmp_path / ".git/scope-excludes"
    exclude.write_text("conftest.py\n", encoding="utf-8")
    config = tmp_path / ".git/scope-config"
    git("config", "--file", str(config), "core.excludesFile", exclude.as_posix())
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert git("ls-files", "--others", "--exclude-standard", "-z") == b""
    with pytest.raises(agent_check.DiscoveryError, match="Git environment overrides"):
        agent_check.discover_changed_files(tmp_path, managed=True)
    assert agent_validation.source_identity(tmp_path) is None


@pytest.mark.parametrize(("autocrlf", "eol", "attribute", "expected"), [
    ("true", "lf", "", b"checkout\r\n"),
    ("YES", "lf", "", b"checkout\r\n"),
    ("On", "lf", "", b"checkout\r\n"),
    ("2", "lf", "", b"checkout\r\n"),
    (None, "lf", "", b"checkout\r\n"),
    ("false", "lf", "", b"checkout\n"),
    ("NO", "crlf", "", b"checkout\n"),
    ("off", "lf", "", b"checkout\n"),
    ("0", "lf", "", b"checkout\n"),
    ("", "lf", "", b"checkout\n"),
    ("input", "crlf", "app/module.py text\n", b"checkout\n"),
    ("INPUT", "CRLF", "app/module.py text\n", b"checkout\n"),
    ("false", "crlf", "app/module.py text=auto\n", b"checkout\r\n"),
    ("FALSE", "CRLF", "app/module.py text=auto\n", b"checkout\r\n"),
    ("true", "crlf", "app/module.py text eol=lf\n", b"checkout\n"),
    ("false", "lf", "app/module.py eol=crlf\n", b"checkout\r\n"),
    ("true", "crlf", "app/module.py -text\n", b"checkout\n"),
])
def test_git_materialized_checkout_keeps_workflow_eligible(
    tmp_path, workflow_checkout, autocrlf, eol, attribute, expected,
):
    git, write = workflow_checkout
    git("config", "core.autocrlf", "false")
    (tmp_path / ".gitattributes").write_bytes(attribute.encode("ascii"))
    runtime = tmp_path / "app/module.py"
    runtime.write_bytes(b"checkout\n")
    git("add", ".gitattributes", "app/module.py")
    git("commit", "-m", "checkout attributes")
    if autocrlf is None:
        with (tmp_path / ".git/config").open("ab") as config:
            config.write(b"\n[core]\n\tautocrlf\n")
    else:
        git("config", "core.autocrlf", autocrlf)
    git("config", "core.eol", eol)
    _materialize_index(tmp_path, git)
    assert runtime.read_bytes() == expected
    assert _checks_for(agent_check.discover_changed_files(tmp_path, managed=True)) == ()
    write(WORKFLOW_TESTS[0])
    scope = agent_check.discover_changed_files(tmp_path, managed=True)
    assert scope.raw_changed_files == ()
    assert scope.files == (WORKFLOW_TESTS[0],)
    assert tuple(check.key for check in _checks_for(scope)) == WORKFLOW_CHECKS


def test_checkout_hidden_raw_difference_generalizes_beyond_the_reference_fixture(
    tmp_path, workflow_checkout,
):
    git, write = workflow_checkout
    git("config", "core.autocrlf", "true")
    _materialize_index(tmp_path, git)
    runtime = tmp_path / "app/module.py"
    crlf = runtime.read_bytes()
    assert b"\r\n" in crlf
    runtime.write_bytes(crlf.replace(b"\r\n", b"\n"))
    write(WORKFLOW_TESTS[0])
    assert git("diff", "--name-only").splitlines() == [WORKFLOW_TESTS[0].encode()]
    scope = agent_check.discover_changed_files(tmp_path, managed=True)
    assert scope.raw_changed_files == ("app/module.py",)
    assert tuple(check.key for check in _checks_for(scope)) == FULL_CHECKS


@pytest.mark.parametrize("attribute", ("filter=unset", "ident", "working-tree-encoding=UTF-16LE"))
def test_unsupported_transformations_block_before_running_clean_filters(
    tmp_path, workflow_checkout, capsys, attribute,
):
    git, write = workflow_checkout
    write(".gitattributes", f"app/module.py {attribute}\n")
    git("add", ".gitattributes")
    git("config", "filter.unset.clean", "echo invoked >> .git/filter-invoked; cat")
    git("config", "filter.unset.smudge", "echo invoked >> .git/filter-invoked; cat")
    write("app/module.py")
    assert agent_check.main(["--run-id", "286a7db0-4bf9-4d54-8186-a18ad770036e"]) == 2
    assert "unsupported Git checkout transformation" in capsys.readouterr().err
    assert not (tmp_path / ".git/filter-invoked").exists()
    assert not (tmp_path / agent_validation.REPORT_PATH).exists()


def test_private_attributes_cannot_override_tracked_line_ending_contract(
    tmp_path, workflow_checkout, capsys,
):
    git, write = workflow_checkout
    write(".gitattributes", "app/module.py text eol=lf\n")
    git("add", ".gitattributes", "app/module.py")
    git("commit", "-m", "tracked LF contract")
    (tmp_path / ".git/info/attributes").write_text("app/module.py text eol=crlf\n", encoding="ascii")
    _materialize_index(tmp_path, git)
    write(WORKFLOW_TESTS[0])
    assert git("check-attr", "eol", "--", "app/module.py").strip() == b"app/module.py: eol: crlf"
    assert b"\r\n" in (tmp_path / "app/module.py").read_bytes()
    assert git("diff", "--name-only").splitlines() == [WORKFLOW_TESTS[0].encode()]
    assert agent_check.main(["--dry-run"]) == 2
    assert "private Git attribute overrides" in capsys.readouterr().err


@pytest.mark.parametrize("attribute_path", (".gitattributes", "app/.gitattributes"))
@pytest.mark.parametrize("visible", ("clean", "docs", "workflow"))
def test_untracked_attribute_sources_block_any_visible_scope(
    tmp_path, workflow_checkout, capsys, attribute_path, visible,
):
    _, write = workflow_checkout
    write(attribute_path, "module.py text eol=crlf\n")
    if visible == "docs":
        write("docs/guide.md")
    if visible == "workflow":
        write(WORKFLOW_TESTS[0])
    assert agent_check.main(["--dry-run"]) == 2
    assert "unindexed worktree .gitattributes" in capsys.readouterr().err
    assert agent_validation.source_identity(tmp_path) is None


def test_modified_indexed_nested_attribute_file_requires_full_checks(tmp_path, workflow_checkout):
    git, write = workflow_checkout
    attributes = "docs/.gitattributes"
    write(attributes, "*.md text eol=lf\n")
    git("add", attributes)
    git("commit", "-m", "indexed nested attributes")
    _materialize_index(tmp_path, git)
    write(WORKFLOW_TESTS[0])
    assert agent_check.discover_changed_files(tmp_path, managed=True).workflow_only
    write(attributes, "*.md text eol=crlf\n")
    scope = agent_check.discover_changed_files(tmp_path, managed=True)
    assert attributes in scope.files
    assert tuple(check.key for check in _checks_for(scope)) == FULL_CHECKS


@pytest.mark.parametrize(("key", "value"), (("core.autocrlf", "maybe"), ("core.eol", "unknown")))
def test_invalid_checkout_configuration_blocks_selection(tmp_path, workflow_checkout, capsys, key, value):
    git, _ = workflow_checkout
    git("config", key, value)
    with pytest.raises(ValueError, match="unsupported Git line-ending configuration"):
        agent_validation.checkout_eol_settings(tmp_path)
    assert agent_check.main(["--dry-run"]) == 2
    assert "ERROR changed-file discovery:" in capsys.readouterr().err


@pytest.mark.parametrize("value", ("diff=astextplain", "text eol=crlf"))
def test_global_attributes_must_leave_checkout_conversion_unchanged(
    tmp_path, workflow_checkout, capsys, value,
):
    git, write = workflow_checkout
    config = tmp_path / ".git/external-attributes"
    config.write_text(f"app/module.py {value}\n", encoding="ascii")
    git("config", "core.attributesFile", config.as_posix())
    assert value.split()[-1].split("=")[1].encode() in git("check-attr", "--all", "--", "app/module.py")
    write(WORKFLOW_TESTS[0])
    if value.startswith("diff="):
        assert agent_check.discover_changed_files(tmp_path, managed=True).workflow_only
    else:
        assert agent_check.main(["--dry-run"]) == 2
        assert "global or system Git attributes affect checkout" in capsys.readouterr().err


def test_system_conversion_is_excluded_from_repository_attribute_proof(
    workflow_checkout, monkeypatch, capsys,
):
    git = agent_validation._git
    isolated = []

    def with_system_rule(root, *args, **kwargs):
        if args[0] == "check-attr":
            assert kwargs.get("env") is None
            return git(root, *args, **kwargs) + b"app/module.py\0eol\0crlf\0"
        if args[0] == "-c" and args[2] == "check-attr":
            isolated.append(kwargs["env"]["GIT_ATTR_NOSYSTEM"])
            assert args[1].startswith("core.attributesFile=")
        return git(root, *args, **kwargs)

    # Model an installed system rule without changing the host Git installation.
    monkeypatch.setattr(agent_validation, "_git", with_system_rule)
    assert agent_check.main(["--dry-run"]) == 2
    assert isolated == ["1"]
    assert "global or system Git attributes affect checkout" in capsys.readouterr().err


@pytest.mark.parametrize("output", (None, b"", b"H app/module.py", b"not an index record\0"))
def test_failed_or_ambiguous_checkout_inspection_blocks_selection(
    workflow_checkout, monkeypatch, capsys, output,
):
    git = agent_validation._git

    def inspection(root, *args, **kwargs):
        if args[-5:] == ("ls-files", "--cached", "--stage", "--eol", "-z"):
            if output is None:
                raise subprocess.CalledProcessError(1, args)
            return output
        return git(root, *args, **kwargs)

    monkeypatch.setattr(agent_validation, "_git", inspection)
    assert agent_check.main(["--dry-run"], runner=lambda check: pytest.fail("unexpected check")) == 2
    assert "ERROR changed-file discovery:" in capsys.readouterr().err


@pytest.mark.parametrize("path", (*WORKFLOW_SCRIPTS, *WORKFLOW_TESTS))
def test_each_allowlisted_python_path_selects_fixed_required_command(tmp_path, workflow_checkout, path):
    _, write = workflow_checkout
    write(path)
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.files == (path,)
    checks = _checks_for(discovery)
    assert tuple(check.key for check in checks) == WORKFLOW_CHECKS
    assert agent_check.command_identity(checks[1]) == ["python", "-m", "pytest", "-q", *WORKFLOW_TESTS]
    assert dict(checks[1].environment)["PYTEST_ADDOPTS"] == ""


def test_workflow_only_across_committed_staged_and_unstaged_paths(tmp_path, workflow_checkout):
    git, write = workflow_checkout
    git("switch", "-c", "feature")
    write("scripts/agent_task.py")
    git("commit", "-am", "workflow commit")
    write("tests/test_agent_task.py")
    git("add", "tests/test_agent_task.py")
    write(PLAYBOOK)
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.files == (PLAYBOOK, "scripts/agent_task.py", "tests/test_agent_task.py")
    assert tuple(check.key for check in _checks_for(discovery)) == WORKFLOW_CHECKS


def test_staged_runtime_change_reverted_only_in_worktree_still_requires_full(tmp_path, workflow_checkout):
    git, write = workflow_checkout
    runtime = "app/module.py"
    original = (tmp_path / runtime).read_text(encoding="utf-8")
    write(runtime)
    git("add", runtime)
    write(runtime, original)
    write(WORKFLOW_TESTS[0])
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.files == (runtime, WORKFLOW_TESTS[0])
    assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS


@pytest.mark.parametrize("path", (
    "app/module.py", "scripts/agent_future.py", "tests/test_agent_future.py", "unknown.dat",
    "requirements-dev.txt", "pyproject.toml", ".gitattributes", "conftest.py",
    "pytest.ini", "tests/fixtures/shared.md", ".github/workflows/ci.yml",
))
def test_full_profile_for_other_changes_alone_or_with_workflow(tmp_path, workflow_checkout, path):
    git, write = workflow_checkout
    write(path)
    if path == ".gitattributes":
        git("add", path)
    for prefix in ((), (WORKFLOW_SCRIPTS[0],)):
        for workflow in prefix:
            write(workflow)
        discovery = agent_check.discover_changed_files(tmp_path)
        assert set(discovery.files) == {path, *prefix}
        assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS


@pytest.mark.parametrize("path", (PLAYBOOK, "docs/guide.md", "AGENTS.md", ".agents/skills/new/SKILL.md"))
def test_docs_only_remain_docs_and_only_playbook_can_accompany_workflow(tmp_path, workflow_checkout, path):
    _, write = workflow_checkout
    assert _checks_for(agent_check.discover_changed_files(tmp_path)) == ()
    write(path)
    assert _checks_for(agent_check.discover_changed_files(tmp_path)) == ()
    write(WORKFLOW_SCRIPTS[0])
    checks = _checks_for(agent_check.discover_changed_files(tmp_path))
    assert tuple(check.key for check in checks) == (WORKFLOW_CHECKS if path == PLAYBOOK else FULL_CHECKS)


@pytest.mark.parametrize("operation", ("rename", "edited-rename", "delete", "copy"))
def test_structural_git_deltas_preserve_runtime_path(tmp_path, workflow_checkout, operation):
    git, write = workflow_checkout
    source = "app/module.py"
    destination = "docs/copied.md"
    if operation == "delete":
        (tmp_path / source).unlink()
        expected = {source}
    else:
        if operation == "copy":
            write(destination, (tmp_path / source).read_text(encoding="utf-8"))
            git("add", destination)
        else:
            git("mv", source, destination)
            if operation == "edited-rename":
                write(destination)
                git("add", destination)
        expected = {source, destination}
    write(WORKFLOW_TESTS[0])
    discovery = agent_check.discover_changed_files(tmp_path)
    assert set(discovery.files) == {*expected, WORKFLOW_TESTS[0]}
    assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS


def test_copy_into_allowlist_and_untracked_destination_cannot_hide_runtime(tmp_path, workflow_checkout):
    git, write = workflow_checkout
    source = "app/module.py"
    destination = "scripts/agent_evidence_io.py"
    # Model a base predating the allowlisted filename.
    git("rm", destination)
    git("commit", "-m", "base without destination")
    write(destination, (tmp_path / source).read_text(encoding="utf-8"))
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.files == (destination,)
    assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS
    git("add", destination)
    discovery = agent_check.discover_changed_files(tmp_path)
    assert discovery.files == (source, destination)
    assert tuple(check.key for check in _checks_for(discovery)) == FULL_CHECKS


@pytest.mark.parametrize("flag", ("--assume-unchanged", "--skip-worktree"))
@pytest.mark.parametrize("visible", (
    (), ("docs/guide.md",), (WORKFLOW_TESTS[0],), ("app/also_visible.py",),
    (WORKFLOW_TESTS[0], "app/also_visible.py"),
))
def test_index_flags_block_discovery_for_any_visible_scope(tmp_path, workflow_checkout, flag, visible):
    git, write = workflow_checkout
    git("update-index", flag, "app/module.py")
    write("app/module.py")
    for path in visible:
        write(path)
    original = git("ls-files", "-v", "--", "app/module.py")
    with pytest.raises(agent_check.DiscoveryError, match="Git index contains hidden"):
        agent_check.discover_changed_files(tmp_path)
    assert git("ls-files", "-v", "--", "app/module.py") == original


@pytest.mark.parametrize("output", (None, b"H app/module.py", b"M app/module.py\0", b"H \0"))
def test_failed_or_incomplete_index_inspection_blocks_checks(
    workflow_checkout, monkeypatch, capsys, output,
):
    real_git = agent_check._git

    def git(root, *args, **kwargs):
        if args == ("ls-files", "--cached", "-v", "-z"):
            if output is None:
                raise agent_check.DiscoveryError("git ls-files failed")
            return output
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(agent_check, "_git", git)
    assert agent_check.main(["--dry-run"], runner=lambda check: pytest.fail("unexpected check")) == 2
    assert "ERROR changed-file discovery:" in capsys.readouterr().err


@pytest.mark.parametrize("output", (None, b"M\0scripts/agent_task.py", b"C100\0scripts/agent_task.py\0", b"X\0p\0"))
def test_failed_or_incomplete_discovery_blocks_check_execution(
    tmp_path, workflow_checkout, monkeypatch, capsys, output,
):
    real_git = agent_check._git

    def git(root, *args, **kwargs):
        if args[0] == "diff" and "--cached" in args:
            if output is None:
                raise agent_check.DiscoveryError("git diff failed")
            return output
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(agent_check, "_git", git)
    assert agent_check.main([], runner=lambda check: pytest.fail("unexpected check")) == 2
    assert "ERROR changed-file discovery:" in capsys.readouterr().err
    assert not (tmp_path / agent_validation.REPORT_PATH).exists()


def test_explicit_changed_files_are_deterministic_and_skip_git(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_check,
        "_git",
        lambda *args, **kwargs: pytest.fail("Git should not run for explicit changed files"),
    )

    discovery = agent_check.discover_changed_files(
        tmp_path,
        supplied_files=("tests\\test_ui.py", "tests/test_ui.py", "app/main.py"),
    )

    assert discovery.files == ("app/main.py", "tests/test_ui.py")
    assert discovery.base == "explicit changed files"


@pytest.mark.parametrize("path", [
    r"C:\SomeUser\private.py", "C:/SomeUser/private.py", r"C:SomeUser\private.py",
    r"\\server\shared\private.py", "/home/SomeUser/private.py", "docs/../private.py",
    "docs//private.py", "docs/./private.py", "docs/report.py:secret", "docs/private.py\n",
])
def test_rejects_absolute_foreign_paths_traversal_and_malformed_components(tmp_path, capsys, path):
    assert agent_check.main(["--changed-file", path]) == 2
    message = capsys.readouterr().err
    assert "ERROR changed-file discovery:" in message
    assert "SomeUser" not in message and "secret" not in message
    assert not (tmp_path / agent_validation.REPORT_PATH).exists()


def test_preserves_normal_native_absolute_and_portable_relative_paths(tmp_path):
    target = tmp_path / "docs" / "guide with spaces.md"
    discovery = agent_check.discover_changed_files(
        tmp_path, supplied_files=(str(target), "./docs/guide with spaces.md", r"docs\guide with spaces.md"),
    )
    assert discovery.files == ("docs/guide with spaces.md",)


def test_native_absolute_aliases_select_the_canonical_python_checks(tmp_path, monkeypatch, capsys):
    relative = "tests/test_agent_task.py"
    if os.name == "nt":
        root = Path(__file__).resolve().parents[1]
        canonical = root / relative
        get_short_name = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short_name.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        assert 0 < get_short_name(str(canonical), buffer, len(buffer)) < len(buffer)
        alias = Path(buffer.value)
        assert alias.samefile(canonical)
        inputs = [(root, canonical), (root, alias)]
    else:
        inputs = []
        for ancestor in ("checkout:parent", "checkout.parent."):
            root = tmp_path / ancestor / "repository"
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            canonical.touch()
            alias = tmp_path / f"alias-{len(inputs)}"
            alias.symlink_to(root, target_is_directory=True)
            inputs.extend(((root, canonical), (root, alias / relative)))

    for root, target in inputs:
        monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", root)
        assert agent_check.discover_changed_files(root, supplied_files=(str(target),)).files == (relative,)
        assert agent_check.main(["--dry-run", "--changed-file", str(target)]) == 0
        output = capsys.readouterr().out
        assert [line.strip().split(":")[0] for line in output.splitlines() if "WOULD RUN" in line] == [
            "WOULD RUN ruff", "WOULD RUN pytest (unit/integration, includes tests/eval)", "WOULD RUN compileall",
        ]
        assert agent_check.main(["--dry-run", "--changed-file", str(root / "tests" / ".." / relative)]) == 2
    outside = tmp_path / "outside.py"
    outside.touch()
    assert agent_check.main(["--dry-run", "--changed-file", str(outside)]) == 2
    assert "outside the repository" in capsys.readouterr().err


def test_native_absolute_case_alias_selects_all_tracked_checks(tmp_path, capsys):
    canonical = tmp_path / "app/main.py"
    canonical.parent.mkdir()
    canonical.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "--", "app/main.py"], cwd=tmp_path, check=True)
    alias = tmp_path / "APP/MAIN.PY"
    if not alias.exists():
        # On case-sensitive filesystems, exercise a real alias to the same file.
        alias.parent.mkdir()
        alias.symlink_to(canonical)
    assert alias.samefile(canonical)
    expected = (
        "ruff", "pytest", "compileall", "playwright-e2e",
        "docker-build", "mcp-dependency", "mcp-stdio", "mcp-http",
    )

    outputs = []
    for path in (canonical, alias):
        files = agent_check.discover_changed_files(tmp_path, supplied_files=(str(path),)).files
        checks = agent_check.select_checks(agent_check.classify_files(files), files)
        assert files == ("app/main.py",)
        assert tuple(check.key for check in checks) == expected
        assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 0
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]
    assert outputs[0].count("WOULD RUN") == len(expected)


def test_native_absolute_exact_matches_check_index_ambiguity(tmp_path, monkeypatch, capsys):
    names = ("app/main.py", "APP/MAIN.PY", "App/Main.py")
    canonical, uppercase, mixed = (tmp_path / name for name in names)
    canonical.parent.mkdir()
    canonical.touch()
    if not uppercase.exists():
        uppercase.parent.mkdir()
        uppercase.touch()
    same_file = canonical.samefile(uppercase)

    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "isolated-index"))

    def git(*args, **kwargs):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, check=True, **kwargs,
        ).stdout.decode("ascii").strip()

    git("init", "--quiet")
    blob = git("hash-object", "-w", names[0])
    entries = "".join(f"100644 {blob}\t{name}\n" for name in names[:2]).encode("ascii")
    git("update-index", "--index-info", input=entries)
    assert set(git("ls-files").splitlines()) == set(names[:2])

    if same_file:
        assert canonical.samefile(mixed)
        for path in (canonical, uppercase, mixed):
            assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 2
            output = capsys.readouterr()
            assert "ambiguous changed-file identity in the Git index" in output.err
            assert not output.out
    else:
        for name, path, count in zip(names[:2], (canonical, uppercase), (8, 3), strict=True):
            assert agent_check.discover_changed_files(tmp_path, supplied_files=(str(path),)).files == (name,)
            assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 0
            assert capsys.readouterr().out.count("WOULD RUN") == count


def test_native_absolute_new_source_path_keeps_python_checks(tmp_path, capsys):
    tracked = tmp_path / "notes.txt"
    tracked.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "--", "notes.txt"], cwd=tmp_path, check=True)
    new = tmp_path / "app/new.py"
    new.parent.mkdir()
    for exists in (False, True):
        if exists:
            # A different directory entry with the same identity is still a new source path.
            os.link(tracked, new)
        files = agent_check.discover_changed_files(tmp_path, supplied_files=(str(new),)).files
        assert files == ("app/new.py",)
        assert agent_check.main(["--dry-run", "--changed-file", str(new)]) == 0
        assert "WOULD RUN pytest" in capsys.readouterr().out


def test_existing_native_absolute_source_fails_closed_when_identity_is_unavailable(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "app/new.py"
    path.parent.mkdir()
    path.touch()
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)

    def unavailable(directory):
        raise PermissionError("directory identity unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(agent_check.os, "listdir", unavailable)
        assert agent_check.main(["--dry-run", "--changed-file", str(path)]) == 2
    output = capsys.readouterr()
    assert "could not establish changed-file identity" in output.err
    assert "Overall: DRY RUN" not in output.out


@pytest.mark.parametrize("suffix", [".", " "])
def test_windows_aliases_cannot_suppress_required_python_checks(tmp_path, capsys, suffix):
    path = "tests/test_agent_task.py"
    assert agent_check.main(["--dry-run", "--changed-file", path]) == 0
    assert "WOULD RUN pytest" in capsys.readouterr().out
    for alias in (path + suffix, str(tmp_path / path) + suffix, f"tests{suffix}/test_agent_task.py"):
        assert agent_check.main(["--dry-run", "--changed-file", alias]) == 2
        output = capsys.readouterr()
        assert "invalid repository-relative changed file" in output.err
        assert "Overall: DRY RUN" not in output.out
    with pytest.raises(agent_check.DiscoveryError, match="invalid repository-relative changed file"):
        agent_check._nul_paths((path + suffix).encode() + b"\0")


def test_real_git_cross_domain_renames_preserve_source_requirements(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    git("init", "--initial-branch", "main")
    (tmp_path / "app" / "ui" / "assets").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "app" / "mcp_server.py").write_text("MCP = True\n", encoding="utf-8")
    (tmp_path / "app" / "ui" / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=Agent Check", "-c", "user.email=agent@example.invalid", "commit", "-m", "base")
    git("switch", "-c", "feature")
    git("mv", "app/mcp_server.py", "docs/mcp_server.py")
    git("mv", "app/ui/assets/app.js", "docs/app.js")
    git("-c", "user.name=Agent Check", "-c", "user.email=agent@example.invalid", "commit", "-m", "renames")

    discovery = agent_check.discover_changed_files(tmp_path, base_ref="main")
    areas = agent_check.classify_files(discovery.files)
    selected = {check.key for check in agent_check.select_checks(areas, discovery.files)}

    assert {
        "app/mcp_server.py",
        "docs/mcp_server.py",
        "app/ui/assets/app.js",
        "docs/app.js",
    } <= set(discovery.files)
    assert {"mcp", "ui"} <= areas
    assert {"docker-build", "mcp-stdio", "mcp-http", "playwright-e2e"} <= selected
    assert agent_check.select_manual_requirements(discovery.files, areas) == ()


def test_check_selection_reuses_full_pytest_for_semantic_evaluation():
    keys = {check.key for check in agent_check.select_checks({"python", "semantic"})}

    assert keys == {"ruff", "pytest", "compileall"}
    ruff_check = next(check for check in agent_check.available_checks() if check.key == "ruff")
    assert ruff_check.command[-3:] == ("app", "tests", "scripts")
    pytest_check = next(check for check in agent_check.available_checks() if check.key == "pytest")
    assert pytest_check.label == "pytest (unit/integration, includes tests/eval)"
    assert pytest_check.command[-1] == "--ignore=tests/e2e"


@pytest.mark.parametrize("installed_version", [None, "3.7.0", "3.8.0"])
def test_parallel_pytest_requires_the_pinned_optional_plugin(monkeypatch, installed_version):
    monkeypatch.setattr(agent_validation, "package_version", lambda package: installed_version)
    probe = agent_check.default_availability_checker()
    serial = next(check for check in agent_check.available_checks() if check.key == "pytest")
    parallel = next(check for check in agent_check.available_checks(parallel_pytest=True) if check.key == "pytest")

    assert probe(serial) is None
    if installed_version == "3.8.0":
        assert probe(parallel) is None
    else:
        assert "python -m pip install pytest-xdist==3.8.0" in probe(parallel)


def test_ui_selection_adds_playwright_e2e_with_chromium_only():
    checks = agent_check.select_checks({"ui"})
    keys = {check.key for check in checks}
    e2e = next(check for check in checks if check.key == "playwright-e2e")

    assert keys == {"ruff", "pytest", "compileall", "playwright-e2e"}
    assert e2e.needs_playwright is True
    assert e2e.needs_chromium is True
    assert "--browser=chromium" in e2e.command
    assert "--tracing=retain-on-failure" in e2e.command


@pytest.mark.parametrize(
    "path",
    sorted(agent_check.UI_BACKEND_PATHS),
)
def test_dashboard_backend_dependencies_select_playwright_e2e(path):
    areas = agent_check.classify_file(path)
    keys = {check.key for check in agent_check.select_checks(areas, [path])}

    assert "ui" in areas
    assert "playwright-e2e" in keys


def test_mcp_selection_adds_existing_container_smokes():
    keys = {check.key for check in agent_check.select_checks({"python", "mcp"})}

    assert keys == {
        "ruff",
        "pytest",
        "compileall",
        "docker-build",
        "mcp-dependency",
        "mcp-stdio",
        "mcp-http",
    }


def test_truenas_only_path_requires_manual_verification(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "ix-dev/community/vaultbridge/app.yaml"],
        runner=lambda check: pytest.fail(f"unexpected automated check: {check.key}"),
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "SKIP / NOT REQUIRED no automated checks selected" in output
    assert "MANUAL / AGENT VERIFICATION REQUIRED TrueNAS deployment/package validation" in output
    assert "Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS" in output
    assert "Overall: PASS\n" not in output


def test_truenas_compose_selects_validation_for_exact_file():
    path = "compose.truenas.yml"
    checks = agent_check.select_checks(agent_check.classify_file(path), [path])
    truenas_check = next(check for check in checks if check.key == "truenas-compose")

    assert truenas_check.command == (
        "docker",
        "compose",
        "-f",
        "compose.truenas.yml",
        "config",
        "--quiet",
        "--no-env-resolution",
    )
    assert truenas_check.needs_docker is True
    assert truenas_check.needs_compose is True


def test_truenas_compose_is_incomplete_when_docker_is_unavailable(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "compose.truenas.yml"],
        runner=lambda check: 0,
        availability=lambda check: "Docker daemon is unavailable" if check.needs_docker else None,
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "REQUIRED BUT UNAVAILABLE docker compose config (compose.truenas.yml)" in output
    assert "Overall: INCOMPLETE" in output


def test_action_openapi_requires_manual_runtime_mapping_verification(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "action_openapi.yaml"],
        runner=lambda check: 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert (
        "MANUAL / AGENT VERIFICATION REQUIRED ChatGPT Action/OpenAPI operation IDs must be checked "
        "against actual FastAPI endpoints"
    ) in output
    assert "Overall: AUTOMATED PASS; MANUAL VERIFICATION REMAINS" in output
    assert "Overall: PASS\n" not in output


@pytest.mark.parametrize("parallel_pytest", [False, True])
def test_dry_run_runs_no_verification_commands(capsys, parallel_pytest):
    def unexpected_runner(check):
        pytest.fail(f"dry-run executed {check.key}")

    exit_code = agent_check.main(
        ["--dry-run", "--changed-file", "app/ui/assets/app.js", *(["--parallel-pytest"] if parallel_pytest else [])],
        runner=unexpected_runner,
        availability=lambda check: pytest.fail(f"dry-run probed {check.key}"),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Detected areas:\n  ui" in output
    assert "WOULD RUN pytest (unit/integration, includes tests/eval)" in output
    full_command = "python -m pytest -q --ignore=tests/e2e"
    expected = full_command
    if parallel_pytest:
        expected += " -n 4 --dist load --max-worker-restart 0 -p scripts.agent_pytest"
    assert [line for line in output.splitlines() if full_command in line] == [
        f"  WOULD RUN pytest (unit/integration, includes tests/eval): {expected}",
    ]
    assert (
        "  WOULD RUN Playwright E2E (Chromium): python -m pytest -q tests/e2e --browser=chromium "
        "--tracing=retain-on-failure --output=test-results/playwright\n"
    ) in output
    assert "SKIP / NOT REQUIRED manual verification" in output
    assert "Overall: DRY RUN" in output


def test_required_docker_unavailable_is_incomplete_and_nonzero(capsys):
    executed: list[str] = []

    exit_code = agent_check.main(
        ["--changed-file", "Dockerfile"],
        runner=lambda check: executed.append(check.key) or 0,
        availability=lambda check: "Docker daemon is unavailable" if check.needs_docker else None,
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert executed == ["ruff", "pytest", "compileall"]
    assert "REQUIRED BUT UNAVAILABLE docker compose config" in output
    assert "REQUIRED BUT UNAVAILABLE docker build" in output
    assert "Overall: INCOMPLETE" in output


def test_failed_required_check_returns_one(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "app/main.py"],
        runner=lambda check: 7 if check.key == "pytest" else 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL pytest (unit/integration, includes tests/eval) (exit 7)" in output
    assert "Overall: FAIL" in output


def test_ui_check_distinguishes_missing_playwright_package(capsys):
    executed: list[str] = []

    exit_code = agent_check.main(
        ["--changed-file", "app/ui/assets/app.js"],
        runner=lambda check: executed.append(check.key) or 0,
        availability=lambda check: (
            agent_check.PLAYWRIGHT_PACKAGE_UNAVAILABLE
            if check.key == "playwright-e2e"
            else None
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "playwright-e2e" not in executed
    assert "REQUIRED BUT UNAVAILABLE Playwright E2E (Chromium)" in output
    assert "Playwright Python package unavailable" in output
    assert "Overall: INCOMPLETE" in output


def test_ui_check_distinguishes_missing_chromium(capsys):
    exit_code = agent_check.main(
        ["--changed-file", "tests/test_ui.py"],
        runner=lambda check: 0,
        availability=lambda check: (
            agent_check.PLAYWRIGHT_CHROMIUM_UNAVAILABLE
            if check.key == "playwright-e2e"
            else None
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 3
    assert "Playwright Chromium browser unavailable" in output
    assert "python -m playwright install chromium" in output


def test_default_availability_checker_detects_missing_playwright_package(monkeypatch):
    monkeypatch.setattr(agent_check, "playwright_package_available", lambda: False)
    check = next(check for check in agent_check.available_checks() if check.key == "playwright-e2e")

    assert agent_check.default_availability_checker()(check) == agent_check.PLAYWRIGHT_PACKAGE_UNAVAILABLE


def test_default_availability_checker_detects_missing_chromium(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_check, "playwright_package_available", lambda: True)
    monkeypatch.setattr(
        agent_check,
        "playwright_chromium_executable",
        lambda: tmp_path / "missing-chromium",
    )
    check = next(check for check in agent_check.available_checks() if check.key == "playwright-e2e")

    assert agent_check.default_availability_checker()(check) == agent_check.PLAYWRIGHT_CHROMIUM_UNAVAILABLE


@pytest.mark.parametrize(("e2e_return_code", "expected_status"), [(0, "PASS"), (7, "FAIL")])
def test_ui_check_reports_e2e_success_or_failure(capsys, e2e_return_code, expected_status):
    exit_code = agent_check.main(
        ["--changed-file", "app/ui/index.html"],
        runner=lambda check: e2e_return_code if check.key == "playwright-e2e" else 0,
        availability=lambda check: None,
    )

    output = capsys.readouterr().out
    assert f"{expected_status} Playwright E2E (Chromium)" in output
    assert exit_code == (0 if e2e_return_code == 0 else 1)


def test_command_start_failure_is_required_but_unavailable():
    check = next(check for check in agent_check.available_checks() if check.key == "ruff")

    results = agent_check.execute_checks(
        [check],
        runner=lambda selected: (_ for _ in ()).throw(OSError("executable unavailable")),
        availability=lambda selected: None,
    )

    result, = results
    assert (result.check, result.status, result.detail) == (
        check, agent_check.UNAVAILABLE, "executable unavailable",
    )
    assert result.launch_attempted is True
    assert result.exit_code is None
    assert agent_check.result_exit_code(results) == 3


def test_wall_clock_adjustment_does_not_change_elapsed_duration():
    check = agent_check.available_checks()[0]
    first = datetime(2026, 11, 22, 12, tzinfo=UTC)
    timestamps = iter((first, first - timedelta(seconds=1)))
    ticks = iter((100, 450))

    result, = agent_check.execute_checks(
        [check],
        runner=lambda selected: 7,
        availability=lambda selected: None,
        clock=agent_validation.Clock(timestamps.__next__, ticks.__next__),
    )

    assert result.started_at == "2026-11-22T12:00:00.000000+00:00"
    assert result.ended_at == "2026-11-22T11:59:59.000000+00:00"
    assert result.elapsed_ns == 350
    assert result.exit_code == 7
    assert agent_check.result_exit_code([result]) == 1
