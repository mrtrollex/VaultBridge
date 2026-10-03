import hashlib
import io
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import cli
from app.core.config import Settings
from app.services.capture import CaptureService
from app.services.frontmatter import FrontmatterParser
from app.services.promotion import PromotionError, PromotionService
from app.services.vault import VaultService

CAPTURE_ID = "12345678-1234-4123-8123-123456789abc"
PROMOTION_ID = "abcdefab-cdef-4abc-8abc-abcdefabcdef"
SOURCE = f"Inbox/Captures/{CAPTURE_ID}.md"


def setup(tmp_path, *, source="declared<source", title="Title", tags=None):
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    capture = CaptureService(vault)
    request = {"content": "Original body", "state": "draft", "capture_id": CAPTURE_ID,
               "captured_at": "2026-10-03T12:00:00+00:00", "title": title,
               "source": source, "tags": ["one", "one"] if tags is None else tags}
    assert capture.capture(request).category == "created"
    return PromotionService(vault), vault


def decision(service, *, action="create", destination="Notes/Target.md", **changes):
    review = service.review(SOURCE, CAPTURE_ID)
    data = {"source_path": SOURCE, "capture_id": CAPTURE_ID,
            "expected_source_sha256": review.source_sha256,
            "approved_content": "Approved body", "action": action,
            "destination": destination, "promotion_id": PROMOTION_ID,
            "approved_at": "2026-10-03T13:00:00+00:00",
            "transfer_fields": ["title", "tags"]}
    if action == "create":
        data["expected_destination"] = "absent"
    else:
        data["expected_destination_sha256"] = service.inspect_destination(destination).sha256
    data.update(changes)
    return data


def test_review_and_create_are_exact_and_source_stays_intact(tmp_path):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    source_before = (tmp_path / SOURCE).read_bytes()
    request = decision(service)
    snapshot = service.review(SOURCE, CAPTURE_ID)
    assert snapshot.body == "Original body"
    assert snapshot.source_sha256 == hashlib.sha256(source_before).hexdigest()
    composed = service.decide(request, snapshot)
    assert composed.approved_at == "2026-10-03T13:00:00Z"
    assert composed.markdown == service.decide(request, snapshot).markdown
    assert composed.markdown.endswith(b"\n---\n\nApproved body")
    assert service.apply(request).category == "created"
    assert (tmp_path / "Notes/Target.md").read_bytes() == composed.markdown
    assert service.apply(request).category == "already_applied"
    assert (tmp_path / SOURCE).read_bytes() == source_before
    metadata = FrontmatterParser.parse(composed.markdown.decode()).metadata
    assert list(metadata) == ["promotion_id", "promotion_decision_sha256", "promotion_approved_at",
                              "promoted_from_capture_id", "promoted_from_capture_path",
                              "promoted_from_sha256", "captured_at", "capture_declared_source",
                              "capture_title", "capture_tags"]
    assert metadata["capture_tags"] == ("one", "one")


def test_append_manifest_markers_and_retry(tmp_path):
    service, _ = setup(tmp_path, source="````<private")
    (tmp_path / "Notes").mkdir()
    target = tmp_path / "Notes/Target.md"
    target.write_bytes(b"---\n{}\n---\n\nExisting")
    request = decision(service, action="append", approved_content="one\ntwo\n")
    composed = service.decide(request, service.review(SOURCE, CAPTURE_ID))
    assert b"\\u003cprivate" in composed.markdown
    assert b"`````json\n" in composed.markdown
    assert composed.markdown.endswith(
        b"one\ntwo\n\n<!-- /vaultbridge-promotion:v1 id=" + PROMOTION_ID.encode() + b" -->\n"
    )
    assert service.apply(request).category == "appended"
    assert target.read_bytes() == b"---\n{}\n---\n\nExisting" + composed.markdown
    assert service.apply(request).category == "already_applied"
    assert target.read_bytes().count(b"vaultbridge-promotion:v1 id=") == 2


@pytest.mark.parametrize("bad", ["capture_type", "created", "capture_state", "source"])
def test_transfer_fields_are_fixed_and_bounded(tmp_path, bad):
    service, _ = setup(tmp_path)
    request = decision(service, transfer_fields=[bad, "tags"])
    with pytest.raises(PromotionError) as error:
        service.decide(request, service.review(SOURCE, CAPTURE_ID))
    assert error.value.category == "invalid_request"


def test_reserved_marker_any_digest_and_changed_source(tmp_path):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    request = decision(service)
    request["approved_content"] = "Text <!-- vaultbridge-promotion:v1 id=" + PROMOTION_ID + " sha256=other -->"
    assert service.apply(request).category == "invalid_request"
    request = decision(service)
    (tmp_path / SOURCE).write_bytes((tmp_path / SOURCE).read_bytes() + b"\nchanged")
    assert service.apply(request).category == "source_changed"
    assert not (tmp_path / "Notes/Target.md").exists()


def test_create_and_append_conflicts(tmp_path):
    service, vault = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    request = decision(service)
    legacy = vault.create_note(title="Target", folder="Notes", content="Approved body", tags=[])
    assert legacy.status == "created"
    assert vault.create_note(title="Target", folder="Notes", content="Approved body", tags=[]).status == "unchanged"
    assert service.apply(request).category == "conflict"
    (tmp_path / "Notes/Target.md").write_bytes(b"Existing")
    append = decision(service, action="append")
    (tmp_path / "Notes/Target.md").write_bytes(b"Changed")
    assert service.apply(append).category == "destination_changed"
    (tmp_path / "Notes/Target.md").unlink()
    assert service.apply(append).category == "destination_missing"


def test_threaded_create_and_append_retries(tmp_path):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    create = decision(service)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(service.apply, [create] * 6))
    assert [result.category for result in results].count("created") == 1
    assert [result.category for result in results].count("already_applied") == 5
    (tmp_path / "Notes/Append.md").write_bytes(b"Old")
    append = decision(service, action="append", destination="Notes/Append.md")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(service.apply, [append] * 6))
    assert [result.category for result in results].count("appended") == 1
    assert [result.category for result in results].count("already_applied") == 5


def test_cli_failure_does_not_echo_private_values(tmp_path):
    service, _ = setup(tmp_path)
    request = decision(service, approved_content="private URL https://example.invalid")
    request["transfer_fields"] = ["secret"]
    output = io.StringIO()
    assert cli.run_promote_apply(Settings(), input_stream=io.BytesIO(json.dumps(request).encode()),
                                 output=output, service=service) == 1
    assert json.loads(output.getvalue()) == {"category": "invalid_request", "committed": False}
    assert "private" not in output.getvalue()


def _process_apply(root, request, barrier, queue):
    barrier.wait(timeout=20)
    vault = VaultService(vault_root=Path(root), max_note_bytes=1_000_000)
    queue.put(PromotionService(vault).apply(request).category)


@pytest.mark.parametrize("action", ["create", "append"])
def test_process_retries_are_coordinated(tmp_path, action):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    if action == "append":
        (tmp_path / "Notes/Target.md").write_bytes(b"Old")
    request = decision(service, action=action)
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(4), context.Queue()
    children = [context.Process(target=_process_apply, args=(str(tmp_path), request, barrier, queue))
                for _ in range(4)]
    for child in children:
        child.start()
    results = [queue.get(timeout=30) for _ in children]
    for child in children:
        child.join(timeout=30)
        assert child.exitcode == 0
    assert results.count("created" if action == "create" else "appended") == 1
    assert results.count("already_applied") == 3


def test_same_id_conflict_partial_marker_and_independent_destination(tmp_path):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    first = decision(service)
    assert service.apply(first).category == "created"
    changed = {**first, "approved_content": "Different"}
    assert service.apply(changed).category == "conflict"
    second = decision(service, destination="Notes/Other.md", approved_content="Different")
    assert service.apply(second).category == "created"
    target = tmp_path / "Notes/Append.md"
    target.write_bytes(b"Old")
    append = decision(service, action="append", destination="Notes/Append.md")
    target.write_bytes(b"Old\n<!-- vaultbridge-promotion:v1 id=" + PROMOTION_ID.encode() + b" sha256=other")
    assert service.apply(append).category == "conflict"


def test_source_invalid_review_and_advisory_never_selects(tmp_path):
    service, _ = setup(tmp_path)
    with pytest.raises(PromotionError) as error:
        service.review("Inbox/Captures/alias.md", CAPTURE_ID)
    assert error.value.category == "invalid_request"
    source = tmp_path / SOURCE
    source.write_bytes(b"\xff")
    with pytest.raises(PromotionError) as error:
        service.review(SOURCE, CAPTURE_ID)
    assert error.value.category == "unsafe_source"


def test_source_change_after_decision_validation_blocks_commit(tmp_path, monkeypatch):
    service, vault = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    request = decision(service)
    original = vault.create_promotion_if_absent

    def change_before_lock(*, path, markdown, check_source):
        source = tmp_path / SOURCE
        source.write_bytes(source.read_bytes() + b"\nnew revision")
        return original(path=path, markdown=markdown, check_source=check_source)

    monkeypatch.setattr(vault, "create_promotion_if_absent", change_before_lock)
    assert service.apply(request).category == "source_changed"
    assert not (tmp_path / "Notes/Target.md").exists()


def test_advisory_evidence_does_not_supply_destination(tmp_path):
    service, vault = setup(tmp_path)

    class Candidates:
        def find_candidates(self, **_kwargs):
            from app.services.duplicate_candidates import DuplicateCandidate

            return [DuplicateCandidate("Inbox/Captures/unsafe.md", "Candidate", "exact_title",
                                       None, None, None, None, None),
                    DuplicateCandidate(SOURCE, "Source", "exact_title",
                                       None, None, None, None, None)]

    service = PromotionService(vault, candidates=Candidates())
    review = service.review(SOURCE, CAPTURE_ID, candidate_limit=2)
    assert [candidate.path for candidate in review.evidence] == [SOURCE]
    assert review.evidence_state == "available"
    assert list(tmp_path.rglob("*.md")) == [tmp_path / SOURCE]


def test_create_lost_response_is_proven_without_repeating_write(tmp_path, monkeypatch):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    request = decision(service)
    original = os.link

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("private destination and content")

    monkeypatch.setattr(os, "link", uncertain)
    result = service.apply(request)
    assert result.category == "already_applied"
    assert result.committed is True
    monkeypatch.setattr(os, "link", original)
    assert service.apply(request).category == "already_applied"


def test_create_link_failure_before_commit_stays_unknown(tmp_path, monkeypatch):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    request = decision(service)

    def uncertain(*_args, **_kwargs):
        raise OSError("private link failure")

    monkeypatch.setattr(os, "link", uncertain)
    result = service.apply(request)
    assert (result.category, result.committed) == ("commit_unknown", False)
    assert not (tmp_path / "Notes/Target.md").exists()


def test_index_failure_keeps_confirmed_markdown(tmp_path):
    service, vault = setup(tmp_path)
    (tmp_path / "Notes").mkdir()

    def unavailable(_path):
        raise OSError("private index failure")

    service = PromotionService(vault, enqueue=unavailable)
    request = decision(service)
    result = service.apply(request)
    assert (result.category, result.committed, result.index_state) == (
        "created", True, "index_unavailable"
    )
    assert service.apply(request).category == "already_applied"


def test_append_partial_write_is_commit_unknown(tmp_path, monkeypatch):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    target = tmp_path / "Notes/Target.md"
    target.write_bytes(b"Old")
    request = decision(service, action="append")
    original = os.fdopen

    class Partial:
        def __init__(self, opened):
            self.opened = opened

        def __enter__(self):
            self.stream = original(self.opened, "ab")
            return self

        def __exit__(self, *_args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def write(self, data):
            self.stream.write(data[:len(data) // 2])
            raise OSError("private content path")

    def partial(opened, mode):
        return Partial(opened) if mode == "ab" else original(opened, mode)

    monkeypatch.setattr(os, "fdopen", partial)
    assert service.apply(request).category == "commit_unknown"
    monkeypatch.setattr(os, "fdopen", original)
    assert service.apply(request).category == "conflict"


def test_append_lost_response_is_proven_without_duplicate(tmp_path, monkeypatch):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    target = tmp_path / "Notes/Target.md"
    target.write_bytes(b"Old")
    request = decision(service, action="append")
    original = os.fsync

    def uncertain(_fd):
        raise OSError("private fsync failure")

    monkeypatch.setattr(os, "fsync", uncertain)
    result = service.apply(request)
    assert (result.category, result.committed) == ("already_applied", True)
    monkeypatch.setattr(os, "fsync", original)
    block = service.decide(request, service.review(SOURCE, CAPTURE_ID)).markdown
    assert target.read_bytes() == b"Old" + block
    assert service.apply(request).category == "already_applied"


@pytest.mark.parametrize("state", ["inbox", "draft"])
def test_append_retry_rejects_destination_that_became_intake(tmp_path, state):
    service, _ = setup(tmp_path)
    (tmp_path / "Notes").mkdir()
    target = tmp_path / "Notes/Target.md"
    target.write_bytes(b"Old")
    request = decision(service, action="append")
    assert service.apply(request).category == "appended"
    prior = target.read_bytes()
    target.write_bytes((f'---\n{{"capture_state": "{state}"}}\n---\n').encode() + prior)
    assert service.apply(request).category == "unsafe_destination"
    assert target.read_bytes().endswith(prior)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink boundary")
def test_posix_symlink_destination_is_unsafe(tmp_path):
    service, _ = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "Notes").symlink_to(outside, target_is_directory=True)
    request = decision(service)
    assert service.apply(request).category == "unsafe_destination"
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory relocation boundary")
@pytest.mark.parametrize("action", ["create", "append"])
def test_posix_relocated_parent_cannot_prove_commit(tmp_path, monkeypatch, action):
    service, _ = setup(tmp_path)
    parent = tmp_path / "Notes"
    parent.mkdir()
    if action == "append":
        (parent / "Target.md").write_bytes(b"Old")
    request = decision(service, action=action)
    if action == "create":
        original = os.link

        def relocate(*args, **kwargs):
            original(*args, **kwargs)
            parent.rename(tmp_path / "Moved")
            parent.mkdir()

        monkeypatch.setattr(os, "link", relocate)
    else:
        original = os.fsync

        def relocate(fd):
            original(fd)
            parent.rename(tmp_path / "Moved")
            parent.mkdir()

        monkeypatch.setattr(os, "fsync", relocate)
    assert service.apply(request).category == "commit_unknown"
    assert not (parent / "Target.md").exists()
    assert (tmp_path / "Moved/Target.md").exists()


def test_cli_rejects_duplicate_and_unknown_keys(tmp_path):
    service, _ = setup(tmp_path)
    for payload in (b'{"source_path":"private","source_path":"other"}',
                    json.dumps({**decision(service), "secret": "private"}).encode()):
        output = io.StringIO()
        assert cli.run_promote_apply(Settings(), input_stream=io.BytesIO(payload),
                                     output=output, service=service) == 1
        assert json.loads(output.getvalue()) == {"category": "invalid_request", "committed": False}
        assert "private" not in output.getvalue()
