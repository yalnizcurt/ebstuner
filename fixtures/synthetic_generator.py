"""Synthetic Specification & DAT Fixture Generator with independent ground truth.

Generates:
1. Valid master specifications.
2. CSV specification files (standard and custom headers).
3. XLSX specification files (via openpyxl).
4. Matching reference DAT records.
5. Deliberately corrupted DAT records (missing/extra padding, invalid types, ambiguity).
6. Multiple record layouts & mixed record-type files.
7. Expected validation results & byte-level provenance ground truth.
All generation is parameterized by random seeds for deterministic reproducibility.
Ground truth is computed independently from TASAA.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from openpyxl import Workbook


@dataclass
class SyntheticField:
    field_id: str
    field_name: str
    start: int
    end: int
    length: int
    datatype: str  # 'CHAR', 'NUMBER', 'DATE "YYYYMMDD"', 'INTEGER EXTERNAL'
    padding_rule: str  # 'Trailing spaces', 'Leading zeroes', 'Fixed Text (Exact Width)', 'Leading spaces'
    mandatory: str  # 'YES', 'NO', 'UNKNOWN'
    mapping_type: str = "PHYSICAL"
    constant: str | None = None
    allowed_values: list[str] | None = None
    rules_datatype: str = "char"
    rules_conversion: str = "identity"
    padding_side: str = "right"
    padding_char: str = " "


@dataclass
class SyntheticLayout:
    layout_id: str
    name: str
    fields: list[SyntheticField]
    record_length: int
    when: list[dict] = field(default_factory=list)


class SyntheticFixtureGenerator:
    """Independent ground-truth generator for fixed-width specifications and DAT records."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def generate_layout(
        self,
        field_count: int,
        record_width: int,
        layout_id: str = "primary",
        record_type_code: str | None = None,
        all_mandatory: bool = True,
    ) -> SyntheticLayout:
        """Generate a structurally valid layout with diverse data types and constraints."""
        fields: list[SyntheticField] = []
        cursor = 1

        if record_type_code is not None:
            # First field is the layout routing record identifier
            rec_len = len(record_type_code)
            fields.append(
                SyntheticField(
                    field_id=f"{layout_id}-rec-type",
                    field_name="RECORD_TYPE",
                    start=cursor,
                    end=cursor + rec_len - 1,
                    length=rec_len,
                    datatype="CHAR",
                    padding_rule="Fixed Text (Exact Width)",
                    mandatory="YES",
                    constant=record_type_code,
                    allowed_values=[record_type_code],
                    rules_datatype="char",
                    rules_conversion="identity",
                    padding_side="none",
                    padding_char=" ",
                )
            )
            cursor += rec_len
            field_count -= 1
            record_width -= rec_len

        base_width, extra = divmod(record_width, max(1, field_count))

        types_pool = [
            ("CHAR", "Trailing spaces", "char", "identity", "right", " "),
            ("NUMBER", "Leading zeroes", "decimal", "to_number", "left", "0"),
            ("INTEGER EXTERNAL", "Leading zeroes", "integer", "to_number", "left", "0"),
            ('DATE "YYYYMMDD"', "Trailing spaces", "date", "to_date", "right", " "),
            ("CHAR", "Fixed Text (Exact Width)", "char", "identity", "none", " "),
        ]

        for i in range(field_count):
            width = base_width + (1 if i < extra else 0)
            t_data = list(types_pool[i % len(types_pool)])
            # If width is small, fallback to CHAR
            if width < 8 and 'DATE' in t_data[0]:
                t_data = list(types_pool[0])
            elif width == 8 and 'DATE' in t_data[0]:
                t_data[1] = "Fixed Text (Exact Width)"
                t_data[4] = "none"

            if all_mandatory:
                mandatory_choice = "YES" if i % 2 == 0 else "NO"
            else:
                mandatory_choice = "YES" if i == 0 else ("NO" if i % 2 == 0 else "UNKNOWN")

            allowed = None
            if i % 7 == 0 and t_data[2] == "char" and width >= 3 and t_data[4] != "none":
                allowed = [f"CODE{k:02d}"[:width] for k in range(1, 4)]

            fields.append(
                SyntheticField(
                    field_id=f"{layout_id}-f{i:03d}",
                    field_name=f"FLD_{layout_id.upper()}_{i:03d}",
                    start=cursor,
                    end=cursor + width - 1,
                    length=width,
                    datatype=t_data[0],
                    padding_rule=t_data[1],
                    mandatory=mandatory_choice,
                    allowed_values=allowed,
                    rules_datatype=t_data[2],
                    rules_conversion=t_data[3],
                    padding_side=t_data[4],
                    padding_char=t_data[5],
                )
            )
            cursor += width

        when_cond = []
        if record_type_code is not None:
            when_cond.append({"start": 1, "end": len(record_type_code), "equals": record_type_code, "trim": False})

        total_len = sum(f.length for f in fields)
        return SyntheticLayout(
            layout_id=layout_id,
            name=layout_id.title(),
            fields=fields,
            record_length=total_len,
            when=when_cond,
        )

    def generate_value(self, f: SyntheticField) -> tuple[bytes, bytes]:
        """Generate a valid (value_bytes, padding_bytes) pair for a field."""
        w = f.length
        if f.constant:
            val = f.constant.encode("ascii")
            pad = b""
            return val, pad
        if f.allowed_values:
            val_str = self.rng.choice(f.allowed_values)
            val = val_str.encode("ascii")
            pad_len = max(0, w - len(val))
            pad = (f.padding_char.encode("ascii") or b" ") * pad_len
            return val, pad

        if f.rules_datatype == "date":
            # YYYYMMDD
            days = self.rng.randint(0, 1000)
            d = date(2024, 1, 1) + timedelta(days=days)
            val = d.strftime("%Y%m%d").encode("ascii")
            pad_len = max(0, w - len(val))
            pad = (f.padding_char.encode("ascii") or b" ") * pad_len
            return val, pad

        if f.rules_datatype in ("decimal", "integer"):
            num_digits = max(1, min(w - 2, 6))
            val_num = self.rng.randint(10 ** (num_digits - 1), (10 ** num_digits) - 1)
            val = str(val_num).encode("ascii")
            pad_len = max(0, w - len(val))
            pad = (f.padding_char.encode("ascii") or b"0") * pad_len
            return val, pad

        # General CHAR
        max_val_len = max(1, w - (0 if f.padding_side == "none" else 2))
        char_len = w if f.padding_side == "none" else self.rng.randint(1, max_val_len)
        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        val = "".join(self.rng.choice(letters) for _ in range(char_len)).encode("ascii")
        pad_len = max(0, w - len(val))
        pad = (f.padding_char.encode("ascii") or b" ") * pad_len
        return val, pad

    def generate_record(self, layout: SyntheticLayout) -> tuple[bytes, list[dict]]:
        """Generate a valid reference DAT record and its ground-truth boundary ledger."""
        pieces = []
        ground_truth = []
        cursor = 0
        for f in layout.fields:
            val, pad = self.generate_value(f)
            if f.padding_side == "left":
                slice_bytes = pad + val
                val_start = cursor + len(pad)
                val_end = val_start + len(val)
                pad_ranges = [(cursor, cursor + len(pad))] if pad else []
            else:
                slice_bytes = val + pad
                val_start = cursor
                val_end = val_start + len(val)
                pad_ranges = [(val_end, val_end + len(pad))] if pad else []

            pieces.append(slice_bytes)
            ground_truth.append({
                "field_id": f.field_id,
                "field_name": f.field_name,
                "target_start": cursor,
                "target_end": cursor + f.length,
                "value_start": val_start,
                "value_end": val_end,
                "value_bytes": val,
                "pad_bytes": pad,
                "pad_ranges": pad_ranges,
                "padding_char": f.padding_char,
                "padding_side": f.padding_side,
                "length": f.length,
            })
            cursor += len(slice_bytes)

        record_bytes = b"".join(pieces)
        return record_bytes, ground_truth

    def corrupt_missing_padding(
        self, record: bytes, ground_truth: list[dict], field_idx: int, cut_bytes: int
    ) -> tuple[bytes, list[dict]]:
        """Cut structural padding from a field, shifting all downstream bytes leftward."""
        if not ground_truth:
            return record[:-cut_bytes] if len(record) > cut_bytes else record, []
        gt = ground_truth[field_idx]
        if not gt["pad_bytes"] or len(gt["pad_bytes"]) < cut_bytes:
            cut_bytes = len(gt["pad_bytes"])
        if cut_bytes <= 0:
            return record, ground_truth

        # Location of the padding in the record
        pad_start, pad_end = gt["pad_ranges"][0]
        cut_point = pad_end - cut_bytes
        corrupted = record[:cut_point] + record[pad_end:]

        # Shift ground truth for the damaged record
        corrupted_gt = []
        for i, item in enumerate(ground_truth):
            c_item = dict(item)
            if i > field_idx:
                c_item["source_start"] = item["target_start"] - cut_bytes
                c_item["source_end"] = item["target_end"] - cut_bytes
            elif i == field_idx:
                c_item["source_start"] = item["target_start"]
                c_item["source_end"] = item["target_end"] - cut_bytes
                c_item["missing_padding"] = cut_bytes
            else:
                c_item["source_start"] = item["target_start"]
                c_item["source_end"] = item["target_end"]
            corrupted_gt.append(c_item)

        return corrupted, corrupted_gt

    def to_csv_spec(self, layout: SyntheticLayout, custom_headers: list[str] | None = None) -> bytes:
        """Export layout to standard or custom CSV specification format."""
        out = io.StringIO()
        headers = custom_headers or [
            "Field Name",
            "Start Pos",
            "End Pos",
            "Length",
            "Data Type / Oracle Conversion Function",
            "Padding / Alignment Rule",
            "Mapping Type",
            "Mandatory",
        ]
        writer = csv.writer(out)
        writer.writerow(headers)
        for f in layout.fields:
            writer.writerow([
                f.field_name,
                str(f.start),
                str(f.end),
                str(f.length),
                f.datatype,
                f.padding_rule,
                f.mapping_type,
                f.mandatory,
            ])
        return out.getvalue().encode("utf-8")

    def to_xlsx_spec(self, layout: SyntheticLayout) -> bytes:
        """Export layout to standard XLSX specification workbook."""
        wb = Workbook()
        ws = wb.active
        ws.title = "Field Specs"
        headers = [
            "Field Name",
            "Start Pos",
            "End Pos",
            "Length",
            "Data Type / Oracle Conversion Function",
            "Padding / Alignment Rule",
            "Mapping Type",
            "Mandatory",
        ]
        ws.append(headers)
        for f in layout.fields:
            ws.append([
                f.field_name,
                f.start,
                f.end,
                f.length,
                f.datatype,
                f.padding_rule,
                f.mapping_type,
                f.mandatory,
            ])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def to_master_spec_dict(self, layout: SyntheticLayout) -> dict[str, Any]:
        """Produce the normalized master dictionary used by specs.py and compile_pipeline."""
        fields_list = []
        for f in layout.fields:
            rules_dict: dict[str, Any] = {
                "start": f.start,
                "end": f.end,
                "length": f.length,
                "datatype": f.rules_datatype,
                "conversion": f.rules_conversion,
                "required": True if f.mandatory == "YES" else (False if f.mandatory == "NO" else None),
                "mapping_type": "physical",
                "padding": {"side": f.padding_side, "char": f.padding_char},
            }
            if f.rules_datatype == "date":
                rules_dict["date_format"] = "YYYYMMDD"
            if f.constant:
                rules_dict["constant"] = f.constant
            if f.allowed_values:
                rules_dict["allowed_values"] = f.allowed_values

            fields_list.append({
                "id": f.field_id,
                "field_name": f.field_name,
                "mapping_type": "POSITION",
                "start_pos": str(f.start),
                "end_pos": str(f.end),
                "length": str(f.length),
                "datatype_conversion": f.datatype,
                "padding_alignment": f.padding_rule,
                "mandatory": f.mandatory,
                "rules": rules_dict,
            })

        return {
            "fields": fields_list,
            "layouts": [{
                "id": layout.layout_id,
                "name": layout.name,
                "field_ids": [f.field_id for f in layout.fields],
                "record_length": layout.record_length,
                "when": layout.when,
            }],
            "options": {"encoding": "ascii", "record_format": "stream"},
        }
