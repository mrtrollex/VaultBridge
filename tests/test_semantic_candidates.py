"""D2 owner-only scoring; synthetic vectors, no model downloads or federation."""

import sqlite3
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from math import sqrt
from threading import Timer
from types import SimpleNamespace

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote
from app.services.knowledge_spaces import SpaceError, SpaceId
from app.services.scoped_budget import BudgetExhausted, CancellationToken, ScopedBudget
from app.services.semantic_search import (
    FASTEMBED_BACKEND_CONTRACT_ID,
    EmbeddingCompatibilityEvidence,
    FastEmbedder,
    SemanticSearchService,
    SemanticSearchUnavailableError,
)

SID = SpaceId("default")
FP = "embedding-v1:" + "0" * 64
EVIDENCE = EmbeddingCompatibilityEvidence(FP, 2, "test-float32-flat-l2/v1")


class Backend:
    def __init__(self):
        self.calls = []
        self.resolutions = 0

    def resolve_embedding_fingerprint(self):
        return FP

    def resolve_embedding_compatibility(self):
        self.resolutions += 1
        return EVIDENCE

    def embed(self, texts):
        self.calls.append(list(texts))
        return [np.array([1, 0], dtype=np.float32) for _ in texts]


def chunk(path, score=0.8, *, index=0, heading=None, content="unrelated"):
    return StoredChunk(path, index, heading, content, np.array(
        [score, sqrt(1 - score**2)], dtype=np.float32,
    ).tobytes(), 2)


def service_fixture(tmp_path, chunks=(), *, state="ready", explicit=False):
    backend = Backend()
    service = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(tmp_path / "index.sqlite3"),
        model_name="test/model", embedder=backend,
        compatibility_evidence=EVIDENCE if explicit else None,
    )
    service.repository.prepare_index(service.index_signature)
    with service.repository.transaction() as session:
        for path in sorted({c.path for c in chunks}):
            session.replace_note(StoredNote(path, 0, 1, "hash", "now"),
                                 [c for c in chunks if c.path == path])
    if state is None:
        with closing(sqlite3.connect(service.db_path)) as db:
            db.execute("DELETE FROM meta WHERE key='index_state'")
            db.commit()
    else:
        service.repository.set_metadata("index_state", state)
    return service, backend


def prepare(service, paths=(), *, budget=None, cancel=None, **kwargs):
    budget = budget or ScopedBudget((SID,))
    cancel = cancel or CancellationToken()
    return service.prepare_federation_candidates(
        tuple(paths), space_id=SID, budget=budget, cancel=cancel, **kwargs,
    )


def score(service, prepared, text="query", *, limit=20):
    return service.search_federation_candidates(
        text, space_id=SID, budget=prepared.budget, cancel=prepared.cancel,
        prepared=prepared, min_score=prepared.descriptor.min_score, limit=limit,
    )


def test_prepare_is_immutable_no_inference_no_writes_and_cached(tmp_path, monkeypatch):
    service, backend = service_fixture(tmp_path, [chunk("a.md")])
    before = service.db_path.read_bytes()
    for name in ("_initialize_state", "is_search_available", "query_basis", "_embed"):
        monkeypatch.setattr(service, name, lambda *a, **k: pytest.fail("legacy state/inference"))
    prepared = prepare(service, ["a.md"])
    assert backend.calls == []
    assert backend.resolutions == 1
    assert prepare(service).descriptor == prepared.descriptor
    assert backend.resolutions == 1
    assert service.db_path.read_bytes() == before
    assert hash(prepared.descriptor) == hash(replace(prepared.descriptor))
    with pytest.raises(FrozenInstanceError):
        prepared.descriptor.dimensions = 3
    with pytest.raises(FrozenInstanceError):
        prepared.owner = None
    used = dict(prepared.budget.used[SID])
    result = score(service, prepared)
    assert result.index_basis == "compatible_ready"
    assert result.candidates[0].path == "a.md"
    assert prepared.budget.used[SID] == used  # D1 payload/rows are never charged twice.
    assert backend.calls == [["query"]]


@pytest.mark.parametrize("field,value", [
    ("model", "other"), ("embedding_fingerprint", "embedding-v1:" + "1" * 64),
    ("index_signature", "other"), ("dimensions", 3), ("backend_contract", "other"),
    ("min_score", 0.29),
])
def test_exact_descriptor_mismatch(tmp_path, field, value):
    service, _ = service_fixture(tmp_path)
    descriptor = prepare(service).descriptor
    other = replace(descriptor, **{field: value})
    assert descriptor != other
    assert len({descriptor, other}) == 2


@pytest.mark.parametrize("kwargs", [
    {"dimensions": 0}, {"dimensions": True}, {"rank_contract": "other"},
    {"semantic_weight": 0.9}, {"lexical_weight": 0.6}, {"relative_floor": 0.7},
    {"min_score": float("nan")}, {"min_score": float("inf")},
])
def test_descriptor_rejects_invalid_contract(tmp_path, kwargs):
    service, _ = service_fixture(tmp_path)
    with pytest.raises(ValueError):
        replace(prepare(service).descriptor, **kwargs)


def test_explicit_backend_evidence_and_no_fabricated_custom_identity(tmp_path):
    service, backend = service_fixture(tmp_path, explicit=True)
    assert prepare(service).descriptor.backend_contract == EVIDENCE.backend_contract
    assert backend.resolutions == 0
    service._federation_evidence = None
    service.embedder = SimpleNamespace(embed=backend.embed, resolve_embedding_fingerprint=lambda: FP)
    with pytest.raises(SemanticSearchUnavailableError):
        prepare(service)
    assert backend.calls == []


def test_fastembed_cold_evidence_uses_artifacts_without_inference(tmp_path, monkeypatch):
    from tests.test_semantic_search_service import artifact_fingerprint

    # Match the production pooled implementation, with declared dimensions.
    artifacts = tmp_path / "model"
    artifact_fingerprint(artifacts)
    pooled = type("PooledEmbedding", (), {
        "__module__": "fastembed.text.pooled_embedding",
        "_model_dir": artifacts,
        "model_description": SimpleNamespace(model_file="model_optimized.onnx", dim=2),
    })()
    backend = FastEmbedder("custom/model", tmp_path / "cache")
    monkeypatch.setattr(backend, "_get_model", lambda: SimpleNamespace(model=pooled))
    monkeypatch.setattr(backend, "embed", lambda *a: pytest.fail("probe inference"))
    evidence = backend.resolve_embedding_compatibility()
    assert evidence.dimensions == 2
    assert evidence.backend_contract == FASTEMBED_BACKEND_CONTRACT_ID
    assert backend.resolve_embedding_compatibility() == evidence


@pytest.mark.parametrize("state,has_chunks,available", [
    ("ready", False, True), ("ready", True, True), (None, False, False),
    (None, True, True), ("indexing", True, False), ("error", True, False),
    ("uninitialized", True, False), ("invalid", True, False),
])
def test_snapshot_basis_restart_and_eligible_empty(tmp_path, state, has_chunks, available):
    service, backend = service_fixture(tmp_path, [chunk("a.md")] if has_chunks else [], state=state)
    if available:
        prepared = prepare(service)  # No eligible rows; legacy availability uses has_chunks.
        assert prepared.snapshot.chunks == ()
        assert score(service, prepared).candidates == ()
    else:
        with pytest.raises(SemanticSearchUnavailableError):
            prepare(service)
    assert backend.calls == ([] if not available else [["query"]])


@pytest.mark.parametrize("state,basis", [
    ("indexing", "compatible_previous_refresh"), ("error", "compatible_previous_error"),
])
def test_previous_basis_requires_owner_completion(tmp_path, state, basis):
    service, _ = service_fixture(tmp_path, [chunk("a.md")])
    assert service.is_search_available()  # Existing ready evidence established by owner.
    service.repository.set_metadata("index_state", state)
    assert prepare(service, ["a.md"]).index_basis == basis


@pytest.mark.parametrize("kind", ["missing", "static", "fingerprint", "explicit", "disabled"])
def test_unavailable_before_embedding(tmp_path, monkeypatch, kind):
    service, backend = service_fixture(tmp_path, [chunk("a.md")])
    if kind == "missing":
        service.repository.set_metadata("index_signature", "")
    elif kind == "static":
        service.chunk_chars += 1
    elif kind == "fingerprint":
        service._federation_evidence = replace(EVIDENCE, embedding_fingerprint="embedding-v1:" + "1" * 64)
    elif kind == "explicit":
        service._resolved_index_signature = "different explicit signature"
    else:
        service._indexing_enabled = False
        monkeypatch.setattr(service.repository, "read_eligible_snapshot", lambda *a, **k: pytest.fail("read"))
    with pytest.raises(SemanticSearchUnavailableError):
        prepare(service, ["a.md"])
    assert backend.calls == []
    if kind in {"missing", "static", "disabled"}:
        assert backend.resolutions == 0


def test_precise_order_threshold_floor_eligibility_and_legacy_parity(tmp_path):
    chunks = [chunk("Z.md", 0.80002), chunk("A.md", 0.80001),
              chunk("low.md", 0.6), chunk("excluded.md", 1.0)]
    service, _ = service_fixture(tmp_path, chunks)
    paths = ["Z.md", "A.md", "low.md"]
    prepared = prepare(service, paths)
    candidates = score(service, prepared).candidates
    assert [c.path for c in candidates] == ["Z.md", "A.md"]
    assert candidates[0].score > candidates[1].score
    assert round(candidates[0].score, 4) == round(candidates[1].score, 4)
    legacy = service.search("query", eligible_paths=frozenset(paths), limit=100)
    for precise, public in zip(candidates, legacy, strict=True):
        assert (precise.path, precise.heading, precise.snippet) == (public.path, public.heading, public.snippet)
        assert tuple(round(getattr(precise, f), 4) for f in ("score", "semantic_score", "lexical_score")) == (
            public.score, public.semantic_score, public.lexical_score,
        )
    threshold = prepare(service, paths, min_score=0.800015)
    assert [c.path for c in score(service, threshold).candidates] == ["Z.md"]
    # A weak eligible universe retains its own local winner.
    assert score(service, prepare(service, ["low.md"])).candidates[0].path == "low.md"


def test_best_chunk_and_python_canonical_ties(tmp_path):
    paths = ["z.md", "A.md", "a.md", "ss.md", "\u00df.md"]
    chunks = [chunk(p, index=1, heading="later") for p in paths]
    chunks += [chunk(p, index=0, heading="earlier") for p in reversed(paths)]
    service, _ = service_fixture(tmp_path, chunks)
    result = score(service, prepare(service, paths)).candidates
    assert [c.path for c in result] == sorted(paths, key=lambda p: (p.casefold(), p))
    assert all(c.heading == "earlier" for c in result)


def test_hybrid_boundary_and_window(tmp_path):
    chunks = [chunk(f"n{i:03}.md", 0.8, content="query") for i in range(105)]
    service, _ = service_fixture(tmp_path, chunks)
    result = score(service, prepare(service, [c.path for c in chunks]), limit=1)
    assert len(result.candidates) == 5
    assert result.candidates[0].score == service._hybrid_score(float(np.float32(0.8)), 0.25)
    assert len(score(service, prepare(service, [c.path for c in chunks]), limit=100).candidates) == 105


def test_floor_and_inclusive_semantic_threshold_boundaries(tmp_path):
    chunks = [chunk("winner.md", 1), chunk("above.md", 0.78001), chunk("below.md", 0.77999)]
    service, _ = service_fixture(tmp_path, chunks)
    candidates = score(service, prepare(service, [c.path for c in chunks])).candidates
    assert [c.path for c in candidates] == ["winner.md", "above.md"]
    threshold = float(np.float32(0.78001))
    assert score(service, prepare(service, ["above.md"], min_score=threshold)).candidates
    assert not score(service, prepare(service, ["above.md"], min_score=threshold + 1e-8)).candidates


def test_precise_window_cap_500(tmp_path):
    paths = [f"n{i:03}.md" for i in range(501)]
    service, _ = service_fixture(tmp_path, [chunk(p) for p in paths])
    result = score(service, prepare(service, paths), limit=100)
    assert len(result.candidates) == 500
    assert result.candidates[-1].path == "n499.md"


def test_direct_interface_and_invalid_arguments_before_read(tmp_path, monkeypatch):
    service, backend = service_fixture(tmp_path, [chunk("a.md")])
    budget, cancel = ScopedBudget((SID,)), CancellationToken()
    result = service.search_federation_candidates(
        "query", ("a.md",), space_id=SID, budget=budget, cancel=cancel,
    )
    assert result.candidates[0].path == "a.md"
    with pytest.raises(FrozenInstanceError):
        result.candidates[0].score = 0
    backend.calls.clear()
    monkeypatch.setattr(service.repository, "read_eligible_snapshot", lambda *a, **k: pytest.fail("read"))
    for kwargs in ({"text": ""}, {"text": "x" * 4097}, {"text": "\u00e9" * 2049},
                   {"limit": 0}, {"limit": 101}, {"limit": True}, {"min_score": float("nan")}):
        args = {"text": "query", "space_id": SID, "budget": budget, "cancel": cancel} | kwargs
        with pytest.raises(ValueError):
            service.search_federation_candidates(**args)
    assert backend.calls == []


@pytest.mark.parametrize("lock_name", ["_federation_evidence_lock", "_embed_lock"])
def test_cancel_while_waiting_for_backend_lock(tmp_path, lock_name):
    service, _ = service_fixture(tmp_path)
    cancel = CancellationToken()
    prepared = prepare(service, cancel=cancel) if lock_name == "_embed_lock" else None
    lock = getattr(service, lock_name)
    lock.acquire()
    timer = Timer(0.01, cancel.cancel)
    timer.start()
    try:
        with pytest.raises(SpaceError, match="operation_cancelled"):
            if prepared is None:
                prepare(service, cancel=cancel)
            else:
                score(service, prepared)
    finally:
        timer.join()
        lock.release()


def test_known_backend_failure_invalidates_cached_evidence(tmp_path):
    service, _ = service_fixture(tmp_path, explicit=True)
    prepared = prepare(service)
    service._invalidate_dynamic_compatibility_cache()
    assert service._federation_evidence is None
    service._fingerprint_resolution_failed = True
    with pytest.raises(SemanticSearchUnavailableError):
        prepare(service)
    with pytest.raises(SemanticSearchUnavailableError):
        score(service, prepared)


def test_nonfinite_rank_signals_fail_whole_contribution(tmp_path, monkeypatch):
    service, _ = service_fixture(tmp_path, [chunk("a.md"), chunk("z.md")])
    prepared = prepare(service, ["a.md", "z.md"])
    original = service._lexical_score
    monkeypatch.setattr(service, "_lexical_score", lambda *a, **k:
                        float("nan") if k["path"] == "z.md" else original(*a, **k))
    with pytest.raises(SemanticSearchUnavailableError):
        score(service, prepared)


def test_replaced_storage_cannot_prepare_or_embed(tmp_path, monkeypatch):
    from app.repositories import semantic_reads

    service, backend = service_fixture(tmp_path, [chunk("a.md")])
    original = semantic_reads._storage_identity
    calls = 0
    def identity(*a, **k):
        nonlocal calls
        calls += 1
        directories, file = original(*a, **k)
        return directories, file if calls == 1 else (file[0], file[1] + 1)
    monkeypatch.setattr(semantic_reads, "_storage_identity", identity)
    with pytest.raises(SemanticSearchUnavailableError):
        prepare(service, ["a.md"])
    assert backend.calls == []
    assert backend.resolutions == 0


@pytest.mark.parametrize("resource", ["semantic_rows", "semantic_bytes", "sqlite_steps", "probes"])
def test_budget_failure_has_no_inference_or_prefix(tmp_path, resource):
    service, backend = service_fixture(tmp_path, [chunk("a.md"), chunk("b.md")])
    with pytest.raises(BudgetExhausted):
        prepare(service, ["a.md", "b.md"], budget=ScopedBudget((SID,), limits={resource: (0, 0)}))
    assert backend.calls == []
    # Transaction/descriptors cleaned up; the next bounded request works.
    assert len(score(service, prepare(service, ["a.md", "b.md"])).candidates) == 2


@pytest.mark.parametrize("phase", ["prepare", "evidence", "embed", "score"])
def test_cancellation_has_no_prefix(tmp_path, monkeypatch, phase):
    service, backend = service_fixture(tmp_path, [chunk("a.md"), chunk("b.md")])
    token = CancellationToken()
    if phase == "prepare":
        token.cancel()
    elif phase == "evidence":
        original = backend.resolve_embedding_compatibility
        monkeypatch.setattr(backend, "resolve_embedding_compatibility", lambda: (token.cancel(), original())[1])
    if phase in {"prepare", "evidence"}:
        with pytest.raises(SpaceError, match="operation_cancelled"):
            prepare(service, ["a.md"], cancel=token)
        assert backend.calls == []
        return
    prepared = prepare(service, ["a.md", "b.md"], cancel=token)
    if phase == "embed":
        original = backend.embed
        monkeypatch.setattr(backend, "embed", lambda texts: (token.cancel(), original(texts))[1])
    else:
        original = service._lexical_score
        monkeypatch.setattr(service, "_lexical_score", lambda *a, **k: (token.cancel(), original(*a, **k))[1])
    with pytest.raises(SpaceError, match="operation_cancelled"):
        score(service, prepared)


@pytest.mark.parametrize("vector,dimensions", [
    (b"x", 2), (np.array([1, 0, 0], dtype=np.float32).tobytes(), 3),
    (np.array([float("nan"), 0], dtype=np.float32).tobytes(), 2),
    (np.array([float("inf"), 0], dtype=np.float32).tobytes(), 2),
    (np.zeros(2, dtype=np.float32).tobytes(), 2),
])
def test_malformed_stored_vectors_discard_whole_contribution(tmp_path, vector, dimensions):
    service, _ = service_fixture(tmp_path, [
        chunk("a.md"), replace(chunk("z.md"), embedding=vector, dimensions=dimensions),
    ])
    prepared = prepare(service, ["a.md", "z.md"])
    with pytest.raises(SemanticSearchUnavailableError):
        score(service, prepared)


@pytest.mark.parametrize("output", [[], [[1, 0], [1, 0]], [[1]], [[0, 0]], [[float("nan"), 0]], [[float("inf"), 0]]])
def test_invalid_backend_vectors(tmp_path, monkeypatch, output):
    service, backend = service_fixture(tmp_path)
    prepared = prepare(service)
    monkeypatch.setattr(backend, "embed", lambda _: output)
    with pytest.raises(SemanticSearchUnavailableError):
        score(service, prepared)


@pytest.mark.parametrize("phase", ["evidence", "embed"])
def test_backend_failure_no_lifecycle_writes(tmp_path, monkeypatch, phase):
    service, backend = service_fixture(tmp_path)
    before = service.db_path.read_bytes()
    def fail(*args):
        raise RuntimeError("backend failed")
    if phase == "evidence":
        monkeypatch.setattr(backend, "resolve_embedding_compatibility", fail)
        with pytest.raises(SemanticSearchUnavailableError):
            prepare(service)
    else:
        prepared = prepare(service)
        monkeypatch.setattr(backend, "embed", fail)
        with pytest.raises(SemanticSearchUnavailableError):
            score(service, prepared)
    assert service.db_path.read_bytes() == before


def test_prepared_owner_and_request_association(tmp_path):
    service, _ = service_fixture(tmp_path)
    prepared = prepare(service)
    other = SemanticSearchService(vault_root=tmp_path, repository=service.repository, embedder=Backend())
    for changed in ({"owner": other}, {"space_id": SpaceId("other")},
                    {"budget": ScopedBudget((SID,))}, {"cancel": CancellationToken()}):
        with pytest.raises(ValueError, match="association"):
            service.search_federation_candidates(
                "query", space_id=SID, prepared=replace(prepared, **changed),
                budget=prepared.budget, cancel=prepared.cancel,
            )


def test_wal_snapshot_and_no_reopen_after_preparation(tmp_path, monkeypatch):
    service, _ = service_fixture(tmp_path, [chunk("a.md")])
    with closing(sqlite3.connect(service.db_path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE chunks SET content='query'")
        db.commit()
        prepared = prepare(service, ["a.md"])
        db.execute("UPDATE chunks SET embedding=x'00'")
        db.commit()
        monkeypatch.setattr(service.repository, "read_eligible_snapshot", lambda *a, **k: pytest.fail("reopen"))
        assert score(service, prepared).candidates[0].lexical_score > 0


def test_all_13_evaluation_case_ranks_scores_and_ordering(tmp_path):
    from tests.eval.runner import DeterministicConceptEmbedder, build_service, load_cases

    class EvaluationBackend(DeterministicConceptEmbedder):
        def resolve_embedding_compatibility(self):
            return EmbeddingCompatibilityEvidence(FP, len(self._CONCEPTS) + self._LEXICAL_DIMENSIONS + 1,
                                                  "deterministic-concepts-float32-flat-l2/v1")

    service, _ = build_service(tmp_path, embedder=EvaluationBackend())
    paths = tuple(p.relative_to(service.vault_root).as_posix() for p in service.vault_root.rglob("*.md"))
    cases = load_cases()
    assert len(cases) == 13
    # Recorded from HEAD's unmodified legacy search, not the shared D2 helper.
    expected = {
        "postgres-backup-en": [("Database/PostgreSQL.md", 0.5817, 0.8139, 0.25)],
        "postgres-replication-en": [("Database/PostgreSQL.md", 0.3926, 0.5474, 0.1714)],
        "truenas-storage-en": [("Storage/TrueNAS.md", 0.488, 0.6604, 0.2417)],
        "docker-deployment-en": [("Deployment/Docker.md", 0.5472, 0.7115, 0.3125)],
        "fastapi-auth-en": [("Web/FastAPI.md", 0.4948, 0.672, 0.2417)],
        "oracle-apex-rest-en": [("Oracle/APEX.md", 0.5602, 0.7124, 0.3429)],
        "hierarchy-database-recovery": [
            ("Runbooks/Recovery Alpha.md", 0.4656, 0.6633, 0.1833),
            ("Runbooks/Recovery Beta.md", 0.3812, 0.5197, 0.1833),
            ("Database/PostgreSQL.md", 0.3707, 0.6108, 0.0278),
        ],
        "hierarchy-storage-recovery": [
            ("Runbooks/Recovery Beta.md", 0.4472, 0.6319, 0.1833),
            ("Runbooks/Recovery Alpha.md", 0.3613, 0.4858, 0.1833),
        ],
        "postgres-obnova-sk": [
            ("Database/PostgreSQL.md", 0.5129, 0.7057, 0.2375),
            ("Runbooks/Recovery Alpha.md", 0.4138, 0.6509, 0.075),
        ],
        "nas-obnova-sk": [
            ("Storage/TrueNAS.md", 0.3713, 0.6032, 0.04),
            ("Runbooks/Recovery Beta.md", 0.3572, 0.6072, 0.0),
            ("Media/Jellyfin.md", 0.3045, 0.4896, 0.04),
        ],
        "oracle-rest-sk": [("Oracle/APEX.md", 0.5374, 0.6935, 0.3143)],
        "fastapi-token-sk": [("Web/FastAPI.md", 0.5392, 0.663, 0.3625)],
        "english-query-slovak-storage": [
            ("Storage/TrueNAS.md", 0.3987, 0.6528, 0.0357),
            ("Runbooks/Recovery Beta.md", 0.3286, 0.5386, 0.0286),
        ],
    }
    for case in cases:
        precise = score(service, prepare(service, paths), case.query, limit=5).candidates[:5]
        legacy = service.search(case.query, limit=5)
        assert [(r.path, r.score, r.semantic_score, r.lexical_score) for r in legacy] == expected[case.case_id]
        assert [c.path for c in precise] == [r.path for r in legacy], case.case_id
        assert precise[0].path == case.expected.path, case.case_id
        for rank, (candidate, result) in enumerate(zip(precise, legacy, strict=True), 1):
            assert candidate.heading == result.heading, (case.case_id, rank)
            for field in ("score", "semantic_score", "lexical_score"):
                assert round(getattr(candidate, field), 4) == getattr(result, field), (case.case_id, rank, field)
