# Amazon Bedrock configuration and verification

Only `AmazonBedrockProvider` creates an LLM client. It uses Boto3's Bedrock Runtime `Converse` operation and native bearer authentication. No direct vendor SDK/API, alternative provider, local runtime, external tool execution or fallback routing is implemented.

Set backend environment variables `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION` and `BEDROCK_MODEL_ID`. The model may be a model identifier or inference-profile identifier/ARN. Select one actually enabled for your account and Region; the application intentionally supplies no default model. The provider fixes its endpoint to the configured AWS Region's official Bedrock Runtime HTTPS host and ignores configured endpoint overrides.

The SDK token resolver is isolated per client, using the native scoped environment-token provider. Managed-file token rotation therefore does not mutate process-wide environment variables or reuse an expired cached client. Automated testing verified a synthetic token produces the native `Authorization: Bearer …` signing path. Boto3 1.43.114 was installed for verification; runtime configuration requires at least 1.42. The installed SDK exposes both `outputConfig` and strict tool specifications.

These choices follow AWS's [API-key documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html) and [structured output documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html). Native JSON Schema output uses `outputConfig.textFormat`. Explicit `BEDROCK_STRUCTURED_MODE=tool` uses a strict `submit_review` response tool when the chosen model supports it. There is no automatic downgrade; unsupported model/schema/inference settings are reported as errors. Pydantic validates every response server-side in either mode.

## Connection screen

The authenticated status route returns region, model, enabled/configured status, credential mode, accessibility from the last test, last successful test time and a sanitized error. It never returns the key or a token fingerprint. Region/model/key changes invalidate cached accessibility. Testing makes a small synthetic structured Converse request; it is the effective model-access check. Model lists or guessed model IDs are not used to claim accessibility.

No real backend token, Region or model was configured during this implementation. The live test was skipped. Native signing and mocked response/error tests passed; they do **not** establish AWS authentication, regional entitlement or live model compatibility. Configure your environment and run the UI test or `RUN_LIVE_BEDROCK=1 .venv/bin/python -m pytest -q tests/test_bedrock.py::test_live_bedrock_connection` to verify these externally controlled prerequisites.

## Credentials and rotation

Local development accepts a backend environment token. For no-restart rotation, point `BEDROCK_TOKEN_FILE` at a private mounted JSON file with `token` and `expires_at`. The timestamp must include its timezone. The approved external credential manager should replace the file atomically before expiry; the next request re-reads it. Files with expired/invalid credentials fail closed. Production AI requires a managed file with 30 seconds to 12 hours remaining lifetime. Provisioning the issuing identity, short-term refresh process and its permissions is an operator responsibility; this application does not pretend to refresh credentials by itself.

Do not add credentials to source files, frontend configuration, request metadata or logs. Boto3/botocore transport logging is kept at WARNING at startup. Invocation payload logging is not enabled by the app; the AWS account operator must also keep account-level model invocation logging disabled unless separately authorized and protected. Run the app behind TLS for remote access.

## Limits, privacy and cost

Input context is limited to 48 KB, output to configured token limits, and parsed JSON to 120 KB. Capabilities are allowlisted; a request performs one inference step. Timeouts and transient retries/backoff share a bounded request budget. Per-owner rate limits apply to inference and connection tests. Auth/access errors and timeouts never trigger another provider.

By default, the corruption and explanation contexts mask DAT values and omit evidence values. Recovery without unmasked, key-matched evidence is answered locally. Explicit sharing includes the selected record's fields and matched evidence only, not entire DAT or workbook contents. Schema and header names may themselves be sensitive; obtain your organization's approval before enabling this feature for client files.

Usage counts are taken from Bedrock responses and persisted in the project audit. The optional per-million input/output price variables calculate an estimate; with no operator-supplied prices, cost is reported as unknown. No price or acceptance metric is fabricated. Confidence labels and weighted scores are uncalibrated review aids.
