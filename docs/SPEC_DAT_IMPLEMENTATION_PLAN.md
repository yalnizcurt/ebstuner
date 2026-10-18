# Spec–DAT Validation Studio: implementation plan

Status: proposed plan for review. This document does not authorize implementation or change application behavior.

## 1. Product outcome

Help a consultant turn a mostly correct but poorly formatted DAT file into a reviewed file that conforms to an approved specification, without counting spaces or editing raw records.

The primary workflow is:

**Upload spec + DAT → AI interprets spec → consultant corrects and saves spec → analyze DAT → review values beside spec → system formats and validates → download DAT.**

The product must work with uploaded CSV, XLSX, and Markdown specifications. The three supplied scenarios are regression examples, not built-in business rules. A CTL upload is not required for this workflow.

## 2. Consultant experience

Use a restrained enterprise finance layout informed by the public HighRadius and BlackLine screens: compact navigation, a small action toolbar, a large review table, clear statuses, and visible issue resolution. Keep technical diagnostics out of the primary workflow. Show tables and explanations rather than JSON or prompts.

### Upload

- Accept one spec document and a DAT file; identify relevant worksheets or sections when necessary.
- Preserve the uploaded documents and original DAT bytes before analysis.
- Show file validation, analysis progress, queued AI work, and recoverable failures in plain language.

### Review spec

AI interprets the source into a draft table containing every supplied field and these ten columns:

1. Field Name
2. Category / Functional Group
3. Mapping Type
4. Start Pos
5. End Pos
6. Length
7. Data Type / Oracle Conversion Function
8. Padding / Alignment Rule
9. Raw Sample Value in DAT File (Quoted)
10. Cleansed Output in Oracle Staging Table

Preserve the source text and location behind each interpretation. Highlight missing, ambiguous, or contradictory rules. Do not fill uncertainty with invented values. Keep extra source metadata available for review.

The consultant can correct the draft and select **Save approved spec**. Store an immutable approved version, its interpreted rules, the source document identity, and who approved it. Validate positions and lengths before approval; do not silently renumber positions when a field changes.

### Review DAT

Keep the ten spec columns and append:

| Added column | Behavior |
| --- | --- |
| Original DAT value | Read-only source reading; unclear boundaries are labelled Needs review |
| Value to use | Editable data only; blank is distinguished from a missing required value |
| Spec match | Passed and total applicable checks, plus percentage when all checks are evaluable |
| Issue / action | Plain-language failure and an explicit action where needed |

Freeze the field name, support horizontal table scrolling, and offer a record selector plus **Next issue**. Keep original and edited values distinguishable. Support keyboard editing, undo, and saving/reopening work. The consultant should not need to choose alignment separately for each edit: it comes from the approved spec.

### Validate and download

The system builds the candidate DAT, validates those exact bytes, and enables **Download DAT** only when the current spec is approved, required AI stages are complete, required checks pass, and unresolved interpretations are cleared. Any subsequent edit invalidates the downloadable result.

## 3. AI responsibilities and API budget

Use Groq `openai/gpt-oss-120b`. AI is a required pipeline stage for spec interpretation and DAT analysis; an unavailable required AI stage remains pending rather than being presented as complete. A compatible, unchanged cached AI result can satisfy that stage.

Parse CSV/XLSX/Markdown locally first so exact source values do not need to be rewritten by the model. Ask AI to interpret headings, mapping semantics, unclear rules, and relevant field groups. For DAT analysis, send compact layout information, grouped findings, and representative records or affected slices. Every DAT record still receives local validation, even if only representative evidence is sent to AI.

AI proposes typed, declarative validation rules and explanations. The application validates the response against a strict internal contract and executes only supported rule types. Model-generated code, SQL, and file-writing instructions are never executed. Original values and proposed repairs must refer to known source records and fields; stale or unsupported responses are rejected.

| Control | Planned behavior |
| --- | --- |
| Request ceiling | Input plus reserved completion must stay at or below 8,048 tokens, including instructions, schemas, and reasoning |
| Working budget | Initially target approximately 5,000 input and 2,000 completion tokens; reduce payloads to leave headroom |
| Token measurement | Use a model-compatible local tokenizer and verify estimates against returned usage; never substitute a byte-length cap for token accounting |
| Normal call target | Approximately one spec-analysis call and one DAT-review call for small inputs; additional bounded batches when completeness requires them |
| Editing | No API call on each keystroke, cell focus, navigation, or unchanged refresh |
| Batching | Group related fields and duplicate issues; track coverage so no field disappears between batches |
| Caching | Key results by organization/project scope, relevant content hashes, model, and interpretation/rule version; invalidate affected results after changes |
| Scheduling | Shared, durable queue; initially one active Groq request per configured credential scope |
| Quotas | Reserve the request budget before dispatch; enforce request, token-per-minute, and daily limits across users and application workers |
| Throttling | Respect response quota/reset headers and Retry-After; show queued status rather than repeatedly calling the provider |
| Retries | Bounded transient retries through the same queue; no retry loop for authentication, invalid schema, or permanent failures |
| Audit | Record call purpose, result status, usage, and relevant versions without logging keys or unnecessary financial values |

Do not make routine live API calls during development tests. Use recorded synthetic responses and a small, explicit live acceptance run.

## 4. Spec and file validation rules

The saved spec is authoritative. Sample values illustrate a format unless the spec explicitly defines a constant or allowed value.

- Model applicable layouts and routing conditions for different record types. Do not assume that every line has the same layout or that the first two characters always identify its type.
- Distinguish physical fields, filler/reserved areas, constants, and system-generated mappings. A generated mapping does not require an artificial DAT position.
- Check position, width, padding, requiredness, text/numeric/date format, and permitted values where specified. Flag unexplained overlaps and conflicting writes; do not reject legitimate shared source slices merely because two mappings read them.
- Keep the source datatype/conversion text. Separate it internally into supported checks and conversion behavior. Unsupported functions or environmental dependencies remain explicitly unresolved.
- Confirm file encoding, record format, length rules, and line endings. Oracle POSITION offsets are byte based; do not equate characters and bytes for multibyte input.
- Preserve leading zeros in identifiers and use exact decimal handling for money.
- Reject overlength values, unencodable characters, and line breaks or control characters that violate the approved layout. Never silently truncate, round, replace, or invent business values.
- Apply transformations only when the approved spec requires them. A local staging-value preview must be labelled as a preview, not a database result.

### Data-only editing

Remove surrounding space padding from the editable value and visibly normalize pasted boundary spaces. Preserve permitted internal spaces. Blank filler and padding bytes are system managed. Reject prohibited internal whitespace instead of silently deleting it.

The formatter uses the approved rules to place each value and fill required spaces or other explicitly defined padding. For example, a 20-byte field containing `ABC Trading` gets nine trailing ASCII spaces when its rule requires left alignment and right space padding. The consultant enters only `ABC Trading`.

If damaged spacing makes field boundaries uncertain, show the positional reading and proposed interpretation separately. Require confirmation before applying a proposed relocation. Preserve unrelated bytes and record order; recalculate any specified count or total through an approved rule and review it when affected.

### Match percentage

For an evaluable field, calculate `passed applicable checks / total applicable checks × 100`, with the check list available to the user. Recalculate the working score after edits and system formatting while preserving the original reading and its initial findings.

Do not display AI self-reported certainty as spec match. Unknown checks produce **Needs review** or **Not verified**, and inapplicable checks are excluded. Zero applicable checks do not produce 100%. File results must disclose unresolved checks and blockers; a high average cannot permit a failed required field to export.

## 5. Implementation sequence and completion gates

| Phase | Deliverable | Completion gate |
| --- | --- | --- |
| 1. Agree on behavior | Confirm unresolved cases and the table workflow; define supported conversions and deployment scale | Product decisions below are recorded before dependent implementation |
| 2. Import and spec interpretation | Bounded CSV/XLSX/MD extraction, AI interpretation, source references, editable ten-column table | All source fields are accounted for; ambiguity prevents approval |
| 3. Approval and persistence | Versioned approved specs, project isolation, immutable sources, restart-safe state | Reopening reproduces the approved version; stale changes are rejected |
| 4. Validation and DAT review | Approved declarative rules, layout routing, per-field results, batched AI analysis, merged table | Every record has a known validation outcome; uncertain extraction is visible |
| 5. Editing and generation | Data-only inputs, automatic formatting, undo, exact-byte validation, download gate | Exported bytes are the same bytes that passed validation; editing revokes old results |
| 6. Production readiness | Identity/access integration, durable AI queue, operational controls, consultant acceptance | Security, recovery, rate-limit, correctness, usability, and load acceptance checks pass |

Implement in the existing application rather than maintaining a separate demonstration. Preserve existing projects and CTL APIs during migration; the new consultant workflow becomes the main entry point. A CTL may remain an advanced compatibility input, but it must not silently override an approved spec.

### Existing code to reuse and extend

| Area | Existing locations | Intended change |
| --- | --- | --- |
| Source import | spreadsheets.py | Reuse bounded CSV/XLSX extraction; add Markdown and spec-specific extraction without breaking evidence imports |
| Spec and response contracts | models.py; compiler.py where applicable | Add approved spec/rule contracts; adapt supported rules to the existing engine without executing generated SQL |
| State and audit | projects.py | Persist source documents, spec versions, AI jobs/results, approvals, and invalidation rules; use transactional updates |
| AI integration | groq_provider.py; ai.py; existing response-contract helpers | Add spec/DAT analysis contracts, token budgeting, batching, validated caching, and quota-aware scheduling |
| HTTP workflow | web.py | Add spec upload/review/approve and analysis-job endpoints; enforce authorization and export gates server side |
| Consultant interface | index.html; static/app.js; static/style.css | Replace the primary CTL-first journey with three simple stages and the merged review table |
| Verification and guidance | tests/; .env.example; README.md; docs/ | Update fixtures, meaningful regression tests, safe configuration examples, API contracts, deployment and recovery instructions |

Use the existing table/editor capabilities first. Select a different grid library only if measured usability or supported volume requires it, with licensing reviewed before adoption.

## 6. Production acceptance

Do not describe the application as enterprise ready solely because it has enterprise styling. Before a production release, verify:

- Organization sign-in and project permissions, with attributable approvals and no cross-project or cross-organization cache leakage. A shared workspace password alone is insufficient for user-level audit.
- Private backend credentials, TLS, bounded uploads and decompression, escaped source/model text, protected write endpoints, and no execution of spreadsheet formulas or model instructions.
- Immutable originals, transactional edits, spec/data version checks, accurate audit records, and no retention cleanup that silently removes active or approved work. Define retention with the operator.
- Durable jobs that survive restarts, duplicate-submit protection, shared quota reservations, Retry-After handling, and safe recovery after provider or worker failure.
- A documented supported file size, row count, concurrent-user envelope, and response-time target established by load testing. Reuse current storage within verified limits; change storage when the agreed workload requires it.
- Backup and restore verification, redacted operational logs, health/readiness checks, monitoring of AI queue/usage/failures, deployment configuration, migration and rollback procedures.
- Consultant acceptance of the complete upload-to-download flow using synthetic examples and at least one additional unfamiliar spec layout.

Required correctness cases include every one of the ten columns, multiple worksheets/Markdown sections, missing metadata, conflicting lengths, generated mappings, multiple record types, multibyte input, permitted internal spaces, overlength values, invalid numbers/dates, 4,800-byte fields/records where applicable, damaged internal spacing, unsupported conversions, stale approvals, and unchanged-file round trips.

Provider tests cover request token ceilings, reasoning/output reservation, oversized-spec batch coverage, malformed or truncated responses, cache invalidation, duplicate requests, shared quotas, daily exhaustion, 429 cooldowns, and restart recovery without consuming the live free-tier quota.

## 7. Decisions to settle before implementation

1. Confirm that raw and cleansed sample values are examples unless explicitly declared constants.
2. Confirm that AI may propose displaced-field interpretations but a consultant must approve their application.
3. Decide how headers, trailers, or other records absent from the uploaded spec are handled. Proposed safe default: preserve original bytes, flag coverage as incomplete, and block validated download until the handling is explicitly approved.
4. Confirm one-record-at-a-time review beside the spec, with Next issue navigation, as the initial interaction.
5. Define deployment identity, expected concurrent consultants, representative file volumes, encoding expectations, and retention requirements before claiming production readiness.

No EBS submission or database execution is included in this plan. Spec conformity and business-data correctness are separate claims; an actual SQL*Loader/EBS acceptance test requires the target configuration and environment.

## References

- [HighRadius public interface guide](https://cloud5g.highradius.com/RRDMSProject/help/global/Content/Order%20to%20Cash/Collections%20Management/Updated%20Collections%20Cloud/Navigating%20the%20System.htm)
- [BlackLine public product screen](https://a.storyblok.com/f/299767/1175x710/77b42d18a6/blackline-cash-application-revolutionary-tech.jpg/m/0x400)
- [Groq rate limits and response headers](https://console.groq.com/docs/rate-limits): account-specific limits and response headers take precedence over published defaults.
- [Groq GPT-OSS 120B model](https://console.groq.com/docs/model/openai/gpt-oss-120b)
- [Oracle SQL*Loader field list reference](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-sql-loader-field-list-contents.html)
