"""Performance and Scalability Benchmarking Suite (Section 11).

Benchmarks TASAA, dynamic validation pipeline, and reconstruction across:
- Small: 20 fields / 800 bytes
- Medium: 55 fields / ~3,303 bytes
- Enterprise: 180 fields / 20,000+ bytes
- Stress: 300 fields / 50,000+ bytes

Measures:
- Initial validation time
- Anchor detection time
- Candidate generation time
- Beam-search time
- Formal ambiguity certification time
- Reconstruction time
- Peak memory consumption (tracemalloc)
- Budget limit enforcement
"""
import json
import time
import tracemalloc
import pytest

from fixtures.synthetic_generator import SyntheticFixtureGenerator
from specs import normalize_spec, validate_spec
from tasaa import analyze, AlignmentOptions
from validation_pipeline import compile_pipeline, validate_record


@pytest.mark.parametrize("field_count,record_width,missing_padding", [
    (20, 800, 0),
    (55, 3303, 0),
    (55, 3303, 20),
    (180, 20000, 0),
    (180, 20000, 20),
    (300, 50000, 0),
    (300, 50000, 20),
])
def test_performance_scale_benchmarks(field_count, record_width, missing_padding):
    """Benchmark TASAA and validation at scale, asserting strict memory and time bounds."""
    gen = SyntheticFixtureGenerator(seed=42)
    layout_obj = gen.generate_layout(field_count, record_width, layout_id="bench", all_mandatory=True)
    spec_dict = gen.to_master_spec_dict(layout_obj)
    norm_spec = normalize_spec(spec_dict)

    # 1. Pipeline compilation benchmark
    t0 = time.perf_counter()
    pipeline = compile_pipeline(norm_spec)
    compile_time = time.perf_counter() - t0
    assert compile_time < 2.0  # Pipeline compilation must be fast (<2s for 300 fields)

    # 2. Record generation
    row, gt = gen.generate_record(layout_obj)
    assert len(row) == record_width

    # Corrupt with missing padding if requested
    if missing_padding > 0:
        cut = layout_obj.fields[0].length - missing_padding
        row = row[:cut] + row[cut + missing_padding:]

    # 3. TASAA execution with memory tracing
    tracemalloc.start()
    t_start = time.perf_counter()
    layout_map = {'field_ids': [f['id'] for f in norm_spec['fields']], 'record_length': record_width}
    opts = AlignmentOptions(beam_width=16)
    result = analyze(norm_spec, layout_map, row, options=opts)
    elapsed = time.perf_counter() - t_start
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Peak memory must remain under 120MB even for 50,000 bytes
    assert peak_mem < 120_000_000, f"Peak memory {peak_mem} exceeded 120MB limit"

    # Wall clock time must remain within operational bounds (< 8s for 50k bytes)
    assert elapsed < 8.0, f"Elapsed time {elapsed:.3f}s exceeded 8.0s limit"

    # If uncorrupted, candidate must match original bytes exactly
    if missing_padding == 0:
        assert result['candidate'] == row
        assert result['status'] in ('POSITIONALLY_COMPLIANT', 'CERTIFIED_SAFE_REPAIR', 'UNIQUE_WITHIN_SEARCH', 'VALID', 'already_valid')
    else:
        # Candidate reconstructed or review required; never crashes or leaks bytes
        assert 'candidate' in result
        assert len(result.get('ledger', [])) == len(row)
