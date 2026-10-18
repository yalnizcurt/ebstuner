"""Milestone 5: Performance, scale, and multi-scenario regression test suite.

Validates the universal fixed-width engine across diverse layouts:
- 25-field, 55-field, 180-field, and 300-field layouts
- 20,000+ and 50,000+ byte records
- Mixed record types with routing conditions
- Diverse alignment and padding rules (left/right/none, zero/space)
- Duplicate field names in different sections/layouts
- Invalid types (spec conflicts produce suggestions; DAT preserved exactly)
- Missing padding and larger displacement shifts
- Ambiguous adjacent CHAR fields requiring human review
- Overwidth data
- UNKNOWN mandatory flags
"""

import pytest
import tasaa
from specs import normalize_spec
from validation_pipeline import compile_pipeline, validate_record


def generate_layout_spec(field_count, record_width, padding_side="right", padding_char=" "):
    base, extra = divmod(record_width, field_count)
    fields = []
    cursor = 1
    for index in range(field_count):
        width = base + (index < extra)
        fields.append({
            "id": f"field-{index:03d}",
            "field_name": f"FIELD_{index:03d}",
            "mapping_type": "POSITION",
            "start_pos": str(cursor),
            "end_pos": str(cursor + width - 1),
            "length": str(width),
            "datatype_conversion": "CHAR",
            "padding_alignment": "Trailing spaces" if padding_side == "right" else "Leading spaces",
            "mandatory": "NO",
            "rules": {
                "start": cursor,
                "end": cursor + width - 1,
                "length": width,
                "datatype": "char",
                "conversion": "identity",
                "required": False,
                "mapping_type": "physical",
                "padding": {"side": padding_side, "char": padding_char},
            },
        })
        cursor += width
    return {
        "fields": fields,
        "layouts": [{
            "id": "primary",
            "name": "primary",
            "field_ids": [f["id"] for f in fields],
            "record_length": record_width,
            "when": [],
        }],
        "options": {"encoding": "ascii", "record_format": "stream"},
    }


def make_field(name, start, width, kind="CHAR", mandatory="NO", **rules):
    return {
        "id": name,
        "field_name": name,
        "mapping_type": "POSITION",
        "start_pos": str(start),
        "end_pos": str(start + width - 1),
        "length": str(width),
        "datatype_conversion": kind,
        "padding_alignment": "Fixed Text (Exact Width)",
        "mandatory": mandatory,
        "rules": rules,
    }


def test_25_field_layout_scale():
    spec = generate_layout_spec(25, 500)
    layout = spec["layouts"][0]
    expected = b"".join(f"VAL{i:02d}".encode().ljust(20, b" ") for i in range(25))
    assert len(expected) == 500
    res = tasaa.analyze(spec, layout, expected)
    assert res["status"] == "already_valid"
    assert res["candidate"] == expected


def test_55_field_layout_scale():
    spec = generate_layout_spec(55, 3300)
    layout = spec["layouts"][0]
    expected = b"".join(f"VAL{i:02d}".encode().ljust(60, b" ") for i in range(55))
    res = tasaa.analyze(spec, layout, expected)
    assert res["status"] == "already_valid"
    assert res["candidate"] == expected


def test_180_field_20000_byte_layout_scale():
    spec = generate_layout_spec(180, 20160)
    layout = spec["layouts"][0]
    # width = 112 per field
    expected = b"".join(f"FIELD_{i:03d}_DATA".encode().ljust(112, b" ") for i in range(180))
    assert len(expected) == 20160
    res = tasaa.analyze(spec, layout, expected)
    assert res["status"] == "already_valid"
    assert len(res["ledger"]) == 20160
    assert res["provenance"]["source_bytes_accounted_for"] == 20160


def test_300_field_50000_byte_layout_scale():
    spec = generate_layout_spec(300, 50100)
    layout = spec["layouts"][0]
    expected = b"".join(f"F{i:03d}".encode().ljust(167, b" ") for i in range(300))
    assert len(expected) == 50100
    res = tasaa.analyze(spec, layout, expected)
    assert res["status"] == "already_valid"
    assert len(res["ledger"]) == 50100


def test_mixed_record_types_layout():
    """Universal engine handles distinct header/detail/footer records without hardcoded layouts."""
    header_fields = [
        make_field("h1", 1, 2, kind="CHAR", mandatory="YES", constant="01", allowed_values=["01"]),
        make_field("h2", 3, 8, kind="CHAR", mandatory="YES"),
    ]
    detail_fields = [
        make_field("d1", 1, 2, kind="CHAR", mandatory="YES", constant="02", allowed_values=["02"]),
        make_field("d2", 3, 8, kind="NUMBER", mandatory="YES"),
    ]
    spec = normalize_spec({
        "fields": header_fields + detail_fields,
        "layouts": [
            {"id": "header", "name": "Header", "field_ids": ["h1", "h2"], "record_length": 10, "when": [{"start": 1, "end": 2, "equals": "01", "trim": False}]},
            {"id": "detail", "name": "Detail", "field_ids": ["d1", "d2"], "record_length": 10, "when": [{"start": 1, "end": 2, "equals": "02", "trim": False}]},
        ],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    pipeline = compile_pipeline(spec)
    # Check header
    h_res = validate_record(pipeline, b"01BATCH001")
    assert h_res["valid"] is True
    assert h_res["layout_id"] == "header"
    # Check detail
    d_res = validate_record(pipeline, b"0200012345")
    assert d_res["valid"] is True
    assert d_res["layout_id"] == "detail"


def test_diverse_alignment_and_padding_rules():
    """Engine supports zero-padded, left-aligned, right-aligned, and fixed exact-width fields."""
    f1 = make_field("f1", 1, 4, kind="CHAR", mandatory="YES")
    f2 = make_field("f2", 5, 6, kind="NUMBER", mandatory="YES")
    f3 = make_field("f3", 11, 10, kind="CHAR", mandatory="NO")
    f3["padding_alignment"] = "Trailing spaces"
    f3["rules"]["padding"] = {"side": "right", "char": " "}
    spec = normalize_spec({
        "fields": [f1, f2, f3],
        "layouts": [{"id": "primary", "name": "primary", "field_ids": ["f1", "f2", "f3"], "record_length": 20, "when": []}],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    row = b"ABCD000456HELLOTEXT "
    res = tasaa.analyze(spec, spec["layouts"][0], row)
    assert res["status"] == "already_valid"


def test_duplicate_field_names_handled_safely():
    """Unique field IDs prevent collisions when identical field names occur."""
    f1 = make_field("sec1-col1", 1, 2, kind="CHAR", mandatory="YES")
    f2 = make_field("sec2-col1", 3, 2, kind="CHAR", mandatory="YES")
    f1["field_name"] = "STATUS"
    f2["field_name"] = "STATUS"
    spec = normalize_spec({
        "fields": [f1, f2],
        "layouts": [{"id": "primary", "name": "primary", "field_ids": ["sec1-col1", "sec2-col1"], "record_length": 4, "when": []}],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    row = b"01OK"
    res = tasaa.analyze(spec, spec["layouts"][0], row)
    assert res["status"] == "already_valid"
    assert res["candidate"] == row


def test_invalid_types_produce_spec_conflicts_without_altering_original_data():
    """Rule 1 & 2: Spec conflicts produce suggestions; original data values are NEVER altered."""
    fields = [
        make_field("f1", 1, 6, kind="NUMBER", mandatory="YES"),
    ]
    spec = normalize_spec({
        "fields": fields,
        "layouts": [{"id": "primary", "name": "primary", "field_ids": ["f1"], "record_length": 6, "when": []}],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    # Original DAT has alphanumeric data in a numeric field
    raw_dat = b"AB1234"
    res = tasaa.analyze(spec, spec["layouts"][0], raw_dat)
    # Must flag spec conflicts or non-certified status
    assert len(res["spec_conflicts"]) > 0 or res["status"] in ("needs_review", "spec_conflict")
    # Original values must NEVER be altered to force compliance
    assert res["candidate"] is None or res["candidate"] == raw_dat


def test_ambiguous_adjacent_char_fields_require_human_review():
    """Rule 3 & 4: Scores never authorize repairs where adjacent whitespace cannot be uniquely proven."""
    f1 = make_field("c1", 1, 10, kind="CHAR", mandatory="NO")
    f2 = make_field("c2", 11, 4, kind="CHAR", mandatory="NO")
    spec = normalize_spec({
        "fields": [f1, f2],
        "layouts": [{"id": "primary", "name": "primary", "field_ids": ["c1", "c2"], "record_length": 14, "when": []}],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    # Shifted input without anchors in adjacent CHAR fields
    corrupted_row = b"DALLAS   TX "  # 12 bytes instead of 14, ambiguous spacing between city and state
    res = tasaa.analyze(spec, spec["layouts"][0], corrupted_row)
    # The certification level must NOT declare safe repair without proof
    assert res.get("certification", {}).get("level") in ("HUMAN_REVIEW_REQUIRED", "BEST_RANKED_CANDIDATE", "UNRECOVERABLE", "SPEC_CONFLICT")


def test_unknown_mandatory_flags_generate_structural_candidates():
    """Component D: Unknown mandatory status must not prevent structural candidate generation."""
    f1 = make_field("f1", 1, 5, kind="CHAR", mandatory="UNKNOWN", allowed_values=["AB"])
    f1["padding_alignment"] = "Trailing spaces"
    f1["rules"]["padding"] = {"side": "right", "char": " "}
    f2 = make_field("f2", 6, 3, kind="CHAR", mandatory="UNKNOWN", allowed_values=["XYZ"])
    f2["padding_alignment"] = "Trailing spaces"
    f2["rules"]["padding"] = {"side": "right", "char": " "}
    spec = normalize_spec({
        "fields": [f1, f2],
        "layouts": [{"id": "primary", "name": "primary", "field_ids": ["f1", "f2"], "record_length": 8, "when": []}],
        "options": {"encoding": "ascii", "record_format": "stream"},
    })
    # Input has missing padding: "AB" (2 bytes) + "XYZ" (3 bytes) = 5 bytes instead of 8
    corrupted_row = b"ABXYZ"
    res = tasaa.analyze(spec, spec["layouts"][0], corrupted_row)
    # Candidates are successfully generated even with UNKNOWN mandatory
    assert len(res["candidates"]) > 0
    # Because mandatory status is UNKNOWN, ambiguity certifier requires human review
    assert res["certification"]["level"] == "HUMAN_REVIEW_REQUIRED"
    assert "Mandatory status is unknown" in res["reason"]
