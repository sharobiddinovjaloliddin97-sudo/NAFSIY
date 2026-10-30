# NAFSIY V0.9.4.2 — Session Locking & Crash Recovery

Test branch migratsiya head’i: `f9a045983bdf`, oldingi revision `7bbcad9d4644`. Asosiy database migratsiya qilinmadi. Deploy, commit va push bajarilmadi.

Bu V0.9.4.2 holati. V0.9.4.3’da authenticated write’lar uchun `X-CSRF-Token` ham talab qilinadi; login/register anonymous CSRF ishlatadi. Yangi test head `ade01cf7b885`. Joriy auth oqimi va testlar: [AUTH_SECURITY.md](AUTH_SECURITY.md). Lease/fencing migration history o‘zgartirilmagan.

## Algorithm

1. JWT, origin va chat ownership tekshiriladi. Request’dagi auth Session rollback qilinib, connection pool’ga qaytariladi. Keyingi DB fazalari o‘zining alohida Session’idan foydalanadi.
2. Qisqa admission transaction chat row’ini lock qiladi. Completed idempotency replay va message fingerprint shu yerda tekshiriladi. Replay chat band bo‘lsa ham agent yoki kvotani qayta ishlatmaydi.
3. `chat_generation_leases.session_id` primary key va conditional PostgreSQL upsert chat uchun yagona faol lease beradi. Faqat `expires_at <= clock_timestamp()` bo‘lsa takeover mumkin. Har acquisition yangi tasodifiy `owner_id` va `version = old_version + 1` oladi. Lease qatori release’da o‘chirilmaydi; token qayta ishlatilmaydi.
4. Processing rezervatsiyaga owner/version bog‘lanadi. Yangi logical request uchun limit, `rate_charged=true`, lease va server history bir transaction’da olinadi va commit qilinadi. Busy so‘rov `409 + Retry-After` oladi; unga yangi rezervatsiya yoki quota yozilmaydi. Savol bir xil kalit bilan o‘zgartirilsa `409` qoladi.
5. Agent boshlanishidan oldin ownership yana renew orqali tekshiriladi. Generation davomida heartbeat alohida qisqa transaction’lar bilan faqat ayni owner, request ID, version va hali tugamagan expiry’ni yangilaydi. Tashqi Groq/RAG kutilayotganda ochiq DB transaction yoki row lock saqlanmaydi.
6. Save transaction avval ownership’ni qayta tekshiradi, chat row’ini lock qiladi, so‘ng lease owner/request/version va DB expiry bilan conditional update bajaradi. Shu update lease row’ini ham lock qiladi. Eski yoki muddati tugagan worker `409` oladi; u chat xabarlarini yozmaydi.
7. User/assistant juftligi, chat metadata, message limit, shu owner/version’ga tegishli completed idempotency response va lease release bitta transaction’da commit qilinadi. Biror qismi xato bo‘lsa hammasi rollback bo‘ladi.
8. Xato yoki cancellation’da faqat ayni owner/request/version lease’i bo‘shatiladi. Completed response va processing rezervatsiya o‘chirilmaydi. Eski worker cleanup’i yangi owner’ga tegmaydi.

Lease va expiry faqat database soati bilan baholanadi. PostgreSQL `clock_timestamp()` transaction boshlanish vaqtiga qotib qolmaydi. [PostgreSQL vaqt funksiyalari](https://www.postgresql.org/docs/18/functions-datetime.html), [conditional ON CONFLICT](https://www.postgresql.org/docs/18/sql-insert.html).

Save fencing tekshiruvi yozish transaction’i boshida linearizatsiya qilinadi. Keyin DB row lock’lari takeover’ni commit/rollback’gacha to‘sadi. Write transaction nominal expiry’dan o‘tib ketsa ham undan oldin yangi owner paydo bo‘la olmaydi. Takroriy request commit’dan keyin completed response’ni ko‘radi; write lock timeout’da esa retry qilinadigan busy javobi qaytadi.

## Policy va timeouts

| Parametr | Qiymat |
| --- | ---: |
| Lease TTL | 30 soniya |
| Heartbeat interval | 8 soniya |
| Generation timeout | 120 soniya |
| Cancellation grace | 2 soniya |
| PostgreSQL statement timeout | 5 soniya, har statement |
| PostgreSQL lock timeout | 3 soniya |
| PostgreSQL idle transaction timeout | 10 soniya |
| App engine connection timeout | 10 soniya |
| App connection pool checkout timeout | 5 soniya |

Renewal xatosi yoki lease yo‘qotilishi agent task’ini cancel qiladi; natija saqlanmaydi. Generation timeout `504`, DB mavjud bo‘lmasa `503`, busy yoki fencing failure `409` qaytaradi. Retry doim avvalgi `Idempotency-Key` bilan bajariladi. `Retry-After` CORS orqali browser JavaScript’iga ham ko‘rinadi.

DB thread operatsiyasi cancellation vaqtida tugaguncha kuzatiladi; Session cleanup bilan parallel ishlatilmaydi. Reservation commit bo‘lgan, lekin async caller natijani olishga ulgurmay bekor qilingan holatda ham aynan qaytgan token bilan cleanup qilinadi. Commit natijasi noaniq bo‘lsa cached completed yozuv o‘chirilmaydi.

Cancellation’ni qabul qilmaydigan provider task’iga 2 soniya grace beriladi. U kech qaytsa, natijasi tashlanadi; bunday task’da database save yo‘li yo‘q. SQL driver’dagi ishlayotgan thread’ni Python majburan to‘xtata olmaydi. DB/connect/pool timeouts normal kutishni chegaralaydi; butunlay uzilgan TCP aloqasi operatsiya vaqtini uzaytirishi mumkin. Bunda ham worker’ga yangi owner nomidan yozish huquqi berilmaydi.

## Crash recovery va quota

- Admission transaction commit bo‘lmagan bo‘lsa lease, rezervatsiya va quota birga rollback bo‘ladi.
- Commit’dan keyin worker qulagan bo‘lsa lease expiry’dan keyin retry yangi owner/version oladi. Agent yangi server history bilan qayta boshlanishi mumkin.
- Bir xil logical key uchun quota bir marta olinadi. `rate_charged` quota bilan birga commit qilinganligi sabab recovery va boshqa daqiqadagi retry ham qo‘shimcha slot olmaydi.
- Save commit qilingan, HTTP response yo‘qolgan bo‘lsa keyingi request cached answer’ni qaytaradi. Expired processing yozuvi hech qachon ko‘r-ko‘rona o‘chirilmaydi.
- Boshqa key expired lease’ni olsa, oldingi processing yozuvi saqlanadi. Uni keyinchalik o‘z key’i bilan retry qilish yangi history’dan foydalanadi; eski worker’ning completion’i fencing’dan o‘tolmaydi.

Bu 5 ta yangi logical request/daqiqa siyosati. Provider retry/cost budget alohida yo‘q: avval qabul qilingan, hali completed bo‘lmagan key’ni ko‘p marta tiklash qo‘shimcha tashqi chaqiruvlar yaratishi mumkin. Bunga alohida attempt budget va monitoring kerak.

Legacy V0.9.4.1 rezervatsiyalarida quota commit’i bo‘lganini aniq biladigan flag yo‘q edi. Migratsiya mavjud yozuvlarni konservativ `rate_charged=true` bilan belgilaydi. Bu eski so‘rovni ikki marta hisoblashni oldini oladi; eski worker aynan quota’dan oldin qulagan bo‘lsa bir logical slot kam hisoblanishi mumkin. Completed legacy javoblar o‘zgarishsiz replay qilinadi.

## Migration

```powershell
.\.venv\Scripts\python.exe -m alembic -x database=test upgrade head
.\.venv\Scripts\python.exe -m alembic -x database=test current
```

Yangi migration `ai_idempotency`ga `lease_owner`, `lease_version`, `rate_charged` qo‘shadi va `chat_generation_leases` yaratadi. Oldingi ikkala revision fayli o‘zgartirilmagan; SHA-256 tekshiriladi. Test branch schema check modellarga mos bo‘lishi kerak. Eski public jadvallardagi aggregate sonlar migration’dan oldingi qiymatlar bilan solishtiriladi.

Eski server versiyalari bu fencing protocol’ini bilmaydi. Rollout oldidan barcha eski worker’larni to‘xtatish/drain qilish shart; eski va yangi worker’larni birga ishlatish qo‘llab-quvvatlanmaydi. Lokal test migratsiyasidan oldin backend listener mavjud emas edi. Asosiy branch rollout’i bu vazifaga kirmaydi. Baseline hali bo‘sh database uchun fresh-install migration emas.

## Tekshiriladigan holatlar

Yakuniy bitta to‘liq run: **92 test — 83 unit/regression + 9 real PostgreSQL, barchasi PASS, skip yo‘q** (`154.768s`). `NAFSIY_RUN_POSTGRES_TESTS=1` bilan bajarildi. Standart opt-insiz run PostgreSQL testlarini skip qiladi.

Frontend `npm run lint` va `npm run build` PASS. Ikkita Edge/Playwright browser scenario suite PASS: oldingi idempotency regression va yangi session-busy/renewal/replay sinovlari. Test branch head va schema check PASS; eski application aggregate sonlari va eski migration SHA-256 qiymatlari saqlangan.

- Oldingi auth, ownership, origin, RAG/Citation Guard, history, 100 message limit va cached replay testlari saqlangan.
- Different-key busy, expired same/different-key recovery, owner/version fencing, expired owner renewal, stale cleanup, heartbeat renewal/failure/takeover, generation timeout, reserve/save cancellation, cancellation’ga qaramay kech qaytgan provider va quota rollback testlari.
- Legacy processing/completed yozuvlar migratsiyada saqlanishi va downgrade mavjud yozuvlarni buzmasligi.
- Haqiqiy PostgreSQL’da 4 process’dan conditional upsert: bitta owner; 4 process’dan same-key API request: bitta agent/slot/juftlik.
- Worker `os._exit(29)` bilan reservation commit’idan keyin va successful save commit’idan keyin majburan to‘xtatiladi; recovery va replay tekshiriladi.
- Yangi owner oldidan/keyin eski worker completion va cleanup urinishlari rad etiladi.
- Heartbeat boshlang‘ich TTL’dan keyin ham owner’ni ushlab turadi; generation boshlanishidan qolgan transaction yo‘q, boshqa chat mustaqil ishlaydi.
- Final write transaction expiry’dan o‘tganda contender haqiqiy PostgreSQL lock’da kutadi, keyin cached javobni oladi; ikkinchi generation boshlanmaydi.
- Browser’da lost committed response, processing retry, reload xatosi, different-key busy, JS-readable `Retry-After`, busy’dan keyingi retry va boshqa generation davomida completed replay.

Real PostgreSQL testlari explicit opt-in bilan test branch’ga ishlaydi; temporary user/chat/lease/idempotency/quota ma’lumotlari tozalanadi. Browser sinovlari vaqtinchalik SQLite database va synthetic agent adapter’dan foydalanadi; tashqi pulli Groq request’lari yuborilmaydi.

## Qolgan xavflar

Exactly-once Groq invocation kafolatlanmaydi: tashqi provider request’i yuborilgandan keyingi crash, process/event-loop stall, network partition yoki lease takeover’da eski tashqi request hali ishlayotgan bo‘lishi mumkin. Bir vaqtning o‘zida faqat bitta DB lease valid bo‘ladi; stale worker natijasi database’ga yozilmaydi.

Database devori soati keskin o‘zgarsa expiry availability’ga ta’sir qiladi. Save transaction’ida allaqachon olingan row lock commit/rollback’gacha ustun turadi; qotib qolgan driver/network uchun server timeouts va operator monitoring’i kerak. Mixed-version rollout, qo‘lda lease row’larini o‘chirish yoki fencing’ni chetlab o‘tuvchi SQL qo‘llab-quvvatlanmaydi.

Durable browser key storage, global/per-user provider concurrency/cost budget, retry attempt budget, completed/abandoned idempotency retention, fresh-install migration va production rollout hali alohida ishlar. Frontend faqat oxirgi pending key’ni saqlaydi; refresh/restart yoki boshqa savol uni yo‘qotishi mumkin.

## Shu bosqichda o‘zgargan fayllar

- `backend/ai/generation.py`
- `backend/api/ai.py`
- `backend/core/idempotency.py`
- `backend/core/leases.py`
- `backend/core/rate_limit.py`
- `backend/db/database.py`
- `backend/db/idempotency_models.py`
- `backend/db/lease_models.py`
- `backend/db/schema_check.py`
- `backend/main.py`
- `frontend/src/app/page.tsx`
- `migrations/env.py`
- `migrations/versions/f9a045983bdf_add_generation_leases_and_recovery.py`
- `tests/test_ai_send.py`
- `tests/test_generation_migration.py`
- `tests/test_generation_postgres.py`
- `tests/test_idempotency_postgres.py`
- `docs/SESSION_LOCKING.md`
- `docs/IDEMPOTENCY.md`
- `docs/PRELAUNCH.md`

Avvaldan mavjud V0.9.4.1 worktree o‘zgarishlari saqlangan. `.env` va backup fayllari publish qilinmaydi.
