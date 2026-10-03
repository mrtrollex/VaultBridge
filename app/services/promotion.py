"""Explicit, portable promotion of one reviewed capture to one chosen note."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.services.capture import _ID, _TIME
from app.services.frontmatter import FrontmatterParser, FrontmatterSerializationError, serialize_frontmatter
from app.services.semantic_search import (
    SemanticIndexOperationError,
    SemanticSearchUnavailableError,
    SynchronizationCancelledError,
)
from app.services.vault import PromotionWriteError, VaultService

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TRANSFER = ("title", "tags", "capture_type")
_TRANSFER_NAMES = {"title": "capture_title", "tags": "capture_tags", "capture_type": "capture_type"}
_REQUIRED = frozenset({"source_path", "capture_id", "expected_source_sha256", "approved_content",
                       "action", "destination", "promotion_id", "approved_at", "transfer_fields"})
_DESTINATION = frozenset({"expected_destination", "expected_destination_sha256"})


class PromotionError(ValueError):
    def __init__(self, category: str = "invalid_request"):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class PromotionReview:
    source_path: str
    capture_id: str
    capture_state: str
    metadata: Mapping[str, object]
    body: str
    source_sha256: str
    evidence: tuple[object, ...] = ()
    evidence_state: str | None = None


@dataclass(frozen=True)
class DestinationReview:
    destination: str
    markdown: str
    sha256: str


@dataclass(frozen=True)
class PromotionDecision:
    source_path: str
    capture_id: str
    expected_source_sha256: str
    approved_content: str
    action: str
    destination: str
    promotion_id: str
    approved_at: str
    transfer_fields: tuple[str, ...]
    captured_at: str
    transfer: Mapping[str, object]
    capture_declared_source: str | None
    expected_destination_sha256: str | None
    decision_sha256: str
    markdown: bytes


@dataclass(frozen=True)
class PromotionResult:
    category: str
    committed: bool = False
    source_path: str | None = None
    destination: str | None = None
    capture_id: str | None = None
    promotion_id: str | None = None
    index_state: str | None = None


def _json_bytes(value: Mapping[str, object], *, escape_lt: bool = False) -> bytes:
    value_json = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False)
    if escape_lt:
        value_json = value_json.replace("<", "\\u003c")
    return value_json.encode("utf-8")


def _markers(promotion_id: str) -> tuple[bytes, bytes]:
    return (f"<!-- vaultbridge-promotion:v1 id={promotion_id}".encode(),
            f"<!-- /vaultbridge-promotion:v1 id={promotion_id}".encode())


def _utc(value: object) -> str:
    if type(value) is not str or not _TIME.fullmatch(value):
        raise PromotionError()
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PromotionError() from None
    return value[:19] + "Z"


def _body(markdown: str) -> str:
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0] not in ("---\n", "---\r\n"):
        raise PromotionError("unsafe_source")
    offset = len(lines[0])
    for line in lines[1:]:
        offset += len(line)
        if line in ("---\n", "---\r\n", "---"):
            body = markdown[offset:]
            if body.startswith("\r\n"):
                return body[2:]
            if body.startswith("\n"):
                return body[1:]
            return body
    raise PromotionError("unsafe_source")


class PromotionService:
    def __init__(self, vault_service: VaultService, *, enqueue: Callable[[str], bool] | None = None,
                 candidates=None):
        self.vault_service = vault_service
        self.enqueue = enqueue
        self.candidates = candidates

    def _capture(self, source_path: str, capture_id: str) -> PromotionReview:
        if (type(source_path) is not str or type(capture_id) is not str
                or not _ID.fullmatch(capture_id)
                or source_path != f"Inbox/Captures/{capture_id}.md"):
            raise PromotionError()
        try:
            data = self.vault_service.read_promotion_bytes(source_path, source=True)
            markdown = data.decode("utf-8")
        except PromotionWriteError as exc:
            raise PromotionError(exc.category) from None
        parsed = FrontmatterParser.parse(markdown)
        if parsed.state != "valid" or parsed.metadata is None:
            raise PromotionError("unsafe_source")
        metadata = parsed.metadata
        if (metadata.get("capture_id") != capture_id
                or metadata.get("capture_state") not in ("inbox", "draft")
                or type(metadata.get("captured_at")) is not str):
            raise PromotionError("unsafe_source")
        try:
            if metadata["captured_at"] != _utc(metadata["captured_at"]):
                raise PromotionError("unsafe_source")
        except PromotionError:
            raise PromotionError("unsafe_source") from None
        for field, bound in (("title", 256), ("source", 2048), ("capture_type", 64)):
            if field in metadata and (type(metadata[field]) is not str
                                      or len(metadata[field].encode("utf-8")) > bound):
                raise PromotionError("unsafe_source")
        if "tags" in metadata:
            tags = metadata["tags"]
            if (type(tags) is not tuple or len(tags) > 16
                    or any(type(tag) is not str or len(tag.encode("utf-8")) > 1024 for tag in tags)):
                raise PromotionError("unsafe_source")
        return PromotionReview(source_path, capture_id, metadata["capture_state"], metadata,
                               _body(markdown), hashlib.sha256(data).hexdigest())

    def review(self, source_path: str, capture_id: str, *, candidate_limit: int | None = None):
        snapshot = self._capture(source_path, capture_id)
        if candidate_limit is None:
            return snapshot
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 20:
            raise PromotionError()
        if self.candidates is None or "title" not in snapshot.metadata:
            return PromotionReview(**{**snapshot.__dict__, "evidence_state": "unavailable"})
        try:
            candidates = self.candidates.find_candidates(
                title=snapshot.metadata["title"], text=snapshot.body, limit=candidate_limit)
        except (OSError, sqlite3.DatabaseError, SemanticIndexOperationError,
                SemanticSearchUnavailableError):
            return PromotionReview(**{**snapshot.__dict__, "evidence_state": "unavailable"})
        safe = tuple(candidate for candidate in candidates if
                     self.vault_service.verify_existing_markdown_path(
                         candidate.path, exact_spelling=True) == candidate.path)
        return PromotionReview(**{**snapshot.__dict__, "evidence": safe, "evidence_state": "available"})

    def inspect_destination(self, destination: str) -> DestinationReview:
        if type(destination) is not str or destination.startswith("Inbox/Captures/"):
            raise PromotionError("unsafe_destination")
        try:
            data = self.vault_service.read_promotion_bytes(destination)
        except PromotionWriteError as exc:
            raise PromotionError(exc.category) from None
        metadata = FrontmatterParser.parse(data.decode("utf-8"))
        if metadata.state == "valid" and metadata.metadata and metadata.metadata.get("capture_state") in (
            "inbox", "draft"
        ):
            raise PromotionError("unsafe_destination")
        return DestinationReview(destination, data.decode("utf-8"), hashlib.sha256(data).hexdigest())

    def decide(self, request: Mapping[str, object], snapshot: PromotionReview) -> PromotionDecision:
        if (type(request) is not dict or request.keys() - (_REQUIRED | _DESTINATION)
                or not _REQUIRED <= request.keys()):
            raise PromotionError()
        action = request["action"]
        if type(action) is not str or action not in ("create", "append"):
            raise PromotionError()
        if (request["source_path"] != snapshot.source_path or request["capture_id"] != snapshot.capture_id
                or type(request["expected_source_sha256"]) is not str
                or not _HASH.fullmatch(request["expected_source_sha256"])):
            raise PromotionError()
        if request["expected_source_sha256"] != snapshot.source_sha256:
            raise PromotionError("source_changed")
        destination = request["destination"]
        if (type(destination) is not str or destination == snapshot.source_path
                or destination.startswith("Inbox/Captures/") or not destination):
            raise PromotionError("unsafe_destination")
        promotion_id = request["promotion_id"]
        if type(promotion_id) is not str or not _ID.fullmatch(promotion_id):
            raise PromotionError()
        approved_at = _utc(request["approved_at"])
        content = request["approved_content"]
        if type(content) is not str:
            raise PromotionError()
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeError:
            raise PromotionError() from None
        if not content.strip():
            raise PromotionError()
        if len(content_bytes) > 65_536:
            raise PromotionError("size_limit")
        if any(character in content for character in ("\r", "\u0085", "\u2028", "\u2029")):
            raise PromotionError()
        markers = _markers(promotion_id)
        if any(prefix in content_bytes for prefix in markers):
            raise PromotionError()
        fields = request["transfer_fields"]
        if (type(fields) is not list or any(type(field) is not str for field in fields)
                or fields != [field for field in _TRANSFER if field in fields]
                or any(field not in snapshot.metadata for field in fields)):
            raise PromotionError()
        transfer = {_TRANSFER_NAMES[field]: list(snapshot.metadata[field]) if field == "tags"
                    else snapshot.metadata[field] for field in fields}
        expected_sha = None
        if action == "create":
            if request.keys() & _DESTINATION != {"expected_destination"} or request["expected_destination"] != "absent":
                raise PromotionError()
        else:
            expected_sha = request.get("expected_destination_sha256")
            if (request.keys() & _DESTINATION != {"expected_destination_sha256"}
                    or type(expected_sha) is not str or not _HASH.fullmatch(expected_sha)):
                raise PromotionError()
        digest_object = dict(action=action, approved_at=approved_at, approved_content=content,
                             capture_id=snapshot.capture_id, captured_at=snapshot.metadata["captured_at"],
                             destination=destination, expected_source_sha256=snapshot.source_sha256,
                             promotion_id=promotion_id, source_path=snapshot.source_path,
                             transfer_fields=fields, transfer=transfer)
        if "source" in snapshot.metadata:
            digest_object["capture_declared_source"] = snapshot.metadata["source"]
        if action == "create":
            digest_object["expected_destination"] = "absent"
        else:
            digest_object["expected_destination_sha256"] = expected_sha
        digest = hashlib.sha256(_json_bytes(digest_object)).hexdigest()
        if action == "create":
            metadata = {"promotion_id": promotion_id, "promotion_decision_sha256": digest,
                        "promotion_approved_at": approved_at, "promoted_from_capture_id": snapshot.capture_id,
                        "promoted_from_capture_path": snapshot.source_path,
                        "promoted_from_sha256": snapshot.source_sha256,
                        "captured_at": snapshot.metadata["captured_at"]}
            if "source" in snapshot.metadata:
                metadata["capture_declared_source"] = snapshot.metadata["source"]
            metadata.update(transfer)
            try:
                markdown = (serialize_frontmatter(metadata) + "\n" + content).encode("utf-8")
            except FrontmatterSerializationError as exc:
                raise PromotionError("size_limit" if exc.reason.endswith("too_large") else "invalid_request") from None
        else:
            manifest = {"source_path": snapshot.source_path, "capture_id": snapshot.capture_id,
                        "source_sha256": snapshot.source_sha256, "captured_at": snapshot.metadata["captured_at"],
                        "approved_at": approved_at, "transfer": transfer}
            if "source" in snapshot.metadata:
                manifest["capture_declared_source"] = snapshot.metadata["source"]
            manifest_bytes = _json_bytes(manifest, escape_lt=True)
            longest = max((len(run) for run in re.findall(rb"`+", manifest_bytes)), default=0)
            fence = b"`" * max(3, longest + 1)
            markdown = (b"\n\n<!-- vaultbridge-promotion:v1 id=" + promotion_id.encode()
                        + b" sha256=" + digest.encode() + b" -->\n" + fence + b"json\n"
                        + manifest_bytes + b"\n" + fence + b"\n\n" + content_bytes
                        + b"\n<!-- /vaultbridge-promotion:v1 id=" + promotion_id.encode() + b" -->\n")
        if len(markdown) > self.vault_service.max_note_bytes:
            raise PromotionError("size_limit")
        return PromotionDecision(snapshot.source_path, snapshot.capture_id, snapshot.source_sha256,
                                 content, action, destination, promotion_id, approved_at, tuple(fields),
                                 snapshot.metadata["captured_at"], transfer, snapshot.metadata.get("source"),
                                 expected_sha, digest, markdown)

    def apply(self, request: Mapping[str, object]) -> PromotionResult:
        try:
            if type(request) is not dict:
                raise PromotionError()
            source_path, capture_id = request.get("source_path"), request.get("capture_id")
            snapshot = self._capture(source_path, capture_id)
            decision = self.decide(request, snapshot)
            def check_source():
                try:
                    data = self.vault_service.read_promotion_bytes(decision.source_path, source=True)
                except PromotionWriteError:
                    raise PromotionWriteError("source_changed") from None
                if hashlib.sha256(data).hexdigest() != decision.expected_source_sha256:
                    raise PromotionWriteError("source_changed")
                self._capture(decision.source_path, decision.capture_id)

            if decision.action == "create":
                category = self.vault_service.create_promotion_if_absent(
                    path=decision.destination, markdown=decision.markdown, check_source=check_source)
            else:
                # Only a reviewed preimage is acceptable before first append. Retry proof
                # is independently checked by VaultService before the preimage comparison.
                category = self.vault_service.append_promotion_once(
                    path=decision.destination, preimage_sha256=decision.expected_destination_sha256,
                    block=decision.markdown, marker_prefixes=_markers(decision.promotion_id),
                    check_source=check_source, check_destination=self._check_destination)
        except (PromotionError, PromotionWriteError) as exc:
            return PromotionResult(exc.category)
        if category not in ("created", "appended", "already_applied"):
            return PromotionResult(category)
        index_state = "index_unavailable"
        if self.enqueue is not None:
            try:
                if self.enqueue(decision.destination):
                    index_state = "index_pending"
            except sqlite3.ProgrammingError:
                raise
            except (OSError, sqlite3.DatabaseError, SemanticIndexOperationError, SynchronizationCancelledError):
                pass
        return PromotionResult(category, True, decision.source_path, decision.destination,
                               decision.capture_id, decision.promotion_id, index_state)

    @staticmethod
    def _check_destination(existing: bytes) -> None:
        parsed = FrontmatterParser.parse(existing.decode("utf-8"))
        if parsed.state == "valid" and parsed.metadata and parsed.metadata.get("capture_state") in (
            "inbox", "draft"
        ):
            raise PromotionWriteError("unsafe_destination")
