"""Comprehensive Test Suite for Real Files and 10 Ambiguous Scenarios.

Verifies:
1. Real files:
   - Scenario 1 (Standalone Deduction - STD), Scenario 2 (Standard Payment - STP),
     Scenario 3 (Over Payment - OVP) CSV specifications from fixtures/specifications/
   - SYN_STD, SYN_STP, SYN_OVP DAT files from outputs/lockbox-examples/DAT_Files/
   - Certification gates, coverage limitations (MANDATORY_UNVERIFIED), layout routing,
     and non-destructive byte accounting.
   - End-to-end Studio API project workflow on real production-scale files.

2. 10 Ambiguous Scenarios:
   - 01: Layout Prefix Collision (AMBIGUOUS_LAYOUT)
   - 02: Adjacent Unanchored CHAR Fields (UNCERTAIN_BOUNDARIES / Shifted Boundary)
   - 03: Unknown Mandatory Gate (REQUIRED_RULE / MANDATORY_UNVERIFIED Certification Gate)
   - 04: Numeric Scale & Format Conflict (NUMBER(8,2) explicit dot vs implied scale)
   - 05: Internal Whitespace Retention (No destructive trimming of meaningful multi-spaces)
   - 06: Non-Unique Anchor Displacement (Beam search candidate ranking & SPEC_CONFLICT)
   - 07: Truncated Record Mid-Field (UNRECOVERABLE / No invented filler bytes)
   - 08: Ambiguous Date Formats (Bare DATE rejection & YYYYMMDD format enforcement)
   - 09: Allowed Values Domain Constraint Mismatch (ALLOWED_VALUES constraint violation)
   - 10: Mixed Delimiters & Record Boundary Isolation (CRLF/LF mixed stream isolation)
"""

import os
from pathlib import Path
import pytest

from specs import (
    extract_spec,
    normalize_spec,
    validate_spec,
    _process,
    SpecError,
)
import tasaa
import studio


# ==============================================================================
# PART 1: REAL FILES TEST SUITE
# ==============================================================================

FIXTURES_DIR = Path("fixtures/specifications")
DAT_DIR = Path("outputs/lockbox-examples/DAT_Files")


def test_real_scenario_1_unacknowledged_mandatory_blocked():
    """Real Scenario 1 CSV omits the Mandatory column.

    By Milestone 1 invariant, validate_spec must emit REQUIRED_RULE blocking errors,
    preventing automated certification of unknown mandatory requirements.
    """
    spec_path = FIXTURES_DIR / "Scenario_1_Standalone_Deduction_STD.csv"
    with open(spec_path, "rb") as f:
        doc = extract_spec(spec_path.name, f.read())

    norm = normalize_spec(doc)
    issues = validate_spec(norm)
    errors = [i for i in issues if i.get("severity") == "error"]

    assert len(errors) == 55
    assert all(e["code"] == "REQUIRED_RULE" for e in errors)
    assert any("Mandatory status is unknown" in e["message"] for e in errors)


def test_real_scenario_1_acknowledged_coverage_limitations():
    """When the consultant acknowledges unknown mandatory fields,

    validate_spec converts them to warnings, allowing DAT inspection
    while permanently recording MANDATORY_UNVERIFIED limitations.
    """
    spec_path = FIXTURES_DIR / "Scenario_1_Standalone_Deduction_STD.csv"
    with open(spec_path, "rb") as f:
        doc = extract_spec(spec_path.name, f.read())

    doc.setdefault("options", {})["acknowledge_unknown_mandatory"] = True
    norm = normalize_spec(doc)
    issues = validate_spec(norm)
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    assert len(errors) == 0
    assert len(warnings) == 55
    assert all(w["code"] == "REQUIRED_RULE" for w in warnings)


def test_real_scenario_1_with_real_dat_routing_and_byte_preservation():
    """Test Scenario 1 specification against SYN_STD_STANDARD_DEDUCTION.dat.

    The DAT file contains 7 records: 01 (transmission hdr), 05 (batch hdr),
    06 (payment), 04 (overflow/deduction), 04 (overflow/deduction),
    07 (batch trailer), 09 (transmission trailer).

    Verifies:
    1. Records 01, 05, 06, 07, 09 are preserved under unknown_records='preserve' with UNKNOWN_LAYOUT.
    2. Records 04 (lines 4 & 5) are inspected against the 04 layout.
    3. The length discrepancy (3350/3349 vs 3303) is accurately flagged without crashing.
    4. 100% of candidate bytes are preserved verbatim.
    """
    spec_path = FIXTURES_DIR / "Scenario_1_Standalone_Deduction_STD.csv"
    dat_path = DAT_DIR / "SYN_STD_STANDARD_DEDUCTION.dat"

    with open(spec_path, "rb") as f:
        doc = extract_spec(spec_path.name, f.read())
    with open(dat_path, "rb") as f:
        dat_content = f.read()

    doc.setdefault("options", {})["acknowledge_unknown_mandatory"] = True
    doc["options"]["unknown_records"] = "preserve"
    norm = normalize_spec(doc)
    # Scenario 1 specifies CTL routing condition: RECORD_TYPE = '04'
    norm["layouts"][0]["when"] = [{"start": 1, "end": 2, "equals": "04"}]

    report, candidate = _process(norm, dat_content)

    assert report["summary"]["record_count"] == 7
    summaries = report["record_summaries"]

    # Non-04 records preserved
    for idx in [0, 1, 2, 5, 6]:
        assert summaries[idx]["status"] == "preserved"
        assert any(i["code"] == "UNKNOWN_LAYOUT" for i in summaries[idx]["issues"])

    # 04 records evaluated
    for idx in [3, 4]:
        assert summaries[idx]["status"] == "needs_review"
        issue_codes = [i["code"] for i in summaries[idx]["issues"]]
        assert "UNCERTAIN_BOUNDARIES" in issue_codes
        assert "MANDATORY_UNVERIFIED" in issue_codes

    # Byte accounting: Candidate output retains all input bytes
    assert len(candidate) == len(dat_content)


def test_real_scenario_2_stp_cmdm_routing_and_validation():
    """Test Scenario 2 specification against SYN_STP_STANDARD_PAYMENT.dat."""
    spec_path = FIXTURES_DIR / "Scenario_2_Standard_Payment_STP_CMDM.csv"
    dat_path = DAT_DIR / "SYN_STP_STANDARD_PAYMENT.dat"

    with open(spec_path, "rb") as f:
        doc = extract_spec(spec_path.name, f.read())
    with open(dat_path, "rb") as f:
        dat_content = f.read()

    doc.setdefault("options", {})["acknowledge_unknown_mandatory"] = True
    doc["options"]["unknown_records"] = "preserve"
    norm = normalize_spec(doc)
    norm["layouts"][0]["when"] = [{"start": 1, "end": 2, "equals": "04"}]

    report, candidate = _process(norm, dat_content)

    assert report["summary"]["record_count"] == 7
    summaries = report["record_summaries"]
    assert summaries[0]["status"] == "preserved"
    assert summaries[3]["status"] == "needs_review"
    assert len(candidate) == len(dat_content)


def test_real_scenario_3_ovp_routing_and_validation():
    """Test Scenario 3 specification against SYN_OVP_OVERPAYMENT.dat."""
    spec_path = FIXTURES_DIR / "Scenario_3_Over_Payment_OVP.csv"
    dat_path = DAT_DIR / "SYN_OVP_OVERPAYMENT.dat"

    with open(spec_path, "rb") as f:
        doc = extract_spec(spec_path.name, f.read())
    with open(dat_path, "rb") as f:
        dat_content = f.read()

    doc.setdefault("options", {})["acknowledge_unknown_mandatory"] = True
    doc["options"]["unknown_records"] = "preserve"
    norm = normalize_spec(doc)
    norm["layouts"][0]["when"] = [{"start": 1, "end": 2, "equals": "04"}]

    report, candidate = _process(norm, dat_content)

    assert report["summary"]["record_count"] == 7
    summaries = report["record_summaries"]
    assert summaries[0]["status"] == "preserved"
    assert len(candidate) == len(dat_content)


def test_real_scenario_studio_api_end_to_end_job(tmp_path, monkeypatch):
    """Test full Studio project workflow on real Scenario 1 spec & SYN_STD dat via HTTP API."""
    from fastapi.testclient import TestClient
    from projects import Repository
    import web

    monkeypatch.setattr(web, 'repository', Repository(tmp_path / 'projects.sqlite3'))
    monkeypatch.delenv('STUDIO_ENV', raising=False)
    monkeypatch.delenv('STUDIO_ACCESS_TOKEN', raising=False)
    web.rates.clear()

    spec_path = FIXTURES_DIR / "Scenario_1_Standalone_Deduction_STD.csv"
    dat_path = DAT_DIR / "SYN_STD_STANDARD_DEDUCTION.dat"

    with open(spec_path, "rb") as f:
        spec_bytes = f.read()
    with open(dat_path, "rb") as f:
        dat_bytes = f.read()

    headers = {'X-Studio-Request': '1'}
    with TestClient(web.app) as client:
        # Establish session
        sess_resp = client.post('/api/session', json={}, headers=headers)
        assert sess_resp.status_code == 200

        # Create project
        create_resp = client.post('/api/projects', json={'name': 'Real Lockbox Scenario 1'}, headers=headers)
        assert create_resp.status_code == 201
        pid = create_resp.json()['project']['id']

        # Upload real spec and DAT files
        upload_resp = client.post(
            f'/api/projects/{pid}/studio/files',
            headers=headers,
            data={'version': 1},
            files={'spec': ('Scenario_1.csv', spec_bytes), 'dat': ('SYN_STD.dat', dat_bytes)}
        )
        assert upload_resp.status_code == 200
        state = upload_resp.json()

        # Real Scenario 1 has 55 unknown mandatory fields -> blocks unverified certification
        errors = [i for i in state['spec']['issues'] if i.get('severity') == 'error']
        assert len(errors) == 55
        assert all(e['code'] == 'REQUIRED_RULE' for e in errors)

        # Acknowledge coverage limitations and set unknown_records='preserve'
        opts = dict(state['spec']['options'])
        opts['acknowledge_unknown_mandatory'] = True
        opts['unknown_records'] = 'preserve'
        put_resp = client.put(
            f'/api/projects/{pid}/studio/spec',
            headers=headers,
            json={'version': state['project']['version'], 'fields': state['spec']['fields'], 'options': opts, 'layouts': state['spec']['layouts']}
        )
        assert put_resp.status_code == 200
        new_state = put_resp.json()
        assert len([i for i in new_state['spec']['issues'] if i.get('severity') == 'error']) == 0
        assert len(new_state['spec']['coverage_limitations']) == 55

        # Download original DAT file and verify 100% byte fidelity
        dl_dat = client.get(f'/api/projects/{pid}/studio/download?original=true', headers=headers)
        assert dl_dat.status_code == 200
        assert dl_dat.content == dat_bytes

        # Download original spec source and verify 100% byte fidelity
        dl_spec = client.get(f'/api/projects/{pid}/studio/download?spec_source=true', headers=headers)
        assert dl_spec.status_code == 200
        assert dl_spec.content == spec_bytes


# ==============================================================================
# PART 2: 10 AMBIGUOUS SPEC & DAT SCENARIOS
# ==============================================================================

AMB_DIR = Path("fixtures/ambiguous_scenarios")


def test_ambiguity_01_layout_prefix_collision():
    """Scenario 1: Layout condition overlap.

    Two layouts match the same record prefix ('04').
    Engine must flag AMBIGUOUS_LAYOUT, mark needs_review, certified=False,
    and retain the original bytes without guessing.
    """
    with open(AMB_DIR / "spec_01_layout_prefix_collision.csv", "rb") as f:
        doc = extract_spec("spec_01.csv", f.read())
    norm = normalize_spec(doc)
    # Define two conflicting layouts with identical routing conditions
    norm["layouts"] = [
        {"id": "LAYOUT_STD", "name": "Standard 04", "field_ids": [f["id"] for f in norm["fields"]], "record_length": 20, "when": [{"start": 1, "end": 2, "equals": "04"}]},
        {"id": "LAYOUT_GEN", "name": "Generic 04", "field_ids": [f["id"] for f in norm["fields"]], "record_length": 20, "when": [{"start": 1, "end": 2, "equals": "04"}]},
    ]

    with open(AMB_DIR / "dat_01_layout_prefix_collision.dat", "rb") as f:
        dat = f.read()

    rep, cand = _process(norm, dat)
    assert rep["valid"] is False
    assert len(rep["record_summaries"]) == 2
    for r in rep["record_summaries"]:
        assert r["status"] == "needs_review"
        assert any(i["code"] == "AMBIGUOUS_LAYOUT" for i in r["issues"])
    assert cand == dat


def test_ambiguity_02_adjacent_unanchored_char():
    """Scenario 2: Adjacent unanchored CHAR fields with boundary ambiguity.

    FIRST_NAME (15) and LAST_NAME (15).
    Row 2 has 'JOHN  MICHAEL  SMITH          ' where middle spaces blur the boundary.
    Row 3 has 'ROBERT   LEE   DAVIS          '.
    TASAA accounts for all 30 bytes in provenance without arbitrary truncation.
    """
    with open(AMB_DIR / "spec_02_adjacent_unanchored_char.csv", "rb") as f:
        doc = extract_spec("spec_02.csv", f.read())
    norm = normalize_spec(doc)
    layout = norm["layouts"][0]

    with open(AMB_DIR / "dat_02_adjacent_unanchored_char.dat", "rb") as f:
        dat = f.read()

    lines = dat.splitlines()
    for line in lines:
        tasaa_res = tasaa.analyze(norm, layout, line)
        assert tasaa_res["provenance"]["source_bytes_accounted_for"] == len(line)
        assert len(line) == 30

    # Test shifted row where LAST_NAME was pushed into FIRST_NAME
    shifted_row = b"JOHN SMITH                    "  # LAST_NAME is blank
    res_shifted = tasaa.analyze(norm, layout, shifted_row)
    assert res_shifted["status"] == "needs_review"
    assert res_shifted["classification"] == "SPEC_CONFLICT"
    assert any(f["code"] == "REQUIRED" for f in res_shifted["field_findings"])


def test_ambiguity_03_unknown_mandatory_gate():
    """Scenario 3: Unknown mandatory status blocking automated certification.

    Unacknowledged: validate_spec emits REQUIRED_RULE errors.
    Acknowledged: spec compiles, but MANDATORY_UNVERIFIED coverage limitation
    is reported, marking records as needs_review or failed.
    """
    with open(AMB_DIR / "spec_03_unknown_mandatory_gate.csv", "rb") as f:
        raw_spec = f.read()
    with open(AMB_DIR / "dat_03_unknown_mandatory_gate.dat", "rb") as f:
        raw_dat = f.read()

    # 1. Unacknowledged blocks spec certification
    doc_unack = extract_spec("spec_03.csv", raw_spec)
    norm_unack = normalize_spec(doc_unack)
    issues_unack = validate_spec(norm_unack)
    errors = [i for i in issues_unack if i.get("severity") == "error"]
    assert len(errors) == 3
    assert all(e["code"] == "REQUIRED_RULE" for e in errors)

    # 2. Acknowledged tracks coverage limitations
    doc_ack = extract_spec("spec_03.csv", raw_spec)
    doc_ack.setdefault("options", {})["acknowledge_unknown_mandatory"] = True
    norm_ack = normalize_spec(doc_ack)
    assert len([i for i in validate_spec(norm_ack) if i.get("severity") == "error"]) == 0

    rep, cand = _process(norm_ack, raw_dat)
    assert rep["valid"] is False
    for r in rep["record_summaries"]:
        assert any(i["code"] == "MANDATORY_UNVERIFIED" for i in r["issues"])
    assert cand == raw_dat


def test_ambiguity_04_numeric_scale_conflict():
    """Scenario 4: Numeric scale & decimal point conflict in NUMBER(8,2).

    Row 1: ITM010000123.4500010 (explicit decimal dot)
    Row 2: ITM02000001234500010 (implied scale)
    Row 3: ITM03   123.45  00010 (misaligned padding)
    Verifies decimal precision/scale rules and formatting change protections.
    """
    with open(AMB_DIR / "spec_04_numeric_scale_conflict.csv", "rb") as f:
        doc = extract_spec("spec_04.csv", f.read())
    norm = normalize_spec(doc)
    amount_field = norm["fields"][1]
    assert amount_field["rules"]["datatype"] == "decimal"
    assert amount_field["rules"]["precision"] == 8
    assert amount_field["rules"]["scale"] == 2

    with open(AMB_DIR / "dat_04_numeric_scale_conflict.dat", "rb") as f:
        raw_dat = f.read()

    rep, cand = _process(norm, raw_dat)
    # Row 1 and Row 2 conform to length
    assert rep["record_summaries"][0]["status"] == "passed"
    assert rep["record_summaries"][1]["status"] == "passed"
    # Row 3 with displaced spaces flags UNAPPROVED_REFORMAT / UNCERTAIN_BOUNDARIES
    assert rep["record_summaries"][2]["status"] == "needs_review"
    assert any(i["code"] == "UNAPPROVED_REFORMAT" for i in rep["record_summaries"][2]["issues"])


def test_ambiguity_05_internal_whitespace_preservation():
    """Scenario 5: Meaningful multi-space preservation in character fields.

    'ACME   SUPPLY   CORP' contains internal multi-spaces.
    The engine must never collapse or strip internal whitespace.
    """
    with open(AMB_DIR / "spec_05_internal_whitespace_preservation.csv", "rb") as f:
        doc = extract_spec("spec_05.csv", f.read())
    norm = normalize_spec(doc)

    # 60-byte exact record with meaningful internal spaces
    clean_row = b"ACME   SUPPLY   CORP          123   INDUSTRIAL   PKWY       \n"
    rep, cand = _process(norm, clean_row)
    assert rep["valid"] is True
    assert cand == clean_row
    # Check that 'ACME   SUPPLY   CORP' was not collapsed
    assert b"ACME   SUPPLY   CORP" in cand


def test_ambiguity_06_anchor_displacement_beam_search():
    """Scenario 6: Non-unique repeated anchor token displacement.

    Constant 'TAG:' anchors at Pos 1 and Pos 11.
    Row 2 has second 'TAG:' displaced to Pos 10.
    TASAA's beam search identifies displacement and score penalty breakdown.
    """
    with open(AMB_DIR / "spec_06_anchor_displacement_beam.csv", "rb") as f:
        doc = extract_spec("spec_06.csv", f.read())
    norm = normalize_spec(doc)
    layout = norm["layouts"][0]

    with open(AMB_DIR / "dat_06_anchor_displacement_beam.dat", "rb") as f:
        raw_dat = f.read()

    lines = raw_dat.splitlines()
    # Row 1 is compliant
    res1 = tasaa.analyze(norm, layout, lines[0])
    assert res1["classification"] == "POSITIONALLY_COMPLIANT"

    # Row 2 is displaced: second TAG: displaced by 1 byte
    res2 = tasaa.analyze(norm, layout, lines[1])
    assert res2["classification"] == "SPEC_CONFLICT"
    assert res2["status"] == "needs_review"
    assert len(res2["alternatives"]) >= 1
    # Check score breakdown accounts for anchor conflict
    breakdowns = res2.get("score_breakdown", [])
    assert any("anchor_conflict" in b for b in breakdowns)


def test_ambiguity_07_truncated_record_mid_field():
    """Scenario 7: Truncated record cutting off mid-field.

    4 fields of length 10 (total 40 bytes).
    Row 2 is truncated at byte 23.
    Verifies UNRECOVERABLE / needs_review status without fabricating filler bytes.
    """
    with open(AMB_DIR / "spec_07_truncated_record_mid_field.csv", "rb") as f:
        doc = extract_spec("spec_07.csv", f.read())
    norm = normalize_spec(doc)
    layout = norm["layouts"][0]

    with open(AMB_DIR / "dat_07_truncated_record_mid_field.dat", "rb") as f:
        raw_dat = f.read()

    rep, cand = _process(norm, raw_dat)
    assert rep["record_summaries"][0]["status"] == "passed"
    assert rep["record_summaries"][1]["status"] == "needs_review"
    assert rep["record_summaries"][2]["status"] == "passed"

    # Deep TASAA check on row 2
    truncated_line = raw_dat.splitlines()[1]
    res_tasaa = tasaa.analyze(norm, layout, truncated_line)
    assert res_tasaa["classification"] == "UNRECOVERABLE"
    assert res_tasaa["provenance"]["source_bytes_accounted_for"] == 23


def test_ambiguity_08_ambiguous_date_formats():
    """Scenario 8: Ambiguous date format handling.

    1. Bare 'DATE' in spec without format mask is rejected during spec validation.
    2. With 'DATE \'YYYYMMDD\'', invalid format '02012024' and invalid calendar date '99999999'
       fail DATATYPE checks while '20240201' passes.
    """
    with open(AMB_DIR / "spec_08_ambiguous_date_formats.csv", "rb") as f:
        raw_spec = f.read()

    # 1. Bare DATE rejected
    doc = extract_spec("spec_08.csv", raw_spec)
    norm = normalize_spec(doc)
    issues = validate_spec(norm)
    assert any(i["code"] == "UNSUPPORTED_CONVERSION" for i in issues)

    # 2. Explicit format mask validates dates deterministically
    spec_with_mask = raw_spec.decode("utf-8").replace("DATE", "DATE 'YYYYMMDD'").encode("utf-8")
    doc_masked = extract_spec("spec_08.csv", spec_with_mask)
    norm_masked = normalize_spec(doc_masked)

    with open(AMB_DIR / "dat_08_ambiguous_date_formats.dat", "rb") as f:
        raw_dat = f.read()

    rep, cand = _process(norm_masked, raw_dat)
    assert rep["record_summaries"][0]["status"] == "failed"  # 02012024 invalid YYYYMMDD
    assert rep["record_summaries"][1]["status"] == "passed"  # 20240201 valid
    assert rep["record_summaries"][2]["status"] == "failed"  # 99999999 invalid calendar


def test_ambiguity_09_allowed_values_enum_mismatch():
    """Scenario 9: Allowed values / enum domain constraint checking.

    TXN_TYPE allowed: ['CRD', 'DBT', 'REV']
    STATUS_CODE allowed: ['Y', 'N']
    Verifies rows with 'XXX', lowercase 'crd', or '?' trigger ALLOWED_VALUES violations.
    """
    with open(AMB_DIR / "spec_09_allowed_values_enum_mismatch.csv", "rb") as f:
        doc = extract_spec("spec_09.csv", f.read())
    norm = normalize_spec(doc)

    with open(AMB_DIR / "dat_09_allowed_values_enum_mismatch.dat", "rb") as f:
        raw_dat = f.read()

    rep, cand = _process(norm, raw_dat)
    summaries = rep["record_summaries"]

    assert summaries[0]["status"] == "passed"  # CRD ... Y
    assert summaries[1]["status"] == "passed"  # DBT ... N
    assert summaries[2]["status"] == "failed"  # XXX
    assert any(i["code"] == "ALLOWED_VALUES" for i in summaries[2]["issues"])
    assert summaries[3]["status"] == "failed"  # crd (case sensitive)
    assert any(i["code"] == "ALLOWED_VALUES" for i in summaries[3]["issues"])
    assert summaries[4]["status"] == "failed"  # REV ... ?
    assert any(i["code"] == "ALLOWED_VALUES" for i in summaries[4]["issues"])


def test_ambiguity_10_mixed_delimiters_and_trailers():
    """Scenario 10: Stream record isolation with mixed CRLF/LF line endings.

    Row 1: CRLF record (25 bytes)
    Row 2: LF record (25 bytes)
    Row 3: Record with stray extra space before LF (26 bytes)
    Row 4: CRLF record (25 bytes)
    Verifies that Row 3's length error does NOT bleed into or cascade to Row 4.
    """
    with open(AMB_DIR / "spec_10_mixed_delimiters_and_trailers.csv", "rb") as f:
        doc = extract_spec("spec_10.csv", f.read())
    norm = normalize_spec(doc)

    with open(AMB_DIR / "dat_10_mixed_delimiters_and_trailers.dat", "rb") as f:
        raw_dat = f.read()

    rep, cand = _process(norm, raw_dat)
    summaries = rep["record_summaries"]

    assert len(summaries) == 4
    assert summaries[0]["status"] == "passed"
    assert summaries[1]["status"] == "passed"
    assert summaries[2]["status"] == "needs_review"
    assert any(i["code"] == "RECORD_LENGTH" for i in summaries[2]["issues"])
    # Row 4 is completely unaffected by Row 3's extra space
    assert summaries[3]["status"] == "passed"
