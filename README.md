# CTL–DAT Alignment Studio

A working local workstation for byte-preserving SQL*Loader inspection, deterministic reconstruction, spreadsheet reconciliation, human review, cell editing and validated DAT generation. Amazon Bedrock Runtime is the only AI integration. AI is optional; no AWS account is needed for deterministic processing or automated tests.

## Run locally

Python 3.10+ is required (tested with Python 3.14.5).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn web:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open [the workstation](http://127.0.0.1:8000). `requirements.lock.txt` records the exact environment used for verification. The application does not automatically read `.env`; export backend configuration before starting it. Docker Compose reads `.env` itself.

Projects, immutable source bytes, working bytes, proposals, approval audit and owner sessions persist in `.studio/projects.sqlite3`. Keep your browser's HttpOnly session cookie to reopen its projects. Cookie expiry is seven days. Download originals and audits before clearing cookies. Local projects expire after seven days without a saved change by default; `STUDIO_RETENTION_DAYS` changes this policy (1–365 days).

## Demonstrate the workflow

1. Create a project; upload `demo_lockbox.ctl`, `truncated.dat`, and optional `reference_values.csv`.
2. Diagnostics identifies record 3 as 91 bytes against a 105-byte layout; other records remain inspectable.
3. Open Repair workspace. Compare internal-space candidates against the trailing-padding hypothesis, including actual scores and field changes. Approve **one** alternative. For this synthetic fixture, adding 14 trailing spaces reproduces `valid.dat` exactly.
4. For reference matching, choose worksheet `CSV`, map relevant columns, and select `RECORD_TYPE`, `BATCH_NAME`, and `ITEM_NUMBER` as join keys. Only rows with a complete, unique key participate. Leading zeros matter. Review conflicts; nothing overwrites DAT values automatically.
5. Use Data editor to change individual cells or selected rows. Type/column filters, frozen record numbers, original-value comparisons, modified/suggestion indicators, keyboard navigation and undo/redo are available. An explicit assumed-layout selector permits correcting damaged record markers without treating a guessed layout as a routing match. Choose left/right padding without counting spaces.
6. Byte inspector shows source/current records, one-based offsets, field boundaries, whitespace and actual hexadecimal bytes.
7. Generate DAT re-parses the current output, verifies checksums and applied proposals, then enables download. Download source bytes, validation JSON and the repair audit independently.

The supplied CSV intentionally differs in some identifier formatting and should be treated as evidence requiring review. A structurally valid file can still contain incorrect business values.

## Configure Amazon Bedrock

Set `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION`, and `BEDROCK_MODEL_ID` exclusively in the backend environment. Choose a Converse model/inference profile that your account can access in that Region; no universally available model is assumed. Use the configuration screen's **Test Bedrock connection** button to verify access. It sends only a synthetic connection acknowledgement.

`BEDROCK_ENABLED=false` disables AI. Other defaults: maximum tokens 4096, temperature 0, total request timeout budget 90 seconds, up to three transient-error retries. `BEDROCK_STRUCTURED_MODE=json_schema` uses native constrained JSON output. For a model supporting strict tool use instead, explicitly set `tool`; the sole tool only returns structured review data and never executes an action. Unsupported model settings produce an error without provider fallback.

The five capabilities are schema intelligence, header mapping, corruption investigation, evidence-backed recovery and validation explanation. Values are masked by default. An explicit checkbox shares the selected record's fields and uniquely matched evidence. Recovery without evidence is answered deterministically as `INSUFFICIENT_EVIDENCE` without making a model call. An AI proposal cannot apply itself.

For rotation without restarting, mount a private `BEDROCK_TOKEN_FILE` containing a JSON object with `token` and `expires_at` (UTC ISO timestamp). An approved external credential manager must atomically refresh it. The backend re-reads it for each invocation. Production AI requires this file and a valid remaining short-term lifetime of 30 seconds to 12 hours; an environment-only credential is allowed locally. This repository does **not** mint credentials or provision an AWS account. See [Bedrock setup](docs/BEDROCK.md).

## Tests and evaluation

```sh
.venv/bin/python -m pytest -q --junitxml=docs/test-results.xml
.venv/bin/python evaluate.py --fixtures fixtures
```

Automated Bedrock tests use explicit synthetic mocks, plus an offline native SDK bearer-signing test. The live connection test is skipped unless `RUN_LIVE_BEDROCK=1` is set with a working configured key and model. A skip is not a successful authentication test.

The evaluation writes computed metrics to `docs/evaluation-results.json`. The corpus measures structural validity and exact reconstruction separately. A valid reconstruction may still be a wrong hypothesis, which is why every candidate requires approval.

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

For deployment, put the same-origin frontend/backend behind a TLS reverse proxy and organizational authentication. Set `STUDIO_ENV=production`, a private `STUDIO_ACCESS_TOKEN` of at least 32 characters, `STUDIO_ALLOWED_HOSTS`, an HTTPS `STUDIO_PUBLIC_ORIGIN`, private persistent storage, and managed short-term Bedrock credentials if AI is enabled. The backend refuses incomplete production configuration. The shared token is an additional gate, not an enterprise identity system. See [security and deployment](docs/SECURITY.md).

## Scope and documentation

- [Engineering report and test status](docs/ENGINEERING_REPORT.md)
- [Repository gap analysis](docs/IMPLEMENTATION_GAP_ANALYSIS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API contracts](docs/API.md)
- [CTL compatibility matrix and limits](docs/CTL_COMPATIBILITY.md)
- [Computed evaluation](docs/evaluation-results.json)

This is a bounded local implementation, not a complete Oracle SQL*Loader emulator or a certification of Oracle EBS business validity. Unsupported CTL behavior blocks validated export. Unvalidated draft downloads are disabled unless `STUDIO_ALLOW_DRAFT_EXPORT=true` is explicitly configured and the user requests a draft.
