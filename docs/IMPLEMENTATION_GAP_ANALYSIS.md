# Repository audit — 15 October 2026

The starting repository contains a standard-library deterministic engine, FastAPI backend, single HTML/JavaScript UI, one synthetic multi-table Lockbox CTL, two DAT files, a CSV and eight pytest tests. All eight original tests pass. There is no Git repository in the supplied directory. The host Python has pytest but lacks the web dependencies; an isolated development environment will be created.

## Reliable capabilities to preserve

- `strip_sql_comments`, `split_top_level` and `extract_parens` respect quoted strings and nested parentheses.
- Inclusive byte offsets, strict field overflow checks, in-place byte patches, CRLF preservation, multiple destination matches and original CLI interface.
- FastAPI and the browser workstation's navigation and visual language.
- The supplied fixtures and eight regression tests.

## Defects and missing capabilities

| Area | Existing behavior / risk | Implementation scope |
|---|---|---|
| CTL | Regex branch detection, mandatory WHEN and standalone field list; ignores constants/transforms; no relative positions, source references or global compatibility checks | Tokenized compiler and condition grammar; explicit diagnostics; metadata, gaps and null/default rules |
| DAT | ASCII edits, permissive decoding, `splitlines` accepts non-record separators; superficial date checks | Explicit single-byte encoding, CR/LF scanner, real calendar/type validation, fixed-record support, unchanged byte fidelity |
| Reconstruction | Only append-spaces candidate, hardcoded score 70 | Bounded insertion beam, edit distance, hard constraints, independently configurable scoring and ambiguity |
| Sheets | Active sheet / first 50 rows only; numeric/string conversion and no reconciliation | Bounded all-sheet import, preserved text and number formats, header detection, manual mapping, key matching, duplicate/conflict evidence |
| AI | None | Single AmazonBedrockProvider, native bearer authentication, Converse, schema validation, five bounded reasoning capabilities, no provider fallback |
| Review | Direct edits and one pad action, no stale-edit guard or undo | Versioned persistent projects, evidence-backed proposals, explicit approval/rejection, atomic bulk changes, undo/redo and checksums |
| Security | Unauthenticated in-memory tokens in URLs; no origin, request-size or project-owner protection | HttpOnly owner sessions, project authorization, CSRF/origin and host checks, upload limits, retention, safe errors and production startup gates |
| Export | Labels any supported subset pass as checked; unverified download always enabled | Fresh round-trip validation, unsupported-feature blocking, explicit draft policy, separate original/validation/audit downloads |
| UI | Renders all records, no mapping/config screens, no byte map/hex/diffs | Paginated diagnostics/grid, keyboard and bulk editing, byte inspector, schema map, repair evidence/history, Bedrock connection screen |
| Operations | Unbounded memory sessions, no deployment/evaluation documentation | SQLite persistence, isolated startup, Docker, synthetic evaluation metrics and compatibility/security/engineering reports |

## Change reach and data risks

Changes reach the engine's public functions and CLI, every backend caller, UI requests and rendering, regression tests, demo fixtures, dependencies, exports and documentation. The parser's implementation must expand because the existing regex entry points cannot safely distinguish WHEN parentheses from inline field lists. Public engine entry points remain compatible. No customer source files will be rewritten; originals remain downloadable. Financial values must never be truncated, guessed, silently reconciled, auto-approved or logged. Unknown CTL semantics must block validated export. Successful structural validation must never imply Oracle EBS load acceptance.

Live Bedrock authentication and regional model access can only be verified with backend credentials and a model configured by the operator. Automated AI tests will use explicit mocks and report live tests as skipped when not configured.
