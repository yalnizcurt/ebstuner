# Architecture

The original FastAPI application, standard-library engine interfaces, CLI, fixtures and byte-patching approach are retained. The old regex-only parser was replaced because it could not distinguish inline field-list parentheses from conditional parentheses. No frontend build system, LLM router, external database or worker queue was introduced.

```text
Browser workstation (same-origin static HTML, CSS, JavaScript)
  -> authenticated FastAPI API (web.py)
     -> owner-scoped, versioned SQLite projects (projects.py)
     -> tokenized CTL compiler + three-state condition evaluator (compiler.py)
     -> raw record reader, field validation, exact byte patches (engine.py)
     -> bounded insertion beam + banded edit distance + scoring (reconstruction.py)
     -> all-sheet import, explicit keys, conflict/spec comparison (spreadsheets.py)
     -> selected capability context -> AmazonBedrockProvider (bedrock.py)
        -> strict response validation -> evidence/width/routing checks
        -> pending proposals -> explicit human review -> deterministic patches
     -> generation -> re-parse -> original/approved byte checks -> download
```

Compiler fields carry source declarations, positions, widths, datatypes, masks, constants, filler roles, null/default predicates and SQL metadata. Every supported destination is evaluated independently; unknown conditions remain unknown. Schema gaps do not become padding instructions automatically.

Project state stores immutable original bytes and working bytes as base64 in a private SQLite JSON document. Version-conditional UPDATEs prevent concurrent requests from losing updates. Record versions/checksums and superseding states prevent stale approvals; alternative reconstructions cannot be applied together. Bulk changes validate completely before the single save. Undo retains 20 change sets; the full project event audit remains until project expiry/deletion or the explicit storage limit. A no-op approval still completes its review state without changing bytes.

`models.py` validates public project, diagnostic, record-page and proposal response contracts while retaining explicit compiler extension metadata. The browser requests paginated diagnostic rows, a paginated cell grid and paginated proposals. Field cells carry branch identifiers. Explicit assumed-layout editing allows repairing an unclassified marker while keeping actual destination evaluation separate. Date/numeric issues remain visible during manual editing; validated export is blocked until structural errors are resolved. Source and working bytes remain independent.

Specialized AI capabilities share one provider and one bounded orchestration route. Schema/mapping calls receive metadata; investigation/explanation/recovery receive one selected record's findings. Values are masked by default. Semantic recovery requires explicit value-sharing consent and exact evidence from a unique key match; the backend validates every reference/value and cannot execute generated code or SQL. A single request produces at most one model call with bounded retries. No autonomous tool execution or recursive model calls exist.

Generation re-parses the exact working bytes, checks source round-trip fidelity, records field/routing differences, and verifies applied proposals against their exact serialized ranges. It preserves each record ending and original order. Semantic spreadsheet warnings can coexist with structural validation; unsupported CTL behavior blocks a validated download. Draft export is a separate explicit policy.

For deployment, static frontend assets are served by the backend, retaining same-origin cookies and avoiding a separate frontend-secret boundary. A TLS proxy can serve/cache those public static assets independently. Persistent storage belongs on a private encrypted volume. One API process is the supported topology: rate limits/caches are process-local. Add a durable processing queue, indexed record/blob storage and shared quotas before expanding the documented local workload limits or worker count.
