"""Release preparation: executable configuration and fail-closed disposable smoke."""
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.core.config import Settings
from app.main import create_app
from app.mcp_server import create_mcp_server
from scripts import verify_named_image as named
from scripts import verify_release_image as release

ROOT = Path(__file__).resolve().parents[1]


def test_cli_timeout_registers_identity_before_execution(monkeypatch, tmp_path):
    qualification = named.Qualification("unused", tmp_path, "1.5.0-rc.1")
    registered = []

    def run(command, **kwargs):
        if command[1] == "run":
            registered.append(set(qualification.containers))
            raise subprocess.TimeoutExpired(command, 180)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(named.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        qualification.cli("query")
    assert registered and registered[0], "mounted CLI was untracked when Docker timed out"
    assert not qualification.containers


def test_actual_entry_point_metadata(tmp_path):
    settings = Settings(vault_path=tmp_path / "vault", semantic_data_path=tmp_path / "data")
    assert create_app(settings=settings).version == "1.5.0-rc.1"
    assert create_mcp_server(settings=settings).version == "1.5.0-rc.1"
    assert not (tmp_path / "data").exists()


def test_named_configuration_and_explicit_docker_injection(tmp_path):
    qualification = named.Qualification("sha256:" + "a" * 64, tmp_path, "1.5.0-rc.1")
    args = qualification.arguments(writes=False)
    env = dict(value.split("=", 1) for i, value in enumerate(args) if i and args[i - 1] == "--env")
    configuration = json.loads(env["KNOWLEDGE_SPACES_JSON"])
    records = configuration["spaces"]
    assert [row["space_id"] for row in records] == ["default", "research", "denied"]
    assert len({row["root"] for row in records}) == len({row["semantic_data_path"] for row in records}) == 3
    assert env["VAULT_PATH"] == "/qualification/default"
    assert env["SEMANTIC_DATA_PATH"] == "/qualification/default-data"
    assert env["MCP_WRITE_ENABLED"] == "false"
    assert all(row["indexing"] == "disabled" and "semantic_retrieval" not in row["capabilities"] for row in records)
    assert records[-1]["read"] == records[-1]["write"] == "deny"
    assert args[:4] == ["--user", "568:568", "--cap-drop", "ALL"]
    assert f"type=bind,src={tmp_path},dst=/qualification" in args
    # Container POSIX paths are validated inside Linux by the actual image run.
    # The native settings parser must receive native absolute paths on Windows.
    for row in records:
        row["root"] = str(tmp_path / row["space_id"])
        row["semantic_data_path"] = str(tmp_path / (row["space_id"] + "-data"))
    native_env = {**env, "KNOWLEDGE_SPACES_JSON": json.dumps(configuration),
                  "VAULT_PATH": records[0]["root"], "SEMANTIC_DATA_PATH": records[0]["semantic_data_path"]}
    assert Settings.from_env(native_env).vault_path == tmp_path / "default"


def test_generic_compose_requires_explicit_named_delivery():
    service = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["obsidian-api"]
    assert "KNOWLEDGE_SPACES_JSON" not in service["environment"]
    assert "env_file" not in service
    assert service["environment"]["VAULT_PATH"] == "/vault"
    assert service["environment"]["SEMANTIC_DATA_PATH"] == "/vault/.obsidian-chatgpt-data"
    assert service["volumes"][0]["target"] == "/vault"
    doc = (ROOT / "docs/V150_RC_RUNBOOK.md").read_text(encoding="utf-8")
    assert "adding JSON to `.env` alone does not forward it" in doc
    for target in ("/vault/default", "/vault/research", "/data/default", "/data/research"):
        assert f"dst={target}" in doc


def test_complete_vault_and_disabled_data_assertions(tmp_path):
    qualification = named.Qualification("unused", tmp_path, "1.5.0-rc.1")
    for sid, content in named.FIXTURES.items():
        (tmp_path / sid).mkdir()
        (tmp_path / sid / "same.md").write_bytes(content)
    qualification.unchanged()
    (tmp_path / "research" / "unexpected.bin").write_bytes(b"no")
    with pytest.raises(AssertionError, match="file/byte delta"):
        qualification.unchanged()
    (tmp_path / "research" / "unexpected.bin").unlink()
    (tmp_path / "denied-data").mkdir()
    with pytest.raises(AssertionError, match="disabled index"):
        qualification.unchanged()


@pytest.mark.parametrize("operation", ["start", "stdio", "cli"])
def test_no_concurrent_writer_or_reader_processes(tmp_path, operation):
    qualification = named.Qualification("unused", tmp_path, "1.5.0-rc.1")
    qualification.containers.add("existing-writer")
    with pytest.raises(AssertionError, match="overlap"):
        getattr(qualification, operation)()


def test_docker_failure_does_not_echo_arguments_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr(named.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=9))
    with pytest.raises(RuntimeError, match="exit 9") as error:
        named.docker("run", "--env", "API_KEY=never-print")
    assert "never-print" not in str(error.value) + capsys.readouterr().out


def test_source_identity_failure_precedes_mounts_and_execution(monkeypatch):
    args = release.parser().parse_args(["--image", "ghcr.io/mrtrollex/vaultbridge@sha256:" + "a" * 64,
        "--expected-revision", "b" * 40, "--expected-version", "1.5.0-rc.1"])
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        return json.dumps([{"Os": "linux", "Architecture": "amd64", "RepoDigests": [], "Config": {}}])
    monkeypatch.setattr(named, "docker", docker)
    monkeypatch.setattr(named.tempfile, "mkdtemp", lambda **kw: pytest.fail("identity failed before mounts"))
    with pytest.raises(AssertionError, match="RepoDigest"):
        named.orchestrate(args)
    assert len(calls) == 1


def test_local_failure_cleans_owned_resources_without_pass(monkeypatch, capsys):
    args = release.parser().parse_args(["--local-image", "vaultbridge:unit", "--expected-revision", "b" * 40,
                                       "--expected-version", "1.5.0-rc.1"])
    calls = []
    roots = []
    original = named.tempfile.mkdtemp
    def mkdtemp(**kw):
        path = original(**kw)
        roots.append(Path(path))
        return path
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "amd64"}])
        if args[:2] == ("container", "ls"):
            return ""
        return json.dumps(["wrong", "wrong"])
    monkeypatch.setattr(named, "docker", docker)
    monkeypatch.setattr(named.tempfile, "mkdtemp", mkdtemp)
    with pytest.raises(AssertionError, match="metadata mismatch"):
        named.orchestrate(args)
    assert all(not path.exists() for path in roots)
    assert "PASS" not in capsys.readouterr().out
    assert not any(args[0] == "start" for args in calls)


class FakeDocker:
    """Daemon state survives client timeout; only runner-owned names may be touched."""

    def __init__(self, qualification):
        self.qualification = qualification
        self.records = {}
        self.events = []
        self.mode = None
        self.timeout = False
        self.real_rmtree = shutil.rmtree

    def spawn(self, command):
        name = command[command.index("--name") + 1]
        q = self.qualification
        assert name in q.containers, "container identity must be registered before subprocess dispatch"
        assert "--rm" not in command
        assert f"{named.Containers.LABEL}={q.lifecycle.prefix}" in command
        assert name not in self.records
        assert q.root.exists()
        self.events.append(("run", name))
        if self.mode == "creation_failure":
            raise OSError("injected client creation failure")
        identifier = hashlib.sha256(name.encode()).hexdigest()
        record = {"Id": identifier, "Config": {"Labels": {named.Containers.LABEL: q.lifecycle.prefix}},
                  "State": {"Running": self.timeout or self.mode == "timeout" or "-d" in command}}
        self.records[name] = record
        if self.mode == "already_removed":
            self.records.pop(name)
            raise subprocess.TimeoutExpired(command, 180)
        if self.timeout or self.mode == "timeout":
            raise subprocess.TimeoutExpired(command, 180)
        if "-cleanup-" in name:
            # Simulate the explicitly authorized cleanup worker's contents-only deletion.
            for child in q.root.iterdir():
                self.real_rmtree(child) if child.is_dir() else child.unlink()
        return name

    def run(self, command, **kwargs):
        assert kwargs["timeout"] <= 180
        args = command[1:]
        if args[:2] == ["image", "inspect"]:
            output = json.dumps([{"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "amd64"}])
        elif args[0] == "run":
            name = self.spawn(command)
            if "-metadata-" in name:
                output = json.dumps(["1.5.0-rc.1"] * 2)
            else:
                output = self.records[name]["Id"] if "-d" in command else "{}"
        elif args[:2] == ["container", "ls"]:
            assert kwargs["timeout"] == 15
            self.events.append(("inventory", tuple(self.records)))
            if self.mode == "inventory_failure":
                return SimpleNamespace(returncode=9, stdout="", stderr="not authoritative absence")
            output = "\n".join(f"{r['Id']} {name}" for name, r in self.records.items())
        elif args[0] == "inspect":
            if "--format" in args:
                output = "0"
            else:
                output = json.dumps([self.records[args[-1]]])
        elif args[0] in {"stop", "rm"}:
            target = args[-1]
            name = next((n for n, r in self.records.items() if target in {n, r["Id"]}), None)
            assert name is not None, "attempted operation on an unrelated/absent container"
            self.events.append((args[0], name))
            assert self.qualification.root.exists(), "writable mount disappeared before teardown"
            if self.mode == args[0] + "_failure":
                return SimpleNamespace(returncode=9, stdout="", stderr="injected failure")
            if args[0] == "stop":
                self.records[name]["State"]["Running"] = False
            elif self.mode != "still_present":
                self.records.pop(name)
            output = ""
        elif args[0] == "logs":
            output = ""
        else:
            raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    def popen(self, command, **kwargs):
        self.spawn(command)
        rows = [{"id": 1, "result": {"structuredContent": {"result": {
                    "content": named.FIXTURES["research"].decode()}}}},
                {"id": 2, "result": {"contents": [{"text": named.FIXTURES["research"].decode()}]}},
                {"id": 3, "result": {"structuredContent": {"items": [
                    {"identity": {"space_id": sid, "path": "same.md"}} for sid in ("default", "research")]}}},
                {"id": 4, "error": {"code": -32601}}]
        return SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO("\n".join(map(json.dumps, rows)) + "\n"),
                               stderr=io.StringIO(), wait=lambda timeout: 0, returncode=0)


@pytest.fixture
def daemon(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix=named.PREFIX)).resolve()
    (root / "sentinel").write_bytes(b"preserve until confirmed absent")
    q = named.Qualification("unused", root, "1.5.0-rc.1")
    fake = FakeDocker(q)
    monkeypatch.setattr(named.subprocess, "run", fake.run)
    monkeypatch.setattr(named.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(named.smoke, "published_url", lambda name: "http://127.0.0.1:1")
    monkeypatch.setattr(named.smoke, "wait_until_live", lambda *a: None)
    monkeypatch.setattr(q, "unchanged", lambda: None)
    try:
        yield q, fake
    finally:
        # Fake daemon failures intentionally preserve the synthetic fixture; no real containers exist.
        assert root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith(named.PREFIX)
        if root.exists():
            fake.real_rmtree(root)


@pytest.mark.parametrize("path", ["http", "metadata", "cli", "stdio", "cleanup"])
def test_all_mounted_paths_register_before_dispatch_and_confirm_removal(daemon, monkeypatch, path):
    q, fake = daemon
    if path == "http":
        q.start()
        q.stop()
    elif path == "metadata":
        assert json.loads(q.metadata()) == ["1.5.0-rc.1"] * 2
    elif path == "cli":
        assert q.cli("query") == {}
        assert q.cli("query") == {}  # Each invocation has a distinct run-sequence identity.
    elif path == "stdio":
        q.stdio()
    else:
        monkeypatch.setattr(named.shutil, "rmtree", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    q.cleanup()
    names = [value for event, value in fake.events if event == "run"]
    assert len(names) == len(set(names)) and all(f"-{path}-" in name for name in names)
    assert not q.containers and not fake.records and not q.root.exists()
    q.cleanup()  # Idempotent even after the mount has been removed.


def test_timeout_stops_and_removes_surviving_container_before_mount_deletion(daemon, monkeypatch):
    q, fake = daemon
    fake.mode = "timeout"
    with pytest.raises(subprocess.TimeoutExpired):
        q.cli("query")
    assert [event for event, value in fake.events if event in {"stop", "rm"}] == ["stop", "rm"]
    original = named.shutil.rmtree
    def delete(*args, **kwargs):
        assert not fake.records and not q.containers, "deletion preceded confirmed teardown"
        assert fake.events[-1] == ("inventory", ())
        return original(*args, **kwargs)
    monkeypatch.setattr(named.shutil, "rmtree", delete)
    q.cleanup()
    assert not q.root.exists()


@pytest.mark.parametrize("mode", ["stop_failure", "rm_failure", "still_present", "inventory_failure"])
def test_unconfirmed_cleanup_preserves_mount_and_reports_residual(daemon, mode):
    q, fake = daemon
    name, command = q.lifecycle.command("cli", *q.arguments(), q.image)
    fake.spawn(["docker", *command])
    fake.records[name]["State"]["Running"] = True
    fake.mode = mode
    for _ in range(2):
        with pytest.raises(RuntimeError, match="cleanup failed; residual") as error:
            q.cleanup()
        assert name in str(error.value) and str(q.root) in str(error.value)
        assert (q.root / "sentinel").read_bytes() == b"preserve until confirmed absent"
        assert name in q.containers


@pytest.mark.parametrize("mode,exception", [("creation_failure", OSError),
                                           ("already_removed", subprocess.TimeoutExpired)])
def test_absent_container_requires_successful_inventory_before_mount_cleanup(daemon, mode, exception):
    q, fake = daemon
    fake.mode = mode
    with pytest.raises(exception):
        q.metadata()
    assert fake.events[-1] == ("inventory", ())
    assert not q.containers
    q.cleanup()
    assert not q.root.exists()
    assert not any(event in {"stop", "rm"} for event, value in fake.events)


@pytest.mark.parametrize("path", ["start", "stdio"])
def test_failed_client_launch_retains_identity_until_outer_cleanup(daemon, path):
    q, fake = daemon
    fake.mode = "creation_failure"
    with pytest.raises(OSError, match="creation failure"):
        getattr(q, path)()
    assert q.containers and q.root.exists()
    q.cleanup()
    assert fake.events[-1] == ("inventory", ())
    assert not q.root.exists() and not q.containers


def test_name_collision_never_stops_unrelated_container(daemon):
    q, fake = daemon
    name, command = q.lifecycle.command("http", *q.arguments(), q.image)
    fake.spawn(["docker", *command])
    fake.records[name]["Config"]["Labels"] = {}
    q.name = name
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        q.stop()
    with pytest.raises(RuntimeError, match="cleanup failed; residual"):
        q.cleanup()
    assert not any(event in {"stop", "rm"} for event, value in fake.events)
    assert (q.root / "sentinel").exists()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_orchestrated_timeout_cannot_delete_a_live_mount_or_claim_pass(daemon, monkeypatch, capsys, cleanup_fails):
    q, fake = daemon
    fake.timeout = True
    fake.mode = "stop_failure" if cleanup_fails else None
    monkeypatch.setattr(named, "Qualification", lambda *a: q)
    monkeypatch.setattr(named.tempfile, "mkdtemp", lambda **kw: str(q.root))
    args = release.parser().parse_args(["--local-image", "vaultbridge:unit", "--expected-revision", "b" * 40,
                                       "--expected-version", "1.5.0-rc.1"])
    expected = RuntimeError if cleanup_fails else subprocess.TimeoutExpired
    with pytest.raises(expected):
        named.orchestrate(args)
    assert q.root.exists() is cleanup_fails
    assert bool(fake.records) is cleanup_fails
    assert "PASS" not in capsys.readouterr().out
