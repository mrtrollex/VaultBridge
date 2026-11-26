from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from scripts import agent_check, agent_evidence, agent_finish, agent_review
from scripts.agent_evidence_io import repository_reader


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    (tmp_path / ".gitignore").write_text(".agent/\nignored.txt\n", encoding="utf-8")
    git("add", ".gitignore")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    monkeypatch.setattr(agent_check, "REPOSITORY_ROOT", tmp_path)
    return tmp_path


def _item(path, **extra):
    return {"path": path, "sha256": hashlib.sha256(b"qualification").hexdigest(), **extra}


def _manifest(root, *artifacts):
    (root / "evidence.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": artifacts}), encoding="utf-8",
    )
    return "evidence.json"


def test_valid_manifest_with_structured_repository_references(repository):
    for name in ("report.md", "matrix.json"):
        (repository / name).write_bytes(b"qualification")
    manifest = _manifest(
        repository, _item("report.md", references=["matrix.json"]), _item("matrix.json"),
    )

    evidence = agent_evidence.validate(repository, manifest)
    assert evidence.artifacts == ("report.md", "matrix.json")
    assert evidence.sha256 == hashlib.sha256((repository / manifest).read_bytes()).hexdigest()
    assert agent_evidence.main([manifest], root=repository) == 0


@pytest.mark.parametrize(
    ("path", "diagnostic"),
    [
        ("missing.txt", "missing required evidence"),
        ("ignored.txt", "Git-ignored"),
        (".agent/raw.txt", "outside .agent"),
        ("../outside.txt", "canonical"),
        ("C:/outside.txt", "repository-relative"),
        ("/outside.txt", "canonical"),
    ],
)
def test_required_evidence_path_failures(repository, path, diagnostic):
    (repository / "ignored.txt").write_bytes(b"qualification")
    (repository / ".agent").mkdir()
    (repository / ".agent/raw.txt").write_bytes(b"qualification")
    manifest = _manifest(repository, _item(path))
    with pytest.raises(agent_evidence.EvidenceError, match=diagnostic):
        agent_evidence.validate(repository, manifest)


@pytest.mark.parametrize(
    ("item", "diagnostic"),
    [
        ({"path": "report.md", "sha256": "xyz"}, "lowercase SHA-256"),
        ({"path": "report.md", "sha256": "0" * 64}, "SHA-256 mismatch"),
        (_item("report.md", references=["unlisted.md"]), "another declared artifact"),
        (_item("report.md", references=["report.md"]), "another declared artifact"),
    ],
)
def test_required_hashes_and_references(repository, item, diagnostic):
    (repository / "report.md").write_bytes(b"qualification")
    manifest = _manifest(repository, item)
    with pytest.raises(agent_evidence.EvidenceError, match=diagnostic):
        agent_evidence.validate(repository, manifest)


def _directory_redirect(alias, outside):
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(alias), str(outside)],
            check=True, capture_output=True,
        )
    else:
        alias.symlink_to(outside, target_is_directory=True)


def test_evidence_cannot_escape_through_directory_link(repository, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-evidence")
    (outside / "raw.txt").write_bytes(b"qualification")
    _directory_redirect(repository / "aliased", outside)
    manifest = _manifest(repository, _item("aliased/raw.txt"))
    with pytest.raises(agent_evidence.EvidenceError, match="repository escape"):
        agent_evidence.validate(repository, manifest)


def test_declared_missing_evidence_blocks_checks_and_removes_stale_packet(repository):
    packet = repository / agent_finish.PACKET_PATH
    packet.parent.mkdir()
    packet.write_text("previous packet", encoding="utf-8")
    assert agent_finish.main(
        ["--task", "Qualify it.", "--evidence-manifest", "missing.json"],
        root=repository,
        runner=lambda *args: pytest.fail("checks ran before evidence preflight"),
    ) == 1
    assert not packet.exists()


@pytest.mark.parametrize("change_after_preflight", [False, True])
def test_finish_rechecks_declared_evidence_before_packet(repository, change_after_preflight):
    artifact = repository / "report.md"
    artifact.write_bytes(b"qualification")
    manifest = _manifest(repository, _item("report.md"))

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            result = agent_check.main(
                command[2:], runner=lambda check: 0, availability=lambda check: None,
            )
            if change_after_preflight:
                artifact.write_bytes(b"replaced")
            return result
        return agent_review.main(command[2:], root=root)

    result = agent_finish.main(
        ["--task", "Qualify it.", "--evidence-manifest", manifest],
        root=repository,
        runner=run,
    )
    packet = repository / agent_finish.PACKET_PATH
    if change_after_preflight:
        assert result != 0
        assert not packet.exists()
    else:
        assert result == 0
        content = packet.read_text(encoding="utf-8")
        assert "Evidence preflight passed." in content
        assert "evidence.json\nreport.md" in content


def test_narrower_individually_valid_manifest_cannot_replace_preflight_manifest(repository, capsys):
    for name in ("report.md", "matrix.json"):
        (repository / name).write_bytes(b"qualification")
    manifest = _manifest(repository, _item("report.md"), _item("matrix.json"))
    original = agent_evidence.validate(repository, manifest)

    def run(command, root):
        if command[1].endswith("agent_check.py"):
            return agent_check.main(
                command[2:], runner=lambda check: 0, availability=lambda check: None,
            )
        # Substitute immediately before the packet process, after the initial
        # finish recheck. The narrowed replacement also validates on its own.
        _manifest(repository, _item("report.md"))
        replacement = agent_evidence.validate(root, manifest)
        assert replacement.artifacts == ("report.md",)
        assert replacement.sha256 != original.sha256
        index = command.index("--evidence-sha256")
        assert command[index + 1] == original.sha256
        return agent_review.main(command[2:], root=root)

    assert agent_finish.main(
        ["--task", "Qualify it.", "--evidence-manifest", manifest], root=repository, runner=run,
    ) != 0
    assert not (repository / agent_finish.PACKET_PATH).exists()
    assert "evidence manifest SHA-256 changed after preflight" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["manifest", "artifact"])
def test_manifest_and_artifact_final_redirects_are_denied(repository, tmp_path_factory, target):
    outside = tmp_path_factory.mktemp("outside")
    (repository / "report.md").write_bytes(b"qualification")
    manifest = _manifest(repository, _item("report.md"))
    filename = manifest if target == "manifest" else "report.md"
    alias = repository / filename
    relocated = outside / filename
    alias.replace(relocated)
    if os.name == "nt":
        _directory_redirect(alias, outside)
    else:
        alias.symlink_to(relocated)
    with pytest.raises(agent_evidence.EvidenceError, match="repository escape"):
        agent_evidence.validate(repository, manifest)


def test_parent_replacement_after_manifest_read_cannot_redirect_artifact(
    repository, tmp_path_factory, monkeypatch,
):
    parent = repository / "artifacts"
    parent.mkdir()
    (parent / "report.md").write_bytes(b"qualification")
    manifest = _manifest(repository, _item("artifacts/report.md"))
    outside = tmp_path_factory.mktemp("outside") / "relocated"
    read = agent_evidence._read_evidence

    def swap_parent(reader, parts, *, manifest=False):
        if not manifest:
            parent.replace(outside)
            _directory_redirect(parent, outside)
        return read(reader, parts, manifest=manifest)

    monkeypatch.setattr(agent_evidence, "_read_evidence", swap_parent)
    with pytest.raises(agent_evidence.EvidenceError, match="repository escape"):
        agent_evidence.validate(repository, manifest)


def test_read_uses_the_verified_handle_through_final_component_replacement(repository, tmp_path_factory):
    target = repository / "report.md"
    target.write_bytes(b"inside")
    outside = tmp_path_factory.mktemp("outside") / "different.md"
    outside.write_bytes(b"outside")

    with repository_reader(repository) as reader, reader(("report.md",)) as stream:
        if os.name == "nt":
            with pytest.raises(PermissionError):
                outside.replace(target)
        else:
            target.unlink()
            target.symlink_to(outside)
        assert stream.read() == b"inside"
