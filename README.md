# CTL–DAT Alignment Studio

A preparation and repair workstation for teams receiving fixed-width DAT files before an Oracle SQL*Loader/EBS import. Load a CTL and DAT, see which bytes belong to each field, locate damaged records, compare reference evidence, approve corrections and download the reviewed DAT plus its audit. The original file stays available byte for byte.

Groq with `openai/gpt-oss-120b` is the default AI provider. AI explains findings and proposes evidence-backed changes; the deterministic engine controls bytes, validation and approvals. Bedrock remains an explicit alternative. No cloud account is needed to inspect, edit or generate files.

The job ends at a reviewed file. The workstation does not submit an EBS concurrent request, run SQL*Loader, check database constraints or certify that EBS will accept a transaction.

## Run locally

Python 3.10+ is required (tested with Python 3.14.5).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn web:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open [the workstation](http://127.0.0.1:8000). `requirements.lock.txt` records the exact environment used for verification. The backend reads a private `.env` in the project directory at startup; existing environment variables take precedence. `.env` is ignored by Git and excluded from the Docker build.

Projects, immutable source bytes, working bytes, proposals, approval audit and owner sessions persist in `.studio/projects.sqlite3`. Keep your browser's HttpOnly session cookie to reopen its projects. Cookie expiry is seven days. Download originals and audits before clearing cookies. Local projects expire after seven days without a saved change by default; `STUDIO_RETENTION_DAYS` changes this policy (1–365 days).

## Start with the sample library

The workspace offers **11 layouts and 26 input variants**, from 6 to 4,800 ASCII bytes per record. Choose a sample and variant in a fresh project, or download its ZIP to inspect the CTL, DAT variants and reference CSV yourself. Each exercise says what to inspect and what a successful result means.

Try these in order:

1. **6 bytes · CHAR(2) + INTEGER EXTERNAL(4)**, invalid integer: inspect `BB-X12`, match `reference.csv` by `CODE`, and review the exact proposed `-012` quantity. `CHAR2INT4` is not one datatype: this layout has two fields.
2. **80 bytes**, missing spaces: compare the 73-byte damaged record with its 80-byte layout, approve trailing padding, then compare the output with `correct.dat`.
3. **4,800 bytes**, invalid amount: find the letter `O` in the amount, reconcile by `ITEM_NUMBER`, and review the evidence before changing it. A full-length record can still be wrong.
4. **4,800 bytes · FIX**, correct input: inspect two back-to-back records without line endings, then generate an unchanged output.

Other cases cover relative fields, scientific notation, dates, nullable/defaulted fields, fillers, constants, multiple record roles and an internal deleted space. Binary integers, SQL transformations and delimited fields are deliberate unsupported examples: validated export must stay blocked. See [the Oracle examples guide](docs/ORACLE_EXAMPLES.md) for every case, syntax and official references.

All examples are synthetic custom `XX` staging layouts. They are inspired by EBS integration work; they are not Oracle-delivered Lockbox CTLs. The 4,800-byte size is a requested exercise size, not an Oracle maximum. ASCII makes characters equal bytes in these examples; `POSITION` refers to bytes.

## Use your own files

1. Create a project; upload `demo_lockbox.ctl`, `truncated.dat`, and optional `reference_values.csv`.
2. Diagnostics identifies record 3 as 91 bytes against a 105-byte layout; other records remain inspectable.
3. Open Repair workspace. Compare internal-space candidates against the trailing-padding hypothesis, including actual scores and field changes. Approve **one** alternative. For this synthetic fixture, adding 14 trailing spaces reproduces `valid.dat` exactly.
4. For reference matching, choose worksheet `CSV`, map relevant columns, and select `RECORD_TYPE`, `BATCH_NAME`, and `ITEM_NUMBER` as join keys. Only rows with a complete, unique key participate. Leading zeros matter. Review conflicts; nothing overwrites DAT values automatically.
5. Use Data editor to change individual cells or selected rows. Type/column filters, frozen record numbers, original-value comparisons, modified/suggestion indicators, keyboard navigation and undo/redo are available. An explicit assumed-layout selector permits correcting damaged record markers without treating a guessed layout as a routing match. Choose left/right padding without counting spaces.
6. Byte inspector shows source/current records, one-based offsets, field boundaries, whitespace and actual hexadecimal bytes.
7. Generate DAT re-parses the current output, verifies checksums and applied proposals, then enables download. Download source bytes, validation JSON and the repair audit independently.

The supplied CSV intentionally differs in some identifier formatting and should be treated as evidence requiring review. A structurally valid file can still contain incorrect business values.

## Configure Groq

Keep the key in the backend environment or private `.env`, never in browser code or a committed file:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=your_private_key
GROQ_MODEL=openai/gpt-oss-120b
```

Restart the backend after changing `.env`, then use the configuration screen to test the connection. A live synthetic connection test succeeded with the configured model on 17 October 2026. This establishes API/model access, not the accuracy of every proposed repair.

Groq requests use strict JSON Schema output and server-side response validation. Defaults are 4,096 output tokens, temperature 0, a 60-second request budget and two transient-error retries. `GROQ_ENABLED=false` disables AI. There is no automatic provider fallback. See [Groq setup and limits](docs/GROQ.md), including official model and API references.

The five capabilities are schema intelligence, header mapping, corruption investigation, evidence-backed recovery and validation explanation. Values are masked by default. An explicit checkbox shares the selected record's fields and uniquely matched evidence. Recovery without evidence returns `INSUFFICIENT_EVIDENCE` locally without a model call. An AI proposal cannot apply itself.

To select Bedrock instead, set `LLM_PROVIDER=bedrock` and its backend AWS variables. Bedrock retains its managed token-file rotation and production credential checks. See [Bedrock setup](docs/BEDROCK.md).

## Tests and evaluation

```sh
.venv/bin/python -m pytest -q --junitxml=docs/test-results.xml
.venv/bin/python evaluate.py --fixtures fixtures
.venv/bin/python build_samples.py
```

The 17 October 2026 regression run passed **145 tests**, with one optional live Bedrock test skipped. Automated provider tests use explicit synthetic mocks; Bedrock also has an offline native SDK bearer-signing check. The live Bedrock test is skipped unless `RUN_LIVE_BEDROCK=1` is set with a working configured key and model. Actual Groq explanation and evidence-backed recovery were separately verified against synthetic samples; see the [live workflow evidence](docs/groq-workflow-verification.json).

The sample manifest records expected findings, record lengths and checksums; `build_samples.py` regenerates the synthetic library. The older evaluation writes computed metrics to `docs/evaluation-results.json`. Historical results in the engineering report are dated and must not be treated as measurements of the new Groq integration. The corpus measures structural validity and exact reconstruction separately. A valid reconstruction may still be a wrong hypothesis, which is why every candidate requires approval.

## CLI

The original standard-library CLI remains available:

```sh
python3 engine.py --ctl demo_lockbox.ctl --dat valid.dat
python3 engine.py --ctl demo_lockbox.ctl --dat truncated.dat --report /tmp/findings.json
python3 engine.py --ctl demo_lockbox.ctl --dat truncated.dat --approve-trailing-pad 3 --output /tmp/corrected.dat
python3 engine.py --ctl demo_lockbox.ctl --dat valid.dat --edit 3 CUSTOMER_NUMBER 0002060 --output /tmp/edited.dat
```

Use `--encoding`, `--record-format`, `--record-length` and `--align` when needed. Output may not overwrite the CTL or original DAT. Invalid/unsupported output requires explicit `--allow-draft-export`.

## Docker and deployment

```sh
docker compose up --build
```

Compose publishes only to `127.0.0.1:8000` and stores projects in a named volume. The container runs as a non-root user. Docker configuration is supplied; the current host has no Docker executable, so an image build has not been verified here.

For deployment, put the same-origin frontend/backend behind a TLS reverse proxy and organizational authentication. Set `STUDIO_ENV=production`, a private `STUDIO_ACCESS_TOKEN` of at least 32 characters, `STUDIO_ALLOWED_HOSTS`, an HTTPS `STUDIO_PUBLIC_ORIGIN`, private persistent storage, and credentials for the selected provider. Production Bedrock AI additionally requires its managed short-term token file. The backend refuses incomplete production configuration. The shared token is an additional gate, not an enterprise identity system. See [security and deployment](docs/SECURITY.md).

## Scope and documentation

- [Oracle examples and syntax](docs/ORACLE_EXAMPLES.md)
- [Groq configuration](docs/GROQ.md)
- [Initial engineering report and historical test status](docs/ENGINEERING_REPORT.md)
- [Starting-repository gap analysis](docs/IMPLEMENTATION_GAP_ANALYSIS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API contracts](docs/API.md)
- [CTL compatibility matrix and limits](docs/CTL_COMPATIBILITY.md)
- [Computed evaluation](docs/evaluation-results.json)

This is a bounded local implementation, not a complete Oracle SQL*Loader emulator or a certification of Oracle EBS business validity. Unsupported CTL behavior blocks validated export. Unvalidated draft downloads are disabled unless `STUDIO_ALLOW_DRAFT_EXPORT=true` is explicitly configured and the user requests a draft.
