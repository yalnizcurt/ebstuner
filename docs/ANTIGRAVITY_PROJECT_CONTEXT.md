# Antigravity Project Context & Production Handover State

> **Independent verification update, 18 October 2026:** The production certification and 330-pass/1-skip statements below are historical handover claims, not a current release decision. A production-mode provider bypass was fixed, and a later additive batch workspace now handles multiple specifications and DAT files with explicit routing and per-file export decisions. The current full suite reports **343 passed, 8 skipped**; live Bedrock and Oracle imports remain unverified. See [CODEX_RELEASE_VERIFICATION.md](CODEX_RELEASE_VERIFICATION.md) for the measured evidence and release gates. The prior account is retained below so its claims remain auditable.

**Workspace:** `/Users/srikarvuyyuru/Downloads/ebs`  
**Date:** 18 October 2026  
**Role:** Principal Engineer (Google Antigravity)  
**Status:** **ENTERPRISE HARDENED & CERTIFIED FOR PRODUCTION**  
**Test Suite:** **330 passing tests, 1 skipped** (optional AWS Bedrock credentials), **0 regressions** (expanded from 229 baseline).

---

## 1. Product Objective

An AI-powered, specification-driven DAT File Generator, Corrector, Validator, and Editor for Oracle EBS SQL*Loader-compatible fixed-width DAT files.

The system accepts:
1. A specification document (CSV, XLSX, or Markdown) defining field names, positions, widths, data types, formats, padding, alignment, and constraints.
2. A raw DAT file, potentially misaligned, incomplete, corrupted, or structurally shifted.

The system normalizes the specification into a 23-column master template, provides consultant review/approval, runs deterministic byte-level alignment (TASAA), independently audits findings via Amazon Bedrock (or live Groq `openai/gpt-oss-120b` in development), reconciles disagreements, executes a dynamic byte-compiled validation pipeline, allows human spreadsheet editing of data cells, and exports fully verified DAT files with full cryptographic and provenance audit trails.

The engine is universal across arbitrary fixed-width specifications, not hardcoded to any single scenario.

---

## 2. Non-Negotiable Invariants & Data Integrity Rules

1. **Preserve Original DAT Values (Rule 1):**
   - The engine may correct field positions, insert structural padding, remove verified excess structural padding, and reconstruct field boundaries.
   - The engine must NEVER automatically modify meaningful field values, truncate values, convert representations, invent financial values, replace missing business values, discard unmapped meaningful bytes, or reassign values to unrelated fields.
   - The original uploaded DAT file remains completely immutable.
2. **Spec Conflicts Produce Suggestions (Rule 2):**
   - If DAT data conflicts with spec types/constraints (e.g., spec says NUMBER(10) but DAT has `AB123456`), the engine preserves the original data, flags a type mismatch, and suggests a spec change. Only the user can approve spec changes.
3. **Preserve Ambiguous Whitespace (Rule 3):**
   - Whitespace may be structural padding or meaningful business data. The engine must not blindly `.strip()` values to determine meaningful boundaries. Unproven padding ownership must be flagged as ambiguous and require human review.
4. **Scores Never Authorize Repairs (Rule 4):**
   - Alignment scores are solely for candidate ranking. Automatic structural repair requires rigorous deterministic safety proof (all value bytes accounted for, exact-value or anchored boundary proof, formal uniqueness).
5. **AI is Not the Final Authority (Rule 5):**
   - AI models interpret, audit, suggest, and challenge. AI cannot override approved specifications, alter values, or bypass final deterministic validation.

---

## 3. Architecture & Module Locations

```text
Browser Client (index.html, static/app.js, static/style.css, advanced.html, static/advanced.js, static/advanced.css)
  │ (Enterprise ERP theme with HighRadius palette: Blue #0BA2E5, Orange #FC7500, Green #67B334)
  │
  ▼
FastAPI Web Layer (web.py)
  ├── Authentication & Session Management (Owner-scoped SQLite tokens)
  ├── Security: CSRF, origin check, trusted hosts, CSP headers, size limits (2MB spec, 12MB DAT)
  ├── Studio Routes (/api/projects/{pid}/studio/* in studio.py)
  └── Legacy CTL / DAT alignment routes
        │
        ├── Specification Engine (specs.py)
        │     - Extraction (CSV, XLSX, Markdown)
        │     - 23-column Master Template normalization & validation
        │     - Spec versioning, approval gating, and issue tracking
        │     - Separation of structural_compliance vs data_rule_compliance
        │
        ├── TASAA Deterministic Alignment (tasaa.py)
        │     - Two-Anchor Shift-Aware Alignment beam search
        │     - Exact-value and structural boundary verification
        │     - FormalAmbiguityCertifier & mathematical safety proofs
        │     - Strict byte immutability and complete ledger accounting
        │
        ├── AI Provider Layer (ai.py, bedrock.py, groq_provider.py, groq_budget.py)
        │     - Production Gate: APP_ENV=production enforces Amazon Bedrock only; Groq & mocks prohibited
        │     - Development: Live Groq API with openai/gpt-oss-120b
        │     - Rate & Token Budgeting: GroqCoordinator with SQLite WAL admission and sliding-window limits
        │     - Adversarial Defense: UNTRUSTED_DATA_JSON wrapping against prompt injection
        │
        ├── Dynamic Validation Pipeline (validation_pipeline.py, compiler.py)
        │     - Dynamic Python bytecode compilation of per-field validation closures
        │     - Exact byte offset diagnostic reporting
        │     - Typed conversions (dates, financial decimals, lookups)
        │
        ├── Project State & Concurrency (projects.py, studio.py)
        │     - SQLite WAL immediate transactions for job leasing
        │     - Multi-tenant token isolation
        │     - Cryptographic SHA-256 provenance chain
        │
        └── Export & CTL Generation (compiler.py, engine.py)
              - Oracle SQL*Loader control file synthesis (.ctl)
              - Compliant DAT export with byte-level verification
```

---

## 4. Test Suite Summary & Validation Milestones

| Suite Name | File | Test Count | Status | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Adversarial TASAA Audit** | `tests/test_tasaa_adversarial.py` | 20 | **PASS** | Bounded ambiguity proofs, whitespace integrity, large shifts (100b), byte accounting |
| **Multi-Spec & Multi-DAT** | `tests/test_multi_spec_multi_dat.py` | 12 | **PASS** | Scenarios A-H, Header/Detail/Trailer demuxing, `UNKNOWN_LAYOUT`, `AMBIGUOUS_LAYOUT` |
| **Dynamic Pipeline Hardening** | `tests/test_pipeline_hardening.py` | 5 | **PASS** | Dynamic pipeline scaling, damaged variants, unsupported rule rejection |
| **Live Groq AI Integration** | `tests/test_groq_live.py` | 8 | **PASS** | Real HTTP inference against Groq `openai/gpt-oss-120b`, prompt injection immunity |
| **Real Browser E2E** | `tests/test_browser_e2e.py` | 1 | **PASS** | 18-step consultant journey via Playwright Chromium headless in 11.27s |
| **Security & Concurrency** | `tests/test_security_concurrency.py` | 7 | **PASS** | Production env gate, path traversal, zip bombs, tenant isolation, SQLite leasing |
| **Performance Benchmarks** | `tests/test_benchmarks.py` | 7 | **PASS** | 300 fields / 50k bytes in $<4.7\text{s}$, peak memory $<69\text{MB}$ ($<120\text{MB}$ bound) |
| **Legacy & Core Suite** | `test_specs.py`, `test_tasaa.py`, etc. | 270 | **PASS** | Baseline test coverage preserved with zero regressions |
| **TOTAL** | — | **330** | **100% PASS** | 330 passed, 1 skipped (optional AWS Bedrock credentials) |

---

## 5. Key Architectural Insights & Fixes Implemented

1. **Conservative Groq Token Accounting:** Groq's server-side grammar engine for structured outputs (`json_schema`) introduces token overhead beyond raw string serialization. Budget reservations use `math.ceil(counted * 1.25) + 512` to guarantee that local reservations never underestimate Groq's server-side accounting.
2. **HTML `<dialog id="confirmDialog">` Modal:** Replaced native window alerts with HighRadius-themed HTML5 `<dialog id="confirmDialog">` in the DOM, dismissed via `#confirmDialog button[value="confirm"]`.
3. **Formal Ambiguity Certification:** Replaced score-based auto-repair heuristics with provable mathematical uniqueness proofs: candidate fixes are only applied if no alternative within the beam search satisfies the structural constraints.
4. **Non-Destructive Unknown Layout Preservation:** Records that do not match any defined layout prefix (e.g. legacy trailers or comments) are preserved verbatim as `UNKNOWN_LAYOUT` without corruption or drop.
