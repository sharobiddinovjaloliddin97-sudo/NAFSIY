# NAFSIY V0.9.6 — production database readiness

Prepared on 2026-10-31. **No deployment, primary connection/migration, production deletion, commit or push was performed.** The migration head remains `c4d8e2a9106f`. All five historical revision files are unchanged.

## Fresh installation

The historical `69e1d999085f` revision is deliberately empty. It records adoption of an existing schema; it cannot build the original tables. Do not use `alembic upgrade head` alone on an empty database or add `create_all()` to application startup.

The new version-controlled artifacts are:

- [baseline.sql](../migrations/bootstrap/baseline.sql): eight original application tables, their original indexes, unique/check constraints and foreign keys. It contains no rows, credentials or source publication text. It excludes indexes/columns/tables introduced by later revisions.
- [integrity.json](../migrations/bootstrap/integrity.json): snapshot and historical migration SHA-256 checksums and a checksum for the reviewed schema references. Hash input normalizes CRLF to LF for portable checkouts; other content is unchanged.
- [schema_fingerprints.json](../migrations/bootstrap/schema_fingerprints.json): references for the exact unversioned original schema and each of the five revisions. Fingerprints cover columns/defaults, PK/FK/unique/check constraints, indexes and catalog object counts. PostgreSQL 16/18 equivalent varchar/text-array check-expression formatting is normalized; column types remain independently checked.

`bootstrap` checks integrity before mutation, takes an advisory lock, verifies the disposable ownership marker and actual database name, checks there are no other connections, then verifies emptiness. The emptiness check covers relations (including views/sequences), routines, types/domains, extra schemas, extra extensions, publications and event triggers. Even a database containing only a type or an empty extra schema is rejected.

Inside one transaction it applies the static original SQL and runs the unchanged chain:

```text
69e1d999085f → 7bbcad9d4644 → f9a045983bdf → ade01cf7b885 → c4d8e2a9106f
```

Final schema comparison includes every SQLAlchemy model and the reviewed fingerprint. An error rolls back this bootstrap transaction. There is no automatic stamping of an arbitrary existing schema, no drop/clean fallback and no overwrite behavior. Repeating bootstrap on an initialized target fails.

### Operator target registration and commands

The new mutating CLI **only accepts disposable targets**. It refuses both primary and the shared Neon test database. This is intentional for this preparation phase. A production installation/promotion requires a separately authorized rollout; this task does not provide automatic primary migration.

Provision a **new empty** isolated database yourself, with a name such as `nafsiy_disposable_<32-hex-suffix>`. If provisioning needs external access or permission that is unavailable, stop. Do not substitute an existing database. Confirm its provider project/branch and direct endpoint independently; a different URL string alone is insufficient proof of isolation.

Supply its direct URL through the local secret manager or `NAFSIY_DISPOSABLE_DATABASE_URL`. Generate an ownership nonce in the operator shell:

```powershell
$env:NAFSIY_DISPOSABLE_NONCE = [guid]::NewGuid().ToString('N')
```

On **that new disposable database only**, its owner sets this database comment using the chosen actual name and nonce:

```sql
COMMENT ON DATABASE nafsiy_disposable_REPLACE_WITH_HEX_SUFFIX
IS 'nafsiy-disposable:REPLACE_WITH_THE_32_HEX_NONCE';
```

This registration is a privileged operator attestation, not a cryptographic proof of external project ownership. The tool also rejects the configured primary/test identities and verifies the connected database name, prefix, marker, emptiness and connection count. Credentials are never command-line arguments or printed diagnostics.

Then use the **same operator shell**, replacing the confirmation name:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations bootstrap --database disposable --confirm-database nafsiy_disposable_REPLACE_WITH_HEX_SUFFIX
.\.venv\Scripts\python.exe -m backend.db.operations preflight --database disposable
```

Never bootstrap an existing application database. There is no automatic promotion command. Keep target credentials exclusive during bootstrap/restore: advisory locks coordinate these tools, but cannot prevent an unrelated privileged client from ignoring the lock. A later approved promotion must remove the disposable designation, update secrets deliberately, and pass the release gates below.

Future migrations require reviewed updates to the integrity inventory and schema references. Adding an unregistered revision or changing historical content fails the artifact inventory/checksum check. Do not regenerate the original SQL from future models and silently call it the old baseline.

## Existing database upgrade

Read-only preflight commands:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations preflight --database test
.\.venv\Scripts\python.exe -m alembic -x database=test current
.\.venv\Scripts\python.exe -m alembic -x database=test check
```

`preflight --database primary` exists for an authorized operator's **read-only** inspection; it was not used in this task. The new CLI uses direct connections and `SET TRANSACTION READ ONLY` for preflight/monitor. It reports revision, pending revisions in order, schema compatibility, model compatibility at head, unexpected tables/schemas, supported PostgreSQL version and privileges. It does not print private rows, credentials or raw SQL errors.

No nonstandard extension is required. Installed extensions are reported; none is automatically created. PostgreSQL 16–18 are accepted by the operator checks; this task exercised 16.14 locally and 18.6 on Neon. PostgreSQL 17 and future releases still need a deployment-specific rehearsal. Connectivity/schema checks do not establish application readiness or migration permission by themselves.

The migration-role preflight requires CONNECT, schema USAGE/CREATE, table ownership and all table DML privileges, plus sequence access. Missing flags cause a nonzero CLI result. A least-privilege runtime or backup role may intentionally lack migration privileges; do not broaden it just to make the migration-role check green. Use a separately controlled migrator role. Runtime should have only its required DML/sequence permissions; backup needs read access, including sequences. Role provisioning/grant changes were not run against primary.

For an existing, **unversioned** database, only the exact reviewed original schema is recognized as an adoption candidate (`requires_baseline_adoption: true`). Unknown/drifted schemas fail closed. A compatible original can be backed up before adoption. No arbitrary schema is stamped and no missing original tables are silently constructed alongside existing data.

Rehearse an upgrade on a separately registered disposable copy after verifying its backup:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations preflight --database disposable
.\.venv\Scripts\python.exe -m backend.db.operations upgrade-disposable --database disposable --confirm-database nafsiy_disposable_REPLACE_WITH_HEX_SUFFIX
.\.venv\Scripts\python.exe -m backend.db.operations preflight --database disposable
```

The rehearsal takes the same advisory lock, verifies ownership and the known schema, runs the chain and checks models/fingerprint. For an exact unversioned original, the empty baseline revision is traversed first, creating Alembic's version record without replacing original tables. Fixture accounts/messages survive both baseline-revision and unversioned-original adoption tests.

Before a future authorized primary upgrade: freeze the release/artifacts, verify revision/schema/privileges, take and independently restore-test a fresh encrypted backup, drain all workers, stop admissions, rehearse the exact chain, obtain rollout authorization, and run one controlled migrator. Do not run the legacy bare `alembic upgrade` command casually: its historical default selects `DATABASE_URL`. The new operator CLI never upgrades primary. No startup migration or automatic DDL is introduced.

Rollback should normally mean draining workers and restoring the verified pre-upgrade backup into a **new empty** target, then validating it before a separately approved cutover. Destructive historical downgrades drop operational tables/columns and may discard audit/idempotency history; they are not a data-preserving rollback plan. A database restore also loses post-snapshot writes. Decide explicitly whether forward repair is safer.

## Backup and restore runbook

The workflow uses a direct PostgreSQL connection, custom-format `pg_dump`, and matching-major `pg_dump`/`pg_restore` binaries. This same-major policy is stricter than the minimum compatibility rules and makes rehearsals reproducible. Pooler endpoints are rejected.

Create a **new** private output directory per run; an existing directory is never overwritten:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations backup --database test --directory backups\REPLACE_WITH_NEW_RUN_ID --pg-bin "$env:LOCALAPPDATA\NAFSIY\tools\postgresql-18\pgsql\bin"
.\.venv\Scripts\python.exe -m backend.db.operations verify-backup --directory backups\REPLACE_WITH_NEW_RUN_ID --pg-bin "$env:LOCALAPPDATA\NAFSIY\tools\postgresql-18\pgsql\bin"
```

For a local PostgreSQL 16 source, select its installed 16 client directory instead. No credentials are passed in argv: subprocesses receive short-lived libpq environment variables. Client output/errors are captured; public CLI output is fixed operational metadata, not dump contents.

New output directories get owner-only permissions: mode 0700/0600 on POSIX and restricted, non-inherited owner ACLs on Windows. Keep them under ignored `backups/` or a dedicated private volume, **never** a web-served directory such as `frontend/public`. Application/public users must not have access. Dumps, manifests, hashes of private rows and restore receipts are sensitive artifacts; do not commit, paste or expose them.

The exporter holds one repeatable-read, read-only transaction and exports its snapshot to `pg_dump`. Row counts and canonical row hashes are computed from that same snapshot, preserving exact numeric decimals rather than rounding through binary floats. The version-2 private manifest records:

- Database/client versions, Alembic revision (including an exact unversioned original), schema checksum, snapshot/isolation metadata and UTC time.
- Whole-dump SHA-256 and size; per-table row count/hash, including version and user tables.
- Sequence observations and an opaque source fingerprint, without URL/password/user-name fields.
- `offsite_encrypted: false` until a separate, verified offsite process exists. Creating a dump is never reported as a successful restore.

Sequences are not MVCC snapshot data. Restoration checks they have not moved backward from the observation; concurrent sequence advancement is allowed. Freeze DDL and administrative sequence resets during backups. Row and schema checksums detect accidental corruption; they do not authenticate an attacker-controlled archive and manifest. Restore only trusted backups, with independent signed/checksummed artifact records and offsite access controls. `pg_restore` can execute source database code.

Restore only to another registered, empty, same-major disposable target:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations restore --database disposable --confirm-database nafsiy_disposable_REPLACE_WITH_HEX_SUFFIX --directory backups\REPLACE_WITH_RUN_ID --pg-bin 'C:\Program Files\PostgreSQL\16\bin'
```

Use the matching 18 binaries for an approved new 18 target. The command verifies integrity/version before restore and target ownership/emptiness before invoking `pg_restore --no-owner --no-acl --exit-on-error --single-transaction`. It never uses `--clean`, overwrites data, or selects an existing restore database. SQL restore errors roll back that restore transaction. A post-commit verification failure leaves the target quarantined and nonempty; do not retry over it—inspect it and provision another target.

Success requires the restored revision, complete schema, every table's row count/content hash and sequence observations to match. A separate receipt is tied to the exact dump checksum. It always has `ready_for_application: false`: technical restoration is not promotion authorization, erasure reconciliation, source-license approval or authentication safety.

### Schedule, offsite storage and recovery objectives

Starting policy, **not yet scheduled or uploaded by this task**:

- Daily backup at 02:00 UTC (07:00 Asia/Tashkent); also before every authorized schema upgrade.
- Retain 30 daily encrypted copies; up to four weekly copies within that same 30-day window. Do not retain monthly user-data copies indefinitely without a separately approved privacy policy. Expire failed/partial local runs only after inspection. Never automatically delete the last verified backup.
- Encrypt the entire backup/manifest/receipt bundle before offsite transfer using a reviewed tool (for example age) and recovery keys held separately from the VPS. Use a private offsite bucket/account, TLS, encryption at rest/KMS, least-privilege write/read roles, access logging and a tested retention/lifecycle policy. Keep a second recovery key under controlled access. No offsite transfer or key provisioning was performed here.
- Verify every checksum/archive, alert if the newest valid backup is older than 26 hours, and perform a full isolated restore at least weekly and before rollout. Record success against the exact dump; alert if the last restore proof is older than seven days. Also validate an encrypted offsite download/decryption, not just the local plaintext archive.

Initial logical-backup **RPO target: 24 hours**, plus detection/scheduling delay; **RTO planning target: two hours**, conditional on a usable target, credentials/keys, provider availability, database size and operators. These are not guarantees. Tiny local fixtures cannot establish production RTO. Dumps do not provide continuous WAL/PITR, roles/global cluster objects, deployment secrets or application configuration. Verify the actual Neon plan's PITR/retention/restore entitlements separately and keep offsite recovery independent of that account.

### Verification performed

- PostgreSQL 16 disposable databases: fresh install, original-version/unversioned upgrades, successful backup/restore with all-table row/schema/sequence checks, concurrent writes excluded by the exported snapshot, wrong nonce/target/nonempty rejection, corrupted dump rejection, and an intentionally FK-invalid archive whose restore rolls back.
- Neon test PostgreSQL 18.6: direct read-only schema/privilege preflight PASS; matching 18 client backup created, checksum and archive-list verification PASS. It is access-controlled and ignored by Git. **Its restore remains unverified.** No isolated 18 server is installed locally, and this task did not externally provision a target or reuse an existing database. A separately authorized, empty 18-target restore is a remaining production blocker.
- No primary backup, primary query/migration, external upload, or live user deletion was performed.

## Table inventory, retention and deletion

| Tables | Data | Account erasure / retention |
| --- | --- | --- |
| `auth_users` | Email, password hash, account ID, status, creation time | Account row deleted; every old JWT subject then fails authentication |
| `chat_sessions`, `chat_messages` | User links, private titles/messages, source JSON | FK cascade removes sessions/messages |
| `user_bookmarks` | Account link, references, possibly private titles | FK cascade |
| `ai_idempotency` | Account/chat IDs, key/fingerprint, cached answer | FK cascade on account/chat deletion; otherwise response retained at least 30 days, then expired tombstone for chat lifetime |
| `chat_generation_leases` | Chat/request/owner/version/expiry | Cascade with chat; stale workers cannot persist |
| `ai_rate_limits` | Raw account-prefixed minute buckets | Own buckets removed on account erasure; normal indexed cleanup after ten-minute grace |
| `auth_rate_limits` | HMAC source/email/account-delete buckets and expiry | Shared anti-abuse counters retained, no plaintext identity; longest current window plus grace is about 70 minutes, with physical deletion dependent on bounded maintenance/backlog |
| `ai_operations`, `ai_provider_attempts` | Provider/model, tokens, costs, attempts, timestamps, nullable request link | Settle active reservations, detach request link, retain cost-only audit with no account/chat ID, prompt, response or source text |
| `ai_budgets` | Shared UTC period totals | Retain spent totals; erasure does not undo provider billing |
| `ai_global_slots`, `ai_operations_gate` | Shared concurrency/fencing coordination | Finish/release matching active capacity; keep shared slot/version and gate rows |
| `surahs`, `ayahs`, `translations` | Shared source corpus | Never deleted by account erasure |
| `alembic_version` | Schema version | Unchanged by account erasure |

Cost-only audit and period aggregates currently retain indefinitely, matching V0.9.4.4. Storage/aggregation growth is monitored; a reviewed archival/deletion policy is needed before significant scale. No new blind audit purge or shared-source deletion was introduced. Retained operation IDs are opaque operational identifiers, not a claim that timing/cost metadata is unconditionally anonymous.

### Authenticated erasure API

`ACCOUNT_DELETION_ENABLED=false` by default. No production enablement was performed. When deliberately enabled in a reviewed environment:

```http
DELETE /api/v1/user/account
Content-Type: application/json
X-CSRF-Token: <current session-bound token>

{"confirmation":"DELETE MY ACCOUNT","password":"<current password>"}
```

It requires a live authenticated account, trusted Origin, session-bound CSRF, the exact confirmation phrase, and password reauthentication. Three reauthentication attempts per five-minute window are allowed through a separate HMAC limiter. Missing/wrong confirmation, CSRF, Origin or password never deletes data. Passwords/tokens are not logged.

Password verification happens without a long-held transaction. The deletion transaction rechecks the current password hash and active account under a user-row lock, locks chats in deterministic order, then takes the financial gate. Create-chat uses the same user lock; send/save uses the chat lock before the gate. Active attempts settle once using the conservative unknown-usage estimate, unused reservation is released, operations detach, own AI minute buckets are removed, and identity/data cascade in one commit. A failed transaction leaves the account/data intact.

Completed cost remains charged. An external provider call may continue or bill after deletion; no cross-process/provider cancellation guarantee is made. Its chat/idempotency lease is gone, so heartbeat/fencing and chat ownership prevent late persistence or reuse. A chat deletion that already detached an operation can leave cost-only work for the existing expiry/recovery path; it contains no account link or private text. Other accounts and shared Quran rows are preserved.

Success is 204, with auth/context cookies cleared and no-store headers. Copied old JWTs fail because the account no longer exists; a new registration gets a different ID. A future UI must dispatch the existing auth-change event, abort local sends and clear pending metadata/cache after success; the browser fixture verifies this consumer behavior. No public destructive UI was added or enabled.

### Erasure versus backups

Live erasure does not rewrite encrypted historical backups, provider records, copies already held by the user, or memory in an external call. The snapshot test explicitly demonstrates that restoring an older backup can bring back a subsequently deleted account. Every restore therefore stays quarantined.

Before enabling production erasure, implement/review an access-controlled erasure-replay ledger outside the restored backup lineage (for example keyed digests of account IDs with a separately protected key), and a promotion checklist that reapplies deletions, validates FKs/usage and invalidates old authentication secrets/sessions. The ledger must survive a full restore and expire consistently with all backup/PITR copies. It was not provisioned offsite here. Until that workflow, deletion UX/privacy policy and provider-retention handling are reviewed, leave the feature disabled. Do not promise instant erasure from all historical copies.

## PostgreSQL performance and pools

[database_performance.json](../evaluations/database_performance.json) contains sanitized EXPLAIN ANALYZE/BUFFERS output from PostgreSQL 16.14, with synthetic fixtures only: 100 accounts, 5,000 chats, 100,000 messages and 25,000 attempts. It excludes predicate literals/private rows. Three warm executions per query were measured:

| Query | Median execution |
| --- | ---: |
| Owned chats ordered by update | 0.070 ms |
| Recent message history | 0.017 ms |
| Logical idempotency key | 0.012 ms |
| Lease by chat | 0.014 ms |
| Expired lease count | 0.921 ms |
| Bounded AI rate cleanup | 0.035 ms |
| 24-hour usage aggregation | 3.981 ms |

Existing chat ownership/update, `(session_id, position)`, unique idempotency, lease PK and rate-created-time indexes cover the hot paths. Expiry/usage scans remain cheap at this fixture size; **no new index or revision was justified/added**. These are warm local server execution times, not Neon network latency, cold-cache performance or a production load test. Alert on growth and remeasure before adding time/expiry indexes or changing retention, particularly for indefinitely retained usage rows and cascading idempotency deletion.

Runtime SQLAlchemy QueuePool is configurable:

| Setting | Default |
| --- | ---: |
| `DB_POOL_SIZE` | 3 |
| `DB_MAX_OVERFLOW` | 1 |
| `DB_POOL_TIMEOUT_SECONDS` | 5 |
| `DB_POOL_RECYCLE_SECONDS` | 1800 |

Pre-ping remains enabled; LIFO/recycling bounds idle connection age. One pool belongs to each worker. Two workers allow at most eight app DB connections; four allow at most sixteen. Maintenance and AI heartbeats share that worker pool. Operator commands use NullPool; backup and restore each need additional control/client connections. Reserve at least 4–6 operational connections and validate the actual Neon plan/role limits before choosing workers. The observed test server `max_connections=901` is a server setting, **not** proof of the account's usable connection quota. Start with a small worker count matched to VPS CPU/RAM and load-test admission, heartbeats and password hashing.

## Operational monitoring

No new public diagnostic endpoint was added. Run the CLI under restricted service credentials and forward aggregates to an access-controlled collector:

```powershell
.\.venv\Scripts\python.exe -m backend.db.operations monitor --database test --directory backups\REPLACE_WITH_RUN_ID
```

It reports connectivity, version drift, storage size, connection count/server limit, expired capacity/chat leases, eligible idempotency redaction backlog, expired AI/auth buckets, backup age/integrity and matching restore receipts. An expired chat lease alone is not an error: rows are intentionally reused. The important alerts are active expired operations and a growing cleanup backlog. Poll storage values over time to measure growth.

Runtime maintenance emits `database_pool_health` alongside the existing AI health logs. Pool occupancy is **process-local**; retain worker identity in the log collector. A CLI NullPool sample cannot measure occupancy in the web workers. Alert on sustained >80% of the per-worker ceiling, pool timeouts/503s, missing health samples, revision/schema drift, unexpected growth, expired-operation/replay backlog increasing over three intervals, backup >26 hours, any integrity failure and absent/stale restore proof. Alert immediately for `offsite_encrypted: false` in a production backup workflow. No external collector, scheduler, encryption key or alert destination was configured by this task.

## Reproducing destructive tests safely

No local database service was reused. Tests created new databases in a newly initialized PostgreSQL 16 cluster under ignored `tmp/database-readiness/cluster`, bound only to `127.0.0.1:55496`. Trust auth is for **synthetic local fixtures only**, never real restored private data or production. No external installation/provisioning was needed; installed 16 server binaries were used.

With that owned cluster running, the test harness checks its actual `data_directory` against this exact workspace path before provisioning. Every database receives a random `nafsiy_disposable_...` name and nonce comment. Cleanup drops only its verified database; it never drops a shared test or primary database.

```powershell
$env:NAFSIY_RUN_POSTGRES_TESTS='1'
$env:NAFSIY_RUN_DISPOSABLE_TESTS='1'
$env:NAFSIY_LOCAL_PG_ADMIN_URL='postgresql+psycopg://nafsiy_readiness@127.0.0.1:55496/postgres'
$env:NAFSIY_LOCAL_PG_DATA='D:\NAFSIY\tmp\database-readiness\cluster'
$env:NAFSIY_TEST_PG_BIN='C:\Program Files\PostgreSQL\16\bin'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These local URL values are synthetic loopback fixtures, with no production credentials. Do not replace them with Neon URLs. If the cluster does not exist, initialize a **new empty directory** using installed matching `initdb` binaries and start it hidden/bound to loopback. `initdb` rejects a nonempty directory. Do not repurpose an existing data directory; external provisioning/download permission is a separate action.

Browser regressions use the guarded temporary SQLite fixture from [AI_OPERATIONS.md](AI_OPERATIONS.md). Run its two existing suites and `node tests/browser/account_deletion.cjs`. The fixture enables deletion only inside its isolated test process. Full final results are in [PRELAUNCH.md](PRELAUNCH.md).

Final complete run: **196/196 PASS**, zero skips, **483.332 seconds** (164 local/unit/regression, 17 Neon and 15 disposable PostgreSQL integration tests). The final exact-decimal backup hashing change was then checked with **18/18 focused database tests PASS** in 21.924 seconds. Lint/build and all three Edge browser suites PASS. Historical revision checksums and Git ignore protection for the new private artifacts were verified.

## Remaining production blockers

- Separately authorize primary preflight/adoption/migration, worker drain and deployment. No automatic primary mutation path was added to the new CLI.
- Restore the current Neon 18 backup into a newly authorized empty 18 target, validate post-restore recovery and deletion replay, and measure realistic RTO with representative data. Local 16 proof is not an 18 restore proof.
- Configure/test encrypted offsite copies, key recovery, scheduler, retention, monitoring/alerts and signed artifact provenance. Current Neon test backup remains plaintext in its access-controlled local directory and has no restore receipt.
- Complete erasure replay/backup policy, user-facing deletion UX, provider retention and legal/privacy review before enabling account deletion.
- Provision least-privilege roles, verify pool quotas and perform concurrent-load/failure testing on the intended VPS/Neon plan.
- Resolve existing source licensing/scholarly review, TLS/Origin/proxy/cookie hardening, provider billing reconciliation and the other PRELAUNCH blockers. Database schema readiness alone is not a release approval.

References: [PostgreSQL pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html), [pg_restore and archive trust](https://www.postgresql.org/docs/18/app-pgrestore.html), [SQLAlchemy pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html), [Neon pooling](https://neon.com/docs/connect/connection-pooling).

## Changed files for V0.9.6

Preexisting staged V0.9.4.4/V0.9.5 work was preserved. This task changed `.env.example`, `backend/core/config.py`, `backend/core/ai_maintenance.py`, `backend/db/database.py`, `backend/api/user_data.py`, `migrations/env.py`, `tests/browser_server.py` and `docs/PRELAUNCH.md`.

New files: `backend/core/account_deletion.py`; `backend/db/catalog.py`, `readiness.py`, `backups.py`, `monitor.py`, `operations.py`, `performance.py`; the three `migrations/bootstrap/` artifacts; `tests/disposable_postgres.py`, `test_database_readiness.py`, `test_account_deletion.py`, `tests/browser/account_deletion.cjs`; `evaluations/database_performance.json`; this document. Private dumps/manifests/receipts and local cluster files remain ignored and are not deliverable source artifacts.
