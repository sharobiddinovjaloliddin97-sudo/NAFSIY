# Current private-staging engineering status ? 2026-10-31

**NOT READY.** Current deliverables, exact file inventory, tests and unresolved gates: [STAGING_READINESS.md](STAGING_READINESS.md). Deployment/rollback: [RENDER_STAGING.md](RENDER_STAGING.md). Repository head is now `e6f1027a93bd`; only local isolated targets were migrated. Linux CI workflow and regression runner completed (215/215 PASS, 0 skips). Primary/shared Neon schemas were not changed. The historical sections below describe earlier releases.

# NAFSIY V0.9.4.4 — Pre-launch holati

Holat: development. Ommaviy production deploy bajarilmadi. Quyidagi tekshiruvlar V1.0 launch tasdig‘i hisoblanmaydi.


V0.9.4.4: [AI_OPERATIONS.md](AI_OPERATIONS.md) — PostgreSQL global generation slots, atomic daily/monthly reservations, actual provider-call attempts, conservative unknown-usage accounting, structured monitoring, safe retention va durable manual browser retry. Test head **`c4d8e2a9106f`**, parent `ade01cf7b885`; schema comparison PASS. Yakuniy test natijalari pastdagi verification bo‘limida. Primary bazaga migratsiya va deploy bajarilmadi. Bu bosqichda commit/push yo‘q; boshlang‘ich V0.9.4.3 snapshot `9ae9741` avvalgi alohida foydalanuvchi topshirig‘i bilan GitHub’ga saqlangan.

V0.9.4.3: [AUTH_SECURITY.md](AUTH_SECURITY.md) — endpoint inventory, session-bound CSRF, anonymous login/register CSRF, PostgreSQL auth abuse limits, proxy contract va behavior changes. Test head **`ade01cf7b885`**, parent `f9a045983bdf`; primary migratsiya qilinmadi. **123/123 PASS: 111 unit/regression + 12 haqiqiy PostgreSQL**, skip yo‘q. Frontend lint/build va 3 browser suite PASS. Keyingi proxy/header edge-case sinovlari ham PASS. Old migration SHA-256 va old table sonlari tekshirildi; commit/push yo‘q.

V0.9.3 backup/restore sinovi yakunlangan: [BACKUP_RESTORE.md](BACKUP_RESTORE.md). V0.9.4.1 idempotency, migratsiya va sinov tafsilotlari: [IDEMPOTENCY.md](IDEMPOTENCY.md). Yangi `7bbcad9d4644` migratsiya faqat test branch’da bajarildi; asosiy branch’da bajarilmadi. Ilova startup’da avtomatik DDL bajarmaydi.

V0.9.4.2 algoritm, policy, testlar va o‘zgargan fayllar: [SESSION_LOCKING.md](SESSION_LOCKING.md). Test branch yangi `f9a045983bdf` head’ga o‘tkazildi; production migratsiyasi va deploy bajarilmadi. Eski migration fayllari o‘zgartirilmagan.

Yakuniy V0.9.4.2 run: **92/92 PASS — 83 unit/regression va 9 haqiqiy PostgreSQL testi**, skip yo‘q. Frontend lint/build va 2 browser scenario suite PASS. Barcha test user/chat/lease/idempotency/quota yozuvlari tozalandi. O‘zgargan/new fayllardagi real `.env` maxfiy qiymatlari, Git ignore va old migration hash’lari alohida tekshirildi; commit/push bajarilmadi.

## V0.9.6 — production database readiness

Runbook and changed-file inventory: [DATABASE_PRODUCTION.md](DATABASE_PRODUCTION.md).

- Fresh install uses checksum-verified `migrations/bootstrap/baseline.sql` for the eight original tables, then traverses all unchanged Alembic revisions. Empty/owned disposable targets only; hidden objects, wrong nonce/name, primary/shared test targets and existing schemas are rejected. No startup `create_all()` or historical migration rewrite.
- Read-only preflight recognizes the exact unversioned original and each reviewed revision; it checks pending upgrades, full schema fingerprints, model compatibility, version, extensions and privileges. Existing-schema upgrades/adoption were tested with preserved fixture accounts/messages. The Neon test branch remains `c4d8e2a9106f`, with no drift or pending upgrade; Alembic comparison PASS. Primary was not queried or migrated.
- Final complete suite: **196/196 backend tests PASS**, zero skips, **483.332 seconds**: 164 local/unit/regression tests, 17 real Neon PostgreSQL tests and 15 disposable PostgreSQL 16 integration tests. Final backup decimal-hash hardening was additionally verified by rerunning **18/18 focused database tests PASS** in 21.924 seconds. Frontend lint/build and all **three Edge browser suites PASS**.
- Backup/restore tests cover schema/all-table content/sequence checks, exported-snapshot consistency, corruption, wrong/nonempty targets, SQL failure rollback and deleted-account resurrection in historical copies. Version-2 manifests preserve exact numeric decimals; a one-unit change at decimal precision `0.0000000001` changes the row fingerprint. Current Neon 18 test backup integrity/archive checks PASS, but **its restore is not verified**: no isolated 18 target was provisioned or reused. Local 16 restore proof does not replace that release gate.
- Authenticated account deletion requires session CSRF, trusted Origin, exact confirmation and password reauthentication; it settles/fences active work, cascades private rows and detaches cost audit links. Shared sources/other accounts survive. It remains **disabled by default** pending deletion UX, offsite erasure replay and privacy review. Restores always remain quarantined for reconciliation.
- Sanitized EXPLAIN ANALYZE fixtures: 100 accounts, 5,000 chats, 100,000 messages, 25,000 attempts. Indexed hot paths took 0.012–0.070 ms; expiry count 0.921 ms and usage aggregate 3.981 ms in warm local runs. No new index/migration was justified. QueuePool defaults are 3 + 1 overflow per worker (16 app connections across four workers); actual plan quotas and VPS load still need validation.
- Operator monitoring covers version drift, storage/connections, cleanup/retention backlog and backup age/integrity/restore proof. Runtime pool health is private structured telemetry. Daily encrypted offsite backups, retention, scheduler/alerts, key recovery and a realistic Neon 18 recovery/RTO rehearsal remain unconfigured release blockers.
- No new Alembic revision, production connection/change, deployment, live `.env` modification, commit or push. Existing staged work was preserved; new private backups and cluster files remain ignored.

## V0.9.5 — source verification and multilingual retrieval

See [SOURCE_VERIFICATION.md](SOURCE_VERIFICATION.md) for provenance, registry states, exact-quotation rules, benchmark definitions/results, changed files and remaining review requirements.

- Quran references now use per-surah Hafs/Kufan boundaries. Source quotations require the correct immutable identifier, content version, language and edition; complete text must match without removing Arabic diacritics or changing verse boundaries. AI commentary is separately labeled unverified. Reference/text equality does not establish religious correctness; `verified` remains false.
- The reviewed runtime allowlist enables `quran-uthmani` and attributed `en.itani`. Uzbek `uz.sodik`, Russian `ru.kuliev`, `en.sahih` and UmmahAPI hadith translations remain permission-pending; Tafsiri Hilol is restricted. Their text is blocked before retrieval. Published Uzbek/Russian translations and the live hadith reader are unavailable until rights/provenance are approved. Uzbek Latin/Cyrillic and Russian questions can still use licensed Arabic/English evidence and unverified AI explanations.
- No new migration. Registry policy is version-controlled code; source metadata fits existing JSON/Text fields. The verified test branch remains at `c4d8e2a9106f`, and Alembic schema comparison detects no upgrade operations. Existing data and prior migrations are preserved.
- The engineering benchmark includes 30 guard cases, 20 live retrieval queries (40 baseline/expanded runs) and five explicit scholar-referral questions. Observed guard precision/rejection is 100%, false-positive rejection 0% for seven schema-compliant positives. Expanded precision@3 is 86.7% in the selected Uzbek Latin/Cyrillic, English and Russian topic sets. This is a small, glossary-overlapping benchmark, not scholarly certification or an end-to-end comparison with the previous Groq planner.
- Live retrieval evaluation made zero Groq calls ($0 AI cost). Existing illustrative maximum two-call reservation is $0.045056; actual end-to-end Groq latency/quality was not evaluated. Full results and latency details: [source_results.json](../evaluations/source_results.json).
- Frontend lint/build and both Edge browser suites pass, including source attribution/status after refresh, literal quotation display, blocked editions, unsupported-quotation warnings, durable manual retry, logout/account switch and CSRF/idempotency headers.
- Final verification on 2026-10-31: **173/173 backend tests PASS**, zero skips, **456.300 seconds**: 156 local/unit/regression tests plus 17 real Neon PostgreSQL tests. This includes 25 source-verification tests and a new source-metadata persistence/replay regression. `alembic -x database=test current` reports `c4d8e2a9106f`; `alembic -x database=test check` reports no new upgrade operations. `git diff --check` passes. No migration was run. The final full run used the stable 30-case benchmark; an earlier development run used mismatched in-memory code and an updated dataset and was superseded.
- Qualified scholarly review, edition collation, translation/hadith licensing, independent multilingual relevance review and broader false-positive evaluation remain release blockers. Existing infrastructure/security/billing risks below remain open. No deployment, primary migration, live `.env` modification, commit or push.

## V0.9.4.4 verification — 2026-10-31

- Full backend command: `NAFSIY_RUN_POSTGRES_TESTS=1 python -m unittest discover -s tests -v`: **147/147 PASS**, no skips, 485.768 seconds. Includes 130 local/unit/regression tests and 17 real Neon PostgreSQL tests, including four-process capacity and daily/monthly budget races.
- `alembic -x database=test current`: `c4d8e2a9106f (head)`, already applied when this verification began; no migration was rerun. `alembic -x database=test check`: no new upgrade operations detected.
- Frontend `npm.cmd run lint` and `npm.cmd run build`: PASS.
- `node tests/browser/ai_operations.cjs`: PASS in headless Microsoft Edge against the isolated SQLite fixture. Covers metadata-only storage, refresh/browser restart without automatic sends, manual status recovery, same-key manual retry, successful completion, logout and account scope changes.
- Previous migration files unchanged; `git diff --check`: PASS. Production database, deployment and live `.env` unchanged; no commit or push.

## Joriy AI oqimi

1. Frontend `POST /api/v1/ai/chats/{session_id}/send` ga faqat `{"message":"..."}` yuboradi. Cookie `credentials: "include"`, `Idempotency-Key` va session-bound `X-CSRF-Token` header’lari bilan yuboriladi.
2. Backend JWT/jti, faol user, signed CSRF, explicit Origin va chat ownership’ini tekshiradi. Anonim/expired session `401`, noto‘g‘ri CSRF yoki origin `403`, valid CSRF bilan begona chat `404` oladi.
3. Kalit user/chat bo‘yicha bazada rezerv qilinadi. Bir xil kalit/savolning completed natijasi agent va limitni qayta ishlatmasdan qaytadi; processing yoki boshqa savol uchun qayta ishlatilgan kalit `409` oladi. Yangi rezervatsiyadan keyin `ai_rate_limits` 60 soniyalik fixed window’da 5 ta urinishga ruxsat beradi. Hisoblagich atomik va backend process’lari bo‘yicha umumiy.
4. Per-chat lease, idempotency owner/version, global slot, kunlik/oylik cost rezervi, bir marta olinadigan logical-request quota va history bitta qisqa admission transaction’da olinadi. Singleton DB gate global budget/capacity qarorlarini process’lar bo‘yicha serial qiladi; quota gate’dan keyin olinadi. Capacity/budget exhaustion `429 + Retry-After` oladi. Busy chat `409 + Retry-After` oladi va limit sarflamaydi. Recovery/retry eski key uchun yana quota sarflamaydi; xatoga uchragan accepted key’ning ilk quota sarfi saqlanadi.
5. Oxirgi 12 ta history xabari bazadan olinadi; har biri kontekst uchun 4000 belgigacha qisqartiriladi. Klient `history`, `role` yoki `sources` yubora olmaydi.
6. Har planner/answer HTTP chaqiruvidan oldin fencing tekshiruvi, alohida attempt marker/counter commit qilinadi. Groq usage tokenlari va estimated USD prompt/javobsiz ledger’da saqlanadi; usage noma’lum bo‘lsa maksimal rezerv konservativ hisoblanadi. Groq + retrieval + Citation Guard javobni yaratadi. DB lease TTL 30 soniya, heartbeat har 8 soniya; generation timeout 120 soniya. Tashqi AI kutilayotganda DB transaction ochiq turmaydi.
7. User va assistant xabarlari, manbalar, chat metadata, 100 xabar chegarasi, completed response va lease release bitta transaction’da saqlanadi. Saqlashdan oldin owner/request/version va DB expiry atomik tekshiriladi. Fencing’dan o‘tmagan eski worker hech qanday xabar saqlamaydi. Xatoda transaction rollback qilinadi; cleanup faqat o‘sha owner/version’ga tegadi, cached response o‘chirilmaydi.
8. Frontend serverdagi chatlarni qayta yuklaydi; `assistant` xabarini o‘zi yozmaydi. Pending UUID/chat/session scope/hash/time localStorage’da yetti kungacha saqlanadi. Refresh/browser restart avtomatik POST qilmaydi; manual status GET yoki aynan o‘sha savolni qo‘lda retry qilish mumkin. JWT/CSRF/savol yoki tarix localStorage’ga qo‘shilmaydi.

Quyidagi eski endpointlar olib tashlangan:

- `POST /api/v1/ai/chat`
- `POST /api/v1/user/chats/{chat_id}/messages`

Sxema Alembic bilan boshqariladi; startup `create_all()` olib tashlangan. Oldingi jadvallar va tarix o‘chirilmagan. SQLite faqat izolyatsiyalangan testlarda ishlatiladi; asosiy limiter va idempotency Neon PostgreSQL’da ishlaydi.

V0.9.4.1: 67 unit test, alohida real Neon ko‘p process testi, frontend lint/build va Edge retry sinovlari o‘tdi. Neon’da 4 process’dan 8 bir xil request uchun bitta agent chaqiruvi, bitta kvota va bitta xabarlar juftligi tasdiqlandi. Sinov ma’lumotlari tozalandi. Brauzerda commit’dan keyingi uzilish, processing `409` va reload xatosida kalit saqlanishi tekshirildi; agent test adapteri bilan ishladi.

## V0.9.2 tekshiruv natijalari

- [x] Login qilmasdan yangi AI endpointiga so‘rov: `401`, model chaqirilmaydi.
- [x] Login bilan yangi endpoint: brauzerda haqiqiy Groq/RAG orqali `200`; frontend payload’ida faqat `message` bor.
- [x] User va assistant juftligi hamda source metadata Neon’da saqlandi. AI xatosidan so‘ng yarim juftlik yozilmadi. Sinov yozuvlari rollback qilindi.
- [x] Boshqa akkauntning chatiga yuborish: `404`, bu urinish boshqa foydalanuvchining kvotasini o‘zgartirmaydi.
- [x] Eski AI va message-write endpointlari OpenAPI’da yo‘q va to‘g‘ridan-to‘g‘ri chaqirilganda `404`/`405` qaytaradi.
- [x] Akkauntning 6-so‘rovi: `429`. Xato urinishlar ham hisoblandi. Haqiqiy Neon’da 4 jarayondan kelgan 10 parallel urinishning 5 tasi qabul qilindi, 5 tasi bloklandi; hisoblagich 5 bo‘lib qoldi. Sinov bucket’i tozalandi.

Qo‘shimcha sinovlar: alohida akkauntlar limitlari, keyingi vaqt oynasi, bazadan olingan history, buzilgan AI natijasi, yozish xatosida rollback, AI kutilayotganda chatni o‘chirish, oxirgi 100 xabarning tartibi va noma’lum request maydonlarini rad etish.

Backend: `python -m unittest discover -s tests -v` — 53 ta test o‘tdi. Frontend: `npm run lint` va `npm run build` o‘tdi. Brauzer sinovi vaqtinchalik bazada haqiqiy backend handlerlari va bitta haqiqiy Groq/RAG yuborishi bilan bajarildi; limit sinovining qolgan javoblari test adapterida yaratildi.

## Saqlangan V0.9.1 himoyalari

Citation Guard `[2:255]`, `[bukhari:1]`, `[tirmidhi:2787.2]` formatidagi havolalarni joriy retrieval ro‘yxati bilan solishtiradi. Noma’lum reference aniqlansa original javob o‘rniga ogohlantirish va bo‘sh `sources` qaytadi. Faqat javobda ishlatilgan manbalar ko‘rsatiladi va `verified: false` saqlanadi.

Guard iqtibos matnini, diniy izohning ma’nosini yoki hadis bahosini mustaqil tasdiqlamaydi. Boshqa formatdagi havolalar bu parser’ga kirmaydi. Mos qavsli citation bo‘lmagan javobda `sources` bo‘sh qoladi.

## Hozirgi chegaralar

- Normal operation’da bitta chatga bitta valid generation lease bor. Crash/stall/takeover’da Groq invocation exactly-once emas; eski tashqi call hali ishlashi mumkin, ammo uning database completion’i fencing bilan to‘siladi.
- Expired processing key’lar yangi owner/version bilan tiklanadi; completed javoblar retention muddati davomida replay qilinadi. V0.9.4.4’dan oldingi worker’larni rollout oldidan drain/stop qilish kerak: mixed-version budget/fencing xavfsiz emas.
- Durable storage faqat oxirgi pending turn uchun; multi-tab last-writer semantikasi bor. Yetti kundan keyin browser metadata o‘chadi. Server completed/abandoned javoblari kamida 30 kun va active lease davomida saqlanadi, keyin key tombstone qoladi (`410`); eski key qayta generation boshlamaydi. Avtomatik retry yo‘q.
- Quota har accepted logical key uchun bir marta. Default global cap 10, provider attempt lifetime limiti 6 HTTP call, development estimate budget kuniga $2/oyiga $20. Planner ham attempt hisoblanadi. Narxlar illustrative: launch oldidan verified pricing bilan sozlash shart. Currency cap yoki remote provider exactly-once kafolati yo‘q; timeout/crash konservativ baholanadi. Davrlar UTC admission vaqtiga tegishli.
- PostgreSQL clock jump, network stall yoki final DB transaction lock’i recovery availability’ni kechiktirishi mumkin. Statement/lock/idle transaction timeouts qo‘shilgan; stale owner nomidan save qilishga ruxsat berilmaydi.
- V0.9.2’dan oldingi xabarlar saqlangan; eski `assistant` yozuvlarining kelib chiqishi retroaktiv tasdiqlanmagan.
- AI limiter fixed-window. Eski minute bucket’lar 10-minute grace va DB-time window tekshiruvi bilan bounded cleanup qilinadi. Auth/AI maintenance har 60 soniyada; downtime/backlog fizik cleanup’ni kechiktirishi mumkin. Cost audit retention hozir indefinite; hajm/deletion policy alohida ko‘rib chiqiladi.
- Signed CSRF barcha cookie-authenticated write’larni qamrab oladi; SameSite/Origin defense in depth bo‘lib qoladi. CLI ham token talab qiladi. JWT revocation va XSS himoyasi alohida masalalar.
- Login per-IP va IP+email limitlari global account lockout yaratmaydi; NAT’dagi userlar source limitni bo‘lishadi. Distributed attacks uchun MFA/WAF/risk controls hali yo‘q. Proxy’da ASGI raw peer saqlanishi, Uvicorn automatic proxy headers o‘chirilishi kerak.
- Registration generic `202`, auto-login yo‘q; keyingi explicit login kerak. Eski jti’siz JWT’lar qayta login talab qiladi. Bu user/chat/password data’ni o‘zgartirmaydi.

## Production oldidan qolgan ishlar

- [x] AI xabarlarini faqat server yozishi, klient message-write yo‘lini yopish va history’ni bazadan olish.
- [x] AI uchun foydalanuvchi bo‘yicha bazada saqlanadigan 5 so‘rov/daqiqa limiti.
- [x] Bir xil user/chat/request key uchun DB unique constraint bilan idempotency va atomik completed response.
- [x] Turli kalitlar uchun PostgreSQL generation lease, heartbeat, fencing, safe processing recovery va atomik quota accounting.
- [x] Durable manual browser retry, provider attempt/cost budget, global concurrency cap va key tombstone bilan idempotency retention.
- [ ] Eski xabarlar provenance, audit retention/export va kengaytirilgan multi-tab retry queue siyosati.
- [x] Login/registration/bootstrap uchun atomic PostgreSQL source va email+source counters, enumeration resistance va auth-rate retention/maintenance.
- [x] Session-bound CSRF barcha authenticated write’lar va logout uchun; anonymous login-CSRF defense, frontend bootstrap/manual refresh va salbiy testlar.
- [x] Eski AI bucket’lar bounded cleanup’i, global provider concurrency/cost/retry budget, privacy-safe JSON monitoring va read-only aggregate CLI.
- [ ] Distributed bot/MFA himoyasi, provider invoice reconciliation, collector/alert integratsiyasi va capacity load test.
- [ ] Production frontend/API Origin, Secure cookie va trusted reverse proxy konfiguratsiyasi hamda tashqi security review.
- [ ] Email verification va JWT/session revocation. Hozir logout cookie’ni o‘chiradi; nusxalangan JWT muddati tugaguncha yaroqli qolishi mumkin.
- [x] Alembic baseline, test branch’da idempotency migratsiyasi va alohida bo‘sh database’da backup’dan tiklash sinovi.
- [ ] Asosiy branch migratsiyasini rollout qilish, fresh-install migration va foydalanuvchi ma’lumotlarini to‘liq o‘chirish sinovi.
- [ ] Iqtibosni asl matnga solishtirish, hadis raqamlash standartlari, diniy ekspertiza va o‘zbekcha semantik retrieval.
- [ ] HTTPS, ishonchli reverse proxy IP’lari, `AUTH_COOKIE_SECURE=true`, monitoring va maxfiy ma’lumotlarsiz loglash.
- [ ] Suhbatda ulashilgan Neon/Groq credentials’larini almashtirish va production secrets boshqaruvi.

Manbalar: [PostgreSQL ON CONFLICT va RETURNING](https://www.postgresql.org/docs/current/sql-insert.html), [SQLAlchemy PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html), [OWASP API Security Top 10](https://devguide.owasp.org/en/07-training-education/07-api-top-ten/), [FastAPI HTTPS](https://fastapi.tiangolo.com/deployment/https/). Bu hujjat yakunlangan mustaqil security audit o‘rnini bosmaydi.
