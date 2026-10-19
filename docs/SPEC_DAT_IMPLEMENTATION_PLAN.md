# DAT Studio improvement plan

Updated: 19 October 2026. Based on the application review and reproduced defects.
This replaces the earlier plan. The changes below are planned, not implemented.

## 1. Product outcome

**Upload specification documents and DAT → Resolve exceptions → Download a specification-aligned DAT.**

The same engine must support different record identifiers, layouts, field counts, lengths, padding conventions and supported types from the approved specification. A valid input downloads unchanged. A repairable input has a working correction path. An unsafe or unresolved input explains exactly what is missing.

Never invent specification values, silently weaken approved rules, or automatically change meaningful DAT values. The user's decision to retain the existing `4` / `04` conflict remains in force; it must remain visible and block conformity until explicitly resolved.

## 2. One user journey

| Step | User experience | Application responsibility |
| --- | --- | --- |
| Upload | Select one or several specifications and DAT files; select **Check files** | Preserve originals, extract rules, identify layouts and show coverage |
| Resolve exceptions | Review grouped problems with a direct **Resolve** action | Validate, propose safe mappings, collect missing decisions and recheck results |
| Download | Select **Download aligned DAT** | Validate exact output bytes; include a concise change and coverage report |

One file and several files use this same journey. Allow missing specifications to be added to an existing project without restarting. Keep the full 23-column master template, byte inspector and AI evidence under **Details**.

Retain the blue, orange and green design. Pair colours with clear status labels. Show the next required action instead of a large unexplained error count.

## 3. Specification model

Extend the existing model only where necessary:

| Level | Defines |
| --- | --- |
| File | Encoding, record boundaries, line endings and supported framing |
| Record | Identifiers, routing conditions, length and applicable fields |
| Field | Stable ID, position, width, datatype, mandatory status, padding and constraints |
| Relationships | Declared ordering, groups, parent-child links, counts and totals |

Separate physical DAT fields from Oracle-generated values and database conversion metadata. Preserve duplicate names through stable field and layout IDs. Samples remain examples unless explicitly declared as constants or allowed values.

Classify applicable requirements as executable, needing clarification, unsupported, or dependent on the target environment. Preserve Unknown mandatory statuses and their acknowledgment; acknowledgment does not establish full coverage.

Arbitrary record names and layouts must require no code changes. Supported format families and resource limits must remain explicit. Reject unsupported framing or encoding before guessing how to read the data.

## 4. Implementation milestones

### 1 — Make specification approval trustworthy

- Connect existing document/worksheet routing extraction to the actual upload paths. Reconcile it with master-template routing.
- Preserve extraction errors through import, revision, approval and compilation. Missing source rows cannot disappear from approval checks.
- Retain numeric precision and scale. Distinguish target `NUMBER(p,s)` from SQL*Loader external field-width syntax. Reject unsupported semantics until implemented.
- Surface uncompiled requirements in extra columns and prose; retained metadata is not verified coverage.
- Share normalization and compilation between single-file and multi-file workflows.

**Acceptance:** A source condition requiring `AA` rejects `BB`; an unnamed nonempty source row blocks approval; `NUMBER(3,2)` cannot accept `123456`; unsupported requirements remain visible.

**Code:** `specs.py`, `validation_pipeline.py`, `studio.py`, `batch_studio.py`.

### 2 — Complete the structural correction workflow

- Reuse TASAA signatures, positional checks, anchors, alternatives and beam search.
- Add one mapping-resolution action: source ranges, proposed field boundaries, preserved values, padding changes and reconstructed preview.
- Save decisions against source checksum and specification version. Reject stale decisions.
- Verify every source byte is accounted for and meaningful values are preserved. Human approval cannot waive these checks.
- Keep explicit value edits separate, with audit history and fresh validation.
- When no defensible mapping exists, identify the missing evidence or corrected source needed. A search limit must not imply that repair is impossible.

**Acceptance:** Missing padding completes through preview, approval and download. Ambiguity resolves only with sufficient evidence. Confirmation alone cannot clear uncertainty, and unexplained non-padding bytes cannot be discarded.

**Code:** `tasaa.py`, `specs.py`, studio services and `static/app.js`.

### 3 — Validate the complete file

- Implement declared ordering, required record occurrences, group membership, parent-child references, counts and control totals.
- Check mixed layouts together in original order, including relationships across specifications.
- Identify Oracle lookups, database sequences and environment-dependent checks separately.
- Do not automatically invent missing business records or rewrite amounts, counts or totals. Require an evidenced user correction when needed.
- Revalidate field, record and file rules against the exact reconstructed bytes.

**Acceptance:** Individually valid rows fail when a declared trailer is missing, a relationship is broken or a total is wrong. Complete mixed-record files pass without hardcoded business identifiers.

**Code:** `validation_pipeline.py`, `specs.py`, `batch_studio.py`.

### 4 — Make AI review bounded and recoverable

- Keep Groq for local testing and retain the configured production provider path.
- Keep full byte ledgers locally. Reuse compact evidence packaging and durable jobs.
- Split oversized evidence into bounded requests with coverage tracking; replace permanent evidence-size failures with resumable processing.
- Allow complete machine-readable specifications to proceed through deterministic checks and human approval. Use AI for interpretation and independent review of uncertainty; required uncertainty reviews remain pending until complete.
- Reconcile AI advice deterministically. Specification changes require new approval; AI cannot change values or waive constraints.
- Resume remaining work after failures and invalidate affected reviews after edits. Never present partial evidence as complete coverage.

**Acceptance:** A valid 180-field, 20,016-byte record completes required review and download. Retries resume saved progress. Unsupported or unsafe AI proposals are rejected.

**Code:** `alignment_audit.py`, `studio.py`, `batch_studio.py`, existing provider and budget modules.

### 5 — Unify the screens

- Replace separate single-file and batch journeys with the three steps above, using shared services. Preserve existing projects and CTL compatibility.
- Group repeated findings by cause. One shifted boundary should lead to one alignment decision, with dependent errors available in Details.
- Give each blocker a working action: attach a layout, clarify a rule, review a mapping, correct a value or retry processing.
- Distinguish unsupported formats/rules, incomplete or contradictory specifications, ambiguous mappings, invalid data and processing failures.
- Show one download action, approved specification version, changes and coverage. Relevant edits invalidate prepared output.

**Acceptance:** Users complete the journey without entering JSON, counting spaces or switching workspaces. Every displayed action works. Saved reviews reopen with decisions and remaining work intact.

**Code:** `index.html`, `static/app.js`, `static/style.css`, studio HTTP handlers.

## 5. Verification and release gate

Add focused regression tests with each milestone. Finish with browser tests that compare downloaded bytes with expected output.

| Case | Required result |
| --- | --- |
| Compliant DAT | Byte-identical output, including record endings |
| Verified missing/extra padding | Expected repair; meaningful values unchanged |
| Ambiguous mapping | Blocked until a valid evidenced decision is saved |
| Missing or conflicting specification | Exact source issue; no invented values |
| Mixed record types | Every record routed once; declared relationships checked |
| Unsupported format/rule | Explicit unsupported result; no false validation claim |
| Invalid value/unexplained bytes | Actionable finding; no automatic substitution or deletion |
| AI outage, oversized evidence, restart | Saved progress resumes; coverage remains truthful |
| Changed specification or data | Affected old approvals and reviews invalidated |

Use at least five independently generated specifications with different identifiers, layouts, field counts, padding, types and lengths. Include 180+ fields and records exceeding 20,000 bytes. Exercise unchanged output, provably repairable input, human-resolved ambiguity and justified rejection. Returning no candidate for every damaged case is not successful generalization.

Retain Scenario 3/OVP and the supplied standalone-deduction files as regressions. Include duplicate field names, missing padding, type mismatches, precision/scale, routing metadata and file totals. Add property-based byte-accounting and preservation tests. Browser tests must cover correction, save/reopen and download, using synthetic AI responses for routine runs.

Release only after supported upload-to-download journeys pass. Report results by behavior, downloaded-byte comparisons and remaining limitations. Test counts alone are insufficient.

## 6. Keep the implementation small

Reuse current persistence, providers, validation and byte-preservation machinery. Consolidate duplicated workflow code as shared paths replace it. No new frontend framework, database or demonstration application is needed.

The deliverable is a DAT conforming to the approved supported specification, with a concise audit. Direct EBS submission and additional document/binary format families are separate extensions. Actual Oracle acceptance requires the target control file, configuration and import run.

References: [Oracle transmission formats](https://docs.oracle.com/cd/E26401_01/doc.122/f10310/T447343T454906.htm), [AutoLockbox validation](https://docs.oracle.com/cd/E18727_01/doc.121/e13522/T355475T382159.htm).
