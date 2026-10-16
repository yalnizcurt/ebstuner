# Groq configuration and verification

Groq is the default provider for this project, using model ID `openai/gpt-oss-120b`. Groq documents that model and its strict JSON Schema support. See the official [model page](https://console.groq.com/docs/model/openai/gpt-oss-120b) and [structured output reference](https://console.groq.com/docs/structured-outputs).

## Set up the backend

Use environment variables or a private `.env` in the repository root:

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=your_private_key
GROQ_MODEL=openai/gpt-oss-120b
```

The backend loads `.env` at startup without executing its contents as shell code. Existing process environment variables take precedence. Keep local secrets private, restrict `.env` permissions to its owner, and restart the backend after changing the file. `.env` is ignored by Git and excluded from the container build. Inject backend secrets separately when deploying a container; do not put them in static/frontend configuration.

Open the configuration screen and run the connection test. `GET /api/ai/status` reports the selected provider/model and a sanitized status; `POST /api/ai/test` sends a synthetic acknowledgement request with no customer data. These authenticated endpoints do not return credentials. The former `/api/ai/bedrock/status` and `/api/ai/bedrock/test` routes remain compatibility aliases and follow the selected provider.

The implementation uses HTTPX directly against `https://api.groq.com/openai/v1/chat/completions`. Its request shape follows Groq's [OpenAI-compatible API](https://console.groq.com/docs/openai); no Groq or OpenAI SDK is required. The `openai/` prefix in the model identifier does not send this integration's requests to the OpenAI API.

| Setting | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `groq` | Explicit provider selection; `bedrock` is the alternative |
| `GROQ_API_KEY` | Empty | Required private backend credential |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Configured model; availability is checked by the live test |
| `GROQ_ENABLED` | `true` | Set `false` for deterministic-only processing |
| `GROQ_MAX_TOKENS` | `4096` | Output token limit, bounded to 256–16,384 |
| `GROQ_TIMEOUT_SECONDS` | `60` | Shared request/retry budget, bounded to 5–120 seconds |
| `GROQ_MAX_RETRIES` | `2` | Transient HTTP retries, bounded to 0–3 |

Provider errors never switch to another provider. Authentication/access/model failures, rate limits, timeouts, incomplete responses and invalid JSON return safe errors while deterministic tools remain usable. There is no automatic downgrade from strict schema output.

## What AI contributes

The schema, mapping, investigation, recovery and explanation capabilities use the existing typed review contract. Requests send temperature 0, low reasoning effort and strict JSON Schema output. Input context is bounded to 48 KB and streamed response bytes to 120 KB. Server-side validation checks the result again; schema compliance alone does not establish a correct financial value.

Values are masked by default. Explicit sharing exposes the selected record's fields and uniquely key-matched reference evidence. Schema/table/field/header names can themselves be sensitive. Obtain authorization for external processing of client data before enabling it. This application does not establish a provider retention, residency or compliance policy on your behalf.

Recovery without source evidence returns `INSUFFICIENT_EVIDENCE` locally. Returned values and evidence references must match the selected record, schema and exact imported reference data. AI cannot execute SQL/code, approve a proposal or directly modify bytes. Approval, stale-version checks and final byte validation remain deterministic.

## Verified on 17 October 2026

A live synthetic Groq connection test succeeded with `openai/gpt-oss-120b`.

A subsequent actual API workflow imported the `receipt_4800` missing-spaces variant with record lengths 4,800 and 4,786. A masked Groq explanation succeeded, reporting 3,054 input and 167 output tokens. After explicit approval of the deterministic trailing-padding candidate, generation produced 9,604 bytes: two 4,800-byte records and their CRLF terminators. The generated output matched the known-good `correct.dat` byte for byte, with SHA-256:

```text
4bd159c8b5b421afa6976a7185f7667924385e6dd35c36cc30d1546f625b38e4
```

A live evidence-backed recovery also passed for `char2_int4`: after matching the reference CSV by unique `CODE`, Groq proposed changing the damaged quantity `-X12` to the exact reference value `-012`, citing `CSV!3:2`. Explicit approval and generation reproduced `correct.dat` exactly. That request used 846 input and 273 output tokens. No real financial data was shared.

See the recorded [workflow verification](groq-workflow-verification.json). These observations establish functioning live explanation and evidence-backed recovery requests and complete synthetic repair/export paths. They do not measure general model accuracy, financial recovery accuracy or Oracle/EBS load acceptance. Token usage is recorded; monetary cost remains unknown unless separately measured. The final regression run passed 145 tests, with one optional live Bedrock test skipped; see [the current test artifact](test-results.xml).

Live Bedrock entitlement/model access, Docker builds, production deployment and actual Oracle SQL*Loader/EBS imports remain unverified. For the retained provider's setup and credential lifecycle, see [Bedrock configuration](BEDROCK.md).
