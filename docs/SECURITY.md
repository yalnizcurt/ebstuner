# Security and deployment boundaries

## Implemented controls

- Server-side Bedrock native bearer tokens only. No key fields, frontend keys, browser persistence, logs, URLs, API responses or provider fallback. Official regional Runtime HTTPS endpoints, certificate verification, isolated token resolver, managed-file rotation and production short-term lifecycle checks.
- Random owner sessions stored as SHA-256 hashes, HttpOnly/SameSite cookies, project ownership checks on every API operation, constant-time comparison for the optional shared access gate, production startup gate and secure cookies. A project identifier alone cannot authorize access.
- Same-origin write checks, required custom request header, trusted Host validation, no permissive CORS, no-store/private data responses, CSP without inline script, frame denial, MIME-sniff protection, no-referrer policy and request correlation IDs.
- Bounded upload bodies, CTL/token/nesting/record limits, sheet/ZIP limits, explicit single-byte encodings, no INFILE path reads, SQL execution, model tool execution or model approval. Model JSON and all evidence/field references are validated. Raw values are HTML-escaped in the browser.
- Explicit manual/semantic approvals, state versions and record checksums, atomic bulk save, immutable originals, no truncation/rounding, approval byte verification during generation, draft policy disabled by default, auditable undo/reset and source downloads.
- Per-owner inference/analysis and login limits. Bounded reconstruction, model context/token/time/retry limits. SQLite lives in a private application directory and its database file is restricted to owner permissions.
- Raw financial values are omitted from operational events/logs. User-requested repair audit downloads necessarily contain proposal values/evidence; protect those as financial data. Backend exceptions return sanitized failures. Project data and review content remain in private project storage for the configured retention period.

## Supported deployment

Run one API worker. Publish only loopback locally; Compose does this by default. For remote use, keep the backend on a private network and terminate HTTPS at an organizational TLS proxy. Set `STUDIO_ENV=production`, a private `STUDIO_ACCESS_TOKEN` (≥32 characters), explicit `STUDIO_ALLOWED_HOSTS`, and an HTTPS `STUDIO_PUBLIC_ORIGIN`. Forward the original Host and protocol only from a trusted proxy. Set allowed hosts to include any private health-check hostname needed by your topology. Mount an encrypted persistent project volume, and mount the approved credential manager's private token file read-only into the app when AI is enabled.

Minimal proxy concept (replace the host and provision TLS according to your organization):

```caddyfile
studio.example.com {
    request_body {
        max_size 30MB
    }
    reverse_proxy 127.0.0.1:8000
}
```

A separate hosted frontend must preserve same-origin routing for `/api`; credentials never belong in static hosting environment variables. Back up private project storage and test restores. Configure an external identity gateway, session recovery and individual audit identities before a shared organizational rollout.

## Explicit remaining gaps

This is a bounded local implementation with production-oriented protections, not a completed enterprise deployment. Shared access-token login is not enterprise SSO/RBAC; owner cookies do not provide account recovery or multi-user project sharing. Browser cookie expiry or deletion can make retained projects inaccessible through the UI. Configure a proper identity/session lifecycle before using it as a long-lived team repository.

SQLite JSON snapshots, per-process rate limits and bounded in-memory parsing/search are not a distributed multi-tenant storage/worker architecture. Retention is enforced when repositories are accessed, not by an independent scheduler; physical removal from backups/WAL/caches is not a certified secure-erasure process. The app does not encrypt SQLite itself, provision TLS, mint/refresh AWS credentials, manage AWS account logging, guarantee regional residency, install organization-specific DLP, or deliver compliance certification. Those controls belong to the deployment environment.

No Docker runtime was installed here; the image/Compose build and remote TLS deployment were not executed. No real Bedrock key or regional model was configured, and no SQL*Loader/EBS environment was available. These are external acceptance checks, not successful tests. Do not load unreviewed output into a production ERP solely because structural validation passed.
