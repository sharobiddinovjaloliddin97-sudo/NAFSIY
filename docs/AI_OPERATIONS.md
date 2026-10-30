# NAFSIY V0.9.4.4 — AI operations

Development only. No production migration or deployment. Migration `c4d8e2a9106f` follows `ade01cf7b885`; previous revisions remain unchanged. Existing CSRF, explicit Origin checks, JWT ownership, citations, per-chat fencing and atomic message persistence remain mandatory.

## Configuration

These defaults are estimates for development, **not a statement of current Groq prices**. Verify provider pricing and set the two price variables before launch. Every worker must use the same model, price, limits and budget configuration. A price change affects new operations; existing operations retain their price snapshot. A model change during an operation fails closed before the next call.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAX_GLOBAL_AI_GENERATIONS` | `10` | Global leased generation pipelines, including planning/retrieval |
| `AI_DAILY_BUDGET_USD` | `2` | UTC daily admission budget; zero stops new generations |
| `AI_MONTHLY_BUDGET_USD` | `20` | UTC monthly admission budget; zero stops new generations |
| `AI_INPUT_USD_PER_MILLION` | `1` | Illustrative USD per million input tokens |
| `AI_OUTPUT_USD_PER_MILLION` | `2` | Illustrative USD per million output tokens |
| `AI_MAX_PROVIDER_ATTEMPTS` | `6` | Maximum actual HTTP-call markers across a logical key's lifetime |
| `AI_MAX_INPUT_TOKENS` | `16384` | Conservative input estimate ceiling for each call |
| `AI_PLANNER_OUTPUT_TOKENS` | `2048` | Planner maximum completion tokens |
| `AI_MAX_OUTPUT_TOKENS` | `4096` | Answer maximum completion tokens |

The planner and answer are **two separately counted provider attempts**, not one. Six permits at most three full two-call pipelines, fewer if previous failures consumed attempts. A marker committed immediately before an HTTP call also counts if the process dies before sending it; this deliberately overcounts uncertain attempts. SDK retries stay disabled. A completed replay consumes no attempt, logical quota, budget reservation or global slot.

Prices must be finite and positive, budgets finite/nonnegative, capacity 1–100 and attempts 1–20. Input/output limits are validated. Output limits include provider reasoning generation as supported by Groq's completion parameter. Input is conservatively estimated as UTF-8 bytes plus framing allowances; this is not a model-specific tokenizer or a guaranteed provider billing token count. History is trimmed oldest-first; oversized source sets are reduced before calling. A still-oversized request is rejected before HTTP. No prompt, source text, response, email, raw JWT, CSRF token or API key is stored in operations/usage tables or their logs.

For a small initial audience, explicitly set verified prices, start with 2–3 concurrent pipelines, keep a small daily/monthly budget, leave SDK retries off, and retain the existing five logical requests per user/minute. Output limits of 1024–2048 may reduce cost but can cause visible truncated-output errors; test them with religious answers before launch. These are operator choices; this task does not alter live `.env` or provider billing settings.

## Admission and race prevention

1. Verify JWT/session-bound CSRF, Origin and chat ownership. Lock the chat row, check fingerprint/idempotency and replay a completed response immediately.
2. Acquire/replace the per-chat owner/version lease using the existing conditional upsert. Busy different-key requests remain `409 + Retry-After`, without Groq.
3. Lock the singleton `ai_operations_gate` row in a short PostgreSQL transaction. This serializes global slot and budget ledger decisions across workers. Recover up to 100 expired operations first.
4. Check **spent + all outstanding reserved cost + this estimate** against both UTC period budgets. Reserve the sum of the independently rounded worst estimates for planner and answer. Acquire a numbered slot using primary-key uniqueness and an expiry-conditional PostgreSQL upsert that increments its fencing version.
5. Charge the existing per-user logical quota only once, after the operations gate has been acquired. This ordering avoids a shared user bucket waiting behind another worker's global admission. Rebase both lease deadlines at the end of admission, while still holding the chat and operations locks and verifying exact ownership, so admission work does not consume the generation's initial lease. Commit idempotency, chat/global leases, budget reservation, operation and quota together. Any rejection rolls everything back.

Global capacity and budget exhaustion return `429`: `AI global capacity exhausted` with `Retry-After: 5`, or `AI spending budget unavailable` with `Retry-After: 60`. A budget retry interval is a polling interval, not a promise that funds become available after 60 seconds. Provider attempt exhaustion returns `429`, `Retry-After: 60` and requires a new explicit logical request; waiting does not reset that key's lifetime counter. Per-user limits remain separate.

Database time determines slot/chat expiry and UTC budget periods. Normal leases are 30 seconds, renewed every 8 seconds; outer generation timeout is 120 seconds, each provider call has a 40-second coroutine timeout and the SDK's existing 30-second timeout. Cancellation grace is two seconds. There is no DB transaction held while waiting on Groq. The operations gate may wait up to 15 seconds (20-second statement timeout while acquiring it); existing short transaction timeouts are restored afterward. Saturation or database faults can still return safe database/lease errors rather than admit work.

Global heartbeat renewal uses a conditional SQL update over current owner, slot/generation versions, active operation and DB expiry. It does **not** wait on the budget gate. It cannot revive an expired or replaced slot. Chat renewal retains its existing fencing; failure in either phase cancels generation. Reaping, attempt settlement and admissions continue to use the gate because they mutate financial balances.

## Attempts, accounting and fencing

Before **each** planner/answer HTTP call, a short transaction checks the current per-chat owner/version and unexpired global slot/version, increments the idempotency lifetime attempt counter conditionally, inserts a unique operation/phase attempt marker and commits. There can be only one planner and one answer marker per operation. Different recovery owners get different operation IDs; counters persist on the logical key.

The usage record contains provider/model, status, nullable input/output token counts, reserved and estimated USD, and start/finish timestamps. Pricing and context ceilings are snapshotted on the operation. Returned usage replaces that attempt's maximum reservation with its measured-token cost estimate. The status transition from `started` makes reconciliation idempotent: duplicate completion notifications cannot charge again. Missing or partial metadata keeps a conservative maximum estimate (or a higher measured partial cost), marked `_estimated`; available token fields are retained. Provider failures, timeout and cancellation use the same conservative policy because remote billing may already have occurred.

Before message persistence, the chat fencing token and current global operation/slot version must still be valid. The user/assistant pair, completed idempotency response, operation completion, unused reservation refund and global slot release commit atomically. Stale workers cannot complete a newer attempt or persist messages. Cleanup uses the exact owner and generation version; even a matching owner with a stale version cannot release the current operation.

## If Groq answered and the process crashes

- If the database save committed, the completed response replays and no new provider request is made.
- If usage committed but message persistence did not, measured usage remains in the ledger. After lease expiry, unused reservation is refunded; an explicit retry may invoke the provider again within the lifetime attempt limit.
- If the provider was invoked but usage did not commit, recovery charges the marked attempt's conservative maximum once and releases the unused pipeline allowance. The lost answer cannot be reconstructed from the usage table. Retry may invoke Groq again.
- If admission committed but no attempt marker did, recovery releases the full estimate; no provider call was authorized yet.

Recovery occurs on new admissions and every 60 seconds in maintenance. Expired capacity is reclaimable by conditional upsert even after a crashed process; it cannot permanently occupy a slot. Heartbeat failure cancels local work; an external call might continue remotely. Late stale usage is rejected rather than modifying a newer attempt. Conservative estimates remain available for later operator reconciliation with provider invoices; no automatic invoice reconciliation or webhook was introduced.

This is **not an exact hard currency cap or provider exactly-once guarantee**. Provider prices/usage, underestimated framing, external billing after timeout, or overlapping remote calls following lease expiry can exceed local estimates. Known cost overruns are recorded in full and block subsequent admissions; existing calls cannot be unbilled. Budget periods are attributed to the operation's UTC admission day/month, including attempts that finish across midnight/month end. Outstanding work is not reclassified into the provider's eventual invoice period. There is no cross-window billing-cap guarantee.

Accounting starts with this version. Migration initializes existing keys' provider counters to zero; earlier calls and provider invoices cannot be reconstructed from chat history. Legacy processing keys are bounded for future attempts, without pretending to count their historical calls. Review the provider's existing account spend independently before production admission.

## Durable browser retry

The only new localStorage item is `nafsiy.pending-ai.v1`: UUID key, chat ID, opaque authentication-session scope, SHA-256 question digest and creation time. No question text or conversation history is written there. The digest is metadata, **not encryption**, and low-entropy questions may be guessed by someone with storage access. CSRF and JWT remain outside localStorage. If storage is disabled, the send fails before POST rather than promising refresh-safe recovery.

Metadata expires after seven days, well inside server response retention. On refresh/browser restart the UI fetches a fresh authenticated session scope, validates the stored shape and exposes a manual recovery control. It never automatically sends an AI POST. `GET /api/v1/ai/chats/{id}/requests/{key}` is an authenticated, ownership-scoped read that returns only `unknown`, `pending`, `completed` or `expired`; it invokes no provider. Completed recovery reloads server messages. Otherwise the user must retype the identical question and explicitly send; the digest/session scope restores the old key. A different question is a new explicit logical request. Only the last pending request is retained per browser origin.

Successful response plus successful chat reload, logout, authentication/session change and scope mismatch clear metadata. Same-user re-login changes scope too. CSRF failures preserve the pending key and require manual retry. A manual discard asks for confirmation because an old remote request might already have completed. Account switches never reuse a previous session's key. Shared-origin tabs have last-writer storage semantics; multi-tab recovery is not a durable queue.

## Retention and maintenance

- Expired AI minute buckets are removed in indexed batches of at most 100 after a ten-minute grace period; both age and bucket window are checked. Recent/active buckets are not removed.
- Completed and abandoned idempotency response payloads are retained at least **30 days since last update**, and longer while that chat has an active lease. Cleanup takes the same chat lock with `SKIP LOCKED`, rechecks age/lease under the lock and never deletes a live reservation.
- After retention, the response becomes an `expired` tombstone. The key/fingerprint/attempt count is retained for the chat's lifetime; retry returns `410`, never fresh generation. Tombstones are not deleted automatically, so maintenance cannot turn an old key into a new request. Deleting a chat still removes its idempotency records; the chat ID is no longer usable.
- Operations/attempt audit and monthly/daily aggregate rows are retained indefinitely for now. Chat/account deletion nulls the operation's idempotency link and leaves cost-only audit rows; they contain no user ID, email or conversation text. No audit deletion is performed in production. A separately reviewed retention/export policy is required as volume grows.
- Each 60-second job reaps at most 100 expired operations, removes at most 100 rate buckets and retires at most 100 replay candidates. Jobs from multiple processes use PostgreSQL locks and conditional state changes. Backlog/downtime can delay physical cleanup and redaction. Cleanup is safe to repeat and does not migrate schema.

## Monitoring

JSON events have UTC timestamps and fixed fields: admission/rejection, provider attempts, token usage and estimated USD, provider timeout/error category, lease fencing rejection, expired-operation recovery, cleanup counts/failures, persistence failure and an operations health snapshot every maintenance cycle. Exception messages, HTTP bodies, prompts and security credentials are excluded. Usage-backed (`success`) and uncertain (`*_estimated`) cost remain distinguishable. Counts/reservations in PostgreSQL are authoritative; logs are not a transactional billing ledger and may be lost or duplicated around process crashes.

Read-only aggregate CLI, test branch by default:

```powershell
.\.venv\Scripts\python.exe -m backend.core.ai_monitor --database test
```

`--database primary` is an explicit operator-only read for after an authorized rollout; it was not used in this task. The CLI reveals only concurrency, configured limits, period spent/reserved totals and 24-hour usage/status aggregates. No public metrics endpoint or new admin credential was added. Forward JSON logs to an access-controlled collector; configure alerts there:

- `spent + reserved` reaches 80%/95% of either budget; any observed overrun.
- Concurrency remains above 80%, repeated capacity rejects, or frequent DB-gate timeouts.
- Any `fencing_rejected`, unexpected recovery spike, unknown/estimated usage growth, repeated provider timeout or `usage_reconciliation_failed`.
- Cleanup failures or a backlog growing across three maintenance cycles; missing health events for more than three minutes.

Provider usage fields and output controls follow [Groq API Reference](https://console.groq.com/docs/api-reference); the application's USD prices remain explicitly configurable rather than copied as permanent provider prices.

## Validation and remaining launch risks

Verified on 2026-10-31: **147/147 backend tests PASS**, no skips (485.768 seconds): 130 local/unit/regression and 17 real Neon PostgreSQL tests. Frontend lint/build and the durable retry Edge browser suite PASS. Test branch was already at `c4d8e2a9106f`; Alembic schema comparison found no new upgrade operations. See [PRELAUNCH.md](PRELAUNCH.md) for commands and scope. Tests use isolated SQLite, a verified Neon test branch, synthetic provider responses, real four-process PostgreSQL races and Edge/Playwright. Browser fixtures are gated by `NAFSIY_BROWSER_TEST=1` and use only temporary SQLite; test endpoints are not imported by production.

Reproduce the new browser suite with the locally configured frontend built/started on `http://localhost:3000`. In a separate terminal start the isolated fixture:

```powershell
$env:NAFSIY_BROWSER_TEST='1'
.\.venv\Scripts\python.exe tests\browser_server.py
```

Use the existing temporary Playwright installation or install it outside the repository:

```powershell
npm install --prefix "$env:TEMP\nafsiy-browser-check" playwright
$env:NAFSIY_PLAYWRIGHT_PATH="$env:TEMP\nafsiy-browser-check\node_modules\playwright"
node tests\browser\ai_operations.cjs
```

Microsoft Edge must be available. Browser API requests are intercepted into port 8102's synthetic fixture, never forwarded to Groq or Neon. The browser process really closes and restarts; its storage state is transferred only in memory, not exported to a file. The fixture test includes reintroducing an old account's pending metadata to confirm scope rejection.

New controls do not replace TLS, correct proxy/CORS/Secure-cookie configuration, JWT revocation/email verification/MFA, billing alerts, provider invoice review, or source/religious review. Singleton admission serialization is intentionally simple for a small audience; load-test transaction latency/heartbeat pressure before increasing capacity. Do not run old workers alongside the new schema/code during rollout. Fresh-install migrations and user-data deletion/audit retention policy remain separate launch work. No deployment, primary migration, commit or push is authorized by this task.

## Changed files

- `.env.example`
- `backend/ai/agent.py`
- `backend/ai/generation.py`
- `backend/ai/provider.py`
- `backend/api/ai.py`
- `backend/core/ai_maintenance.py`
- `backend/core/ai_monitor.py`
- `backend/core/ai_operations.py`
- `backend/core/config.py`
- `backend/core/idempotency.py`
- `backend/core/leases.py`
- `backend/core/rate_limit.py`
- `backend/db/ai_models.py`
- `backend/db/idempotency_models.py`
- `backend/db/operations_models.py`
- `backend/db/schema_check.py`
- `backend/main.py`
- `docs/AI_OPERATIONS.md`
- `docs/PRELAUNCH.md`
- `frontend/src/app/page.tsx`
- `frontend/src/lib/csrf.ts`
- `frontend/src/lib/pendingAI.ts`
- `migrations/env.py`
- `migrations/versions/c4d8e2a9106f_add_ai_operations_and_budgets.py`
- `tests/browser/ai_operations.cjs`
- `tests/browser_server.py`
- `tests/operations_fixture.py`
- `tests/test_ai.py`
- `tests/test_ai_operations.py`
- `tests/test_ai_operations_postgres.py`
- `tests/test_ai_send.py`
- `tests/test_generation_migration.py`
- `tests/test_generation_postgres.py`
- `tests/test_idempotency_postgres.py`
