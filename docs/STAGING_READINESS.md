# NAFSIY private staging engineering report

Date: 2026-10-31, Asia/Tashkent. **NOT READY FOR PRIVATE STAGING**.

Local implementation is reviewable, but the external recovery, infrastructure and
human release gates below are unresolved. Passing automated tests does not establish
religious correctness, licensing permission, production security or deployment readiness.

## Local follow-up validation ? 2026-10-31

Baseline for this follow-up: committed V0.9.7 `fab7d44`, clean working tree;
head remains **e6f1027a93bd**. The 213-test full-suite result below is retained as
historical evidence. This follow-up reran the affected **six backend tests** (five
deployment tests plus one staging-health test), not the entire suite.

| Remaining local task | Exact result |
| --- | --- |
| Docker backend/frontend execution | **BLOCKED**: Docker/Podman absent from PATH and standard installation locations; WSL launcher returned usage text, no usable Linux environment identified. No images were built or run. |
| Production-like configuration validation | **PASS**: synthetic settings only, no DB connection; rejects credentials in frontend origin, invalid ports, insecure cookies, wrong origins, disabled verification, deletion enablement, missing providers and excessive capacity. This does not validate provider credentials. |
| Render blueprint and startup review | **PASS**: current provider schema validation; private API, web HTTP liveness, no startup migrations/automatic deploys. Added 150s platform shutdown allowances, 130s Uvicorn grace and direct Node frontend entrypoint. Linux signal behavior remains BLOCKED without a Linux runtime. |
| Staging health checks | **PASS**: isolated in-memory tests return 503 for absent/wrong schema revision and 200 for exact head; no primary Neon connection. |
| Next lint / production build / TypeScript | **PASS**, rebuilt with same-origin API and development build settings to test runtime staging behavior separately. |
| HTTPS frontend/backend integration and synthetic load | **PASS**: verified local TLS, Secure cookies and runtime HSTS; 100/100 reads at concurrency 8 (p50 656.04ms, p95 716.59ms, p99 1168.96ms); four AI requests returned two 200 and two 429; 31.53s request completed through the proxy. Synthetic Windows/SQLite scope only. |
| Live SMTP, encrypted offsite recovery, Neon 18 restore, Render deployment | **BLOCKED / NOT PERFORMED**, unchanged. Local fixtures are not evidence for these external gates. |

Local fixes: Next proxy timeout increased from the installed default of 30s to
135s for the backend's 120s generation budget; frontend starts Node directly for
container signal delivery; frontend HSTS is emitted from the runtime staging proxy
rather than depending on build-time APP_ENV; malformed/credential-bearing public
origins are rejected. `experimental.proxyTimeout` is specific to the pinned Next
version and must be revalidated during upgrades.

The new `tools/run_local_staging_checks.py` owns loopback ports 8103/3002/3443,
a temporary SQLite database and an ephemeral certificate trusted only by its test
client. It seeds synthetic verified accounts directly; it does **not** exercise
external email delivery. Deletion stays disabled, including inside this fixture.
It exercises built Next in runtime staging mode through real local TLS, validates
Secure/HttpOnly/SameSite cookies, CSRF/Origin/ownership, idempotent replay, upstream
error sanitization/recovery and logout. No real Groq calls or Neon connections are
made. Results are in `evaluations/local_staging_results.json`; Windows/TLS bridge
and synthetic SQLite/AI latency must not be presented as Render/Neon benchmarks.

Remaining owner/infrastructure requirements are unchanged: provide an existing
Linux/Docker runner or approve host installation/feature enablement; authorize
separate Render/Neon resources and reviewer scope; supply SMTP/test-recipient,
offsite/key-recovery and monitoring credentials; authorize a new empty PG18 restore
target and approved provider-call budget; complete privacy, erasure-journal and
scholarly/licensing review. **No deployment, commit, push, paid provisioning,
production DB connection or account-deletion enablement occurred in this follow-up.**

Follow-up changed files (separate from the historical V0.9.7 inventory below):

- `Dockerfile.backend`
- `backend/core/deployment.py`
- `docs/RENDER_STAGING.md`
- `docs/STAGING_READINESS.md`
- `evaluations/local_staging_results.json`
- `frontend/Dockerfile`
- `frontend/next.config.ts`
- `frontend/src/proxy.ts`
- `render.yaml`
- `requirements-dev.txt`
- `tests/local_staging_server.py`
- `tests/test_deployment.py`
- `tests/test_staging_health.py`
- `tools/run_local_staging_checks.py`

## Repository and preservation

Initial branch `main`, clean working tree, commit `c0fef4e` (V0.9.6). Existing source,
data and migration history were preserved. No commit, push, provisioning, public
publication, primary migration, real account erasure or real email delivery occurred.
The live `.env` and frontend `.env.local` were not edited. Configured current secret
values were checked against tracked/new files and all reachable Git revision archives: zero
matches. Private env, backup and temporary paths are ignored. This is a scoped
configured-secret audit, not proof that every possible historic credential is absent.

Latest repository Alembic revision: **e6f1027a93bd**, parent **c4d8e2a9106f**.
It adds `auth_users.email_verified`, `auth_users.auth_version` and hashed
`auth_recovery_tokens` with expiry/user indexes and account-erasure cascade.
Every historical migration byte checksum is unchanged; only the new revision and
its reviewed schema fingerprint were added to the bootstrap integrity manifests.
The new head was applied only to owned disposable local PostgreSQL databases.
Neither the primary nor shared Neon test schema was migrated or used as a restore
target. Previous documentation's c4d8e2a9106f test-branch state is historical evidence,
not a claim that this new head is already present on Neon.

## Completed work by phase

1. **Audit:** verified branch/worktree/history, private-file ignores, configured
   secret matches and historical migration preservation. Pinned the installed
   runtime dependency closure; separated deployment-validation dependencies.
2. **Authentication:** verified-email gate, expiring single-use verification/reset
   links, SHA-256 token storage, generic recovery responses, source/pair limits,
   existing signed anonymous CSRF/Origin protection, mandatory new login after
   reset, and account-wide JWT generation revocation on logout/reset. Copied JWTs
   and bootstrap scopes are rejected after revocation. In-flight password logins
   retain the version that was authenticated, so concurrent reset cannot upgrade
   an old-password login into a new session. SMTP uses an adapter with mandatory
   STARTTLS; default development delivery discards messages without logging links.
   Browser UI imports and removes token fragments, supports successive links in
   one tab, requires explicit confirmation and stores no password/token locally.
3. **Database/recovery:** preserved guarded baseline/bootstrap and restore tooling;
   exercised the new schema on isolated PG16. Added an operator-only restic adapter
   with snapshot receipt and full repository integrity check, staging-only scheduled
   backup runner and cron/wrapper examples. Added disabled account privacy/deletion
   UX and the crash/replay/quarantine design in ERASURE_REPLAY.md. The external
   journal, offsite repository, scheduler and PG18 proof remain blocked.
4. **Render preparation:** schema-validated blueprint with gated Next web service
   and private FastAPI service, non-root Dockerfiles, `.dockerignore`, safe startup
   without DDL, separate environment templates, same-origin API proxy, explicit
   HTTPS origins/Secure cookies, staging configuration rejection, health checks,
   reviewed role-grant example and deployment/rollback runbook.
5. **Reliability:** fixed-field structured HTTP telemetry, generic unexpected-error
   responses, response/security headers, private CLI diagnostics, offsite receipt
   visibility, bounded recovery-token cleanup and detailed alert thresholds.
   Verified Groq model pricing from provider documentation; staging uses $0.15/$0.60
   per million input/output tokens, while development estimates remain conservative.
   Local PostgreSQL multiprocess admission, budget, lease and crash-recovery tests
   and a 16-request single-use token race through a two-connection pool passed.
6. **Source policy:** existing code allowlist/restrictions are unchanged. Tafsiri
   Hilol and permission-pending Uzbek/Russian translations and hadith remain blocked.
   Scholarly review, rights-holder permission, edition collation and independent
   multilingual relevance review remain human gates. Tests do not certify religion.
7. **Regression:** backend, real isolated PostgreSQL, Alembic, frontend and browser
   checks were executed. Detailed final results are recorded below. No paid Groq
   generation or real SMTP/offsite operation was used to substitute for a missing gate.

## Test results

Final backend regression after the cookie-issuance race fix: **213/213 PASS**,
zero skips, in **129.804 seconds**: 180 local/unit/regression tests, 18 real local
PostgreSQL tests and 15 disposable database tests. This includes the final
login/reset cookie-issuance race regression. Earlier development failures were
fixed and superseded by this complete run.

Other final checks:

| Check | Result / limit |
| --- | --- |
| Alembic `check`, model comparison, schema fingerprint and pending revisions | PASS on new nonce-owned local PG16 target; head e6f1027a93bd, no upgrade operations |
| Historical migration integrity | PASS, all five old migration files preserved |
| Frontend ESLint | PASS |
| Next production build / TypeScript | PASS; same-origin build, account and recovery routes included |
| Edge browser recovery | PASS; verification/reset, explicit action, fragment cleanup/same-tab replacement, single use, no local token/password storage |
| Edge browser AI operations | PASS; durable manual retry/status, refresh, logout/account switch |
| Edge browser source verification | PASS; attribution/persistence, blocked editions, quotation/CSRF/idempotency behavior |
| Edge browser account deletion | PASS in explicitly enabled synthetic SQLite fixture only |
| Built Next staging gateway probe | PASS on loopback with random synthetic credentials; pages/assets/API gated, cookies and CSRF proxied to isolated backend |
| Render provider JSON Schema and local deployment invariants | PASS; private API, no automatic deploy/migration, no hardcoded secret fields |
| `pip check` | PASS |
| npm production dependency audit | PASS, zero reported vulnerabilities at check time |
| Configured-secret source/history scan and ignores | PASS, zero configured-secret matches; scoped audit |
| Docker image builds / Linux smoke tests | BLOCKED: Docker unavailable |
| Actual restic encryption/offsite recovery | BLOCKED: restic and approved offsite repository/key recovery unavailable; adapter tests use mocks |
| Neon PostgreSQL 18 restore | BLOCKED: no authorized empty isolated PG18 target; local PG16 is not equivalent evidence |
| Intended Render/Neon/Groq load and upstream outage rehearsal | BLOCKED: services/plan/authorized provider-call budget unavailable; local synthetic tests only |
| Live SMTP delivery/domain authentication | BLOCKED: no authorized provider configuration; capture/discard adapters only |

Test reproduction on the existing owned loopback PG16 cluster:

```powershell
$env:NAFSIY_LOCAL_PG_ADMIN_URL='postgresql+psycopg://nafsiy_readiness@127.0.0.1:55496/postgres'
$env:NAFSIY_LOCAL_PG_DATA='D:\NAFSIY\tmp\database-readiness\cluster'
$env:NAFSIY_TEST_PG_BIN='C:\Program Files\PostgreSQL\16\bin'
.\.venv\Scripts\python.exe tools/run_isolated_regression.py
```

The runner verifies the cluster's exact `data_directory`, creates nonce-owned
databases, overrides the child `TEST_DATABASE_URL` in memory, and cleans up only
its owned targets. Do not replace these local fixture settings with Neon credentials.
Browser suites require a fresh opted-in `tests/browser_server.py` process and a
built frontend on loopback port 3000 with `APP_ENV=development`. The shared fixture
retains real rate limits; repeated suites require a fresh fixture, not a limiter bypass.
Use `NAFSIY_PLAYWRIGHT_PATH` for the existing local Playwright installation.

## Pending external and human gates

- Explicitly authorize a new empty isolated Neon PG18 restore target and matching
  PG18 clients; rehearse current-backup and encrypted-offsite recovery, erasure
  reconciliation, session/token invalidation and measured RPO/RTO. Never reuse
  primary/shared test as a target.
- Approve offsite vendor/location, credentials, encrypted restricted local staging,
  key recovery, retention, scheduler, alert routing and independent artifact/journal
  provenance. Adapter unit tests do not prove a real backup exists.
- Approve Render provisioning/cost and private reviewer access; build/test both
  Linux images. Configure independent Neon staging credentials and least-privilege
  roles; validate actual pool quota, network/header contract and request timeouts.
- Authorize an SMTP provider/test recipient, verify delivery, SPF/DKIM/DMARC and
  provider data retention. Disabled delivery intentionally cannot recover a real
  user's account. Mail is synchronous with a bounded timeout; a durable production
  outbox/retry policy and timing-based enumeration risk need operational review.
- Configure private monitoring, dead-man/restore alerts, provider-side spend limits,
  billing reconciliation and intended-platform load/failure tests with an approved
  provider-call budget. MFA/WAF/distributed abuse protection and independent
  security review remain separate requirements.
- Implement/review the external erasure journal before enabling destructive account
  deletion. Existing legal/provider retention and user-facing policy decisions are
  unresolved; the UI and staging validator deliberately prevent enablement.
- Obtain explicit scholarly/licensing decisions for desired editions and intended
  reviewer/content scope. Restricted content cannot be enabled by environment alone.
- No previous pre-revocation backend is an approved rollback target. Review a
  compatible image or keep maintenance closed and roll forward; do not downgrade
  the security schema or run mixed old/new workers.

## Architecture, environment and runbooks

[RENDER_STAGING.md](RENDER_STAGING.md) contains the full service architecture,
required environment **names only**, approval boundaries, step-by-step deployment,
backup scheduling, alert thresholds and rollback instructions.
[ERASURE_REPLAY.md](ERASURE_REPLAY.md) contains the disabled deletion/journal design.
Existing [DATABASE_PRODUCTION.md](DATABASE_PRODUCTION.md),
[AUTH_SECURITY.md](AUTH_SECURITY.md), [AI_OPERATIONS.md](AI_OPERATIONS.md) and
[SOURCE_VERIFICATION.md](SOURCE_VERIFICATION.md) retain their historical evidence.

Provider documentation consulted: [Render blueprint specification](https://render.com/docs/blueprint-spec),
[Render private services](https://render.com/docs/private-services),
[Groq model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b).

## Exact changed files

The final inventory below is generated from Git's tracked diff and nonignored new
files. Local secrets, logs, backup artifacts, build output and database files are
excluded. No files are staged or committed by this report.

<!-- CHANGED_FILES -->

- `.dockerignore`
- `.env.example`
- `.env.staging.example`
- `Dockerfile.backend`
- `backend/api/auth.py`
- `backend/core/auth_rate_limit.py`
- `backend/core/config.py`
- `backend/core/deployment.py`
- `backend/core/email_delivery.py`
- `backend/core/http_monitor.py`
- `backend/core/recovery.py`
- `backend/db/auth_models.py`
- `backend/db/monitor.py`
- `backend/db/offsite.py`
- `backend/main.py`
- `docs/AI_OPERATIONS.md`
- `docs/AUTH_SECURITY.md`
- `docs/DATABASE_PRODUCTION.md`
- `docs/ERASURE_REPLAY.md`
- `docs/PRELAUNCH.md`
- `docs/RENDER_STAGING.md`
- `docs/STAGING_READINESS.md`
- `frontend/Dockerfile`
- `frontend/next.config.ts`
- `frontend/src/app/account-recovery/page.tsx`
- `frontend/src/app/account/page.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/components/AuthPanel.tsx`
- `frontend/src/components/HadithReader.tsx`
- `frontend/src/components/QuranReader.tsx`
- `frontend/src/lib/csrf.ts`
- `frontend/src/lib/userData.ts`
- `frontend/src/proxy.ts`
- `migrations/bootstrap/integrity.json`
- `migrations/bootstrap/schema_fingerprints.json`
- `migrations/versions/e6f1027a93bd_account_recovery.py`
- `ops/backup.cron.example`
- `ops/run-backup.example`
- `ops/runtime-role.sql.example`
- `render.yaml`
- `requirements-dev.txt`
- `requirements.lock`
- `requirements.txt`
- `tests/browser/recovery.cjs`
- `tests/browser_server.py`
- `tests/security_client.py`
- `tests/test_auth.py`
- `tests/test_database_readiness.py`
- `tests/test_deployment.py`
- `tests/test_recovery.py`
- `tests/test_recovery_postgres.py`
- `tests/test_user_data.py`
- `tools/audit_repository.py`
- `tools/backup_job.py`
- `tools/run_isolated_regression.py`
- `tools/test_staging_gateway.py`
- `tools/validate_deployment.py`
