# Real Files and Ambiguous Specification/DAT Test Results

**Date:** October 20, 2026  
**Test Suite:** [`tests/test_real_and_ambiguous_scenarios.py`](file:///Users/srikarvuyyuru/Downloads/ebs/tests/test_real_and_ambiguous_scenarios.py)  
**Total Tests:** 16 Passing (Repository Total: 389 passing, 8 skipped)  
**Execution Time:** 0.84s (Suite) / 74.44s (Full Repository)

---

## 1. Real Files Testing

We tested the pipeline directly against the real production-like lockbox specifications and synthetic bank transmission DAT files in the repository:

### Test Inputs
- **Specifications (`fixtures/specifications/`):**
  - [`Scenario_1_Standalone_Deduction_STD.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/specifications/Scenario_1_Standalone_Deduction_STD.csv) (63 fields, 55 physical positions, length 3,303 bytes)
  - [`Scenario_2_Standard_Payment_STP_CMDM.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/specifications/Scenario_2_Standard_Payment_STP_CMDM.csv) (63 fields, 55 physical positions, length 3,303 bytes)
  - [`Scenario_3_Over_Payment_OVP.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/specifications/Scenario_3_Over_Payment_OVP.csv) (63 fields, 55 physical positions, length 3,303 bytes)
- **DAT Files (`outputs/lockbox-examples/DAT_Files/`):**
  - [`SYN_STD_STANDARD_DEDUCTION.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/outputs/lockbox-examples/DAT_Files/SYN_STD_STANDARD_DEDUCTION.dat) (7 records, 10,424 bytes)
  - [`SYN_STP_STANDARD_PAYMENT.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/outputs/lockbox-examples/DAT_Files/SYN_STP_STANDARD_PAYMENT.dat) (7 records, 10,413 bytes)
  - [`SYN_OVP_OVERPAYMENT.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/outputs/lockbox-examples/DAT_Files/SYN_OVP_OVERPAYMENT.dat) (7 records, 10,348 bytes)

### Key Real-File Findings & Invariant Verifications

1. **Mandatory Certification Gate (`REQUIRED_RULE`):**
   - The real CSV specifications omit the `Mandatory` column (55 physical fields default to `UNKNOWN`).
   - By design, [`validate_spec`](file:///Users/srikarvuyyuru/Downloads/ebs/specs.py) emits **55 blocking errors** (`REQUIRED_RULE`), preventing false certification of unverified mandatory requirements.
   - When the consultant explicitly sets `options['acknowledge_unknown_mandatory'] = True`, the errors become **55 explicit warnings** and are permanently tracked as `coverage_limitations`, allowing safe inspection without hiding the limitation.

2. **Multi-Record Lockbox Routing & Unknown Record Preservation:**
   - Real Lockbox DAT files contain multi-record transmission streams:
     - Record `01`: Transmission Header (95 bytes)
     - Record `05`: Batch Header (114 bytes)
     - Record `06`: Remittance/Payment (3,343–3,368 bytes)
     - Record `04`: Deduction/Overflow Records (3,304–3,350 bytes)
     - Record `07`: Batch Trailer (94 bytes)
     - Record `09`: Transmission Trailer (69 bytes)
   - When configured with routing condition `RECORD_TYPE = '04'` and `unknown_records: 'preserve'`:
     - Records `01`, `05`, `06`, `07`, `09` are preserved verbatim with status `preserved` and issue code `UNKNOWN_LAYOUT`.
     - Records `04` (lines 4 & 5) are routed to the deduction layout.

3. **Byte Accounting & Non-Destructive Integrity:**
   - Record `04` lines have 3,349/3,350 bytes in the DAT file versus 3,303 bytes in the CSV spec (a 46/47 byte trailing discrepancy in the raw sample).
   - Rather than silently trimming or corrupting trailing bytes, the engine flags `UNCERTAIN_BOUNDARIES` and `MANDATORY_UNVERIFIED`, maintaining **100% source byte accounting** (`len(candidate) == len(raw_dat)`).

4. **Studio HTTP API End-to-End Workflow:**
   - Full test via [`TestClient(web.app)`](file:///Users/srikarvuyyuru/Downloads/ebs/tests/test_real_and_ambiguous_scenarios.py):
     - Created project -> Uploaded real `Scenario_1.csv` and `SYN_STD.dat` -> Verified 55 `REQUIRED_RULE` errors -> Acknowledged mandatory options via PUT -> Verified 0 errors and 55 coverage limitations -> Downloaded original DAT & spec source with 100% byte fidelity.

---

## 2. The 10 Ambiguous Scenarios

All 10 scenarios have standalone CSV specifications and matching DAT files under [`fixtures/ambiguous_scenarios/`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/):

| # | Scenario Name | Ambiguity Type | Spec File | DAT File | Verified Result |
|---|---------------|----------------|-----------|----------|-----------------|
| **1** | **Layout Prefix Collision** | Overlapping routing conditions | [`spec_01_layout_prefix_collision.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_01_layout_prefix_collision.csv) | [`dat_01_layout_prefix_collision.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_01_layout_prefix_collision.dat) | Flagged `AMBIGUOUS_LAYOUT`, `needs_review`, `certified=False`, 0 guessed layouts, 100% original bytes preserved. |
| **2** | **Adjacent Unanchored CHAR** | Boundary blur between text fields | [`spec_02_adjacent_unanchored_char.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_02_adjacent_unanchored_char.csv) | [`dat_02_adjacent_unanchored_char.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_02_adjacent_unanchored_char.dat) | TASAA accounts for 30/30 bytes. Shifted row detects `SPEC_CONFLICT` with `REQUIRED` blank violation on `LAST_NAME`. |
| **3** | **Unknown Mandatory Gate** | Missing mandatory status on critical fields | [`spec_03_unknown_mandatory_gate.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_03_unknown_mandatory_gate.csv) | [`dat_03_unknown_mandatory_gate.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_03_unknown_mandatory_gate.dat) | Unacknowledged: 3 blocking `REQUIRED_RULE` errors. Acknowledged: `MANDATORY_UNVERIFIED` limitation tracks on every record; prevents false 100% compliant claim. |
| **4** | **Numeric Scale & Format Conflict** | Explicit decimal dot vs implied scale in `NUMBER(8,2)` | [`spec_04_numeric_scale_conflict.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_04_numeric_scale_conflict.csv) | [`dat_04_numeric_scale_conflict.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_04_numeric_scale_conflict.dat) | Tracks precision=8, scale=2. Positional dot preserved. Displaced padding flags `UNAPPROVED_REFORMAT` & `UNCERTAIN_BOUNDARIES`. |
| **5** | **Internal Whitespace Retention** | Meaningful multi-spaces in character fields | [`spec_05_internal_whitespace_preservation.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_05_internal_whitespace_preservation.csv) | [`dat_05_internal_whitespace_preservation.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_05_internal_whitespace_preservation.dat) | Multi-spaces in `ACME   SUPPLY   CORP` never stripped or collapsed. Byte-for-byte candidate identity preserved. |
| **6** | **Anchor Displacement Beam Search** | Shifted constant token in repeated anchor layout | [`spec_06_anchor_displacement_beam.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_06_anchor_displacement_beam.csv) | [`dat_06_anchor_displacement_beam.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_06_anchor_displacement_beam.dat) | Row 1: `POSITIONALLY_COMPLIANT`. Row 2 (displaced `TAG:`): TASAA reports `SPEC_CONFLICT`, `anchor_conflict` penalty, alternative candidate ranking, `certified=False`. |
| **7** | **Truncated Record Mid-Field** | Record cutoff mid-field (23 of 40 bytes) | [`spec_07_truncated_record_mid_field.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_07_truncated_record_mid_field.csv) | [`dat_07_truncated_record_mid_field.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_07_truncated_record_mid_field.dat) | Flagged `UNCERTAIN_BOUNDARIES`, `UNRECOVERABLE`, `needs_review`. Does NOT fabricate filler bytes; accounts for all 23/23 bytes. |
| **8** | **Ambiguous Date Formats** | Bare `DATE` without format mask | [`spec_08_ambiguous_date_formats.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_08_ambiguous_date_formats.csv) | [`dat_08_ambiguous_date_formats.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_08_ambiguous_date_formats.dat) | Bare `DATE` rejected with `UNSUPPORTED_CONVERSION`. Format mask `YYYYMMDD` strictly fails `02012024` & `99999999` while passing `20240201`. |
| **9** | **Allowed Values Enum Mismatch** | Domain enum violations (`CRD`, `DBT`, `REV`) | [`spec_09_allowed_values_enum_mismatch.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_09_allowed_values_enum_mismatch.csv) | [`dat_09_allowed_values_enum_mismatch.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_09_allowed_values_enum_mismatch.dat) | Rows with `XXX`, lowercase `crd`, or `?` flag `ALLOWED_VALUES` constraint violations without silent coercion. |
| **10** | **Mixed Delimiters & Trailers** | Stream isolation with mixed CRLF & LF | [`spec_10_mixed_delimiters_and_trailers.csv`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/spec_10_mixed_delimiters_and_trailers.csv) | [`dat_10_mixed_delimiters_and_trailers.dat`](file:///Users/srikarvuyyuru/Downloads/ebs/fixtures/ambiguous_scenarios/dat_10_mixed_delimiters_and_trailers.dat) | Row 1 (CRLF) passes; Row 2 (LF) passes; Row 3 (stray space) flags `RECORD_LENGTH`; Row 4 (CRLF) passes without cascade corruption. |

---

## 3. Verification Command

To rerun this test suite at any time:
```bash
./.venv/bin/python -m pytest tests/test_real_and_ambiguous_scenarios.py -v
```
To run the full project test suite:
```bash
./.venv/bin/python -m pytest -q
```
Result: **389 passed, 8 skipped in 74s**.
