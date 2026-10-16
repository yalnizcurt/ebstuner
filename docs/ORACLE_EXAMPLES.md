# Oracle syntax and synthetic sample exercises

Use these files to learn the workstation and verify a repair against known bytes. They model custom `XX` staging schemas, with EBS-inspired receipt fields and record roles. They are not copies of Oracle-delivered CTLs, a complete EBS interface implementation or a certification of ERP acceptance.

## `CHAR2INT4`: two fields, two different numeric representations

`CHAR2INT4` is not a SQL*Loader datatype. This project's six-byte text example declares:

```sql
LOAD DATA
INFILE 'correct.dat'
INTO TABLE XX_CHAR_INT
(
  CODE     POSITION(1:2) CHAR(2),
  QUANTITY POSITION(3:6) INTEGER EXTERNAL(4)
)
```

For its sample row `AA0042`, Studio displays `CODE=AA` and `QUANTITY=0042`, preserving the leading zeros.

| Declaration | Representation | Studio sample |
|---|---|---|
| `CHAR(2)` | Two character bytes in this ASCII fixture | `AA` |
| `INTEGER EXTERNAL(4)` | Readable numeric text | `0042`, `-012` |
| `FLOAT EXTERNAL(12)` | Readable regular/scientific numeric text | `1.25E+03`, space padded |
| `INTEGER(4)` | Four binary bytes; platform representation matters | Illustrative little-endian 42, deliberately unsupported |

`POSITION` uses one-based byte offsets with inclusive ends, even when character-length semantics apply elsewhere. `POSITION(*)` follows the prior field. For multiple table layouts, use an explicit starting position when the record must restart; the cursor does not restart automatically. These distinctions follow the [Oracle SQL*Loader Field List Reference, sections 10.2 and 10.4](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-sql-loader-field-list-contents.html).

Studio's integer/decimal/exponent format checks are additional advisory rules. They do not reproduce target database conversion, NLS settings or constraints. The nullable exercise uses `PRESERVE BLANKS` so its blank numeric `DEFAULTIF` can produce zero; the engine keeps those source bytes blank. See [Oracle's field processing and blank-preservation rules, sections 10.6 and 10.12](https://docs.oracle.com/en/database/oracle/oracle-database/19/sutil/oracle-sql-loader-field-list-contents.html).

## How this relates to EBS AutoLockbox

Oracle Receivables documents these delivered transmission formats and locates their control files under `$AR_TOP/bin`:

| Control file | Documented purpose |
|---|---|
| `arxmpl.ctl` | Example with header, receipt and overflow roles |
| `ardeft.ctl` | Default BAI format |
| `arconv.ctl` | Payment conversion from another system |
| `arxcurr.ctl` | Cross-currency receipts |
| `arzeng.ctl` | Japanese Zengin bank input |

A changed transmission format needs a corresponding CTL change. This relationship is documented in [Oracle Receivables Implementation Guide — Transmission Formats](https://docs.oracle.com/cd/E18727_01/doc.121/e13510/T447343T454906.htm). Use your installed release's delivered files and configured transmission definition as the authority for a real integration. The sample names, field offsets and record markers below are this project's choices.

## The bundled library

There are **11 layouts / 26 input variants** under `fixtures/oracle/`. Every sample directory contains `layout.ctl`, `correct.dat`, `reference.csv` and any damaged DAT variants. `manifest.json` records expected findings, field offsets, exact record lengths and checksums. `build_samples.py` reproducibly generates them.

All samples use ASCII, so characters equal bytes. Stream examples use CRLF terminators outside the stated record width; `fixed_4800` has no separators. The 4,800-byte width is an exercise requirement, not an Oracle maximum or a standard EBS transmission size.

| Sample directory | Bytes per record | Combinations and variants | Join key |
|---|---:|---|---|
| `char2_int4` | 6 | `CHAR` + readable integer; correct / invalid integer | `CODE` |
| `mixed_80` | 80 | Leading-zero ID, signed decimal, leap-day date; correct / missing spaces / bad date | `TRANSACTION_ID` |
| `relative_240` | 240 | Relative positions, signed integer, scientific rate, date; correct / bad exponent | `TRANSACTION_ID` |
| `nullable_480` | 480 | Blank-preserving defaults/nulls, filler and constant; correct / nonblank invalid amount | `TRANSACTION_ID` |
| `multi_record_960` | 960 | Custom header, receipt, overflow and trailer destinations; correct / unknown marker | `ITEM_NUMBER` |
| `receipt_2400` | 2,400 | Receipt fields and 15 attributes; correct / missing spaces / bad amount / bad date / internal deletion | `ITEM_NUMBER` |
| `receipt_4800` | 4,800 | Receipt fields, 15 attributes and eight invoice/application pairs; same five variants | `ITEM_NUMBER` |
| `fixed_4800` | 4,800 | Two back-to-back fixed records; correct / bad amount | `ITEM_NUMBER` |
| `binary_integer4` | 6 | Binary integer reference; compatibility must block validated export | None |
| `sql_expression` | 10 | SQL transformation reference; retained but never executed | None |
| `delimited_csv` | 7 | Delimited-field reference; fixed-width editor must reject unsupported semantics | None |

“Correct” in the last three rows means intentionally well-defined reference input for an unsupported feature, not a Studio validation success. Those cases must stay blocked. For supported samples, `correct.dat` is the byte-level comparison target; it does not prove business acceptance by an ERP.

## Walk through a repair

1. Create a new project and load `receipt_4800` → missing spaces. Loading samples into an existing source set is blocked so originals cannot be replaced silently.
2. Check the exercise and field map. Record 2 has 4,786 bytes against the 4,800-byte layout. View its original/current bytes in the inspector.
3. Run deterministic analysis. Review the trailing-padding hypothesis and approve it explicitly. Other alignment hypotheses may also look structurally plausible; scores are review aids, not probabilities.
4. Generate and download. Compare the result to that sample's `correct.dat`. Its two CRLF-terminated records total 9,604 bytes. The actual live workflow and exact comparison are recorded in [Groq verification](GROQ.md#verified-on-17-october-2026).
5. Repeat in a new project using bad amount. Map `reference.csv` by `ITEM_NUMBER`; the displayed source evidence gives a specific known value. Review the proposal before approval. AI explanation can assist, but it does not supply authority for inventing an amount.

Try the internal-deletion variants separately. Inserting one missing byte can shift many subsequent fields back into place, but wide CHAR spans can leave multiple plausible answers. The current wider-than-2,048-byte reconstruction uses boundary/trailing candidates, not the full multi-insertion beam. An exact recovery is not guaranteed; keep the original and use evidence/manual review where needed.

## Use real EBS files

Start from your installed release's CTL and matching transmission definition, with bank-approved test data and expected outcomes. Confirm encoding, record framing, actual field widths, database transformations, destination tables and business validation with the EBS team. Any unsupported Studio feature blocks a validated export; source downloads stay available. Only a controlled run through the appropriate Oracle/EBS test environment can establish import acceptance.

The supported subset and processing limits are listed in [CTL compatibility](CTL_COMPATIBILITY.md). No SQL*Loader executable, Oracle database or EBS instance was used to certify this synthetic library.
