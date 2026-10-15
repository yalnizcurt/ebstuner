# API contracts

All application routes use `/api`. `POST /api/session` establishes a seven-day HttpOnly, SameSite=Strict owner session. Its JSON body is `{ "access_token": "…" }` when the backend access gate is configured, otherwise `{}`. `GET /api/session` reports whether sign-in is required. Cookies authorize project ownership; project IDs alone do not. Every write requires `X-Studio-Request: 1` and a permitted Origin. Production cookies also require HTTPS. An authenticated OpenAPI document is available at `GET /api/openapi.json`.

Project/record/diagnostic/proposal responses use Pydantic contracts in `models.py`, including typed statuses, byte fields, score features and pagination payloads. Write bodies are Pydantic models with unknown properties forbidden. Project mutations require `version`, obtained from the latest `project.version`. A stale version returns 409 with `STALE_VERSION`; refresh and review before retrying. Validation errors return 422, missing/foreign projects 404, unauthenticated requests 401, forbidden origins 403, limits 413/429. Bedrock failures return a sanitized capability error; credentials are never returned. Responses include a request correlation ID and no-store headers.

| Method | Route | Contract |
|---|---|---|
| POST | `/projects` | `{name}` → empty owner project, version 1 |
| GET | `/projects` | Owner projects only |
| DELETE | `/projects/{id}` | Delete the authorized project |
| POST | `/projects/{id}/files` | Multipart `ctl`, `dat`, optional `supplement`; `version`, `encoding`, `record_format` (auto/stream/fixed), optional `record_length`, `role`. Source set may be imported only once |
| POST | `/projects/{id}/parse-ctl` | Re-read existing compiled schema/state |
| POST | `/projects/{id}/inspect-dat` | Reinspect existing DAT/state |
| GET | `/projects/{id}/schema` | Compiler IR, source spans, gaps, diagnostics and compatibility |
| GET | `/projects/{id}/diagnostics` | Counts, checksum, encoding, record-format and export status |
| GET | `/projects/{id}/state` | Paged diagnostics plus selected record, schema, sheet preview, current proposals and recent audit. Query `record`, `page`, `page_size` (≤100), `search`, `record_type`, `status`, `sort`, `descending` |
| GET | `/projects/{id}/records` | Cell-grid page; query `page`, `page_size` (≤100), `record_type`, optional explicit `layout_branch` for hypothetical inspection |
| GET | `/projects/{id}/records/{n}` | Full record fields plus source/current preview and actual hex bytes |
| POST | `/projects/{id}/analyze` | `{version, records?: [n], weights?: {...}}`; bounded deterministic reconstruction |
| POST | `/projects/{id}/map-spreadsheet` | `{version, sheet, mapping: {HEADER: CTL_FIELD}, keys:[CTL_FIELD], role}`. Reference/example/transaction rows reconcile by complete, unique exact keys. For `field_specifications`, map headers to FIELD_NAME / START / END / DATATYPE / TARGET_TABLE; keys optional |
| PATCH | `/projects/{id}/records` | `{version, edits:[{record, field, value, align?:left/right, branch?:index}]}`; up to 500 atomic edits. Supplying a branch can explicitly select a hypothetical layout for manual marker correction |
| PATCH | `/projects/{id}/records/{n}` | Same edits envelope, all edits must name record n |
| GET | `/projects/{id}/repair-proposals` | `offset`, `limit` (≤250), `pending_only`; returns total and page |
| POST | `/projects/{id}/repairs/review` | `{version, ids:[...], action:approve/reject, overrides?:{proposal_id: edited_value}}`; atomic bulk review; conflicting alternatives/overlapping patches rejected |
| POST | `/projects/{id}/repairs/{repair_id}/approve` | `{version}`; explicit single-proposal approval |
| POST | `/projects/{id}/repairs/{repair_id}/reject` | `{version}`; reject pending proposal |
| POST | `/projects/{id}/history/{undo,redo,reset}` | `{version}`; reset is itself undoable |
| POST | `/projects/{id}/ai/recover` | `{version, capability:schema/mapping/investigate/recover/explain, record:n, share_values:false}`; at most one Bedrock inference step, returns persisted review/state or deterministic skip reason |
| GET | `/ai/bedrock/status` | Safe connection/configuration metadata |
| POST | `/ai/bedrock/test` | Synthetic Converse test of configured model |
| POST | `/projects/{id}/validate` | Fresh re-parse and approval/source round-trip comparison |
| POST | `/projects/{id}/generate` | `{version, draft:false}`; validates and records output checksum; draft requires server policy and explicit request |
| GET | `/projects/{id}/download` | Generated current DAT only; checksum/status headers |
| GET | `/projects/{id}/download?original=true` | Immutable source bytes, independent of current validation |
| GET | `/projects/{id}/report` | Complete downloadable validation JSON, schema, checksums, changed fields/routing, applied-byte checks |
| GET | `/projects/{id}/audit` | Complete downloadable project audit/proposal/AI usage JSON |

Branch indexes are zero-based; physical record numbers and CTL byte positions are one-based. End positions are inclusive. Unsupported syntax is visible and cannot receive a validated export label. A `validated_with_warnings` status may include unresolved spreadsheet evidence conflicts while preserving current DAT values. None of the success statuses certifies Oracle EBS import acceptance.
