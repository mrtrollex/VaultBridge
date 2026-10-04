from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Literal

from app.services.semantic_search import (
    SemanticResult,
    SemanticSearchService,
    SemanticSearchUnavailableError,
)
from app.services.vault import BoundedMarkdownPathFact, NoteReadResult, VaultService

DUPLICATE_CANDIDATE_OVERFETCH_FACTOR = 3
DUPLICATE_CANDIDATE_LIMIT = 50


@dataclass(frozen=True)
class DuplicateCandidate:
    path: str
    title: str
    match_type: Literal["exact_title", "semantic"]
    score: float | None
    semantic_score: float | None
    lexical_score: float | None
    snippet: str | None
    heading: str | None


@dataclass(frozen=True, slots=True)
class DuplicateBatchResult:
    by_source: tuple[tuple[str, tuple[DuplicateCandidate, ...]], ...]
    raced_paths: tuple[str, ...] = ()
    source_unavailable: bool = False
    candidate_unavailable: bool = False
    semantic_unavailable: bool = False

    @property
    def state(self) -> Literal["complete", "partial"]:
        return (
            "partial" if self.source_unavailable or self.candidate_unavailable
            or self.semantic_unavailable else "complete"
        )


def normalize_note_title(title: str) -> str:
    """Normalize a note title conservatively for exact-equivalence matching."""
    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.strip().split()).casefold()


class DuplicateCandidateService:
    """Compose live title evidence with verified existing semantic search results."""

    def __init__(
        self,
        *,
        vault_service: VaultService,
        semantic_search_service: SemanticSearchService,
    ) -> None:
        self.vault_service = vault_service
        self.semantic_search_service = semantic_search_service

    def find_bounded_batch(
        self,
        *,
        universe: tuple[str, ...],
        sources: tuple[NoteReadResult, ...],
        semantic_candidates: bool = False,
        path_facts: dict[str, BoundedMarkdownPathFact] | None = None,
    ) -> DuplicateBatchResult:
        """Use one verified filename lookup for a bounded set of already-read sources."""
        def order(path: str) -> tuple[str, str]:
            return path.casefold(), path
        if (
            len(universe) > 10_000 or len(set(universe)) != len(universe)
            or tuple(sorted(universe, key=order)) != universe
            or len(sources) > 20
            or tuple(sorted((source.path for source in sources), key=order))
            != tuple(source.path for source in sources)
            or len({source.path for source in sources}) != len(sources)
            or any(source.path not in universe for source in sources)
            or (path_facts is not None and set(path_facts) != set(universe))
        ):
            raise ValueError("invalid bounded duplicate batch")
        def still_verified(path: str) -> bool:
            if path_facts is not None:
                return self.vault_service.verify_bounded_markdown_path(path_facts[path])
            return self.vault_service.verify_existing_markdown_path(
                path, exact_spelling=True,
            ) == path

        lookup: dict[str, list[str]] = {}
        for path in universe:
            lookup.setdefault(normalize_note_title(PurePosixPath(path).stem), []).append(path)

        source_unavailable = False
        candidate_unavailable = False
        raced_paths: set[str] = set()
        rows: list[tuple[str, tuple[DuplicateCandidate, ...]]] = []
        for source in sources:
            if not still_verified(source.path):
                source_unavailable = True
                raced_paths.add(source.path)
                continue
            title = normalize_note_title(PurePosixPath(source.path).stem)
            selected: list[DuplicateCandidate] = []
            for path in lookup.get(title, ()):
                if path == source.path:
                    continue
                if not still_verified(path):
                    candidate_unavailable = True
                    raced_paths.add(path)
                    continue
                selected.append(DuplicateCandidate(
                    path=path,
                    title=PurePosixPath(path).stem,
                    match_type="exact_title",
                    score=None, semantic_score=None, lexical_score=None,
                    snippet=None, heading=None,
                ))
                if len(selected) == 5:
                    break
            rows.append((source.path, tuple(selected)))
        return DuplicateBatchResult(
            by_source=tuple(rows),
            raced_paths=tuple(sorted(raced_paths, key=order)),
            source_unavailable=source_unavailable,
            candidate_unavailable=candidate_unavailable,
            semantic_unavailable=semantic_candidates,
        )

    def find_candidates(
        self,
        *,
        title: str,
        text: str = "",
        folder: str = "",
        limit: int = 5,
        min_score: float = 0.28,
    ) -> list[DuplicateCandidate]:
        canonical_folder = ""
        if folder:
            existing_folder = self.vault_service.existing_relative_path(folder)
            if existing_folder is None:
                return []
            canonical_folder = existing_folder

        requested_title = normalize_note_title(title)
        exact_candidates = [
            DuplicateCandidate(
                path=path,
                title=PurePosixPath(path).stem,
                match_type="exact_title",
                score=None,
                semantic_score=None,
                lexical_score=None,
                snippet=None,
                heading=None,
            )
            for path in self.vault_service.live_markdown_paths(folder=canonical_folder)
            if normalize_note_title(PurePosixPath(path).stem) == requested_title
        ]

        candidate_limit = min(
            DUPLICATE_CANDIDATE_LIMIT,
            limit * DUPLICATE_CANDIDATE_OVERFETCH_FACTOR,
        )
        query = title if not text else f"{title}\n\n{text}"
        try:
            semantic_results = self.semantic_search_service.search(
                query,
                folder=canonical_folder,
                limit=candidate_limit,
                min_score=min_score,
            )
        except SemanticSearchUnavailableError:
            if exact_candidates:
                return exact_candidates[:limit]
            raise

        return self._merge_candidates(
            exact_candidates,
            semantic_results,
            folder=canonical_folder,
            limit=limit,
        )

    def _merge_candidates(
        self,
        exact_candidates: list[DuplicateCandidate],
        semantic_results: list[SemanticResult],
        *,
        folder: str,
        limit: int,
    ) -> list[DuplicateCandidate]:
        exact_by_path = {candidate.path: candidate for candidate in exact_candidates}
        semantic_only: list[DuplicateCandidate] = []
        seen_semantic_paths: set[str] = set()

        for result in semantic_results:
            verified_path = self.vault_service.verify_existing_markdown_path(
                result.path,
                folder=folder,
            )
            if verified_path is None or verified_path in seen_semantic_paths:
                continue
            seen_semantic_paths.add(verified_path)
            semantic_candidate = self._candidate_from_semantic_result(result, verified_path)
            exact_candidate = exact_by_path.get(verified_path)
            if exact_candidate is not None:
                exact_by_path[verified_path] = replace(
                    exact_candidate,
                    score=semantic_candidate.score,
                    semantic_score=semantic_candidate.semantic_score,
                    lexical_score=semantic_candidate.lexical_score,
                    snippet=semantic_candidate.snippet,
                    heading=semantic_candidate.heading,
                )
            else:
                semantic_only.append(semantic_candidate)

        merged = [exact_by_path[candidate.path] for candidate in exact_candidates]
        merged.extend(semantic_only)
        return merged[:limit]

    @staticmethod
    def _candidate_from_semantic_result(
        result: SemanticResult,
        verified_path: str,
    ) -> DuplicateCandidate:
        return DuplicateCandidate(
            path=verified_path,
            title=PurePosixPath(verified_path).stem,
            match_type="semantic",
            score=result.score,
            semantic_score=result.semantic_score,
            lexical_score=result.lexical_score,
            snippet=result.snippet,
            heading=result.heading,
        )
