from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from app.services.frontmatter import (
    MAX_FRONTMATTER_BYTES,
    MAX_ITEMS,
    MAX_MAPPING_KEY_BYTES,
    MAX_PORTABLE_FIELD_VALUES,
    MAX_PORTABLE_VALUE_BYTES,
    MAX_SCALAR_BYTES,
    FrontmatterDiagnostic,
    FrontmatterParser,
    PortableFieldDiagnostic,
    PortableFieldsProjection,
    project_portable_fields,
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


def portable_fields(payload: str) -> PortableFieldsProjection:
    frontmatter = FrontmatterParser.parse(document(payload))
    assert frontmatter.state == "valid"
    projection = project_portable_fields(frontmatter)
    assert projection is not None
    return projection


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


@pytest.mark.parametrize(
    "frontmatter",
    [
        FrontmatterParser.parse("# No frontmatter\n"),
        FrontmatterParser.parse("---\naliases: [unterminated\n---\n"),
    ],
)
def test_portable_fields_are_unavailable_for_absent_or_invalid_frontmatter(frontmatter):
    assert project_portable_fields(frontmatter) is None


def test_missing_portable_fields_are_independently_absent():
    projection = portable_fields("other: metadata\n")

    assert projection.aliases.state == "absent"
    assert projection.aliases.occurrences == ()
    assert projection.aliases.diagnostics == ()
    assert projection.tags.state == "absent"
    assert projection.tags.occurrences == ()
    assert projection.tags.diagnostics == ()


def test_only_exact_top_level_portable_field_names_are_recognized():
    projection = portable_fields("Aliases: [not, portable]\nnested: {tags: [not, portable]}\n")

    assert projection.aliases.state == "absent"
    assert projection.tags.state == "absent"


def test_scalar_and_sequence_forms_preserve_exact_values_indexes_order_and_duplicates():
    projection = portable_fields(
        'aliases: " Alias "\n'
        'tags: ["#Parent/Child", MiXeD, café, "café", MiXeD]\n'
    )

    assert projection.aliases.state == "valid"
    assert [(item.value, item.source_index) for item in projection.aliases.occurrences] == [
        (" Alias ", 0)
    ]
    assert projection.tags.state == "valid"
    assert [(item.value, item.source_index) for item in projection.tags.occurrences] == [
        ("#Parent/Child", 0),
        ("MiXeD", 1),
        ("café", 2),
        ("café", 3),
        ("MiXeD", 4),
    ]


def test_empty_sequence_is_valid_with_no_occurrences_or_diagnostics():
    projection = portable_fields("aliases: []\n")

    assert projection.aliases.state == "valid"
    assert projection.aliases.occurrences == ()
    assert projection.aliases.diagnostics == ()


@pytest.mark.parametrize("invalid_value", ["null", "true", "12", "1.5", "{}"])
def test_invalid_alias_scalar_does_not_affect_valid_tags(invalid_value):
    projection = portable_fields(f"aliases: {invalid_value}\ntags: [one, two]\n")

    assert projection.aliases.state == "invalid"
    assert projection.aliases.occurrences == ()
    assert projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="field_type", field="aliases"),
    )
    assert [item.value for item in projection.tags.occurrences] == ["one", "two"]


@pytest.mark.parametrize(
    ("invalid_value", "source_index"),
    [
        ("[valid, false, later]", 1),
        ("[valid, null, later]", 1),
        ("[valid, 2, later]", 1),
        ("[valid, 2.5, later]", 1),
        ("[valid, {}, later]", 1),
        ("[valid, [nested], later]", 1),
    ],
)
def test_invalid_alias_sequence_member_does_not_affect_valid_tags(invalid_value, source_index):
    projection = portable_fields(f"aliases: {invalid_value}\ntags: tag\n")

    assert projection.aliases.state == "invalid"
    assert projection.aliases.occurrences == ()
    assert projection.aliases.diagnostics == (
        PortableFieldDiagnostic(
            reason="member_type",
            field="aliases",
            source_index=source_index,
        ),
    )
    assert [item.value for item in projection.tags.occurrences] == ["tag"]


def test_invalid_tags_do_not_affect_valid_aliases():
    projection = portable_fields("aliases: [first, second]\ntags: [valid, false]\n")

    assert [item.value for item in projection.aliases.occurrences] == ["first", "second"]
    assert projection.tags.state == "invalid"
    assert projection.tags.diagnostics == (
        PortableFieldDiagnostic(reason="member_type", field="tags", source_index=1),
    )


def test_invalid_portable_field_does_not_invalidate_generic_frontmatter():
    frontmatter = FrontmatterParser.parse(document("aliases: false\ntags: tag\nother: metadata\n"))
    assert frontmatter.state == "valid"
    assert frontmatter.metadata == {"aliases": False, "tags": "tag", "other": "metadata"}

    projection = project_portable_fields(frontmatter)

    assert projection is not None
    assert projection.aliases.state == "invalid"
    assert projection.tags.state == "valid"
    assert frontmatter.state == "valid"
    assert frontmatter.metadata == {"aliases": False, "tags": "tag", "other": "metadata"}


def test_empty_values_are_omitted_with_ordered_bounded_diagnostics():
    projection = portable_fields('aliases: ["", "  ", keep, "\t", second]\n')

    assert projection.aliases.state == "valid"
    assert [(item.value, item.source_index) for item in projection.aliases.occurrences] == [
        ("keep", 2),
        ("second", 4),
    ]
    assert projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="empty_value", field="aliases", source_index=0),
        PortableFieldDiagnostic(reason="empty_value", field="aliases", source_index=1),
        PortableFieldDiagnostic(reason="empty_value", field="aliases", source_index=3),
    )


def test_all_empty_values_remain_a_valid_field():
    projection = portable_fields('tags: ["", " ", "\t"]\n')

    assert projection.tags.state == "valid"
    assert projection.tags.occurrences == ()
    assert [diagnostic.source_index for diagnostic in projection.tags.diagnostics] == [0, 1, 2]
    assert all(diagnostic.reason == "empty_value" for diagnostic in projection.tags.diagnostics)


@pytest.mark.parametrize(
    ("source_count", "expected_state"),
    [
        (MAX_PORTABLE_FIELD_VALUES - 1, "valid"),
        (MAX_PORTABLE_FIELD_VALUES, "valid"),
        (MAX_PORTABLE_FIELD_VALUES + 1, "invalid"),
    ],
)
def test_portable_field_source_value_count_bound(source_count, expected_state):
    projection = portable_fields(f"aliases: [{','.join('item' for _ in range(source_count))}]\n")

    assert projection.aliases.state == expected_state
    if expected_state == "valid":
        assert len(projection.aliases.occurrences) == source_count
        assert projection.aliases.diagnostics == ()
    else:
        assert projection.aliases.occurrences == ()
        assert projection.aliases.diagnostics == (
            PortableFieldDiagnostic(reason="source_value_count", field="aliases"),
        )


@pytest.mark.parametrize(
    ("source_count", "expected_state"),
    [
        (MAX_PORTABLE_FIELD_VALUES, "valid"),
        (MAX_PORTABLE_FIELD_VALUES + 1, "invalid"),
    ],
)
def test_source_count_is_checked_before_empty_value_omission(source_count, expected_state):
    empty_values = ",".join('""' for _ in range(source_count))
    projection = portable_fields(f"aliases: [{empty_values}]\n")

    assert projection.aliases.state == expected_state
    assert projection.aliases.occurrences == ()
    if expected_state == "valid":
        assert len(projection.aliases.diagnostics) == MAX_PORTABLE_FIELD_VALUES
        assert all(item.reason == "empty_value" for item in projection.aliases.diagnostics)
    else:
        assert projection.aliases.diagnostics == (
            PortableFieldDiagnostic(reason="source_value_count", field="aliases"),
        )


@pytest.mark.parametrize(
    ("value", "expected_state"),
    [
        ("x" * (MAX_PORTABLE_VALUE_BYTES - 1), "valid"),
        ("x" * MAX_PORTABLE_VALUE_BYTES, "valid"),
        ("x" * (MAX_PORTABLE_VALUE_BYTES + 1), "invalid"),
        ("é" * 511 + "a", "valid"),
        ("é" * 512, "valid"),
        ("é" * 512 + "a", "invalid"),
    ],
)
def test_portable_field_utf8_value_byte_bound(value, expected_state):
    assert len(value.encode("utf-8")) in {
        MAX_PORTABLE_VALUE_BYTES - 1,
        MAX_PORTABLE_VALUE_BYTES,
        MAX_PORTABLE_VALUE_BYTES + 1,
    }
    projection = portable_fields(f'aliases: ["{value}"]\n')

    assert projection.aliases.state == expected_state
    if expected_state == "valid":
        assert [item.value for item in projection.aliases.occurrences] == [value]
    else:
        assert projection.aliases.occurrences == ()
        assert projection.aliases.diagnostics == (
            PortableFieldDiagnostic(reason="value_size", field="aliases", source_index=0),
        )


def test_oversized_whitespace_is_omitted_before_usable_value_size_validation():
    whitespace = " " * (MAX_PORTABLE_VALUE_BYTES + 1)
    projection = portable_fields(f'aliases: ["{whitespace}"]\n')

    assert projection.aliases.state == "valid"
    assert projection.aliases.occurrences == ()
    assert projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="empty_value", field="aliases", source_index=0),
    )


def test_projection_does_not_mutate_generic_metadata_or_nested_values():
    frontmatter = FrontmatterParser.parse(
        document("aliases: [one, two, one]\ntags: [tag]\nother: {nested: [1, 2]}\n")
    )
    assert frontmatter.state == "valid"
    assert frontmatter.metadata is not None
    metadata = frontmatter.metadata
    aliases = metadata["aliases"]
    nested = metadata["other"]

    projection = project_portable_fields(frontmatter)

    assert projection is not None
    assert frontmatter.metadata is metadata
    assert frontmatter.metadata["aliases"] is aliases
    assert frontmatter.metadata["other"] is nested
    assert frontmatter.metadata == {
        "aliases": ("one", "two", "one"),
        "tags": ("tag",),
        "other": {"nested": (1, 2)},
    }


def test_validation_reports_only_the_deterministic_first_failure():
    oversized = "sensitive-" + "x" * MAX_PORTABLE_VALUE_BYTES
    too_many_with_bad_member = ["ok"] * MAX_PORTABLE_FIELD_VALUES + ["false"]
    count_projection = portable_fields(
        f"aliases: [{','.join(too_many_with_bad_member[:-1])}, false]\n"
    )
    member_projection = portable_fields(f'aliases: ["{oversized}", false]\n')
    size_projection = portable_fields(f'aliases: ["{oversized}", "{oversized}later"]\n')

    assert count_projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="source_value_count", field="aliases"),
    )
    assert member_projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="member_type", field="aliases", source_index=1),
    )
    assert size_projection.aliases.diagnostics == (
        PortableFieldDiagnostic(reason="value_size", field="aliases", source_index=0),
    )
    assert oversized not in repr(count_projection.aliases.diagnostics)
    assert oversized not in repr(member_projection.aliases.diagnostics)
    assert oversized not in repr(size_projection.aliases.diagnostics)


def test_portable_projection_and_diagnostics_are_immutable():
    projection = portable_fields('aliases: ["", value]\ntags: tag\n')

    with pytest.raises(FrozenInstanceError):
        projection.aliases.state = "invalid"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        projection.aliases.diagnostics[0].source_index = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        projection.aliases.occurrences[0] = projection.aliases.occurrences[0]  # type: ignore[index]
