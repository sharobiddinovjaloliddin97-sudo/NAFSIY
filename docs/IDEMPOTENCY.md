# NAFSIY V0.9.4.1 — AI Idempotency Protection

Holat: implementatsiya va test branch sinovlari bajarildi. Asosiy database’ga migratsiya qo‘llanmadi; production deploy bajarilmadi.

V0.9.4.3 auth update: AI send’da `Idempotency-Key` bilan birga session-bound `X-CSRF-Token` ham kerak. CSRF `403` manual retry’da idempotency key saqlanadi. Login/register va boshqa write’lar: [AUTH_SECURITY.md](AUTH_SECURITY.md). Joriy test head `ade01cf7b885`; quyidagi eski revision ma’lumotlari tarixiy.

Bu hujjat V0.9.4.1 tarixiy holatini tasvirlaydi. V0.9.4.2’da session lease, fencing, crash recovery va atomik logical-request quota qo‘shildi: [SESSION_LOCKING.md](SESSION_LOCKING.md). Joriy test head `f9a045983bdf`. Failed processing yozuvlar endi o‘chirilmaydi; owner lease’i release qilinadi va bir xil key qayta quota sarflamasdan tiklanadi.

## API oqimi

`POST /api/v1/ai/chats/{session_id}/send` hali ham faqat `{"message":"..."}` oladi. Endi `Idempotency-Key` UUID header’i majburiy. Cookie, JWT, origin, chat ownership, server history, RAG, Citation Guard va 5 urinish/daqiqa limiti saqlangan. CORS `Idempotency-Key` header’iga ruxsat beradi.

Ownership tekshiruvidan keyin `(user_id, session_id, request_key)` unique constraint va PostgreSQL `INSERT ... ON CONFLICT DO NOTHING ... RETURNING` bilan kalit rezerv qilinadi. Rezervatsiya agent chaqirilishidan oldin alohida commit qilinadi. UUID bir xil canonical formatga keltiriladi; SHA-256 fingerprint API tomonidan chetlari tozalangan savoldan hisoblanadi.

| Holat | Natija | Yangi agent chaqiruvi / limit |
| --- | --- | --- |
| Yangi kalit | `processing` yozuvi, limit va generation | Ha, limit ruxsat bersa |
| Bir xil kalit/savol, hali `processing` | `409 Request is already processing` | Yo‘q |
| Bir xil kalit/savol, `completed` | Saqlangan `answer`, `sources`, `model` | Yo‘q |
| Shu kalit, boshqa savol | `409` | Yo‘q |
| Header yo‘q yoki UUID noto‘g‘ri | `422` | Yo‘q |
| Begona chat / login yo‘q | `404` / `401` | Yo‘q |

`completed` holati va saqlangan response user/assistant xabarlari, chat metadata’si va 100 ta xabar chegarasi bilan bitta transaction’da yoziladi. Completion yozish xatosi ikkala chat xabarini ham rollback qiladi. Chat yoki user o‘chirilsa idempotency yozuvlari FK `ON DELETE CASCADE` bilan o‘chadi.

Tutib olingan generation, validatsiya, limit yoki saqlash xatosida faqat `processing` rezervatsiya o‘chiriladi. Oldin commit qilingan `completed` natija cleanup’da o‘chmaydi. Shu sabab response commit’dan keyin yo‘qolsa ham o‘sha kalit bilan retry saqlangan javobni qaytaradi. Haqiqiy muvaffaqiyatsiz urinishdan keyingi retry agentni yana ishlatishi va yana limit sarflashi mumkin. Xatolar saqlangan muvaffaqiyatli javob sifatida replay qilinmaydi.

## Frontend

`pendingRequestRef` oxirgi session ID, savol va UUID’ni saqlaydi. Xuddi shu savol/chat uchun tarmoq xatosidan yoki `409 processing`dan keyingi retry o‘sha UUID’dan foydalanadi. Muvaffaqiyatli javob va muvaffaqiyatli chat reload’dan keyin ref tozalanadi. Reload xatosida kalit saqlanadi; auth o‘zgarganda tozalanadi. Tez ketma-ket yuborish ref orqali ham bloklanadi. Avtomatik retry yo‘q.

## Migratsiya va test muhit

- Baseline: `69e1d999085f`.
- Yangi head: `7bbcad9d4644` — `add ai idempotency`.
- Test branch’da upgrade va schema check o‘tdi; mavjud application jadvallaridagi yozuvlar soni o‘zgarmadi.
- `backend/main.py` avtomatik `Base.metadata.create_all()` ishlatmaydi. Sxema Alembic bilan boshqariladi.
- `backend/db/schema_check.py` yangi modelni tekshiradi va farq bo‘lsa nonzero exit code qaytaradi.

`.env` ichidagi `TEST_DATABASE_URL` manziliga ishlaydigan buyruqlar:

```powershell
.\.venv\Scripts\python.exe -m alembic -x database=test upgrade head
.\.venv\Scripts\python.exe -m alembic -x database=test current
```

Test selector direct connection talab qiladi va test host/database juftligi asosiy `DATABASE_URL` bilan bir xil bo‘lsa SQL bajarishdan oldin to‘xtaydi. Project/branch Neon Console’da ham tekshiriladi. Oddiy `alembic upgrade head` esa `DATABASE_URL`ga ishlaydi; uni ushbu bosqichda asosiy branch uchun ishlatish ruxsat etilmagan.

Schema check’ni faqat shu child process’da test database’ga yo‘naltirish:

```powershell
@'
import os
import runpy
from dotenv import dotenv_values
os.environ["DATABASE_URL"] = dotenv_values(".env")["TEST_DATABASE_URL"].replace(
    "postgresql://", "postgresql+psycopg://", 1
)
runpy.run_module("backend.db.schema_check", run_name="__main__")
'@ | & .\.venv\Scripts\python.exe -
```

Ilova `DATABASE_URL` ko‘rsatgan database bilan ishlaydi. Asosiy branch’da yangi jadval hali yo‘q bo‘lsa, unga qaragan yangi AI endpoint `503` qaytaradi. Test server uchun alohida terminal process’ida `DATABASE_URL` test connection’ga yo‘naltirilishi kerak; `.env`dagi asosiy qiymat avtomatik almashtirilmagan. Asosiy branch rollout’i alohida keyingi qadam.

## Tekshiruvlar

- `python -m unittest discover -s tests -v`: 67 ta test o‘tdi; real PostgreSQL testi standart rejimda opt-in sababli skip qilinadi.
- Real Neon testi alohida bajarildi: 4 process’dan 8 bir xil so‘rov, bitta agent chaqiruvi, bitta limit sarfi, bitta user/assistant juftligi va bitta `completed` yozuvi. Keyingi replay `200`, o‘zgargan savol `409`; ikkisi ham agentni chaqirmadi. Vaqtinchalik user/chat/idempotency/limit yozuvlari tozalandi.
- Unit testlar missing/invalid UUID, canonical UUID replay, boshqa savol, processing parallel retry, to‘lgan limitda replay, xatodan keyingi retry, haqiqiy asyncio cancellation, completion xatosida rollback, commit’dan keyingi response xatosi, user/chat scope, chat cascade, CORS va startup DDL yo‘qligini tekshiradi.
- Migration upgrade/downgrade izolyatsiyalangan SQLite sxemasida tekshirildi; mavjud jadvallar saqlanadi va upgrade sxemasi modellarga mos.
- Frontend `npm run lint` va `npm run build` o‘tdi.
- Edge/Playwright’da haqiqiy frontend va backend handlerlari vaqtinchalik SQLite bazada tekshirildi: commit’dan keyin uzilgan response, processing paytidagi uzilish va `409`, chat reload xatosi, keyin retry. 4 unique turn uchun 4 agent chaqiruvi, 4 limit sarfi va 8 xabar. Frontend body’da faqat `message` yuborildi. Bu sinovlarda agent test adapteri bilan almashtirildi; pulli tashqi AI so‘rovlari qilinmadi.

PostgreSQL testini takrorlash faqat migratsiya qilingan test branch uchun:

```powershell
$env:NAFSIY_RUN_POSTGRES_TESTS = "1"
try {
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_idempotency_postgres.py -v
} finally {
    Remove-Item Env:\NAFSIY_RUN_POSTGRES_TESTS
}
```

## V0.9.4.2 uchun qolgan chegaralar

- Bitta chatga turli kalitlar bilan kelgan parallel generation hali serializatsiya qilinmaydi.
- Server qulaganda, DB cleanup ishlamaganda yoki cancellation rezervatsiya commit’i atrofida yuz berganda qolgan `processing` yozuvlari uchun lease/recovery yo‘q. Bunday holatda avtomatik o‘chirib qayta generation boshlanmaydi; bir xil kalit `409` olishi mumkin.
- Tarmoq, request cancellation va thread’da davom etayotgan DB commit atrofidagi barcha race holatlari yopildi deb da’vo qilinmaydi.
- Ref sahifa yangilanganda/browser qayta ochilganda yo‘qoladi. Faqat oxirgi pending turn kaliti saqlanadi; boshqa savol yuborish ham eski pending kalitni almashtiradi.
- Completed response’lar uchun TTL/cleanup yo‘q. Ular message history trim qilinganidan keyin ham chat/user o‘chirilguncha saqlanadi.
- Baseline bo‘sh database’ni noldan yaratmaydi. Eski backup `69e1d999085f` holatidagi 9 ta jadvalni saqlaydi; yangi idempotency jadvali uning ichiga retroaktiv qo‘shilmagan.

Mexanizm: [PostgreSQL INSERT / ON CONFLICT](https://www.postgresql.org/docs/18/sql-insert.html), [SQLAlchemy PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert).
