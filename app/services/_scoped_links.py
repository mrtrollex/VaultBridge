"""Incremental recognition owned by the relationship dialect layer.

Two independent legacy skip cursors share a source-position driver. No eager
parser, line array, regex search or complete positioned list is used.
"""

from app.services.markdown_links import MarkdownLinkResolver
from app.services.wikilinks import WikilinkResolver

BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


class Cursor:
    def __init__(self, text, budget):
        self.text, self.budget = text, budget

    def at(self, index):
        if index >= len(self.text):
            return ""
        self.budget.probe()
        return self.text[index]

    def seek(self, start, token):
        index = start
        while index < len(self.text):
            char = self.at(index)
            if char in BREAKS:
                return None
            if char == token[0] and all(self.at(index + j) == c for j, c in enumerate(token)):
                return index
            index += 1
        return None

    def wiki(self, start):
        end = self.seek(start + 2, "]]")
        if end is None:
            return None, start + 2, True
        pipe = heading = None
        for index in range(start + 2, end):
            char = self.at(index)
            if char in "[]\x00" or (char == "|" and pipe is not None):
                return None, end + 2, False
            if char == "|":
                pipe = index
            elif char == "#" and heading is None and pipe is None:
                heading = index
        target_end = heading if heading is not None else pipe if pipe is not None else end
        target = self.trim(start + 2, target_end)
        fragment = self.trim(heading + 1, pipe if pipe is not None else end) if heading is not None else None
        alias = self.trim(pipe + 1, end) if pipe is not None else None
        if not target or fragment == "" or alias == "":
            return None, end + 2, False
        return (target, fragment, alias), end + 2, False

    def trim(self, start, end):
        """Inspect normalization boundaries under the same source parse credit."""
        while start < end and self.at(start).isspace():
            start += 1
        while start < end and self.at(end - 1).isspace():
            end -= 1
        return self.text[start:end]

    def note_destination(self, start, end, *, angle):
        """Legacy destination validation, with no native whole-span scans.

        Each examined code point is charged immediately before inspection, so
        validation and normalization have the same cancellation seam as seek().
        Strings are copied only after their spans have been validated.
        """
        fragment = None
        scheme = False
        for index in range(start, end):
            char = self.at(index)
            if char == "\x00" or (not angle and char.isspace()):
                return None
            if char == "#" and fragment is None:
                fragment = index
            if fragment is None:
                if index == start:
                    scheme = "A" <= char <= "Z" or "a" <= char <= "z"
                elif scheme:
                    if char == ":":
                        return None
                    scheme = ("A" <= char <= "Z" or "a" <= char <= "z"
                              or "0" <= char <= "9" or char in "+.-")
        destination_end = fragment if fragment is not None else end
        if destination_end - start < 3:
            return None
        first, second = self.at(start), self.at(start + 1)
        # PurePosixPath absolute and PureWindowsPath drive/UNC checks. A lone
        # rooted backslash has no Windows drive, even when followed by ':'.
        if first == "/" or (first != "\\" and second == ":") or (first == "\\" and second in "\\/"):
            return None
        if any(self.at(destination_end - 3 + offset) != char for offset, char in enumerate(".md")):
            return None
        return self.text[start:destination_end], self.text[fragment + 1:end] if fragment is not None else None

    def markdown(self, start):
        end = start + 1
        while end < len(self.text) and self.at(end) not in "[]\x00" + BREAKS:
            end += 1
        char = self.at(end)
        if not char or char in BREAKS:
            return None, end
        if char == "[":
            return None, end
        if char == "\x00" or self.at(end + 1) != "(":
            return None, end + 1
        destination_start = end + 2
        angle = self.at(destination_start) == "<"
        close = self.seek(destination_start + int(angle), ">" if angle else ")")
        if close is None:
            return None, self.line_end(destination_start)
        if angle and self.at(close + 1) != ")":
            return None, close + 1
        raw_start = destination_start + int(angle)
        parsed = self.note_destination(raw_start, close, angle=angle)
        if parsed is None:
            return None, close + 1 + int(angle)
        return (*parsed, self.text[start + 1:end]), close + 1 + int(angle)

    def line_end(self, start):
        while start < len(self.text) and self.at(start) not in BREAKS:
            start += 1
        return start

    def code_end(self, start):
        run = start + 1
        while self.at(run) == "`":
            run += 1
        length = run - start
        index = run
        while index < len(self.text) and self.at(index) not in BREAKS:
            if self.at(index) != "`":
                index += 1
                continue
            end = index + 1
            while self.at(end) == "`":
                end += 1
            if end - index == length:
                return end
            index = end
        return run


def recognize(content, budget):
    cursor = Cursor(content, budget)
    index = wiki_next = markdown_next = 0
    fence = None
    fence_length = 0
    line_start = True
    wiki_line_missing = False
    while index < len(content):
        budget.cancel.check()
        if not budget.available:
            return
        char = cursor.at(index)
        if line_start:
            wiki_line_missing = False
            probe = index
            while probe - index < 3 and cursor.at(probe) in {" ", "\t"}:
                probe += 1
            marker = cursor.at(probe)
            if marker in {"`", "~"}:
                end = probe
                while cursor.at(end) == marker:
                    end += 1
                if end - probe >= 3:
                    tail_end = cursor.line_end(end)
                    if fence is None:
                        fence, fence_length = marker, end - probe
                    elif marker == fence and end - probe >= fence_length and not cursor.trim(end, tail_end):
                        fence = None
                    index = tail_end
                    line_start = False
                    continue
            line_start = False
        if char in BREAKS:
            line_start = True
            index += 1
            continue
        if fence is not None:
            index += 1
            continue
        if index >= wiki_next and not wiki_line_missing and char == "[" and cursor.at(index + 1) == "[":
            spec, wiki_next, wiki_line_missing = WikilinkResolver.recognize_bounded_at(cursor, index)
            if spec is not None:
                yield index, "obsidian_wikilink", spec
                if not budget.available:
                    return
        if index >= markdown_next:
            if char == "`":
                markdown_next = cursor.code_end(index)
            elif char == "[" and (index == 0 or cursor.at(index - 1) != "!"):
                spec, markdown_next = MarkdownLinkResolver.recognize_bounded_at(cursor, index)
                if spec is not None:
                    yield index, "markdown_link", spec
                    if not budget.available:
                        return
        index += 1
