# CTL compatibility matrix

“Fully supported” means supported physical parsing, routing and serialization for this implementation's fixed-width subset. It does not promise Oracle table constraints, NLS conversion results, SQL transformations or EBS business acceptance. The compiler never executes CTL SQL, resolves INFILE paths or contacts a database.

| Construct | Classification | Behavior |
|---|---|---|
| LOAD DATA; INSERT / APPEND / REPLACE / TRUNCATE | Fully supported as metadata | Load mode displayed; database mutation is never performed |
| Multiple INTO TABLE; unconditional destinations | Fully supported | Every definitely matching destination is returned |
| Repeated names across tables | Fully supported | Branch-qualified cells; unqualified ambiguous edits rejected |
| Quoted strings/identifiers, doubled quotes, line/block comments | Fully supported | Tokenized with original declaration offsets and line numbers |
| Nested/grouped WHEN with AND, =, !=, <> | Fully supported | Three-state evaluation; false AND unknown is false |
| Named field predicates | Fully supported in bounded cases | Branch-local fields preferred; global fallback only for unique physical definitions; named character values trim trailing blanks unless PRESERVE BLANKS |
| Absolute byte-range predicates | Fully supported | Compare original bytes without trimming |
| POSITION(start:end), POSITION(start), POSITION(*), POSITION(*+n) | Fully supported with known lengths | Inclusive byte positions; relative cursor within a layout; CHAR(n) and external numeric lengths supported |
| Relative first physical field across INTO TABLE branches | Partially supported | Layout metadata shown; runtime cursor dependency flagged |
| CHAR, INTEGER EXTERNAL, DECIMAL EXTERNAL, FLOAT EXTERNAL | Supported physical fixed-width subset | Strict byte widths; signed decimal/scientific text retained without rounding; Studio adds format diagnostics |
| DATE masks RRMMDD, YYMMDD, MMDDRR, MMDDYY, YYYYMMDD, YYYY-MM-DD, DD-MON-YYYY, DDMMYYYY, MM/DD/YYYY | Fully supported for shape/calendar validation | Actual invalid dates rejected. RR/YY century interpretation and NLS language/business semantics are not certified |
| FILLER / BOUNDFILLER | Fully supported physically | Bytes and datatype validated, no destination-column binding claimed |
| CONSTANT | Fully supported as metadata | Consumes no physical bytes; shown read-only |
| NULLIF / DEFAULTIF with supported predicates | Supported subset | Interpreted values displayed separately from raw values; unsupported predicates diagnosed |
| PRESERVE BLANKS | Supported subset | Raw bytes always preserved; named-field interpretation respects declaration |
| TRAILING NULLCOLS before WHEN/field list | Supported subset | Missing trailing fields allowed for structural inspection; missing business values remain unknown |
| SQL field transformation strings | Partially supported | Exact expression retained; no execution or model-authored replacement; validated export blocked |
| Unmapped intervals and extra trailing bytes | Fully supported | Visible in byte maps and preserved; not treated as arbitrary overflow errors |
| CRLF, LF, CR and no final terminator | Fully supported | Each record retains its original ending; vertical tabs are not line separators |
| INFILE 'path' "FIX n" / explicit fixed byte settings | Fully supported | Exact physical record length; partial final record fails validation |
| ASCII / US7ASCII, latin1 / WE8ISO8859P1, cp1252 / WE8MSWIN1252 | Fully supported | Strict single-byte decoding and byte offsets; conflicting charset settings diagnosed |
| Other record-format strings; VAR, CONTINUEIF, CONCATENATE | Unsupported | Explicit diagnostic; validated export blocked |
| Delimited fields, ENCLOSED BY, FIELDS TERMINATED BY, character-position semantics, UTF-8/multibyte DAT | Unsupported | No guessed fixed-width interpretation or silent decoding |
| Binary INTEGER/FLOAT, packed decimal, LOB/SDF, generated SEQUENCE/RECNUM, OPTIONS including SKIP | Unsupported/partial metadata | Explicit diagnostic or parse rejection; validated export blocked |
| OR/NOT/relational operators in conditions; arbitrary SQL conditions | Unsupported | Unevaluable, never assumed true |
| Overlapping physical fields within one branch | Partially supported | Diagnostic; validated export blocked. Identical spans across separate matching destinations remain inspectable |

## Numeric checks and sample scope

Studio applies declared-format checks to external numeric fields as an additional advisory rule. Oracle database conversion, target column types and NLS settings can accept or reject different text; the checker does not reproduce that execution environment. Readable `INTEGER EXTERNAL(4)` and binary `INTEGER(4)` are different declarations. See [the syntax and examples guide](ORACLE_EXAMPLES.md) and the [official SQL*Loader reference](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-sql-loader-field-list-contents.html).

The bundled library has 11 synthetic custom staging layouts / 26 variants, including 4,800-byte stream and fixed records. These are exercises, not Oracle-delivered formats or a claimed Oracle record-size limit. Exact generated lengths and expected outcomes are in `fixtures/oracle/manifest.json`.

## Processing and reconstruction limits

- CTL 2 MB / 100,000 tokens / 64 nesting levels; physical field offsets and fixed lengths at most 1,000,000 bytes.
- DAT and spreadsheet 12 MB each; combined request at most 30 MB; DAT at most 20,000 records. Spreadsheet at most 30 worksheets, 20,000 rows per sheet, 200 columns, 400,000 cells and 80 MB uncompressed XLSX contents. Formula cells are ignored with warnings; external workbook links are not followed.
- Reconstruction inspects at most 100 damaged records in a request, inserts at most 64 missing spaces, handles at most 128 fields and 16,384-byte target records, and uses an eight-second search budget. Multi-insertion beam search is limited to 12 steps and 2,048-byte records, with a beam width of 24. Wider supported records still receive boundary/trailing candidates.
- The repair API's record list can target another bounded group. Candidates outside these bounds are not guessed. No automatic repair policy is enabled. Deletion, substitution of unknown business characters, and full recovery of missing financial values require source evidence/manual edits.
- Candidates must preserve every non-whitespace source byte, preserve unmapped positions, satisfy supported datatypes and retain routing. Scores compare original field-value preservation, datatype checks, routing, exact spreadsheet evidence, edit distance and nearby same-type field consistency. Scores do not express calibrated probabilities.
- Absence of a datatype error cannot establish a CHAR field's business correctness. Cross-record semantic diagnostics use explicitly chosen spreadsheet join keys; duplicate DAT or sheet keys block reconciliation.
- No user/customer data was used in tests. The synthetic Lockbox fixture exercises seven destination branches, alternative routing, dates, amounts, gaps and repeated names. An additional test covers simultaneous matching destinations with different repeated-name offsets.
