# Final engineering report — 15 October 2026

## Delivery status

The existing app has been extended into a functioning local CTL–DAT workstation. The documented fixed-width subset supports upload, schema inspection, diagnostics, deterministic repair candidates, spreadsheet reconciliation, approval, cell/bulk editing, undo/redo, byte inspection, generation and downloads. The original eight regression tests remain unchanged and pass.

This is **not a claim that every acceptance criterion is complete**: real Bedrock authentication/model access, Oracle SQL*Loader/EBS execution and a production deployment require external configuration or environments absent here. Enterprise identity, distributed processing and complete Oracle grammar coverage remain outside this bounded local implementation.

## Implemented and tested capabilities

| Capability | Status and evidence |
|---|---|
| CTL intelligence | Tokenized parser, grouped AND routing, multiple/unconditional destinations, quoted/comments/nested syntax, positions/relative lengths, source references, gaps, constants, fillers and null/default metadata. Unknown behavior explicitly diagnosed. Tests cover simultaneous destinations and repeated names |
| Raw DAT processing | Strict ASCII/latin1/cp1252, CR/LF scanner, fixed records, original bytes/checksum, exact field patches, leading zeros, signs, meaningful whitespace and unmapped-byte preservation |
| Constraints | Physical widths/missing bytes, real calendar dates, integer/decimal shapes, classification, unknown/unmatched routing, branch overlaps, unsupported syntax and key-based cross-record conflicts |
| Reconstruction | Positional extraction, trailing/boundary padding, bounded insertion beam, banded edit distance, type/routing hard constraints, exact sheet evidence and nearby-record comparison. Multiple hypotheses remain reviewable; missing values are never invented |
| Scoring | Configurable six-feature weights tested independently. Per-field source preservation, datatype, routing, spreadsheet evidence, edit cost and cross-record consistency; explicit breakdown and score separation. Tied or weakly typed candidates are flagged ambiguous |
| Spreadsheets | All bounded worksheets, header detection/manual mapping, multiple roles, unique exact joins, nulls, duplicates on both sides, unmatched records, financial conflict proposals, field-specification comparison, text/zero-formatted identifiers and decimal strings. Formula cells ignored with warnings |
| Bedrock provider | One AmazonBedrockProvider, native bearer resolver, Converse, constrained JSON/strict response tool modes, managed-file rotation, safe status/test screen, bounded context/tokens/retries/time, rate limits and usage/cost metadata. Native SDK signing and response/error contracts tested offline |
| Specialized AI workflows | Schema, mapping, investigator, recovery planner and explainer have separate capability instructions/context selection through one bounded orchestrator. Evidence-free recovery skips inference. Recovery references, field/record scope, types and routing are validated. Model inference quality is not measured by mocks |
| Human review | Proposed/pending/approved/rejected/applied/superseded states, original/proposed values, positions/evidence/expected bytes, bulk approve/reject, modified approvals, alternative/overlap rejection, project/record versions, checksum guards, reanalysis supersession and undo |
| Browser workstation | Workspace, schema byte maps, paged/filterable/sortable diagnostics, paged review queue, editable grid, record/type/column search, selected-row edits, padding choice, keyboard navigation, source/modified/suggestion indicators, validation details, assumed-layout marker correction, actual-byte hex and original/current views |
| Generation | Fresh output re-parse, applied-value byte verification, original/current checksum and field/routing comparison, unchanged-record count, exact endings/order, validated/warnings/blocked statuses, explicit draft policy, original/validation/audit downloads |
| Security/storage | Owner-scoped private SQLite, HttpOnly sessions, optional/production login gate, strict origins/hosts, CSP/escaping, request/upload/ZIP/nesting limits, safe error/correlation IDs, retention, atomic optimistic updates and private backend credentials |

## Modules changed

- `engine.py`: retained original helper/public/CLI entry points; extended encoding, record formats, types, physical validation and exact serialization. Source files cannot be overwritten by CLI output.
- `compiler.py`: tokenized IR compiler and supported condition grammar/evaluator.
- `reconstruction.py`: hard constraints, bounded reconstruction, banded distance and configurable scoring.
- `spreadsheets.py`: XLSX/CSV import, mapping, reconciliation and field specification comparison.
- `bedrock.py`: only LLM provider, strict schemas, capability prompts, native signing, rotation, connection and error handling.
- `projects.py`: persistent state, review/approval/evidence enforcement, undo/redo, version/checksum guards and export comparisons.
- `models.py`: typed public state, record, diagnostics and proposal response contracts.
- `web.py`: authenticated project API, upload/security boundaries, pagination and all workflow routes.
- `index.html`, `static/app.js`, `static/style.css`: integrated enterprise workstation; actual connected actions and an in-page confirmation dialog.
- `evaluate.py`, `tests/`, `fixtures/`, dependencies/lock, Docker/Compose, `.env.example`, README and documentation.

The supplied directory is not a Git checkout; changes were delivered directly in the shared repository directory. No customer source file was rewritten or external deployment performed.

## Automated verification

Final run: **84 passed, 1 skipped, 0 failures, 0 errors**, 3.405 seconds. The skipped test is the opt-in live Bedrock connection test. A test-framework deprecation warning about httpx is informational. JavaScript syntax checking passed.

See [machine-readable test results](test-results.xml). Tests cover original byte fidelity, field/route behavior, internal/trailing corruption, no truncation, invalid dates/numbers, unsupported syntax, encoding, large fields, multiple sheets, duplicates/conflicts, owner isolation, CSRF/body limits, stale edits/approvals, atomic bulk changes, original retention, undo/redo, draft policy, evidence hallucination rejection, strict AI output, invalid/expired key errors, throttling and timeouts. The actual native Boto3 bearer signer was tested with a synthetic token without network transmission.

Verified installed versions: FastAPI 0.143.2, Boto3 1.43.114, OpenPyXL 3.1.5, pytest 9.1.1. `requirements.lock.txt` contains the exact installed environment.

## Browser and CLI acceptance

The actual browser was used to create a project and upload the supplied CTL, damaged DAT and CSV. It displayed seven CTL destinations and six records, including record 3 at 91/105 bytes. The trailing-padding correction was explicitly reviewed and applied. A manual customer-number cell edit was saved and undone; source bytes stayed independent. The CSV reconciled two complete unique transaction keys with zero conflicts and four records without complete unique supplemental matches. The in-page confirmation dialog rendered correctly; cancelling reset preserved current bytes, and a confirmed reset was successfully undone. The byte inspector displayed the corrected 105-byte record and actual hexadecimal bytes.

Generation and browser download succeeded with zero structural findings. The downloaded file was compared on disk to `valid.dat` and is **byte-identical**. SHA-256:

`a45a1cf16ce5f02f55ce38c2e237a82738988bbaf6a592e74403fa92365f23ab`

The verified downloaded bytes are preserved as `fixtures/lockbox-browser-export.dat`. The original CLI's explicitly approved 14-space repair independently produced the same known-good bytes. Browser evidence is in [browser-verification.json](browser-verification.json) and [the verified export screenshot](studio-verified.png).

## Computed evaluation — synthetic-v1

| Metric | Observed result |
|---|---:|
| Structural classification on 18 explicit supported-check cases | 100.0% |
| Unchanged DAT round-trip preservation | 100.0% |
| Exact first-candidate reconstruction, 6 corruption cases | 33.3% |
| Exact known bytes present among returned candidates | 66.7% |
| Returned candidates | 14 |
| Deterministic exact-header mapping accuracy, two headers | 100.0% |
| Candidates requiring approval | 100.0% |
| Automatic repairs applied | 0 |
| Mean / maximum measured local case processing latency | 18.409 / 319.072 ms |

These are small synthetic-corpus measurements, not production accuracy claims. Insertion-only recovery cannot recreate missing non-space characters. Valid-looking character fields and multiple padding placements remain ambiguous. No automatic repair ran, so false-auto-repair rate is undefined. Live AI proposal acceptance, model quality, cost per analysis and live token usage are unmeasured. See [the full computed results](evaluation-results.json); no live Bedrock calls were made.

## Bedrock status

**Implemented and offline contract-tested; not live authenticated.** No backend key, AWS Region or accessible model was configured. The configuration screen reports this honestly. Connection testing sends synthetic data and checks the configured model, without guessing a model identifier. API-key/model access acceptance criteria remain externally unverified. See [configuration and AWS references](BEDROCK.md).

## Known limitations and production-readiness gaps

See the full [compatibility matrix](CTL_COMPATIBILITY.md) and [security/deployment boundaries](SECURITY.md). Major limits are the bounded fixed-width/single-byte subset; no Oracle SQL/NLS/business execution; insertion-only deterministic recovery; exact keys without fuzzy financial identity matching; formula cells not executed; ≤12 MB DAT/sheet and ≤20,000 records; bounded synchronous reconstruction; SQLite snapshots; 20 undo sets; and process-local quotas. Unsupported syntax blocks a validated export, while original bytes remain downloadable.

Shared-token/owner-cookie sessions are not enterprise SSO/RBAC or account recovery. Encryption at rest, organizational identity, TLS termination, a real managed short-term credential issuer/refresher, protected AWS logging configuration, backups, shared quotas, durable workers and a production load/security review must be supplied/verified before organizational deployment. Docker files are provided but Docker was unavailable on this host, so container builds and remote deployment are untested. Oracle/EBS load success is not claimed.

## Run and configure

Start locally with `.venv/bin/python -m uvicorn web:app --host 127.0.0.1 --port 8000 --no-access-log`, then open [the workstation](http://127.0.0.1:8000). Use the [README](../README.md) for a fresh environment. Configure the three required Bedrock variables only in the backend, or supply the private managed token file for rotation. `.env.example` contains names with empty values only. Deterministic mode continues without AWS. Production startup additionally requires the configured HTTPS origin, trusted hosts and private access gate described in the security document.
