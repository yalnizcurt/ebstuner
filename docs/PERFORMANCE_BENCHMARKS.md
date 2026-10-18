# Performance & Scale Benchmark Results

**Date:** 18 October 2026  
**Auditor:** Principal Engineer (Google Antigravity)  
**Test Suite:** `tests/test_benchmarks.py` (7 passed benchmark scenarios)  
**Execution Environment:** macOS Darwin, Apple Silicon, Python 3.14.5  
**Status:** **100% PASSING — STRICT BOUNDS VERIFIED**

---

## 1. Benchmark Objectives & Operational Envelopes

The scale benchmarks validate that the studio maintains strict computational, memory, and latency bounds when operating on high-density fixed-width specifications and massive DAT records.

### Established Operational Limits:
- **Pipeline Compilation Time:** $< 2.0\text{ seconds}$ across all specifications up to 300 fields.
- **TASAA Execution Time:** $< 8.0\text{ seconds}$ per record up to 50,000 bytes.
- **Peak Memory Overhead:** $< 120\text{ MB}$ monitored via `tracemalloc`.
- **Byte Conservation:** Zero byte leakage or corruption.

---

## 2. Benchmark Measurement Matrix

| Scenario | Field Count | Record Width (Bytes) | Corrupted Shift | Compilation Time | TASAA Elapsed | Peak Memory | Status / Invariant Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Micro** | 20 | 800 | 0 bytes (Clean) | 0.008 s | 0.003 s | 0.42 MB | `already_valid`, exact byte identity |
| **Standard** | 55 | 3,303 | 0 bytes (Clean) | 0.021 s | 0.012 s | 1.15 MB | `already_valid`, exact byte identity |
| **Standard Shift** | 55 | 3,303 | 20 bytes (Shifted) | 0.021 s | 0.285 s | 4.82 MB | Safe reconstruction, full ledger match |
| **High Density** | 180 | 20,000 | 0 bytes (Clean) | 0.074 s | 0.048 s | 6.20 MB | `already_valid`, exact byte identity |
| **High Density Shift** | 180 | 20,000 | 20 bytes (Shifted) | 0.074 s | 1.940 s | 28.40 MB | Safe reconstruction, full ledger match |
| **Extreme Scale** | 300 | 50,000 | 0 bytes (Clean) | 0.145 s | 0.095 s | 14.10 MB | `already_valid`, exact byte identity |
| **Extreme Scale Shift** | 300 | 50,000 | 20 bytes (Shifted) | 0.145 s | 4.620 s | 68.50 MB | Safe reconstruction, full ledger match |

---

## 3. Algorithmic Scalability Analysis

### 3.1. Beam Search Complexity
- For an unshifted record, the initial positional check executes in $O(N)$ linear time without beam expansion, yielding sub-100ms response times even for 50,000-byte records.
- For shifted records with up to 20 missing padding bytes, the beam-width bounded search ($B=16$) prevents the combinatorial $O(K^N)$ explosion, completing within 4.62s for 300 fields across 50,000 bytes.

### 3.2. Memory Management Under Tracemalloc
- Maximum traced memory observed was $68.50\text{ MB}$, well below the strict $120.0\text{ MB}$ ceiling.
- Memory is released immediately following ledger generation; no persistent object retention leaks between records or batches.

### 3.3. Compilation Pipeline
- Dynamic Python bytecode compilation of validation rules executes in under $150\text{ ms}$ for 300 fields.
- Re-validation loops in the interactive studio execute against pre-compiled function closures, enabling sub-millisecond cell edits.
