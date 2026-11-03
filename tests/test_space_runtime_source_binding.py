"""Composed source-root pinning with real SQLite and local deterministic inference."""

import pytest

from app.repositories.semantic import SemanticRepository
from app.repositories.semantic_binding import BINDING_KEYS, IndexBindingUnavailableError
from app.services.knowledge_spaces import ReadScope, SpaceId
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.semantic_search import SemanticSearchService
from app.services.space_runtime import compose_space_runtime
from tests.eval.runner import DeterministicConceptEmbedder
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration
from tests.test_space_runtime_namespace_shutdown import rows


def test_denied_root_cannot_enter_default_index(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path, 3, denied=("space-1",))).start()
    runtime.scheduler.wait(20)
    owner = binding(runtime)._owners.semantic
    denied = binding(runtime, "space-1")._owners
    peer = binding(runtime, "space-2")._owners.semantic
    original = (owner.vault_root, owner.max_note_bytes, owner._sync_configuration_revision)
    before = rows(owner.db_path), rows(peer.db_path)
    (denied.vault.vault_root / "same.md").write_text("DENIED_SECRET notes", encoding="utf-8")
    calls = list(backend.calls)
    try:
        rejected = False
        try:
            owner.reconfigure(vault_root=denied.vault.vault_root, max_note_bytes=42)
        except IndexBindingUnavailableError:
            rejected = True
        if not rejected:
            owner.sync()  # Exercise the exact pre-fix leak, rather than just the missing guard.
        result = runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("default"),)))
        leaked = any("DENIED_SECRET" in item.snippet for item in result.items)
        print(dict(reconfigure_rejected=rejected, denied_content_leaked=leaked))
        assert rejected and not leaked
        assert (owner.vault_root, owner.max_note_bytes, owner._sync_configuration_revision) == original
        assert (rows(owner.db_path), rows(peer.db_path)) == before
        assert backend.calls[len(calls):] == [("notes",)]  # Only the explicit allowed search.
        assert not denied.vault.vault_root.with_name("space-1-data").exists()
        assert owner.sync()["unchanged"] == 1
        assert owner.search("notes", min_score=0)[0].snippet.find("default") >= 0
        assert not any("DENIED_SECRET" in chunk.content for chunk in owner.repository.load_chunks())
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("name", ["default", "space-1"])
@pytest.mark.parametrize("target", ["allowed", "unconfigured"])
@pytest.mark.parametrize("started", [False, True])
def test_bound_reconfigure_rejects_before_work(tmp_path, backend, monkeypatch, name, target, started):
    runtime = compose_space_runtime(configuration(tmp_path))
    if started:
        runtime.start()
        runtime.scheduler.wait(20)
    owner = binding(runtime, name)._owners.semantic
    other = binding(runtime, "space-1" if name == "default" else "default")._owners.vault.vault_root
    if target == "unconfigured":
        other = tmp_path / "ordinary"
        other.mkdir()
        (other / "same.md").write_text("unrelated notes", encoding="utf-8")
    before = vars(owner).copy()
    stored = rows(owner.db_path) if started else None
    activity = len(backend.instances), len(backend.calls)

    def forbidden(*args, **kwargs):
        pytest.fail("work preceded source-root rejection")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(owner.repository, "validate_expected_binding", forbidden)
            patch.setattr(owner.repository, "_connect", forbidden)
            patch.setattr(runtime.resource, "operation", forbidden)
            patch.setattr(type(owner), "_eligible_files", forbidden)
            patch.setattr(type(owner), "_target_files", forbidden)
            with pytest.raises(IndexBindingUnavailableError):
                owner.reconfigure(vault_root=other, max_note_bytes=42)
        assert vars(owner) == before
        assert (len(backend.instances), len(backend.calls)) == activity
        if started:
            assert rows(owner.db_path) == stored
        else:
            assert not owner.db_path.exists()
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("name", ["default", "space-1"])
@pytest.mark.parametrize("operation", ["sync", "targeted", "empty_targeted", "begin", "rebuild",
                                       "initialize", "signature", "embed", "search", "prepare", "scoped"])
def test_execution_boundaries_reject_inconsistent_source(tmp_path, backend, monkeypatch, name, operation):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    owner = binding(runtime, name)._owners.semantic
    original = owner.vault_root
    before = {key: rows(binding(runtime, key)._owners.semantic.db_path) for key in ("default", "space-1")}
    activity = len(backend.instances), len(backend.calls)
    sid = SpaceId(name)

    def forbidden(*args, **kwargs):
        pytest.fail("repository/model/discovery work preceded source-root rejection")

    owner.vault_root = binding(runtime, "space-1" if name == "default" else "default")._owners.vault.vault_root
    actions = {
        "sync": owner.sync, "targeted": lambda: owner.sync_paths(["same.md"]),
        "empty_targeted": lambda: owner.sync_paths([]), "begin": owner.begin_sync,
        "rebuild": owner.rebuild, "initialize": lambda: owner._initialize_state(create_storage=True),
        "signature": lambda: owner.index_signature, "embed": lambda: owner._embed(["notes"]),
        "search": lambda: owner.search("notes"),
        "prepare": lambda: owner.prepare_federation_candidates(
            ("same.md",), space_id=sid, budget=ScopedBudget((sid,)), cancel=CancellationToken()),
        "scoped": lambda: runtime.operations.scoped_semantic_search("notes", ReadScope((sid,))),
    }
    try:
        with monkeypatch.context() as patch:
            patch.setattr(owner.repository, "validate_expected_binding", forbidden)
            patch.setattr(owner.repository, "_connect", forbidden)
            patch.setattr(runtime.resource, "operation", forbidden)
            patch.setattr(type(owner), "_eligible_files", forbidden)
            patch.setattr(type(owner), "_target_files", forbidden)
            with pytest.raises(IndexBindingUnavailableError):
                actions[operation]()
        assert (len(backend.instances), len(backend.calls)) == activity
        assert {key: rows(binding(runtime, key)._owners.semantic.db_path) for key in before} == before
    finally:
        owner.vault_root = original
        runtime.shutdown()


@pytest.mark.parametrize("name", ["default", "space-1"])
def test_same_root_remains_supported(tmp_path, backend, name):
    with compose_space_runtime(configuration(tmp_path)) as runtime:
        runtime.scheduler.wait(20)
        owner = binding(runtime, name)._owners.semantic
        owner.reconfigure(vault_root=owner.vault_root, max_note_bytes=500_000)
        owner.reconfigure(max_note_bytes=400_000)
        assert owner.sync()["unchanged"] == 1
        assert owner.search("notes", min_score=0)
        metadata = dict(rows(owner.db_path)[0])
        if name == "default":
            assert owner.repository._expected_binding is None
            assert not set(BINDING_KEYS) & metadata.keys()
        else:
            assert tuple(metadata[key] for key in BINDING_KEYS) == owner.repository._expected_binding.values


def test_genuine_legacy_owner_can_reconfigure_source(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    (root / "old.md").write_text("old notes", encoding="utf-8")
    owner = SemanticSearchService(vault_root=root, repository=SemanticRepository(tmp_path / "index.sqlite3"),
                                  embedder=DeterministicConceptEmbedder())
    assert owner.sync()["indexed"] == 1
    other = tmp_path / "other"
    other.mkdir()
    (other / "new.md").write_text("new notes", encoding="utf-8")
    owner.reconfigure(vault_root=other, max_note_bytes=500_000)
    assert owner.rebuild()["indexed"] == 1
    assert owner.vault_root == other
    assert {chunk.path for chunk in owner.repository.load_chunks()} == {"new.md"}
    assert {item.path for item in owner.search("notes", min_score=0)} == {"new.md"}
    assert not set(BINDING_KEYS) & dict(rows(owner.db_path)[0]).keys()
