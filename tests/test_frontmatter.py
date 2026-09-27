from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from app.services.frontmatter import (
    MAX_FRONTMATTER_BYTES,
    MAX_ITEMS,
    MAX_MAPPING_KEY_BYTES,
    MAX_SCALAR_BYTES,
    FrontmatterDiagnostic,
    FrontmatterParser,
)


def document(payload: str, *, opener_ending: str = "\n", closer: str = "---", closer_ending: str = "\n") -> str:
    return f"---{opener_ending}{payload}{closer}{closer_ending}body"


def assert_invalid(markdown: str, reason: str) -> FrontmatterDiagnostic:
    result = FrontmatterParser.parse(markdown)
    assert result.state == "invalid"
    assert result.metadata is None
    assert result.diagnostic is not None
    assert result.diagnostic.reason == reason
    return result.diagnostic


@pytest.mark.parametrize(
    "markdown",
    [
        "",
        "# Note\n",
        " ---\nkey: value\n---\n",
        "----\nkey: value\n---\n",
        "--- # comment\nkey: value\n---\n",
        "---\rkey: value\r---\r",
    ],
)
def test_near_misses_and_markdown_without_an_exact_opener_are_absent(markdown):
    result = FrontmatterParser.parse(markdown)

    assert result.state == "absent"
    assert result.metadata is None
    assert result.diagnostic is None


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("---\nkey: value\n---\nbody", {"key": "value"}),
        ("\ufeff---\nkey: value\n...\nbody", {"key": "value"}),
        ("---\r\nkey: value\r\n---\r\nbody", {"key": "value"}),
        ("---\nkey: value\n---", {"key": "value"}),
        ("---\n---\nbody", {}),
    ],
)
def test_exact_envelopes_are_valid(markdown, expected):
    result = FrontmatterParser.parse(markdown)

    assert result.state == "valid"
    assert result.metadata == expected
    assert result.diagnostic is None


@pytest.mark.parametrize("markdown", ["---", "---\nkey: value", "\ufeff---\r\nbody"])
def test_exact_opener_without_closer_is_malformed(markdown):
    assert_invalid(markdown, "malformed_envelope")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("- item\n", "non_mapping_root"),
        ("key: [unterminated\n", "invalid_yaml"),
        ("key: first\nkey: second\n", "duplicate_key"),
        ("outer:\n  key: first\n  key: second\n", "duplicate_key"),
        ("first: {}\n--- # second document\nsecond: {}\n", "multiple_documents"),
        ("key: &anchor value\n", "disallowed_yaml_feature"),
        ("key: &anchor value\nother: *anchor\n", "disallowed_yaml_feature"),
        ("key: *missing\n", "disallowed_yaml_feature"),
        ("base: &base {one: 1}\nmerged: {<<: *base}\n", "disallowed_yaml_feature"),
        ("merged: {<<: {one: 1}}\n", "disallowed_yaml_feature"),
        ("key: !!str value\n", "disallowed_yaml_feature"),
        ("key: !custom value\n", "disallowed_yaml_feature"),
        ("? [not, a, string]\n: value\n", "non_string_key"),
        ("1: value\n", "non_string_key"),
        ("number: .inf\n", "unsupported_value"),
        ("number: -.Inf\n", "unsupported_value"),
        ("number: .NaN\n", "unsupported_value"),
    ],
)
def test_invalid_yaml_profiles_return_stable_reason(payload, reason):
    diagnostic = assert_invalid(document(payload), reason)

    assert diagnostic.line is None or diagnostic.line >= 1
    assert diagnostic.column is None or diagnostic.column >= 1


def test_yaml_12_core_scalar_semantics_are_owned_by_the_parser():
    result = FrontmatterParser.parse(
        document(
            "true_value: true\n"
            "false_value: FALSE\n"
            "null_value: null\n"
            "tilde_value: ~\n"
            "yes_value: yes\n"
            "no_value: no\n"
            "on_value: on\n"
            "off_value: off\n"
            "date_value: 2026-09-26\n"
            "leading_zero: 0123\n"
            "integer: -42\n"
            "octal: 0o17\n"
            "hexadecimal: 0x1f\n"
            "float: 12.5\n"
            "scientific: 1e3\n"
            'quoted_true: "true"\n'
        )
    )

    assert result.state == "valid"
    assert result.metadata == {
        "true_value": True,
        "false_value": False,
        "null_value": None,
        "tilde_value": None,
        "yes_value": "yes",
        "no_value": "no",
        "on_value": "on",
        "off_value": "off",
        "date_value": "2026-09-26",
        "leading_zero": 123,
        "integer": -42,
        "octal": 15,
        "hexadecimal": 31,
        "float": 12.5,
        "scientific": 1000.0,
        "quoted_true": "true",
    }


def test_decimal_integer_within_scalar_bound_does_not_depend_on_python_digit_limit():
    digits = "9" * 5_000

    result = FrontmatterParser.parse(document(f"large: {digits}\n"))

    assert result.state == "valid"
    assert result.metadata is not None
    assert isinstance(result.metadata["large"], int)
    assert result.metadata["large"].bit_length() > 16_000


@pytest.mark.parametrize(
    ("envelope_size", "expected_state"),
    [
        (MAX_FRONTMATTER_BYTES - 1, "valid"),
        (MAX_FRONTMATTER_BYTES, "valid"),
        (MAX_FRONTMATTER_BYTES + 1, "invalid"),
    ],
)
def test_frontmatter_envelope_byte_bound(envelope_size, expected_state):
    # opener + comment marker + filler + LF + EOF closer = filler + 9 bytes
    markdown = f"---\n#{'x' * (envelope_size - 9)}\n---"
    result = FrontmatterParser.parse(markdown)

    assert len(markdown.encode("utf-8")) == envelope_size
    assert result.state == expected_state
    if expected_state == "invalid":
        assert result.diagnostic == FrontmatterDiagnostic(reason="frontmatter_too_large")


def test_oversized_envelope_check_does_not_require_materializing_encoded_source():
    markdown = f"---\nkey: {'x' * (MAX_FRONTMATTER_BYTES * 2)}\n---"

    assert_invalid(markdown, "frontmatter_too_large")


@pytest.mark.parametrize(
    ("source_size", "expected_state"),
    [
        (MAX_SCALAR_BYTES - 1, "valid"),
        (MAX_SCALAR_BYTES, "valid"),
        (MAX_SCALAR_BYTES + 1, "invalid"),
    ],
)
def test_scalar_source_representation_byte_bound(source_size, expected_state):
    scalar = f'"{"x" * (source_size - 2)}"'
    result = FrontmatterParser.parse(document(f"key: {scalar}\n"))

    assert len(scalar.encode("utf-8")) == source_size
    assert result.state == expected_state
    if expected_state == "invalid":
        assert result.diagnostic is not None
        assert result.diagnostic.reason == "scalar_too_large"


@pytest.mark.parametrize(
    ("key_size", "expected_state"),
    [
        (MAX_MAPPING_KEY_BYTES - 1, "valid"),
        (MAX_MAPPING_KEY_BYTES, "valid"),
        (MAX_MAPPING_KEY_BYTES + 1, "invalid"),
    ],
)
def test_mapping_key_byte_bound(key_size, expected_state):
    result = FrontmatterParser.parse(document(f"{'k' * key_size}: value\n"))

    assert result.state == expected_state
    if expected_state == "invalid":
        assert result.diagnostic is not None
        assert result.diagnostic.reason == "mapping_key_too_large"


@pytest.mark.parametrize(
    ("depth", "expected_state"),
    [(7, "valid"), (8, "valid"), (9, "invalid")],
)
def test_container_depth_bound_counts_root_mapping_as_one(depth, expected_state):
    sequence_depth = depth - 1
    payload = f"value: {'[' * sequence_depth}item{']' * sequence_depth}\n"
    result = FrontmatterParser.parse(document(payload))

    assert result.state == expected_state
    if expected_state == "invalid":
        assert result.diagnostic is not None
        assert result.diagnostic.reason == "container_too_deep"


@pytest.mark.parametrize(
    ("aggregate_items", "expected_state"),
    [(MAX_ITEMS - 1, "valid"), (MAX_ITEMS, "valid"), (MAX_ITEMS + 1, "invalid")],
)
def test_aggregate_mapping_entry_and_sequence_item_bound(aggregate_items, expected_state):
    sequence_items = aggregate_items - 1
    payload = f"items: [{','.join('0' for _ in range(sequence_items))}]\n"
    result = FrontmatterParser.parse(document(payload))

    assert result.state == expected_state
    if expected_state == "invalid":
        assert result.diagnostic is not None
        assert result.diagnostic.reason == "too_many_items"


def test_valid_metadata_is_deeply_immutable_and_preserves_order_and_duplicates_in_sequences():
    result = FrontmatterParser.parse(
        document("zeta: [third, first, third]\nalpha:\n  nested_b: 2\n  nested_a: 1\n")
    )

    assert result.state == "valid"
    assert result.metadata is not None
    assert list(result.metadata) == ["zeta", "alpha"]
    assert result.metadata["zeta"] == ("third", "first", "third")
    nested = result.metadata["alpha"]
    assert isinstance(nested, Mapping)
    assert list(nested) == ["nested_b", "nested_a"]
    with pytest.raises(TypeError):
        result.metadata["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        nested["new"] = "value"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.state = "absent"  # type: ignore[misc]


def test_invalid_result_is_privacy_safe_bounded_and_leaves_markdown_unchanged():
    secret_key = "private-account-name"
    secret_value = "private-account-value"
    markdown = document(f"{secret_key}: {secret_value}\n{secret_key}: duplicate\n")
    original = markdown[:]

    result = FrontmatterParser.parse(markdown)

    assert result.state == "invalid"
    assert result.metadata is None
    assert result.diagnostic is not None
    rendered_diagnostic = repr(result.diagnostic)
    assert result.diagnostic.reason == "duplicate_key"
    assert 1 <= (result.diagnostic.line or 1) <= MAX_FRONTMATTER_BYTES
    assert 1 <= (result.diagnostic.column or 1) <= MAX_FRONTMATTER_BYTES
    assert secret_key not in rendered_diagnostic
    assert secret_value not in rendered_diagnostic
    assert markdown == original


def test_parser_rejects_non_decoded_input_instead_of_accepting_a_path(tmp_path):
    with pytest.raises(TypeError, match="decoded text"):
        FrontmatterParser.parse(tmp_path / "Note.md")  # type: ignore[arg-type]
