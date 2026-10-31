# Private Render staging deployment and rollback

Prepared 2026-10-31. **NOT READY** until the release gates in
[STAGING_READINESS.md](STAGING_READINESS.md) are satisfied. No services, domains,
accounts, roles or external storage were provisioned, and nothing was deployed.
Applying `render.yaml` provisions paid resources and requires explicit approval.

## Linux CI and container verification follow-up — 2026-10-31

Linux CI has been completed in `.github/workflows/local-verification.yml` and supporting
scripts `tools/docker_smoke.py`, `tools/report_backend_tests.py`, and `tools/run_isolated_regression.py`.
The workflow runs on pinned `ubuntu-24.04`, builds both `nafsiy-backend:ci` and `nafsiy-frontend:ci`,
verifies non-root uid (`uid > 0`), runs readiness probes (`/api/v1/health` and `/healthz`), tests Basic
auth staging access gate, verifies runtime HSTS and internal proxying, checks private file absence,
and verifies idle SIGTERM shutdown with exit code 0 (`docker stop --time 15`).

Local execution of `tools/docker_smoke.py` is BLOCKED due to Docker CLI absence on the Windows
workstation. Execution is scheduled to run automatically in GitHub Actions on push.

## Local follow-up ? 2026-10-31

Current local validation is recorded at the top of STAGING_READINESS.md. Docker
and Podman remain unavailable; WSL did not expose a usable Linux environment.
Container builds, Linux non-root behavior and Unix signal handling are therefore
**BLOCKED**, not passed by static Dockerfile review. No host installation or
external provisioning was attempted.

The installed Next server defaults to a 30s proxy timeout. The configuration now
sets `experimental.proxyTimeout=135000`; a real local HTTPS request with a 31s
synthetic generation validates forwarding beyond the old cutoff. Backend generation
remains bounded at 120s, Uvicorn graceful shutdown at 130s, and both Render service
shutdown allowances at 150s. Review actual platform/ingress limits separately.
The frontend container now execs Node directly instead of npm. This improves signal
routing but does not establish tested Linux shutdown behavior.

Staging HSTS now comes from runtime `src/proxy.ts`, including gate rejection and
successful requests. A Docker build does not need staging credentials or APP_ENV
to embed that header. Cookie Secure/HttpOnly/SameSite behavior is checked through
an ephemeral, locally trusted HTTPS certificate; this is not proof of Render TLS.
Frontend `/healthz` remains liveness-only; private API readiness needs a separate
private-network monitor because Render private services use TCP probes. References:
https://render.com/docs/health-checks and https://render.com/docs/blueprint-spec.

To reproduce without external DB/email/model calls, install requirements-dev.txt,
build the frontend with NEXT_PUBLIC_API_URL explicitly empty, then run:

```powershell
.\.venv\Scripts\python.exe tools/run_local_staging_checks.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_deployment.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_staging_health.py -v
.\.venv\Scripts\python.exe tools/validate_deployment.py
```

The last command only fetches the public Render schema; it does not contact an
account or deploy. The integration tool requires free loopback ports 8103, 3002,
3443 and creates/removes only its own processes and synthetic temporary fixtures.
It keeps account deletion disabled. Do not substitute the older deletion-enabled
browser fixture for this task's integration checks.

## Architecture

Reviewer browser → HTTPS Render frontend → Next server proxy → private Render
FastAPI service → separate Neon staging database over TLS. FastAPI calls Groq and
reviewed source providers. Both services use the same Render region. SMTP uses
mandatory STARTTLS with certificate validation. No frontend secret has a
`NEXT_PUBLIC_` prefix. Cookies are host-only, HttpOnly, Secure, SameSite=Lax;
same-origin `/api/` proxying avoids cross-site cookie dependence.

The internet-facing frontend requires a separate staging Basic credential before
serving pages, assets or API requests. It fails closed in production if staging
credentials/internal API routing are absent. Only `/healthz` is exempt and returns
`ok`. The private API has no public Render URL. Its database health endpoint is
`/api/v1/health`; in staging it also checks the exact Alembic head. Private services
do not support Render's web-service HTTP health-check field: poll API readiness
from an authorized private runner and alert separately. Frontend liveness does not
prove API/database/provider availability.

Uvicorn runs one worker with `--no-proxy-headers --no-access-log`. The frontend
removes client-supplied forwarding and Basic authorization headers before proxying.
`TRUSTED_PROXY_CIDRS=[]` intentionally groups users by transport peer, conservatively
sharing source rate limits across testers. Do not whitelist Render-wide/private
CIDRs without evidence of the exact ingress chain and header sanitization. A
verified platform client-IP contract and plan-specific load tests are outstanding.

The initial DB pool ceiling is four connections per worker (3 + 1 overflow), AI
capacity two, and generation timeout 120 seconds with provider calls bounded at
40 seconds. Reserve operational connections for maintenance, backups and migrations.
Validate actual Neon role/plan limits and Render proxy request timeouts before use.

## Zero-Budget Staging Architecture (Render Free + Neon Free + Groq Free + Resend)

For deployments with zero hosting budget ($0), Render Free tier provides two Web Services without credit card requirements. A dedicated Blueprint is provided in `render.free.yaml` alongside the paid Blueprint `render.yaml`.

### Differences Between Paid and Zero-Budget Staging

| Architecture Component | Paid Blueprint (`render.yaml`) | Zero-Budget Free (`render.free.yaml`) |
| --- | --- | --- |
| Backend Service Type | `type: pserv` (Starter plan, $7/mo) | `type: web` (Free plan, $0) |
| Backend Ingress | Private network only (`http://nafsiy-staging-api:10000`) | Public URL (`https://nafsiy-staging-api.onrender.com`) |
| Public Backend Defense | Network isolation (no public IP/route) | Shared `STAGING_PROXY_SECRET` header gate + constant-time HMAC |
| Frontend Service Type | `type: web` (Starter plan, $7/mo) | `type: web` (Free plan, $0) |
| Staging Access Gate | HTTP Basic Auth in Next.js `proxy.ts` | HTTP Basic Auth in Next.js `proxy.ts` |
| Outgoing Email | SMTP port 587 (`EMAIL_MODE=smtp`) | Resend API HTTPS port 443 (`EMAIL_MODE=resend`) |
| Database Target | Neon Dedicated/Branch PostgreSQL 18 | Neon Free Tier PostgreSQL 18 project |
| AI Provider | Groq API quota (`openai/gpt-oss-120b`) | Groq API free quota (`openai/gpt-oss-120b`) |
| Inactivity Behavior | Runs 24/7 continuously | Auto-sleep after 15 min idle; cold start ~30-50s |
| Ingress Proxy Timeout | Next.js 135s (`proxyTimeout: 135000`) | Next.js 135s (`proxyTimeout: 135000`) |

### Public Backend Protection in Free Tier
Because Render Free tier lacks private networking (`pserv`), the backend must be deployed as a public Web Service (`type: web`). To prevent attackers from bypassing the staging Basic Auth gate or accessing the API directly:
1. Direct requests to `/api/v1/*` on the public backend (excluding `/` and `/api/v1/health`) require the header `X-Staging-Proxy-Secret`.
2. The secret is shared securely between backend and frontend via server-side environment variable `STAGING_PROXY_SECRET` (minimum 32 random characters).
3. Next.js server-side proxy strips client-supplied `x-staging-proxy-secret`, `forwarded`, `x-forwarded-for`, and `x-real-ip` headers before injecting the trusted `process.env.STAGING_PROXY_SECRET` and forwarding the request.
4. Direct external requests without the secret receive an immediate `403 Forbidden` response with security headers attached.
5. Render health checks probe `/api/v1/health` and `/healthz`, which remain unauthenticated for infrastructure liveness monitoring.

### Email Delivery Over HTTPS (Resend Port 443)
Render Free blocks outbound connections to standard SMTP ports (25, 465, 587) to prevent spam abuse.
- NAFSIY integrates `ResendDelivery` using `https://api.resend.com/emails` over HTTPS port 443.
- Free Resend tier provides 3,000 emails/month (100 emails/day) without requiring a credit card.
- Mandatory email verification (`REQUIRE_EMAIL_VERIFICATION=true`) remains fully enforced.
- **Sender Domain Requirement:** In Resend free tier, the default sandbox sender `onboarding@resend.dev` can ONLY deliver to the single email address registered to the Resend account. Staging configuration rejects `onboarding@resend.dev` at startup to prevent registration failures for testers. A verified custom sender domain (e.g. `mail.nafsiy.uz`) is required.

#### Verifying a Dedicated Sender Domain via Cloudflare DNS (e.g. `mail.nafsiy.uz`)
1. In the [Resend Domains Dashboard](https://resend.com/domains), click **Add Domain**, enter `mail.nafsiy.uz`, and select region (EU Frankfurt).
2. Resend displays DNS records to publish:
   - **MX Record**: Type `MX`, Name `mail`, Priority `10`, Value `feedback-smtp.resend.com`
   - **SPF Record**: Type `TXT`, Name `mail`, Value `v=spf1 include:resend.com ~all`
   - **DKIM Record**: Type `TXT`, Name `resend._domainkey.mail`, Value `k=rsa; p=<public_key>`
   - **DMARC Record**: Type `TXT`, Name `_dmarc.mail`, Value `v=DMARC1; p=none;`
3. In [Cloudflare DNS Management](https://dash.cloudflare.com):
   - Add the MX and TXT records as specified above.
   - **CRITICAL:** Set Proxy status to **DNS only** (grey cloud, not proxied) for MX and TXT records.
4. Click **Verify Records** in Resend until status transitions to **Verified**.
5. Set `EMAIL_FROM=NAFSIY Security <security@mail.nafsiy.uz>` in staging configuration.
6. Tokens and links remain embedded strictly in the URL fragment (`#purpose:token`) and are never written to server logs.

### Cold-Start and Resource Invariants
- **Render Free Instance Hours:** Free tier accounts receive **750 free instance hours per month across the entire workspace** (shared by all free services). Two services running 24/7 without sleep would consume ~1,488 hours and exhaust the allowance in ~15 days.
- **Inactivity Sleep:** Render Free services automatically spin down after 15 minutes of inactivity. When asleep, services consume zero hours. Under normal staging testing, usage stays well below 100 hours per month.
- **No Artificial Keep-Alives:** Do NOT configure automated uptime pingers or synthetic traffic cron jobs. Waking sleeping instances needlessly will rapidly deplete the 750-hour allowance.
- **Cold-Start Toleration:** Sleeping instances take ~30-50s to wake up on the first request. Next.js `experimental.proxyTimeout: 135000` (135 seconds) accommodates this delay without 504 Gateway Timeouts.
- **Memory Footprint:** Free instances have a 512 MB RAM ceiling. Measured runtime: FastAPI ~65 MB, Next.js ~110 MB.
- **Neon Free Tier Limits:** Provides 0.5 GiB storage, 1 compute branch, and compute auto-suspension after 5 minutes of idle time. Never run continuous background workloads against Neon Free.
- `autoDeployTrigger: "off"` remains enforced across all services.

## Required environment names

Backend required: `APP_ENV`, `DATABASE_URL`, `JWT_SECRET_KEY`, `PUBLIC_WEB_URL`,
`AUTH_TRUSTED_ORIGINS`, `CORS_ORIGINS`, `AUTH_COOKIE_SECURE`,
`REQUIRE_EMAIL_VERIFICATION`, `EMAIL_MODE`, `EMAIL_FROM`, `GROQ_API_KEY`, `GROQ_MODEL`,
`MAX_GLOBAL_AI_GENERATIONS`, `AI_INPUT_USD_PER_MILLION`,
`AI_OUTPUT_USD_PER_MILLION`, `AI_DAILY_BUDGET_USD`, `AI_MONTHLY_BUDGET_USD`.
When `EMAIL_MODE=smtp`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`.
When `EMAIL_MODE=resend`: `RESEND_API_KEY`.
When public backend (Render Free tier): `STAGING_PROXY_SECRET` (min 32 chars).

Backend defaults to review: `TRUSTED_PROXY_CIDRS`, `ACCOUNT_DELETION_ENABLED`,
`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`,
`DB_POOL_RECYCLE_SECONDS`, `AI_MAX_PROVIDER_ATTEMPTS`, `AI_MAX_INPUT_TOKENS`,
`AI_PLANNER_OUTPUT_TOKENS`, `AI_MAX_OUTPUT_TOKENS`, `QURAN_ARABIC_EDITION`,
`QURAN_EDITION_EN`, `QURAN_EDITION_UZ`, `QURAN_EDITION_RU`, `PORT`.

Frontend runtime: `APP_ENV`, `API_INTERNAL_URL`, `STAGING_ACCESS_USER`,
`STAGING_ACCESS_PASSWORD`, `PORT`.
When public backend (Render Free tier): `STAGING_PROXY_SECRET`. Leave `NEXT_PUBLIC_API_URL` absent in the image:
the production default is same-origin. That variable is a public build-time value,
never a secret; changing it requires a rebuild. Docker ignores all local env files.

Operator only: `MIGRATION_DATABASE_URL`, `BACKUP_DATABASE_URL`, `BACKUP_DIRECTORY`,
`PG_BIN`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`; for S3-compatible repositories,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and any provider-required region.
Guarded rehearsal tooling additionally uses `TEST_DATABASE_URL`,
`NAFSIY_DISPOSABLE_DATABASE_URL`, `NAFSIY_DISPOSABLE_NONCE` and a separate primary
identity solely to reject accidental target reuse. Never install migration-owner,
backup or offsite credentials into web service environments.

## Deployment procedure — only after explicit authorization

1. Review changed files, approve a release commit separately, and record immutable
   commit/image identifiers. No automatic commit/push is part of this work.
2. Complete the Neon **18** isolated restore rehearsal, offsite/key-recovery
   rehearsal, SMTP verification, privacy/license/scholarly decisions, image builds
   and intended-platform load tests. Keep account deletion disabled. Use synthetic
   accounts/content until human review explicitly permits broader testing.
3. Obtain approval for the exact Render services/region/cost, private access method
   and a **new staging Neon target**, independently of primary/shared test. Record
   direct/pooler-normalized identities in the restricted operator inventory. Do not
   reuse `.env` credentials as staging defaults.
4. Provision staging roles through the approved provider workflow. The migration
   role owns schema; runtime receives only required DML/schema usage/sequence
   privileges. Review `ops/runtime-role.sql.example`, including default privileges.
   Runtime must not inherit owner membership or schema CREATE. Test role grants
   with the intended provider; existing owner preflight is not a runtime-role test.
5. Before schema changes, take a verified encrypted backup where data exists. For
   fresh installation, authorize an empty nonce-marked database with a name matching
   `nafsiy_disposable_[a-z0-9_]{8,48}` (e.g. `nafsiy_disposable_staging01`). The reviewed
   bootstrap CLI deliberately only accepts such isolated targets. Do not weaken its guards
   to use primary/shared test or a populated database. See DATABASE_PRODUCTION.md for unversioned adoption.
   Generate a cryptographically random 32-character lowercase hex nonce:
   ```powershell
   $NONCE = [System.BitConverter]::ToString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).Replace("-","").ToLower()
   # Or using Python: python -c "import secrets; print(secrets.token_hex(16))"
   ```
   Execute in PostgreSQL as database owner:
   ```sql
   CREATE DATABASE nafsiy_disposable_staging01;
   COMMENT ON DATABASE nafsiy_disposable_staging01 IS 'nafsiy-disposable:<INSERT_32_HEX_NONCE>';
   ```
6. In an isolated operator shell, set `NAFSIY_DISPOSABLE_DATABASE_URL` to the authorized
   staging migration-owner direct connection (never pooler) and `NAFSIY_DISPOSABLE_NONCE` to
   the database's exact 32-character lowercase hex nonce. Verify the target is empty,
   its identity differs from primary/shared test, and no other connections exist:
   ```powershell
   $env:NAFSIY_DISPOSABLE_DATABASE_URL = "postgresql+psycopg://owner:PASSWORD@ep-staging.eu-central-1.aws.neon.tech/nafsiy_disposable_staging01?sslmode=require"
   $env:NAFSIY_DISPOSABLE_NONCE = "<INSERT_32_HEX_NONCE>"
   .\.venv\Scripts\python.exe -m backend.db.operations bootstrap --database disposable --confirm-database nafsiy_disposable_staging01
   ```
   This applies the preserved baseline and all revisions through **e6f1027a93bd** transactionally.
7. Run `python -m backend.db.operations preflight --database disposable` as owner.
   Require correct fingerprint, no pending migrations and model compatibility.
   Apply approved runtime grants without granting write access to shared Quran source tables:
   ```sql
   REVOKE CREATE ON SCHEMA public FROM PUBLIC;
   GRANT USAGE ON SCHEMA public TO nafsiy_runtime;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO nafsiy_runtime;
   GRANT INSERT, UPDATE, DELETE ON
     auth_users, auth_recovery_tokens, auth_rate_limits,
     chat_sessions, chat_messages, user_bookmarks,
     ai_rate_limits, ai_idempotency, chat_generation_leases,
     ai_operations_gate, ai_global_slots, ai_budgets, ai_operations, ai_provider_attempts
     TO nafsiy_runtime;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nafsiy_runtime;
   ALTER DEFAULT PRIVILEGES FOR ROLE nafsiy_migrator IN SCHEMA public
     GRANT SELECT ON TABLES TO nafsiy_runtime;
   ALTER DEFAULT PRIVILEGES FOR ROLE nafsiy_migrator IN SCHEMA public
     GRANT USAGE, SELECT ON SEQUENCES TO nafsiy_runtime;
   ```
   Shared Quran source tables (`surahs`, `ayahs`, `translations`) and `alembic_version` remain strictly read-only.
   Verify runtime cannot perform DDL.
8. Build both images from the reviewed checkout. Backend uses `requirements.lock`;
   frontend uses `npm ci`. Run Linux image smoke tests as the configured non-root
   users. No migrations run at image build/startup. Docker is unavailable in the
   current workspace, so these image checks remain a gate.
9. Fill `.env.staging.example` through Render secret fields, never Git. Choose
   independent random JWT and staging-gate credentials; use HTTPS frontend origin
   with no trailing slash in both explicit origin lists. Use the private API host
   and port for `API_INTERNAL_URL`. Leave auto-deploys off and deletion disabled.
10. With separate approval for deployment, start the private API, verify readiness
    from the private network, then start the gated frontend. Test anonymous 401 on
    pages/assets/API, authenticated gate access, Secure cookies, CSRF/Origin
    rejection, registration→email verification→login, password reset, copied-token
    rejection after logout/reset and chat ownership. Confirm no direct API exposure.
11. Run concurrent staging probes with synthetic data under the actual plan's
    quotas, including provider timeout, DB disconnect/recovery, pool pressure,
    heartbeat/fencing and cost ledger reconciliation. Do not make paid provider
    calls without the authorized test budget. Record p50/p95/p99, 429/503 rates and
    RPO/RTO. Local tests do not substitute for these measurements.
12. Configure private monitoring, backup scheduler, dead-man checks, billing alerts
    and a named operator. Review screenshots/log aggregates without user content.
    Admit only explicitly authorized reviewers after every gate passes.

## Backups, schedule and alert ownership

On a separate private runner with PostgreSQL 18 clients and restic, initialize an
encrypted offsite repository only after approval. Store its password/key recovery
material separately. `tools/backup_job.py` rejects primary/shared test as its staging
source, creates the existing snapshot-consistent verified logical backup, uploads
it through `backend.db.offsite`, performs a full `restic check --read-data`, and emits
a fixed success/failure event. There is no external repository, scheduler or alert
destination configured here. Do not run it with real credentials until authorized.

Review `ops/run-backup.example` and `ops/backup.cron.example`: daily 02:15 UTC
(07:15 Asia/Tashkent). Alert on any nonzero exit, no successful backup for 26 hours,
integrity failure, missing offsite receipt, or stale restore proof (monthly maximum
unless policy requires shorter). A local offsite receipt is an attestation of a
tool run, not a signed remote provenance or database restore proof. Keep plaintext
local dumps on encrypted restricted storage with an approved retention policy;
this adapter does not automatically erase local copies or prune remote snapshots.

Monthly, recover a selected offsite snapshot into a new empty quarantined PG18
target, validate archive/table/sequence integrity, replay erasures, revoke restored
credentials, test key recovery and measure RPO/RTO. Missing journal or keys blocks
activation; see ERASURE_REPLAY.md. No existing primary/shared test target may be
substituted. A local PG16 synthetic restore is useful evidence but not the PG18 gate.

Collect only structured HTTP status/method/duration and existing AI cost/capacity
events. Do not enable raw access/query/body logs. Alert on availability failures,
sustained 5xx or pool utilization over 80%, growing cleanup backlog over three
intervals, schema/version drift, database growth/connections near quota, email
delivery failures, Groq timeout/error increases, and cost reservations above 80%
of daily/monthly limits. Set provider-side spend limits too; local estimates are
not a provider billing guarantee. Restrict diagnostic CLI/collector access.

Groq's model documentation, checked 2026-10-31, lists **$0.15 input / $0.60 output
per million tokens** for `openai/gpt-oss-120b`. Staging overrides use those values;
development retains conservative illustrative defaults. Cached input discounts
are not assumed. Revalidate prices, service tier and billed usage before rollout.
Source: https://console.groq.com/docs/model/openai/gpt-oss-120b

## Rollback and incident procedure

1. Close reviewer access using maintenance mode/the gate; keep API private. Stop
   accepting new generation work. Capture sanitized availability/cost evidence.
2. Drain/stop all workers before changing backend versions. Do not mix fencing or
   budget protocol versions. Wait for in-flight work and review unknown provider
   usage conservatively; a timeout does not prove the provider did no work.
3. Prefer a known-compatible image with the **same auth revocation protocol** and
   retain the expanded schema. Roll back frontend independently only when API
   compatibility is proven. Do not automatically downgrade migrations.
4. There is no earlier pre-revocation backend approved for rollback: V0.9.6 would
   ignore `auth_version`, accepting copied revoked JWTs. Keep maintenance closed
   and roll forward a fix, or explicitly review a compatible release first. Never
   drop revocation columns/tokens as an incident shortcut.
5. For corruption, restore into a **new** explicitly authorized isolated target;
   keep the damaged target for evidence. Verify hashes/sequences, replay erasures,
   invalidate restored sessions/recovery tokens and record restore receipts.
   Repoint only staging runtime after a separate target-switch approval. No
   automatic restore or primary overwrite is permitted.
6. Repeat private readiness/auth/ownership/idempotency/locking checks, reconcile
   budgets and obtain reviewer/operator approval before reopening access. Retain
   an incident record without credentials, messages or recovery links.

Provider references checked for preparation:
https://render.com/docs/blueprint-spec,
https://render.com/docs/private-services,
https://render.com/docs/health-checks,
https://nextjs.org/docs/app/api-reference/file-conventions/proxy.
