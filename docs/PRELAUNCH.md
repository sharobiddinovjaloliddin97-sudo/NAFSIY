# NAFSIY V0.9.4.3 — Pre-launch holati

Holat: development. Ommaviy production deploy bajarilmadi. Quyidagi tekshiruvlar V1.0 launch tasdig‘i hisoblanmaydi.

V0.9.4.3: [AUTH_SECURITY.md](AUTH_SECURITY.md) — endpoint inventory, session-bound CSRF, anonymous login/register CSRF, PostgreSQL auth abuse limits, proxy contract va behavior changes. Test head **`ade01cf7b885`**, parent `f9a045983bdf`; primary migratsiya qilinmadi. **123/123 PASS: 111 unit/regression + 12 haqiqiy PostgreSQL**, skip yo‘q. Frontend lint/build va 3 browser suite PASS. Keyingi proxy/header edge-case sinovlari ham PASS. Old migration SHA-256 va old table sonlari tekshirildi; commit/push yo‘q.

V0.9.3 backup/restore sinovi yakunlangan: [BACKUP_RESTORE.md](BACKUP_RESTORE.md). V0.9.4.1 idempotency, migratsiya va sinov tafsilotlari: [IDEMPOTENCY.md](IDEMPOTENCY.md). Yangi `7bbcad9d4644` migratsiya faqat test branch’da bajarildi; asosiy branch’da bajarilmadi. Ilova startup’da avtomatik DDL bajarmaydi.

V0.9.4.2 algoritm, policy, testlar va o‘zgargan fayllar: [SESSION_LOCKING.md](SESSION_LOCKING.md). Test branch yangi `f9a045983bdf` head’ga o‘tkazildi; production migratsiyasi va deploy bajarilmadi. Eski migration fayllari o‘zgartirilmagan.

Yakuniy V0.9.4.2 run: **92/92 PASS — 83 unit/regression va 9 haqiqiy PostgreSQL testi**, skip yo‘q. Frontend lint/build va 2 browser scenario suite PASS. Barcha test user/chat/lease/idempotency/quota yozuvlari tozalandi. O‘zgargan/new fayllardagi real `.env` maxfiy qiymatlari, Git ignore va old migration hash’lari alohida tekshirildi; commit/push bajarilmadi.

## Joriy AI oqimi

1. Frontend `POST /api/v1/ai/chats/{session_id}/send` ga faqat `{"message":"..."}` yuboradi. Cookie `credentials: "include"`, `Idempotency-Key` va session-bound `X-CSRF-Token` header’lari bilan yuboriladi.
2. Backend JWT/jti, faol user, signed CSRF, explicit Origin va chat ownership’ini tekshiradi. Anonim/expired session `401`, noto‘g‘ri CSRF yoki origin `403`, valid CSRF bilan begona chat `404` oladi.
3. Kalit user/chat bo‘yicha bazada rezerv qilinadi. Bir xil kalit/savolning completed natijasi agent va limitni qayta ishlatmasdan qaytadi; processing yoki boshqa savol uchun qayta ishlatilgan kalit `409` oladi. Yangi rezervatsiyadan keyin `ai_rate_limits` 60 soniyalik fixed window’da 5 ta urinishga ruxsat beradi. Hisoblagich atomik va backend process’lari bo‘yicha umumiy.
4. Per-chat lease, idempotency owner/version, bir marta olinadigan logical-request quota va history bitta qisqa admission transaction’da olinadi. Busy chat `409 + Retry-After` oladi va limit sarflamaydi. Oldin qabul qilingan key recovery/retry’da yana hisoblanmaydi; yangi 6-logical request `429` oladi. Xatoga uchragan accepted key’ning ilk quota sarfi saqlanadi.
5. Oxirgi 12 ta history xabari bazadan olinadi; har biri kontekst uchun 4000 belgigacha qisqartiriladi. Klient `history`, `role` yoki `sources` yubora olmaydi.
6. Groq + retrieval + Citation Guard javobni yaratadi. DB lease TTL 30 soniya, heartbeat har 8 soniya; generation timeout 120 soniya. Har renewal alohida transaction. Tashqi AI kutilayotganda chat transaction/row lock ochiq turmaydi.
7. User va assistant xabarlari, manbalar, chat metadata, 100 xabar chegarasi, completed response va lease release bitta transaction’da saqlanadi. Saqlashdan oldin owner/request/version va DB expiry atomik tekshiriladi. Fencing’dan o‘tmagan eski worker hech qanday xabar saqlamaydi. Xatoda transaction rollback qilinadi; cleanup faqat o‘sha owner/version’ga tegadi, cached response o‘chirilmaydi.
8. Frontend serverdagi chatlarni qayta yuklaydi; `assistant` xabarini o‘zi yozmaydi.

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
- Expired processing key’lar yangi owner/version bilan tiklanadi; completed natijalar o‘zgartirilmaydi. Oldingi V0.9.4.1 worker’larini rollout oldidan drain/stop qilish kerak: mixed-version fencing xavfsiz emas.
- Ref refresh/restart’dan keyin yo‘qoladi; faqat oxirgi pending turn saqlanadi. Completed/abandoned response retention va durable browser storage hali yo‘q. Avtomatik retry bajarilmaydi.
- Quota har accepted logical key uchun bir marta. Recovery’dagi haqiqiy provider chaqiruvlari uchun alohida attempt/cost budget va global concurrency cap hali yo‘q. Legacy processing uchun konservativ backfill bir eski noaniq crash holatida slotni kam hisoblashga olib kelishi mumkin.
- PostgreSQL clock jump, network stall yoki final DB transaction lock’i recovery availability’ni kechiktirishi mumkin. Statement/lock/idle transaction timeouts qo‘shilgan; stale owner nomidan save qilishga ruxsat berilmaydi.
- V0.9.2’dan oldingi xabarlar saqlangan; eski `assistant` yozuvlarining kelib chiqishi retroaktiv tasdiqlanmagan.
- AI limiter fixed-window hisoblanadi; eski AI bucket’lar cleanup’i hali qolgan. Auth limiter esa indexed expiry, opportunistic cleanup va 60 soniyalik maintenance bilan ishlaydi.
- Signed CSRF barcha cookie-authenticated write’larni qamrab oladi; SameSite/Origin defense in depth bo‘lib qoladi. CLI ham token talab qiladi. JWT revocation va XSS himoyasi alohida masalalar.
- Login per-IP va IP+email limitlari global account lockout yaratmaydi; NAT’dagi userlar source limitni bo‘lishadi. Distributed attacks uchun MFA/WAF/risk controls hali yo‘q. Proxy’da ASGI raw peer saqlanishi, Uvicorn automatic proxy headers o‘chirilishi kerak.
- Registration generic `202`, auto-login yo‘q; keyingi explicit login kerak. Eski jti’siz JWT’lar qayta login talab qiladi. Bu user/chat/password data’ni o‘zgartirmaydi.

## Production oldidan qolgan ishlar

- [x] AI xabarlarini faqat server yozishi, klient message-write yo‘lini yopish va history’ni bazadan olish.
- [x] AI uchun foydalanuvchi bo‘yicha bazada saqlanadigan 5 so‘rov/daqiqa limiti.
- [x] Bir xil user/chat/request key uchun DB unique constraint bilan idempotency va atomik completed response.
- [x] Turli kalitlar uchun PostgreSQL generation lease, heartbeat, fencing, safe processing recovery va atomik quota accounting.
- [ ] Durable browser retry, provider retry/cost budget, idempotency retention va eski xabarlar provenance siyosati.
- [x] Login/registration/bootstrap uchun atomic PostgreSQL source va email+source counters, enumeration resistance va auth-rate retention/maintenance.
- [x] Session-bound CSRF barcha authenticated write’lar va logout uchun; anonymous login-CSRF defense, frontend bootstrap/manual refresh va salbiy testlar.
- [ ] Eski AI bucket’lar cleanup’i, global provider concurrency/cost/retry budget va distributed bot/MFA himoyasi.
- [ ] Production frontend/API Origin, Secure cookie va trusted reverse proxy konfiguratsiyasi hamda tashqi security review.
- [ ] Email verification va JWT/session revocation. Hozir logout cookie’ni o‘chiradi; nusxalangan JWT muddati tugaguncha yaroqli qolishi mumkin.
- [x] Alembic baseline, test branch’da idempotency migratsiyasi va alohida bo‘sh database’da backup’dan tiklash sinovi.
- [ ] Asosiy branch migratsiyasini rollout qilish, fresh-install migration va foydalanuvchi ma’lumotlarini to‘liq o‘chirish sinovi.
- [ ] Iqtibosni asl matnga solishtirish, hadis raqamlash standartlari, diniy ekspertiza va o‘zbekcha semantik retrieval.
- [ ] HTTPS, ishonchli reverse proxy IP’lari, `AUTH_COOKIE_SECURE=true`, monitoring va maxfiy ma’lumotlarsiz loglash.
- [ ] Suhbatda ulashilgan Neon/Groq credentials’larini almashtirish va production secrets boshqaruvi.

Manbalar: [PostgreSQL ON CONFLICT va RETURNING](https://www.postgresql.org/docs/current/sql-insert.html), [SQLAlchemy PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html), [OWASP API Security Top 10](https://devguide.owasp.org/en/07-training-education/07-api-top-ten/), [FastAPI HTTPS](https://fastapi.tiangolo.com/deployment/https/). Bu hujjat yakunlangan mustaqil security audit o‘rnini bosmaydi.
