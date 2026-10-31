# Current authentication additions ? 2026-10-31

Repository head `e6f1027a93bd` adds email verification, expiring hashed single-use recovery tokens and account-wide session generation revocation. Logout increments `auth_version`, invalidating copied JWTs and all other sessions; reset additionally removes outstanding recovery links. Verification is required by staging validation and optional in development. Existing accounts are unverified after migration; there is no automatic verification backfill. New JWTs carry the generation authenticated by the password check, including when reset races cookie issuance. Legacy jti-bearing tokens imply generation zero and cease working after revocation. CSRF bootstrap also rejects revoked/unverified sessions.

All new recovery POST endpoints require signed anonymous CSRF and trusted Origin. Request responses are generic, with IP/pair limits; confirmation is IP-limited. Verification links expire after 60 minutes, reset links after 30. Only SHA-256 token digests are stored; concurrent consumption is atomic on PostgreSQL. Raw links stay in email/in-memory test adapters, never application logs. Development delivery defaults to discard; production SMTP is not configured. Expired tokens are removed in bounded batches. Email timing is not constant-time; delivery is synchronous with a ten-second SMTP timeout and failure event. Logout is account-wide, including other devices; a concurrent reset/logout can make an in-flight login cookie immediately invalid and the user must log in again.

UI links use fragments, clear them after import (including later links in the same tab), require an explicit action and do not persist passwords/tokens. Account deletion remains disabled; see [ERASURE_REPLAY.md](ERASURE_REPLAY.md). Current tests and remaining gates are in [STAGING_READINESS.md](STAGING_READINESS.md). Earlier stateless-logout descriptions below are historical and superseded by this addition. No primary migration or live provider email was performed.

# NAFSIY V0.9.4.3 — CSRF & Authentication Security

Holat: implementatsiya va test branch sinovlari bajarildi. Yangi head `ade01cf7b885`, parent `f9a045983bdf`. Primary database migratsiya qilinmadi; deploy, commit yoki push bajarilmadi.

## Endpoint inventory

| Method va path | Himoya |
| --- | --- |
| POST `/api/v1/auth/login` | Signed anonymous pre-session CSRF + Origin + auth limits |
| POST `/api/v1/auth/register` | Signed anonymous pre-session CSRF + Origin + registration limits |
| POST `/api/v1/auth/logout` | JWT + session-bound CSRF + Origin |
| POST `/api/v1/user/chats` | JWT + session-bound CSRF + Origin |
| DELETE `/api/v1/user/chats/{chat_id}` | JWT + session-bound CSRF + Origin + ownership |
| POST `/api/v1/user/bookmarks` | JWT + session-bound CSRF + Origin |
| DELETE `/api/v1/user/bookmarks/{bookmark_id}` | JWT + session-bound CSRF + Origin + ownership |
| POST `/api/v1/ai/chats/{session_id}/send` | JWT + session-bound CSRF + Origin + ownership + Idempotency-Key |

`get_current_user()` unsafe method’larda CSRF tekshiradi. Shuning uchun logout va barcha hozirgi cookie-authenticated write’lar markaziy tekshiruvdan o‘tadi. Router-level Origin tekshiruvlari ham saqlangan. Read-only endpointlar CSRF header talab qilmaydi. Quran cache kabi umumiy public source olish user-account write hisoblanmaydi.

## Token design

- Har yangi login fresh 128-bit random `jti` bilan JWT yaratadi; JWT HttpOnly cookie’da qoladi. Signature, issuer, audience, iat, expiry, user va jti tekshiriladi.
- CSRF token `v1.issued.expiry.nonce.signature` formatida, random 256-bit nonce va HMAC-SHA256 signature bilan ishlaydi. Kalitlar JWT signing secret’dan purpose bo‘yicha alohida HMAC kalit sifatida olinadi.
- Authenticated CSRF token actual JWT cookie’ning barcha baytlariga HMAC orqali bog‘langan. U boshqa user yoki boshqa login session’i bilan ishlamaydi, hatto loginlar bir soniyada bo‘lsa ham.
- Anonymous login/register tokeni alohida signed, random HttpOnly `nafsiy_csrf_context` cookie’ga bog‘langan. Authenticated token va anonymous token purpose’lari almashmaydi. Login va logout pre-session context’ni tozalaydi.
- CSRF lifetime 15 daqiqa, auth token JWT expiry’dan oshmaydi; anonymous context lifetime 30 daqiqa. Format/expiry/signature xatosi bir xil `403 {"detail":"CSRF validation failed"}` beradi. Yaroqsiz JWT esa `401`; untrusted Origin ham `403` oladi.
- Cookies host-only, HttpOnly, SameSite=Lax; Secure flag `AUTH_COOKIE_SECURE`ga bog‘liq. Lax mustaqil CSRF himoyasi deb qabul qilinmaydi. Production HTTPS’da Secure=true bo‘lishi shart.

`GET /api/v1/auth/csrf` uchun `X-CSRF-Request: 1` kerak. Endpoint Origin va Fetch Metadata’ni tekshiradi; cross-site/simple navigatsiya bootstrap bo‘la olmaydi. CORS faqat mavjud explicit localhost frontend originlariga credentials/custom headers bilan ruxsat beradi. `null`, untrusted yoki duplicate Origin rad etiladi. Origin yo‘q CLI client ham signed CSRF token va cookie bilan ishlashi kerak; CSRF bypass yo‘q.

Bootstrap `csrf_token`, `auth_csrf_token`, `expires_at` va opaque HMAC `session_scope` qaytaradi. Scope autentifikatsiya credential’i emas; frontend session almashganini aniqlash uchun ishlatiladi. JWT, JWT secret yoki API key response’ga chiqarilmaydi. Javob `Cache-Control: no-store, private`, `Vary: Origin, Cookie` bilan qaytadi. Invalid/expired session cookie bo‘lsa bootstrap anonymous token berishi mumkin; authenticated write’ga ruxsat bermaydi.

## Frontend flow

`frontend/src/lib/csrf.ts` bootstrap’ni credentials bilan oladi, parallel fetch’larni birlashtiradi va tokenni faqat memory’da saqlaydi. Expiry yaqinlashganda xavfsiz GET refresh qilinadi. AuthPanel, userData API va AI send bir xil helper’dan foydalanadi; credentials va Idempotency-Key saqlangan.

Server CSRF `403` qaytarsa cache invalid qilinadi, foydalanuvchiga qayta yuborish aytiladi. **Write avtomatik takrorlanmaydi.** Shu session’da AI retry eski idempotency key’ni saqlaydi. Session scope o‘zgarsa eski write yuborilishidan oldin to‘xtatiladi, auth/chat/bookmark holati yangilanadi. JWT expiry `401` yoki authenticated bootstrap token yo‘qligi bilan login holatiga qaytaradi.

Login/register bootstrap anonymous token’dan, logout va qolgan write’lar session token’dan foydalanadi. Login/logout va BroadcastChannel orqali auth o‘zgarishida token cache tozalanadi. JWT yoki CSRF token localStorage’ga yozilmaydi; URL/query param orqali yuborilmaydi.

## Auth rate policy

| Scope | Limit | Window | Hisoblash |
| --- | ---: | ---: | --- |
| Login source IP/network | 30 | 5 daqiqa | Hamma admitted attempt; boshqa emailga o‘tish limitni reset qilmaydi |
| Login normalized email + source IP/network | 5 | 5 daqiqa | Failed va in-flight verification; success aynan o‘z slotini qaytaradi |
| Registration IP/network | 5 | 1 soat | Yangi va mavjud email uchun bir xil |
| Registration email + IP/network | 3 | 1 soat | Duplicate va concurrent registration ham hisoblanadi |
| CSRF bootstrap IP/network | 60 | 1 daqiqa | Anonymous/authenticated bootstrap uchun |

PostgreSQL conditional upsert va primary key counters’ni process’lar bo‘yicha atomik qiladi. IP slot commit qilingach pair slot olinadi; pair rad etilgan request ham IP slot sarflaydi. Pair slot verification’dan oldin rezerv qilinadi, shu sabab parallel request’lar 5 ta verification chegarasidan o‘tolmaydi. Crash paytidagi slot konservativ failure sifatida window oxirigacha qolishi mumkin.

Global email-only account lockout yo‘q. Boshqa IP’dan hujum qilish legitimate user’ni hamma joyda qulflamaydi. Bir NAT/IP’dagi userlar source budget’ni bo‘lishadi; ayni IP+email 5 xatodan keyin window tugaguncha correct password bilan ham `429` olishi mumkin. Bu vaqtinchalik va boshqa source’ga tarqalmaydi; account `is_active` o‘zgartirilmaydi. Distributed botlar uchun MFA/CAPTCHA/WAF yoki qo‘shimcha risk signals hali kerak.

Rate keys — purpose-separated HMAC digest’lar. Raw email/IP, parol, JWT yoki user conversation bu jadvalda yo‘q. IPv4 canonical address va IPv6 `/64` group ishlatiladi. SQL/API xatolarida connection string yoki request/token qiymatlari log qilinmaydi; errors generic `401/429/503`, rate rejection `Retry-After` bilan keladi.

## Retention

`auth_rate_limits.expires_at` indexed. Entry window oxiri + 10 daqiqada cleanup uchun eligible bo‘ladi: eng uzun scope uchun 70 daqiqagacha. Har admission ko‘pi bilan 500 expired key’ni o‘chiradi. Lifespan maintenance har 60 soniyada, ko‘pi bilan 10 ta 500-row batch bilan faqat expired auth-limit yozuvlarini tozalaydi. User, chat, message va AI idempotency ma’lumotlariga tegmaydi.

Normal healthy service’da removal odatda keyingi cleanup siklida bo‘ladi. App to‘xtashi, DB outage yoki katta backlog cleanup’ni kechiktirishi mumkin; production’da backlog/cleanup health monitoring va zarur bo‘lsa tashqi scheduled maintenance kerak. Fixed-window limitlar chegarada burst qilishga ruxsat berishi mumkin.

## Trusted reverse proxy contract

ASGI `scope.client` **asl transport peer** bo‘lishi kerak. `run-dev.ps1` endi `--no-proxy-headers` ishlatadi. Manual/production start ham quyidagicha bo‘lishi kerak:

```powershell
python -m uvicorn backend.main:app --no-proxy-headers
```

Uvicorn/gunicorn/platform oldindan client IP’ni o‘zboshimchalik bilan rewrite qilmasligi kerak: application asl peer’ni qayta tiklay olmaydi. `.env.example`da `TRUSTED_PROXY_CIDRS=[]` default — hech kimga ishonilmaydi. Faqat haqiqiy proxy IP/CIDR’larini aniq whitelist qiling; `/0` konfiguratsiyasi rad etiladi. Proxy attacker bergan header’ni tozalashi yoki haqiqiy peer’ni append qilishi kerak.

Trusted peer’dan kelgan X-Forwarded-For chain o‘ngdan chapga, birinchi untrusted source’gacha ko‘riladi. Duplicate XFF header’lar tartib bo‘yicha birlashtiriladi; attacker qo‘shgan leftmost hop yoki garbage real client o‘rnini bosa olmaydi. Trusted suffix buzilgan yoki chain chegaradan oshgan bo‘lsa transport peer’ga fallback qilinadi. Untrusted peer’dan kelgan XFF, X-Real-IP va Forwarded source’ni almashtirmaydi. Proxy spoofing va IPv6 canonicalization testlari mavjud.

`AUTH_TRUSTED_ORIGINS` explicit backend/frontend Origin guard ro‘yxati. CORS frontend allowlist alohida explicit localhost originlari bilan qolgan. Production domain’lari, TLS, proxy CIDR va cookie flag deployment oldidan aniq sozlanishi kerak; wildcard qo‘shilmagan.

## Authentication behavior changes

1. Registration endi hamma valid new/existing email uchun **generic `202`** qaytaradi. ID/email yoki auth cookie berilmaydi, password overwrite qilinmaydi. UI keyingi explicit login’ni so‘raydi.
2. Registration har ikki holatda Argon2 hash va bir xil `INSERT ... ON CONFLICT DO NOTHING` yo‘lidan o‘tadi. Wrong-password login uchun mavjud/nonexistent/inactive account generic `401` oladi; dummy Argon2 verification saqlangan. Unknown hash format ham generic unauthorized bo‘ladi. Network/DB timing uchun absolyut constant-time kafolati berilmaydi.
3. Eski `jti` yo‘q JWT’lar `401` oladi — upgrade’dan keyin qayta login kerak. User/password/chat ma’lumotlari o‘zgartirilmagan.
4. Logout endi valid session va CSRF talab qiladi. Stateless JWT server-side revocation hali yo‘q: cookie o‘chiriladi, oldindan ko‘chirilgan JWT expiry’gacha yaroqli bo‘lishi mumkin.
5. CLI va eski API client’lar cookie jar bilan bootstrap olishi va tegishli `X-CSRF-Token` yuborishi kerak. Login/register uchun anonymous token, authenticated write uchun session token ishlatiladi. AI uchun Idempotency-Key hamon majburiy.

## Migration va natijalar

```powershell
.\.venv\Scripts\python.exe -m alembic -x database=test upgrade head
.\.venv\Scripts\python.exe -m alembic -x database=test current
```

`ade01cf7b885` faqat `auth_rate_limits` table/check/index yaratadi, parent `f9a045983bdf`. Test target primary’dan boshqa ekanligi tekshirildi. Schema check PASS. Old migration fayllari SHA-256 va old public table aggregate’lari tekshirildi; old user/chat/message data saqlangan. Eski worker’lar CSRF bilmaydi, mixed-version rollout qo‘llab-quvvatlanmaydi; old worker’larni avval drain/stop qilish kerak. Primary branch bu vazifada yangilanmagan; unga qaragan yangi auth flow yangi jadval bo‘lmaguncha `503` oladi.

Yakuniy to‘liq run: **123/123 PASS — 111 unit/regression + 12 real PostgreSQL, skip yo‘q (`282.458s`)**. Proxy/header chegaraviy o‘zgarishlaridan keyin auth security’ning 28 testi va 2 cross-origin testi qayta PASS. Native PostgreSQL retention tekshiruvi ham o‘tdi: faqat expired fixture o‘chirildi, live fixture saqlandi, tashqi transaction rollback qilindi. Frontend lint/build PASS; 3 Edge/Playwright suite PASS.

- Eski AI/RAG/citation/idempotency/lease/crash-recovery/auth/user-data testlari saqlangan.
- CSRF missing/invalid/expired, login-CSRF, user/session separation, logout, bookmark, AI+idempotency, Origin/CORS, JWT expiry/legacy session, HMAC keys/TTL cleanup, proxy spoofing va account enumeration testlari.
- 4-process failed login sinovida har bir known/unknown pair’ning 8 attempt’idan 5 tasi verification’ga yetdi, 3 tasi `429`; responses bir xil. Boshqa source’dan correct login ishladi.
- 4-process registration’da 8 turli email uchun 5 accepted/3 limited; bir xil email uchun 3 accepted/5 limited va bitta user yozuvi.
- Browser’da generic registration→explicit login, HttpOnly cookies, real bookmark button POST/DELETE, CSRF `403`dan keyin **manual** retry+same idempotency key, lost response, untrusted bootstrap, external session swap old write’ni to‘xtatishi, JWT expiry va protected logout tekshirildi.

Browserlar vaqtinchalik SQLite va synthetic agent bilan ishladi; tashqi pulli Groq chaqiruvlari yuborilmadi. Real Neon test fixtures o‘ziga tegishli user/chat/lease/idempotency/quota/HMAC bucket yozuvlarini tozalaydi. Test-only endpoints repository backend’ga qo‘shilmagan. Auth maintenance test browser’da ham faqat temporary engine’ga bog‘landi.

## Qolgan cheklovlar

JWT revocation, email verification, MFA/password-reset oqimi, distributed credential stuffing/bot protection va security monitoring alohida qolmoqda. NAT’dagi userlar source limitni bo‘lishadi. XSS bo‘lsa CSRF himoyasi browserdagi hujumni to‘xtata olmaydi. TLS/CORS/proxy noto‘g‘ri sozlansa deployment xavfsizligi buziladi. Root signing key rotation CSRF/session/limiter key namespace’larini ham almashtiradi; eski HMAC row’lar expiry cleanup bilan yo‘qoladi.

V0.9.4.2’dagi Groq exactly-once cheklovi, durable browser idempotency storage, provider retry/cost budget va fresh-install migration hali o‘z kuchida. Ushbu testlar mustaqil pentest yoki production launch tasdig‘i emas.

## Shu bosqichdagi fayllar

- `.env.example`
- `backend/api/auth.py`
- `backend/core/auth_tokens.py`
- `backend/core/csrf.py`
- `backend/core/request_security.py`
- `backend/core/auth_rate_limit.py`
- `backend/core/auth_maintenance.py`
- `backend/core/config.py`
- `backend/db/auth_security_models.py`
- `backend/db/schema_check.py`
- `backend/main.py`
- `migrations/env.py`
- `migrations/versions/ade01cf7b885_add_authentication_rate_limits.py`
- `run-dev.ps1`
- `frontend/src/lib/csrf.ts`
- `frontend/src/lib/userData.ts`
- `frontend/src/components/AuthPanel.tsx`
- `frontend/src/app/page.tsx`
- `tests/security_client.py`
- `tests/test_auth.py`
- `tests/test_auth_security.py`
- `tests/test_auth_security_postgres.py`
- `tests/test_ai_send.py`
- `tests/test_user_data.py`
- `tests/test_idempotency_postgres.py`
- `tests/test_generation_postgres.py`
- `docs/AUTH_SECURITY.md`
- `docs/PRELAUNCH.md`
- `docs/SESSION_LOCKING.md`
- `docs/IDEMPOTENCY.md`

Avvalgi uncommitted V0.9.4.1/V0.9.4.2 o‘zgarishlar saqlangan. `.env`, frontend `.env.local` va backups ignored/untracked ekanligi tekshirildi. Changed/new fayllarda haqiqiy configured API/JWT/database secret qiymatlari bo‘yicha **0 match** topildi. Eski migration SHA-256 va old table sonlari saqlangan; test auth counters tozalangan. Connection strings va user conversations hisobotga chiqarilmadi; commit/push bajarilmagan. Bu tekshiruv Git tarixining to‘liq secret auditi emas.

Manbalar: [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html).
