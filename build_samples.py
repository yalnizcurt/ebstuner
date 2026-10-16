"""Build reproducible, synthetic Oracle-syntax learning fixtures, never seeded EBS formats."""
import csv
import hashlib
import io
import json
from pathlib import Path
from engine import compile_ctl, inspect, physical_lines

ROOT = Path(__file__).parent / 'fixtures' / 'oracle'
ORACLE = 'https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-sql-loader-field-list-contents.html'
EBS = 'https://docs.oracle.com/cd/E18727_01/doc.121/e13510/T447343T454906.htm'


def layout(fields, total, table, when='', relative=False, fixed=False):
    declarations = []; positions = {}; cursor = 1
    for name, width, datatype in fields:
        positions[name] = (cursor, cursor + width - 1)
        pos = 'POSITION(*)' if relative else f'POSITION({cursor}:{cursor + width - 1})'
        declarations.append(f'  {name} {pos} {datatype}')
        cursor += width
    assert cursor - 1 <= total
    if cursor <= total:
        positions['COMMENTS'] = (cursor, total)
        declarations.append(f'  COMMENTS POSITION({cursor}:{total}) CHAR({total - cursor + 1})')
    prefix = 'LOAD DATA\n' + (f'INFILE \'correct.dat\' "FIX {total}"\n' if fixed else "INFILE 'correct.dat'\n")
    return prefix + f'INTO TABLE {table}' + (f" WHEN (1:2) = '{when}'" if when else '') + '\n(\n' + ',\n'.join(declarations) + '\n)\n', positions


def record(positions, values, total):
    raw = bytearray(b' ' * total)
    for name, value in values.items():
        a, b = positions[name]; value = str(value).encode('ascii')
        assert len(value) <= b - a + 1, (name, value)
        raw[a - 1:b] = value.ljust(b - a + 1, b' ')
    return bytes(raw)


def mutate(row, positions, field, value):
    a, b = positions[field]; raw = bytearray(row)
    raw[a - 1:b] = value.encode().ljust(b - a + 1, b' ')
    assert len(raw) == len(row)
    return bytes(raw)


def write_case(identifier, title, description, ctl, rows, positions, variants, supported=True, fixed=False, keys=None):
    folder = ROOT / identifier; folder.mkdir(parents=True, exist_ok=True)
    join = lambda data: b''.join(data) if fixed else b''.join(r + b'\r\n' for r in data)
    (folder / 'layout.ctl').write_text('-- Synthetic custom staging layout; not an Oracle-delivered EBS control file.\n' + ctl)
    source = (folder / 'layout.ctl').read_text()
    (folder / 'correct.dat').write_bytes(join(rows))
    schema = compile_ctl(source)
    fields = [f for f in schema.branches[0].fields if f.start and not f.filler]
    reference = io.StringIO(newline=''); writer = csv.writer(reference)
    writer.writerow([f.name for f in fields])
    for row in rows: writer.writerow([row[f.start - 1:f.end].decode('ascii').strip(' ') for f in fields])
    (folder / 'reference.csv').write_text(reference.getvalue())
    entries = [{'id': 'correct', 'label': 'Correct input' if supported else 'Unsupported reference input', 'file': 'correct.dat',
                'expected_valid': supported, 'expected_records_with_findings': 0 if supported else len(rows),
                'exercise': 'Inspect all fields, then generate and compare the untouched output.' if supported else 'Read the compatibility findings. Validated export must remain blocked.'}]
    for variant_id, label, data, count, exercise in variants:
        (folder / f'{variant_id}.dat').write_bytes(join(data))
        entries.append({'id': variant_id, 'label': label, 'file': f'{variant_id}.dat', 'expected_valid': False,
                        'expected_records_with_findings': count, 'exercise': exercise})
    for entry in entries:
        raw = (folder / entry['file']).read_bytes(); report = inspect(source, raw)
        assert (report['export_status'] == 'structurally_validated') == entry['expected_valid'], (identifier, entry['id'], report['compatibility'])
        assert report['records_with_findings'] == entry['expected_records_with_findings'], (identifier, entry['id'], report['records_with_findings'])
        entry['sha256'] = hashlib.sha256(raw).hexdigest()
        entry['record_lengths'] = [len(r) for r, _ in physical_lines(source, raw)]
    item = {'id': identifier, 'title': title, 'description': description, 'supported': supported,
            'record_length': max(map(len, rows)), 'record_count': len(rows), 'record_format': 'fixed' if fixed else 'stream',
            'encoding': 'ascii', 'line_ending': 'none' if fixed else 'CRLF', 'keys': keys or [],
            'fields': [{'name': name, 'start': a, 'end': b, 'width': b - a + 1} for name, (a, b) in positions.items()],
            'variants': entries, 'sources': [ORACLE, EBS],
            'scope': 'Synthetic custom XX staging schema. Not a copy of ardeft.ctl, arconv.ctl or an EBS certification.'}
    (folder / 'expected.json').write_text(json.dumps(item, indent=2) + '\n')
    return item


def build():
    cases = []
    ctl, pos = layout([('CODE', 2, 'CHAR(2)'), ('QUANTITY', 4, 'INTEGER EXTERNAL(4)')], 6, 'XX_CHAR_INT')
    rows = [b'AA0042', b'BB-012', b'CC0000']
    cases.append(write_case('char2_int4', '6 bytes · CHAR(2) + INTEGER EXTERNAL(4)', 'Two character bytes followed by four readable numeric bytes. These are two fields, not a CHAR2INT4 datatype.', ctl, rows, pos,
        [('bad_numeric', 'Invalid integer', [rows[0], b'BB-X12', rows[2]], 1, 'Record 2 QUANTITY contains X. Reconcile reference.csv using CODE as the unique key; review the proposed -012 value.')], keys=['CODE']))

    fields = [('RECORD_TYPE', 2, 'CHAR(2)'), ('TRANSACTION_ID', 10, 'CHAR(10)'), ('QUANTITY', 4, 'INTEGER EXTERNAL(4)'),
              ('AMOUNT', 12, 'DECIMAL EXTERNAL(12)'), ('TRANSACTION_DATE', 8, 'DATE "YYYYMMDD"')]
    ctl, pos = layout(fields, 80, 'XX_MIXED_80')
    rows = [record(pos, {'RECORD_TYPE': 'IN', 'TRANSACTION_ID': f'{i:010}', 'QUANTITY': '0007', 'AMOUNT': '-1234.50', 'TRANSACTION_DATE': '20240229', 'COMMENTS': 'Synthetic transaction'}, 80) for i in (1, 2)]
    cases.append(write_case('mixed_80', '80 bytes · text, integer, decimal and date', 'Leading-zero IDs, a signed amount, leap-day date and trailing text padding.', ctl, rows, pos,
        [('missing_spaces', 'Seven trailing spaces removed', [rows[0], rows[1][:-7]], 1, 'Approve the trailing-padding candidate for record 2; compare the download to correct.dat.'),
         ('bad_date', 'Invalid leap day', [rows[0], mutate(rows[1], pos, 'TRANSACTION_DATE', '20230229')], 1, 'A full-length record can still contain an invalid calendar date.')], keys=['TRANSACTION_ID']))

    fields = [('RECORD_TYPE', 2, 'CHAR(2)'), ('TRANSACTION_ID', 10, 'CHAR(10)'), ('QUANTITY', 4, 'INTEGER EXTERNAL(4)'),
              ('RATE', 12, 'FLOAT EXTERNAL(12)'), ('TRANSACTION_DATE', 8, 'DATE "YYYYMMDD"')]
    ctl, pos = layout(fields, 240, 'XX_RELATIVE_240', relative=True)
    rows = [record(pos, {'RECORD_TYPE': 'RT', 'TRANSACTION_ID': f'{i:010}', 'QUANTITY': '+012', 'RATE': '1.25E+03', 'TRANSACTION_DATE': '20261017', 'COMMENTS': 'Relative POSITION(*) and scientific notation'}, 240) for i in (1, 2)]
    cases.append(write_case('relative_240', '240 bytes · relative fields and scientific notation', 'POSITION(*) follows the previous field. FLOAT EXTERNAL is text, unlike binary FLOAT.', ctl, rows, pos,
        [('bad_exponent', 'Invalid exponent', [rows[0], mutate(rows[1], pos, 'RATE', '1.25E+XX')], 1, 'Inspect RATE at bytes 17–28; use exact reference evidence rather than inventing a rate.')], keys=['TRANSACTION_ID']))

    fields = [('RECORD_TYPE', 2, 'CHAR(2)'), ('TRANSACTION_ID', 10, 'CHAR(10)'), ('QUANTITY', 4, 'INTEGER EXTERNAL(4) DEFAULTIF QUANTITY=BLANKS'),
              ('AMOUNT', 12, 'DECIMAL EXTERNAL(12) NULLIF AMOUNT=BLANKS'), ('TRANSACTION_DATE', 8, 'DATE "YYYYMMDD" NULLIF TRANSACTION_DATE=BLANKS'),
              ('RESERVED', 20, 'FILLER CHAR(20)')]
    # FILLER precedes POSITION in Oracle field declarations.
    ctl, pos = layout(fields, 480, 'XX_NULLS_480')
    ctl = ctl.replace('INTO TABLE', 'PRESERVE BLANKS\nINTO TABLE', 1)
    ctl = ctl.replace('RESERVED POSITION(37:56) FILLER CHAR(20)', 'RESERVED FILLER POSITION(37:56) CHAR(20)')
    ctl = ctl.replace('\n)\n', ",\n  STATUS CONSTANT 'NEW'\n)\n")
    rows = [record(pos, {'RECORD_TYPE': 'NU', 'TRANSACTION_ID': f'{i:010}', 'COMMENTS': 'Blank numeric default is zero; amount/date are NULL; source bytes stay blank.'}, 480) for i in (1, 2)]
    cases.append(write_case('nullable_480', '480 bytes · NULLIF, DEFAULTIF, FILLER and CONSTANT', 'Blank quantities interpret as zero; blank amounts and dates as NULL. Constants consume no data bytes.', ctl, rows, pos,
        [('bad_numeric', 'Nonblank invalid number', [rows[0], mutate(rows[1], pos, 'AMOUNT', 'ABC')], 1, 'NULLIF only applies to blank input; ABC is an invalid numeric format.')], keys=['TRANSACTION_ID']))

    common = [('RECORD_TYPE', 2, 'CHAR(2)'), ('BATCH_NAME', 10, 'CHAR(10)'), ('ITEM_NUMBER', 6, 'CHAR(6)'),
              ('CUSTOMER_NUMBER', 12, 'CHAR(12)'), ('RECEIPT_NUMBER', 20, 'CHAR(20)'), ('CURRENCY_CODE', 3, 'CHAR(3)'),
              ('REMITTANCE_AMOUNT', 14, 'DECIMAL EXTERNAL(14) NULLIF REMITTANCE_AMOUNT=BLANKS'),
              ('DEPOSIT_DATE', 8, 'DATE "YYYYMMDD" NULLIF DEPOSIT_DATE=BLANKS')]
    parts = []
    for n, (marker, table) in enumerate([('01', 'HEADER'), ('06', 'RECEIPT'), ('04', 'OVERFLOW'), ('09', 'TRAILER')]):
        part, pos = layout(common, 960, 'XX_LOCKBOX_' + table, when=marker)
        parts.append(part if not n else part[part.index('INTO TABLE'):])
    ctl = ''.join(parts)
    rows = [record(pos, {'RECORD_TYPE': marker, 'BATCH_NAME': 'BATCH00001', 'ITEM_NUMBER': f'{n:06}',
                       **({'CUSTOMER_NUMBER': '000000123456', 'RECEIPT_NUMBER': 'RCPT000001', 'CURRENCY_CODE': 'USD', 'REMITTANCE_AMOUNT': '1234.50', 'DEPOSIT_DATE': '20261017'} if marker == '06' else {}),
                       'COMMENTS': f'Synthetic {marker} record; explicit positions restart each INTO TABLE'}, 960)
            for n, marker in enumerate(['01', '06', '04', '06', '09'], 1)]
    cases.append(write_case('multi_record_960', '960 bytes · header, receipt, overflow and trailer', 'EBS-inspired record roles in custom XX staging tables. Each layout explicitly restarts at byte 1; codes are illustrative.', ctl, rows, pos,
        [('bad_marker', 'Unknown record identifier', [rows[0], mutate(rows[1], pos, 'RECORD_TYPE', 'ZZ'), *rows[2:]], 1, 'Record 2 has no destination. Correct its marker only after checking its intended layout.')], keys=['ITEM_NUMBER']))

    for total in (2400, 4800):
        invoices = [(f'INVOICE_NUMBER{i}', 20, 'CHAR(20)') if j == 0 else (f'AMOUNT_APPLIED{i}', 14, f'DECIMAL EXTERNAL(14) NULLIF AMOUNT_APPLIED{i}=BLANKS') for i in range(1, 9) for j in (0, 1)] if total == 4800 else []
        attributes = [(f'ATTRIBUTE{i}', 120 if total == 2400 else 200, f'CHAR({120 if total == 2400 else 200})') for i in range(1, 16)]
        ctl, pos = layout(common + invoices + attributes, total, f'XX_AR_RECEIPT_{total}')
        rows = [record(pos, {'RECORD_TYPE': '06', 'BATCH_NAME': 'BATCH00001', 'ITEM_NUMBER': f'{n:06}', 'CUSTOMER_NUMBER': '000000123456',
                           'RECEIPT_NUMBER': f'RCPT{n:016}', 'CURRENCY_CODE': 'USD', 'REMITTANCE_AMOUNT': '800.00', 'DEPOSIT_DATE': '20261017',
                           **({f'INVOICE_NUMBER{i}': f'INV{i:017}' for i in range(1, 9)} if invoices else {}),
                           **({f'AMOUNT_APPLIED{i}': '100.00' for i in range(1, 9)} if invoices else {}),
                           **{f'ATTRIBUTE{i}': f'SYNTHETIC ATTRIBUTE {i} ROW {n}' for i in range(1, 16)}, 'COMMENTS': 'Synthetic custom record; remaining bytes are intentional spaces.'}, total) for n in (1, 2)]
        space_offset = pos['REMITTANCE_AMOUNT'][0] - 1 + len('800.00')
        assert rows[1][space_offset:space_offset + 1] == b' '
        variants = [('missing_spaces', 'Fourteen trailing spaces removed', [rows[0], rows[1][:-14]], 1, 'Review the trailing-padding hypothesis. After approval the generated bytes must equal correct.dat.'),
                    ('bad_amount', 'Invalid amount at full width', [rows[0], mutate(rows[1], pos, 'REMITTANCE_AMOUNT', '8OO.00')], 1, 'Letter O is not zero. Reconcile reference.csv with ITEM_NUMBER as a unique key.'),
                    ('bad_date', 'Invalid date at full width', [rows[0], mutate(rows[1], pos, 'DEPOSIT_DATE', '20260230')], 1, 'Length checks pass; calendar validation must fail.'),
                    ('shifted_field', 'Internal space deleted', [rows[0], rows[1][:space_offset] + rows[1][space_offset + 1:]], 1, 'Deleting one byte shifts every later field. Returned alignment hypotheses require review; exact recovery is not guaranteed.')]
        cases.append(write_case(f'receipt_{total}', f'{total:,} bytes · receipt and 15 attributes' + (' + 8 invoice applications' if invoices else ''), 'ASCII: characters equal bytes. This is a custom stress layout, not an Oracle standard record length or delivered EBS CTL.', ctl, rows, pos, variants, keys=['ITEM_NUMBER']))
        if total == 4800:
            fixed_ctl = ctl.replace("INFILE 'correct.dat'", "INFILE 'correct.dat' \"FIX 4800\"")
            cases.append(write_case('fixed_4800', '4,800 bytes · FIX records without line endings', 'Two exactly 4,800-byte records, stored back to back. No CR or LF separators.', fixed_ctl, rows, pos,
                [('bad_amount', 'Invalid amount in second fixed record', [rows[0], mutate(rows[1], pos, 'REMITTANCE_AMOUNT', '8OO.00')], 1, 'The second record starts at absolute byte 4801. Review the amount field without inserting line endings.')], fixed=True, keys=['ITEM_NUMBER']))

    ctl = "LOAD DATA INFILE 'correct.dat' INTO TABLE XX_BINARY (CODE POSITION(1:2) CHAR(2), QUANTITY POSITION(3:6) INTEGER(4))"
    cases.append(write_case('binary_integer4', 'Unsupported · CHAR(2) + binary INTEGER(4)', 'INTEGER(4) is a native binary field, not four decimal digits. This example uses an illustrative little-endian value 42; platform byte order matters.', ctl, [b'AA*\0\0\0'], {'CODE': (1, 2), 'QUANTITY': (3, 6)}, [], supported=False))
    ctl = "LOAD DATA INTO TABLE XX_SQL (CODE POSITION(1:2) CHAR(2), AMOUNT POSITION(3:10) CHAR(8) \"TO_NUMBER(:AMOUNT)/100\")"
    cases.append(write_case('sql_expression', 'Unsupported · SQL transformation', 'Oracle evaluates TO_NUMBER(:AMOUNT)/100. Studio retains it as metadata and blocks validated export instead of executing SQL.', ctl, [b'AA00012345'], {'CODE': (1, 2), 'AMOUNT': (3, 10)}, [], supported=False))
    ctl = "LOAD DATA INTO TABLE XX_CSV FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' (CODE CHAR(2), QUANTITY INTEGER EXTERNAL(4))"
    cases.append(write_case('delimited_csv', 'Unsupported · delimited SQL*Loader fields', 'Oracle supports delimited fields; the current editor requires fixed byte positions. Importing this example must show the unsupported declaration.', ctl, [b'AA,0042'], {'CODE': (1, 2), 'QUANTITY': (4, 7)}, [], supported=False))
    (ROOT / 'manifest.json').write_text(json.dumps({'version': 1, 'samples': cases}, indent=2) + '\n')
    print(f'Built {len(cases)} synthetic layouts; {sum(len(x["variants"]) for x in cases)} input variants; maximum 4800 bytes per record.')


if __name__ == '__main__': build()
