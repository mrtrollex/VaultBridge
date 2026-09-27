from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath, PureWindowsPath

from app.services.vault import VaultService

_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_URI_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class MarkdownLink:
    """One bounded inline Markdown note-link occurrence."""

    destination: str
    fragment: str | None
    label: str
    resolved_path: str | None = None


class MarkdownLinkResolver:
    """Parse and safely resolve source-relative inline Markdown note links."""

    def __init__(self, vault_service: VaultService) -> None:
        self._vault_service = vault_service

    @staticmethod
    def parse(markdown: str) -> tuple[MarkdownLink, ...]:
        """Return bounded inline Markdown note links in source order."""
        links: list[MarkdownLink] = []
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
                links.extend(MarkdownLinkResolver._parse_line(line))

        return tuple(links)

    def resolve(self, link: MarkdownLink, *, source_path: str) -> MarkdownLink:
        """Return a new occurrence with its verified canonical target when live."""
        resolved_path = self._vault_service.verify_source_relative_markdown_path(
            source_path,
            link.destination,
        )
        return replace(link, resolved_path=resolved_path)

    def resolve_markdown(
        self,
        markdown: str,
        *,
        source_path: str,
    ) -> tuple[MarkdownLink, ...]:
        """Parse and resolve source-relative note links without mutating vault state."""
        return tuple(
            self.resolve(link, source_path=source_path) for link in self.parse(markdown)
        )

    @staticmethod
    def _parse_line(line: str) -> list[MarkdownLink]:
        links: list[MarkdownLink] = []
        cursor = 0
        line_length = len(line)

        while cursor < line_length:
            if line[cursor] == "`":
                run_end = cursor + 1
                while run_end < line_length and line[run_end] == "`":
                    run_end += 1
                marker = line[cursor:run_end]
                closing = MarkdownLinkResolver._find_code_span_close(line, marker, run_end)
                cursor = closing + len(marker) if closing is not None else run_end
                continue

            if line[cursor] != "[" or (cursor > 0 and line[cursor - 1] == "!"):
                cursor += 1
                continue

            link, cursor = MarkdownLinkResolver._parse_at(line, cursor)
            if link is not None:
                links.append(link)

        return links

    @staticmethod
    def _find_code_span_close(line: str, marker: str, start: int) -> int | None:
        cursor = start
        while True:
            closing = line.find(marker, cursor)
            if closing < 0:
                return None
            before_is_tick = closing > 0 and line[closing - 1] == "`"
            after = closing + len(marker)
            after_is_tick = after < len(line) and line[after] == "`"
            if not before_is_tick and not after_is_tick:
                return closing
            cursor = closing + 1

    @staticmethod
    def _parse_at(line: str, start: int) -> tuple[MarkdownLink | None, int]:
        label_end = start + 1
        while label_end < len(line) and line[label_end] not in "[]\x00":
            label_end += 1
        if label_end >= len(line):
            return None, len(line)
        if line[label_end] == "[":
            return None, label_end
        if line[label_end] == "\x00":
            return None, label_end + 1
        if label_end + 1 >= len(line) or line[label_end + 1] != "(":
            return None, label_end + 1
        label = line[start + 1 : label_end]

        destination_start = label_end + 2
        if destination_start >= len(line):
            return None, len(line)
        if line[destination_start] == "<":
            angle_end = line.find(">", destination_start + 1)
            if (
                angle_end < 0
                or angle_end + 1 >= len(line)
                or line[angle_end + 1] != ")"
            ):
                return None, len(line) if angle_end < 0 else angle_end + 1
            raw_destination = line[destination_start + 1 : angle_end]
            end = angle_end + 2
        else:
            destination_end = line.find(")", destination_start)
            if destination_end < 0:
                return None, len(line)
            raw_destination = line[destination_start:destination_end]
            if any(character.isspace() for character in raw_destination):
                return None, destination_end + 1
            end = destination_end + 1

        parsed = MarkdownLinkResolver._note_destination(raw_destination)
        if parsed is None:
            return None, end
        destination, fragment = parsed
        return MarkdownLink(destination, fragment, label), end

    @staticmethod
    def _note_destination(raw_destination: str) -> tuple[str, str | None] | None:
        if not raw_destination or "\x00" in raw_destination:
            return None
        destination, separator, fragment = raw_destination.partition("#")
        posix_path = PurePosixPath(destination)
        windows_path = PureWindowsPath(destination)
        if (
            not destination
            or not destination.endswith(".md")
            or _URI_SCHEME_PATTERN.match(destination)
            or destination.startswith("//")
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            return None
        return destination, fragment if separator else None
