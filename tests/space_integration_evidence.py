"""Reproducible G parity export; synthetic corpus only, no external model.

Run with ``python -m tests.space_integration_evidence --output PATH``.
Checks every public score/best chunk and precise scoped search/Query result against
an independent legacy index, then exports exact binary64 scores and legacy v1 bytes.
The checked-in reference was generated against the accepted A–F merge baseline.
"""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.capture import CaptureService
from app.services.knowledge_query import KnowledgeQuery
from app.services.knowledge_spaces import ReadScope, SpaceId
from app.services.promotion import PromotionService
from app.services.scoped_budget import CancellationToken, ScopedBudget
from app.services.scoped_reads import ScopedKnowledgeQuery
from app.services.semantic_search import EmbeddingCompatibilityEvidence
from app.services.space_runtime import compose_space_runtime
from app.services.vault import VaultService
from tests.eval.runner import CORPUS_ROOT, DeterministicConceptEmbedder, build_service, load_cases, run_evaluation
from tests.test_promotion import CAPTURE_ID, SOURCE, decision
from tests.test_space_runtime import configuration


class EvidenceBackend(DeterministicConceptEmbedder):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def resolve_embedding_compatibility(self):
        return EmbeddingCompatibilityEvidence(self.resolve_embedding_fingerprint(),
                                              len(self.embed(("probe",))[0]), "concepts/v1")

    def validate_embedding_backend(self):
        pass

    def invalidate_embedding_compatibility_cache(self):
        pass


def score_rows(results, *, scoped=False):
    return [dict(path=r.identity.canonical_relative_path if scoped else r.path,
                 score=float(r.score).hex(), semantic_score=float(r.semantic_score).hex(),
                 lexical_score=float(r.lexical_score).hex(), heading=r.heading, snippet=r.snippet)
            for r in results]


def legacy_bytes(vault, capture_owner, promotion_owner):
    request = dict(content="Original body", state="draft", capture_id=CAPTURE_ID,
                   captured_at="2026-10-03T12:00:00+00:00", title="Title",
                   source="declared<source", tags=["one", "one"])
    assert capture_owner.capture(request).category == "created"
    source = (vault.vault_root / SOURCE).read_bytes()
    assert capture_owner.capture(request).category == "already_applied"
    (vault.vault_root / "Notes").mkdir()
    artifacts = {"capture_hex": source.hex(), "capture_sha256": hashlib.sha256(source).hexdigest()}
    for action in ("create", "append"):
        destination = f"Notes/{action}.md"
        if action == "append":
            (vault.vault_root / destination).write_bytes(b"Existing")
        request = decision(promotion_owner, action=action, destination=destination)
        composed = promotion_owner.decide(request, promotion_owner.review(SOURCE, CAPTURE_ID))
        assert promotion_owner.apply(request).category == ("created" if action == "create" else "appended")
        before = (vault.vault_root / destination).read_bytes()
        assert promotion_owner.apply(request).category == "already_applied"
        assert (vault.vault_root / destination).read_bytes() == before
        assert (vault.vault_root / SOURCE).read_bytes() == source
        artifacts[action] = dict(decision_sha256=composed.decision_sha256,
                                 markdown_hex=composed.markdown.hex(), destination_hex=before.hex(),
                                 destination_sha256=hashlib.sha256(before).hexdigest())
    return artifacts


def export_evidence(root):
    cases = load_cases()
    assert len(cases) == 13
    reference, _ = build_service(root / "reference", embedder=EvidenceBackend())
    selected_id = SpaceId("default")
    eligible = tuple(sorted(p.relative_to(CORPUS_ROOT).as_posix() for p in CORPUS_ROOT.rglob("*.md")))
    try:
        outcomes = run_evaluation(reference, cases)
        public = {o.case.case_id: dict(rank=o.rank, rows=score_rows(o.results)) for o in outcomes}
        assert all(o.rank is not None and o.rank <= o.case.expected.within_top_k for o in outcomes)
        precise = {case.case_id: score_rows(reference.search_federation_candidates(
            case.query, eligible, space_id=selected_id, limit=5,
            budget=ScopedBudget((selected_id,)), cancel=CancellationToken()).candidates[:5]) for case in cases}
    finally:
        reference.close_synchronization()
        reference.repository.close_initializations()

    settings = configuration(root / "runtime").model_copy(update={
        "semantic_model": "vb022/deterministic-concepts", "semantic_chunk_chars": 250, "semantic_chunk_overlap": 0,
    })
    with patch("app.services.semantic_resources.FastEmbedder", EvidenceBackend):
        with compose_space_runtime(settings) as runtime:
            # Startup may see the original fixture; explicitly reconcile after replacing it.
            runtime.scheduler.wait(20)
            for owners in runtime.registry._owners.values():
                (owners.vault.vault_root / "same.md").unlink()
                for source in CORPUS_ROOT.rglob("*.md"):
                    target = owners.vault.vault_root / source.relative_to(CORPUS_ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_text(encoding="utf-8").encode("utf-8"))
                assert owners.scheduler.request_full_sync()
            runtime.scheduler.wait(20)
            for sid, owners in runtime.registry._owners.items():
                actual = run_evaluation(owners.semantic, cases)
                assert {o.case.case_id: dict(rank=o.rank, rows=score_rows(o.results)) for o in actual} == public
                scope = ReadScope((sid,))
                for case in cases:
                    search = runtime.operations.scoped_semantic_search(case.query, scope)
                    query = runtime.operations.scoped_query(ScopedKnowledgeQuery(
                        scope, KnowledgeQuery(semantic_text=case.query, limit=5)))
                    assert score_rows(search.items, scoped=True) == precise[case.case_id]
                    assert score_rows(query.items, scoped=True) == precise[case.case_id]
            # Equal corpora in two selected spaces exercise global precise tie ordering.
            for case in cases:
                merged = runtime.operations.scoped_semantic_search(
                    case.query, ReadScope(tuple(runtime.registry._by_id)))
                assert len(merged.coverage) == 2 and merged.state == "complete"
                expected = [(sid.value, row) for sid in runtime.registry._by_id for row in precise[case.case_id]]
                expected.sort(key=lambda pair: (
                    -float.fromhex(pair[1]["score"]), -float.fromhex(pair[1]["semantic_score"]),
                    -float.fromhex(pair[1]["lexical_score"]), pair[0], pair[1]["path"].casefold(), pair[1]["path"]))
                assert list(zip((r.identity.space_id.value for r in merged.items),
                                score_rows(merged.items, scoped=True), strict=True)) == expected[:5]

    legacy_root = root / "v1-reference"
    legacy_root.mkdir()
    vault = VaultService(vault_root=legacy_root, max_note_bytes=1_000_000)
    legacy = legacy_bytes(vault, CaptureService(vault), PromotionService(vault))
    with compose_space_runtime(configuration(root / "v1-runtime", 1, disabled=("default",))) as runtime:
        owners = runtime.registry._owners[SpaceId("default")]
        assert legacy_bytes(owners.vault, owners.capture, owners.promotion) == legacy
    return dict(format_version=1, semantic_public=public, semantic_precise=precise, legacy_v1=legacy)


def evidence_bytes():
    with TemporaryDirectory(prefix="vb151-g-") as directory:
        payload = export_evidence(Path(directory))
    return (json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    content = evidence_bytes()
    args.output.write_bytes(content)
    print(f"13 semantic cases; v1 capture/create/append parity; sha256={hashlib.sha256(content).hexdigest()}")


if __name__ == "__main__":
    main()
