"""Deterministic TASAA performance smoke benchmark.

Run: .venv/bin/python benchmarks/benchmark_tasaa.py
"""

import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasaa import analyze


def fixture(field_count, record_width):
    base, extra = divmod(record_width, field_count)
    fields = []
    row = bytearray()
    cursor = 1
    for index in range(field_count):
        width = base + (index < extra)
        value = f"F{index:03d}".encode()
        if width < len(value):
            raise ValueError("Benchmark field width is too small")
        fields.append({
            "id": f"field-{index:03d}",
            "field_name": f"Field {index}",
            "rules": {
                "start": cursor,
                "end": cursor + width - 1,
                "length": width,
                "datatype": "char",
                "conversion": "identity",
                "required": False,
                "mapping_type": "physical",
                "padding": {"side": "right", "char": " "},
            },
        })
        row.extend(value.ljust(width, b" "))
        cursor += width
    return {"fields": fields}, {"field_ids": [f["id"] for f in fields], "record_length": record_width}, bytes(row)


def run_case(field_count, record_width, missing_padding=0):
    spec, layout, expected = fixture(field_count, record_width)
    row = expected
    if missing_padding:
        for index, field in enumerate(spec["fields"]):
            field["rules"]["allowed_values"] = [f"F{index:03d}"]
        cut = spec["fields"][0]["rules"]["length"] - missing_padding
        row = row[:cut] + row[cut + missing_padding:]
    tracemalloc.start()
    started = time.perf_counter()
    result = analyze(spec, layout, row)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result["candidate"] == expected
    assert len(result["ledger"]) == len(row)
    return {
        "fields": field_count,
        "record_bytes": record_width,
        "missing_padding_bytes": missing_padding,
        "status": result["status"],
        "wall_seconds": round(elapsed, 6),
        "peak_memory_bytes": peak,
        "phase_seconds": result.get("timings", {}),
    }


if __name__ == "__main__":
    for field_count, record_width in [(55, 3303), (180, 20000), (300, 50000)]:
        print(json.dumps(run_case(field_count, record_width), sort_keys=True), flush=True)
        print(json.dumps(run_case(field_count, record_width, missing_padding=20), sort_keys=True), flush=True)
