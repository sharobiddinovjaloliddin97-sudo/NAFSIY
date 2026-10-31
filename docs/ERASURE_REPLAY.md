# Account erasure and restore quarantine

Account deletion stays disabled in staging validation and by default. The `/account`
page explains consequences and displays a disabled action; it does not collect a
deletion request or promise that erasure has occurred. Existing synthetic test
fixtures can still exercise the password/CSRF/confirmation protected handler.
Enabling a destructive user workflow requires a separate reviewed implementation
of the journal protocol below, provider-retention policy and privacy/legal approval.

## Required protocol before enablement

1. The user reviews removal of account, chats, bookmarks, active generations and
   recovery tokens, plus the documented backup and provider retention periods.
   Require the current password, session CSRF, trusted Origin and the exact text
   `DELETE MY ACCOUNT`; show a final review with Cancel as the initial action.
2. Append a durable signed pending erasure event to a separately protected,
   encrypted offsite journal before deleting live data. Use a random event ID,
   monotonic journal sequence, timestamp, schema version and HMAC of the immutable
   account ID under a journal-only key. Do not store email, chat text or passwords.
   Record key IDs and maintain access-controlled key recovery separately from DB
   backups. Pending events are replay obligations even if the final receipt is lost.
3. Within the existing account/chat locking and financial-settlement transaction,
   delete private rows and invalidate recovery tokens/sessions. The current FK
   cascade includes recovery tokens. Retained financial aggregates must be detached
   from user/chat/request identifiers under the approved retention policy.
4. Append a signed completed event after commit, then acknowledge erasure. A crash
   between steps is reconciled idempotently by event ID. Failure to persist the
   pending event must prevent live erasure. Failure after DB commit must not be
   reported as proof that the account still exists.
5. Retain journal events for at least the longest DB/offsite retention window plus
   recovery margin. Pruning backups or journal events is an explicitly approved
   operator action. Revoking the journal key while backups remain would defeat
   erasure replay and is prohibited by the protocol.

## Restoring a historical backup

Restore only into a newly authorized empty isolated target. Keep network access
quarantined; a successful dump/hash/sequence receipt has `ready_for_application=false`.
Validate journal signatures, key IDs, continuity and the latest sequence obtained
from the independent offsite journal. An unavailable, stale or incomplete journal
blocks activation. Iterate restored user IDs, compute their keyed hashes, and
reapply every pending/completed matching erasure through the same settlement and
deletion logic, without sending mail. Use idempotent replay receipts tied to dump
hash and journal sequence. Confirm absence of private rows and detached audit links.

Invalidate **all** restored sessions by increasing every remaining account's
`auth_version`, and delete all restored recovery tokens while quarantined. Rotate
the staging JWT signing key if restoring into a new security boundary. Otherwise
historical sessions, tokens and revoked credentials may become valid again.

Compare expected post-replay counts, schema and ownership boundaries, and run
synthetic security probes. Fingerprints legitimately change after erasure; preserve
both the original restore verification and the post-replay receipt. Two-person
review of evidence is required before releasing quarantine. This document is a
design, not an implemented external erasure journal or completed privacy review.
