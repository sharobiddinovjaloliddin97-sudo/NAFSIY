# Private Render staging deployment and rollback

Prepared 2026-10-31. **NOT READY** until the release gates in
[STAGING_READINESS.md](STAGING_READINESS.md) are satisfied. No services, domains,
accounts, roles or external storage were provisioned, and nothing was deployed.
Applying `render.yaml` provisions paid resources and requires explicit approval.

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

## Required environment names

Backend required: `APP_ENV`, `DATABASE_URL`, `JWT_SECRET_KEY`, `PUBLIC_WEB_URL`,
`AUTH_TRUSTED_ORIGINS`, `CORS_ORIGINS`, `AUTH_COOKIE_SECURE`,
`REQUIRE_EMAIL_VERIFICATION`, `EMAIL_MODE`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `GROQ_API_KEY`, `GROQ_MODEL`,
`MAX_GLOBAL_AI_GENERATIONS`, `AI_INPUT_USD_PER_MILLION`,
`AI_OUTPUT_USD_PER_MILLION`, `AI_DAILY_BUDGET_USD`, `AI_MONTHLY_BUDGET_USD`.

Backend defaults to review: `TRUSTED_PROXY_CIDRS`, `ACCOUNT_DELETION_ENABLED`,
`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`,
`DB_POOL_RECYCLE_SECONDS`, `AI_MAX_PROVIDER_ATTEMPTS`, `AI_MAX_INPUT_TOKENS`,
`AI_PLANNER_OUTPUT_TOKENS`, `AI_MAX_OUTPUT_TOKENS`, `QURAN_ARABIC_EDITION`,
`QURAN_EDITION_EN`, `QURAN_EDITION_UZ`, `QURAN_EDITION_RU`, `PORT`.

Frontend runtime: `APP_ENV`, `API_INTERNAL_URL`, `STAGING_ACCESS_USER`,
`STAGING_ACCESS_PASSWORD`, `PORT`. Leave `NEXT_PUBLIC_API_URL` absent in the image:
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
   `nafsiy_disposable_[a-z0-9_]{8,48}`. The reviewed bootstrap CLI deliberately only
   accepts such isolated targets. Do not weaken its guards to use primary/shared
   test or a populated database. See DATABASE_PRODUCTION.md for unversioned adoption.
6. In an isolated operator shell, set `NAFSIY_DISPOSABLE_DATABASE_URL` to the authorized
   staging migration-owner direct connection and `NAFSIY_DISPOSABLE_NONCE` to the
   database's exact `nafsiy-disposable:<nonce>` comment. Verify the target is empty,
   its identity differs from primary/shared test, and no other connections exist.
   Run `python -m backend.db.operations bootstrap --database disposable
   --confirm-database ACTUAL_NAME`. This applies the preserved baseline and all
   revisions through **e6f1027a93bd** transactionally. Existing reviewed isolated
   schemas use `upgrade-disposable` after preflight instead. These commands require
   separate target approval; they have not been run on Neon by this task.
7. Run `python -m backend.db.operations preflight --database disposable` as owner.
   Require correct fingerprint, no pending migrations and model compatibility.
   Run Alembic `check` using a supplied connection or a separately verified direct
   test target; never let a default command fall back to primary. Apply approved
   runtime grants and verify runtime cannot perform DDL.
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
