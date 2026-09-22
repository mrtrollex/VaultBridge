from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType

from app.services.vault import LiveMarkdownPathCandidate, VaultService

_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class Wikilink:
    """One parsed wikilink and its optional verified canonical target."""

    target: str
    heading: str | None = None
    alias: str | None = None
    resolved_path: str | None = None


@dataclass(frozen=True)
class WikilinkResolutionSnapshot:
    """Immutable exact-name lookup derived from one live candidate enumeration."""

    unqualified_paths: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_candidates(
        cls,
        candidates: Sequence[LiveMarkdownPathCandidate],
    ) -> WikilinkResolutionSnapshot:
        grouped: dict[str, list[str]] = defaultdict(list)
        for candidate in candidates:
            grouped[PurePosixPath(candidate.discovered_path).name].append(
                candidate.canonical_path
            )
        return cls(
            unqualified_paths=MappingProxyType(
                {name: tuple(paths) for name, paths in grouped.items()}
            )
        )


class WikilinkResolver:
    """Parse and exactly resolve Obsidian wikilinks against a live vault."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service

    @staticmethod
    def parse(markdown: str) -> tuple[Wikilink, ...]:
        """Return valid wikilinks outside fenced code in source order."""
        links: list[Wikilink] = []
        fence_character: str | None = None
        fence_length = 0

        for line in markdown.splitlines():
            fence_match = _FENCE_PATTERN.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence_character is None:
                    fence_character = marker[0]
                    fence_length = len(marker)
                elif (
                    marker[0] == fence_character
                    and len(marker) >= fence_length
                    and not fence_match.group(2).strip()
                ):
                    fence_character = None
                    fence_length = 0
                continue

            if fence_character is None:
                links.extend(WikilinkResolver._parse_line(line))

        return tuple(links)

    def resolve(self, link: Wikilink) -> Wikilink:
        """Return a new link with a path only for one exact verified target."""
        return self._resolve(link, self.resolution_snapshot())

    def resolution_snapshot(self) -> WikilinkResolutionSnapshot:
        """Build one exact-name lookup from the current live Markdown candidates."""
        return WikilinkResolutionSnapshot.from_candidates(
            self._vault_service.live_markdown_path_candidates()
        )

    def _resolve(
        self,
        link: Wikilink,
        snapshot: WikilinkResolutionSnapshot,
    ) -> Wikilink:
        candidate = self._candidate_path(link.target)
        if candidate is None:
            return replace(link, resolved_path=None)

        normalized = candidate.replace("\\", "/")
        if "/" in normalized:
            verified = self._vault_service.verify_existing_markdown_path(
                candidate,
                exact_spelling=True,
            )
            return replace(link, resolved_path=verified)

        matches = snapshot.unqualified_paths.get(normalized, ())
        resolved_path = matches[0] if len(matches) == 1 else None
        return replace(link, resolved_path=resolved_path)

    def resolve_markdown(
        self,
        markdown: str,
        *,
        snapshot: WikilinkResolutionSnapshot | None = None,
    ) -> tuple[Wikilink, ...]:
        """Parse and resolve a Markdown string without mutating vault state."""
        live_snapshot = self.resolution_snapshot() if snapshot is None else snapshot
        return tuple(self._resolve(link, live_snapshot) for link in self.parse(markdown))

    @staticmethod
    def _parse_line(line: str) -> list[Wikilink]:
        links: list[Wikilink] = []
        cursor = 0
        while True:
            start = line.find("[[", cursor)
            if start < 0:
                return links
            end = line.find("]]", start + 2)
            if end < 0:
                return links

            body = line[start + 2 : end]
            if "[[" not in body:
                parsed = WikilinkResolver._parse_body(body)
                if parsed is not None:
                    links.append(parsed)
            cursor = end + 2

    @staticmethod
    def _parse_body(body: str) -> Wikilink | None:
        if "[" in body or "]" in body or "\x00" in body or body.count("|") > 1:
            return None

        target_with_heading, separator, raw_alias = body.partition("|")
        raw_target, heading_separator, raw_heading = target_with_heading.partition("#")
        target = raw_target.strip()
        heading = raw_heading.strip() if heading_separator else None
        alias = raw_alias.strip() if separator else None

        if not target or (heading_separator and not heading) or (separator and not alias):
            return None
        return Wikilink(target=target, heading=heading, alias=alias)

    @staticmethod
    def _candidate_path(target: str) -> str | None:
        normalized = target.strip().replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(normalized)
        if (
            not normalized
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in posix_path.parts
        ):
            return None

        suffix = posix_path.suffix
        if not suffix:
            return f"{normalized}.md"
        return normalized
