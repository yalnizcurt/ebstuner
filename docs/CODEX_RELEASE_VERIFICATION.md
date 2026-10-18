# Independent DAT Studio release verification

18 October 2026. This assessment supersedes the release verdict in `PRODUCTION_READINESS_ASSESSMENT.md`; that document is retained as historical handover evidence. **Verdict: suitable for continued development and a controlled synthetic-data pilot, not certified for enterprise financial production or Oracle EBS acceptance.** The multi-file milestone update below supersedes the older single-file limitations recorded later in this report.

## Multi-file milestone update

An additive batch workspace now supports repeated CSV/XLSX specification and DAT uploads in one project. Users review and approve each specification independently, set explicit DAT-to-spec/layout associations, and inspect paginated records and fields. Routing evaluates approved layout conditions across those associations. Multiple matches and unknown records retain their original bytes and remain unvalidated. Approved spec versions and original file SHA-256 values are bound to each processed result. The original single-file workflow remains available.

Batch processing calls the existing TASAA and specification validation path per routed record. Failed records keep their original bytes. The batch report includes file and record summaries, hashes, AI status, validation findings, and certified structural-correction provenance. AI receives bounded evidence for the selected spec/record; a finding requires an evidence-bound human decision. Final generation reruns deterministic routing and exact-byte validation. Individual valid files can be exported even when another file is blocked; the archive names included and blocked files in its manifest, and includes validation reports and available per-byte provenance.

The batch project has a conservative **16 MB cumulative original-file limit**, a **20,000-record limit per DAT**, the existing 2 MB/spec and 12 MB/DAT limits, and at most 20 specifications and 20 DATs. These bounds suit a controlled pilot with the current SQLite JSON project storage. Batch AI is synchronous per record, with each paid response checkpointed for retry; it is not yet a background job with a multiworker lease. Large live-AI batches, process restart under load, and production throughput remain unqualified.

New tests are `tests/test_batch_studio_api.py`, `tests/test_batch_benchmarks.py`, and two Playwright cases in `tests/test_browser_e2e.py`. They cover one-to-one, one-to-many, many-to-one mixed layouts, many-to-many, unknown and ambiguous routing, stale versions, partial file failure, UNKNOWN mandatory coverage, AI findings and human decisions, exact individual/archive bytes, exported-file reimport, and certified repair provenance. A measured generated workload processed **2,400 records across two specs and two DATs (130,400 input bytes) in 12.997 seconds**, with **4.2 MB peak traced Python allocation**; all 2,400 records validated and originals remained identical. Traced allocation is not process RSS. Live AI and Oracle imports were not part of this benchmark.

After the batch implementation and an XLSX original-byte upload check, the complete `.venv/bin/python -m pytest -q` run reports **343 passed, 8 skipped, 1 warning in 69.40 seconds**. The three Playwright tests and ten batch API tests are included; the eight skips remain seven opt-in live Groq cases and one optional live Bedrock case. `node --check static/app.js`, Python compilation of the batch routes, and `git diff --check` pass.

The earlier statements below about one-file-only UI and incomplete browser multi-file testing describe the *pre-milestone baseline*. The new browser/API workflow passes for the tested synthetic cases. Production Bedrock, Oracle EBS loading, large live-AI batch behavior, and enterprise security remain open gates.

## Reproduced baseline and fixes

The initial unrestricted `python -m pytest -q` run produced **322 passed, 6 failed, 1 skipped, 1 error**. Six live Groq tests attempted network access by default; the browser test could not bind a local server inside the restricted runner. Live provider tests now require `RUN_LIVE_GROQ_TESTS=1`, and do not read private `.env` credentials during ordinary CI. With local browser binding permitted, `.venv/bin/python -m pytest -q` now reports **330 passed, 8 skipped, 1 warning in 52.81 seconds**. The warning is a Starlette/httpx deprecation warning. Historical tests were retained.

`RUN_LIVE_GROQ_TESTS=1 .venv/bin/python -m pytest -q tests/test_groq_live.py` made real Groq calls with synthetic content and passed **8 tests in 8.96 seconds**. This verifies the configured development provider for those calls, not future model availability, every schema size, or Bedrock equivalence. The eight default skips are seven opt-in live Groq tests and one optional live Bedrock test; the non-network Groq failure-path test runs normally.

The previous provider gate checked only `APP_ENV`, while web security checked `STUDIO_ENV`. `STUDIO_ENV=production` could therefore select a mock provider. `ai.production_mode()` now makes both components enforce production restrictions for either setting. New security tests reproduce both environment combinations. Production permits Bedrock only, and requires a long access token, allowed hosts, and an HTTPS public origin. This is an application guard, not an independent security certification.

The existing browser test used a synthetic AI provider. It now checks a real Playwright download and exact DAT bytes after a user edit: `01CUST999900012500\n01CUST000200009950\n`. It passed as part of the full run. It does not exercise live Groq in the browser, reimport the download, or demonstrate every requested UI step.

## Code and integrity audit

| Area | Verified behavior and limit |
|---|---|
| TASAA | Uses compiled field rules, positional checks, anchors, candidate generation, bounded beam search, exact-count checks within its declared interpretation model, source/output provenance, and an independent preservation verifier. Scores alone cannot authorize automatic repair. An exact structural mapping is **not semantic proof** of original business meaning; the result now always reports `semantic_proof: false`. Review remains required where global uniqueness is not established. The exact-count model cannot prove interpretations outside supported typed field, padding, date, constant, and layout rules. |
| Preservation | Automated repair is checked for retained original value bytes, order, ownership, inserted declared padding, and complete source-byte accounting; the reconstructed row is revalidated. Equal input/output byte counts alone are not used as proof. User edits have a separate path. Adversarial tests cover adjacent fields, repeated or misleading anchors, meaningful spaces, overwidth data, conflicting constants, and cancellation of missing/extra padding. |
| Scenario independence | `tests/test_tasaa_generalization.py` exercises the **same** implementation against five independently generated layouts of 4, 9, 17, 55 and 180 fields, widths 37, 108, 255, 3,303 and 20,016 bytes, with varied types and padding. These complement Scenario 3 and the existing 300-field tests. They do not prove support for arbitrary unsupported formats or conditional semantics. |
| Specification approval | Scenario 3 has 63 mappings and 55 missing mandatory flags. All 55 are classified as missing metadata in `SCENARIO3_APPROVAL_FINDINGS.md`; explicit UNKNOWN acknowledgment remains a visible coverage limitation and cannot be reported as verified. No mandatory value was invented. |
| Validation pipeline | Approved specs create a typed `RuleStep` execution plan at runtime; this is **not generated Python bytecode/closures**, contrary to the prior handover. The Scenario 3 compiled plan had 228 steps and 55 coverage limitations, including 55 `MANDATORY_UNVERIFIED` items. Unsupported rule text is rejected. Corrected rows and user edits are revalidated. The plan version derives from the spec version. |
| Multi-layout and multiple files | This was a pre-milestone gap. The additive batch workspace now supports multi-file upload, explicit associations, mixed routing, per-file status and individual/archive export. The original single-file workflow remains available. See the milestone update above for bounds and tests. |
| AI contracts | Groq development structured calls passed live. Budgeting reserves `ceil(counted_tokens * 1.25) + 512` plus output and reconciles usage; that estimate is a guard, not a guarantee against every server-side accounting change. Bedrock uses a typed response path and backend credentials, with no production fallback to Groq or mock. Local Bedrock status was `not_configured` (no region/model/credentials), so live structured Bedrock inference is unverified. AI output cannot bypass deterministic validation. |
| Scale | `tests/test_benchmarks.py` passed in the regression suite, including a 300-field/50,000-byte case. Earlier single-record timings in `PERFORMANCE_BENCHMARKS.md` were not independently reproduced as a batch service-level guarantee. The current 2 MB spec and 12 MB DAT upload limits, in-memory request buffering, process-local rate limits, and SQLite/background-worker deployment require realistic batch and concurrent-worker qualification. |

## Release gates

| Gate | Result | Evidence or blocker |
|---|---|---|
| Deterministic application functionality | PASS for tested single-project workflow | Full suite and downloaded-byte browser test; 55 UNKNOWN requirements still limit Scenario 3 coverage. |
| TASAA reconstruction safety | PASS within documented proof domain | Preservation and adversarial tests; no claim of semantic uniqueness. |
| Multi-scenario generalization | PASS for tested layouts | Five independent generated specs plus existing mixed-layout and scale tests. |
| Dynamic validation completeness | PASS for supported typed rules; FAIL for arbitrary rules | Coverage report exposes 55 Scenario 3 limitations; unsupported rules are rejected. |
| Live local Groq | PASS for tested synthetic calls | Eight opt-in live tests; runtime model/rate limits remain external dependencies. |
| Live production Bedrock | BLOCKED | No configured region, model access, or live credentials in this environment. |
| Real-browser workflow | PASS for tested synthetic multi-file cases; FAIL for complete live-provider acceptance | Three Playwright cases pass, including multi-file upload, explicit routing, exact downloads, reimport and blocked ambiguous routes. Live provider use in browser remains untested. |
| Security and authorization | PASS for tested app guards; NOT TESTED for enterprise certification | Production-provider gate fixed; external identity, storage, deployment, penetration testing and multiworker controls not assessed. |
| Reliability and performance | NOT TESTED at production workload | Synthetic single-record benchmarks and concurrency unit tests pass; no realistic batch/restart/multiworker qualification. |
| Oracle EBS acceptance | BLOCKED | No authorized SQL*Loader/EBS sandbox, logs, bad/discard files, or actual import. |

## Next release work

1. Obtain business approval for the 55 UNKNOWN Scenario 3 mandatory rules or retain visible limited coverage in every output.
2. Qualify larger concurrent multi-file jobs and the synchronous per-record AI path; add durable background job leasing before multiworker production deployment.
3. Run browser acceptance with the live development provider and full review decisions on representative batch exceptions.
4. Configure an approved Bedrock region/model and IAM role; run real structured audit, failure, timeout, and model-access preflight in the deployment environment.
5. Qualify real batch sizes, concurrency, restart recovery, storage retention, and security controls; keep upload limits until measured requirements justify changes.
6. Run controlled Oracle SQL*Loader/EBS imports with synthetic data and retain control files, loader logs, bad/discard files, and acceptance results.

Production deployment must set `APP_ENV=production` or `STUDIO_ENV=production`, `AI_PROVIDER=bedrock`, a 32+ character `STUDIO_ACCESS_TOKEN`, `STUDIO_ALLOWED_HOSTS`, and HTTPS `STUDIO_PUBLIC_ORIGIN`; configure Bedrock region/model/IAM access and disable mock settings. Both environment names now activate the same production gate. No external certification is claimed.
