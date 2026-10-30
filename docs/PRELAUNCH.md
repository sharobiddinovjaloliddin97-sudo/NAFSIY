# NAFSIY V0.9.2 — Server-Side AI Chat + Rate Limiting

Holat: development. Ommaviy production deploy bajarilmadi. Quyidagi tekshiruvlar V1.0 launch tasdig‘i hisoblanmaydi.

## Joriy AI oqimi

1. Frontend `POST /api/v1/ai/chats/{session_id}/send` ga faqat `{"message":"..."}` yuboradi. Cookie `credentials: "include"` orqali yuboriladi.
2. Backend JWT, faol foydalanuvchi, origin va chat ownership’ini tekshiradi. Anonim so‘rov `401`, begona chat `404`, begona origin `403` oladi.
3. Neon’dagi `ai_rate_limits` jadvali akkaunt uchun 60 soniyalik fixed window’da 5 ta urinishga ruxsat beradi. `ON CONFLICT DO UPDATE ... WHERE ... RETURNING` hisoblagichni atomik yangilaydi. Limit bitta chat yoki bitta server jarayoniga bog‘liq emas.
4. Limit urinish boshlanishida alohida commit qilinadi. Groq yoki keyingi saqlash xato qilsa ham urinish hisoblanadi. 6-so‘rov `429` va oynaning oxirigacha bo‘lgan `Retry-After` soniyalarini qaytaradi.
5. Oxirgi 12 ta history xabari bazadan olinadi; har biri kontekst uchun 4000 belgigacha qisqartiriladi. Klient `history`, `role` yoki `sources` yubora olmaydi.
6. Groq + retrieval + Citation Guard javobni yaratadi. Tashqi AI so‘rovi kutilayotganda chat yozish tranzaksiyasi ochiq ushlab turilmaydi.
7. User va assistant xabarlari, manbalar, chat sarlavhasi/vaqti hamda oxirgi 100 ta xabarni qoldirish bitta tranzaksiyada bajariladi. Saqlash qismi xato qilsa juftlik rollback bo‘ladi. Saqlash vaqtida chat egasi qayta tekshiriladi.
8. Frontend serverdagi chatlarni qayta yuklaydi; `assistant` xabarini o‘zi yozmaydi.

Quyidagi eski endpointlar olib tashlangan:

- `POST /api/v1/ai/chat`
- `POST /api/v1/user/chats/{chat_id}/messages`

`ai_rate_limits` development’da mavjud `create_all()` bilan yaratiladi. Oldingi jadvallar va tarix o‘chirilmagan. SQLite faqat izolyatsiyalangan testlarda ishlatiladi; asosiy limiter Neon PostgreSQL’da ishlaydi.

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

- Saqlash bosqichidagi chat row lock xabar pozitsiyalarini himoya qiladi. Lekin butun AI generation bitta chat bo‘yicha serializatsiya qilinmagan: parallel so‘rovlar bir xil eski kontekst bilan javob yaratishi mumkin.
- Idempotency key hali yo‘q. Server commit qilganidan keyin tarmoq uzilsa, mijoz xato olgan bo‘lsa ham ikkala xabar saqlangan bo‘lishi mumkin. Frontend savolni inputga qaytaradi va qayta yuborishdan oldin suhbatni yangilashni taklif qiladi. Avtomatik retry bajarilmaydi.
- V0.9.2’dan oldingi xabarlar saqlangan; eski `assistant` yozuvlarining kelib chiqishi retroaktiv tasdiqlanmagan.
- Rate limiter fixed-window hisoblanadi. Eski bucket’lar uchun rejalashtirilgan cleanup hali qo‘shilmagan.
- Origin tekshiruvi to‘liq CSRF himoyasi emas: mavjud helper ruxsat etilgan frontend yoki backendning o‘z origini tekshiradi; Origin headersiz CLI so‘rovlari ham qabul qilinadi.

## Production oldidan qolgan ishlar

- [x] AI xabarlarini faqat server yozishi, klient message-write yo‘lini yopish va history’ni bazadan olish.
- [x] AI uchun foydalanuvchi bo‘yicha bazada saqlanadigan 5 so‘rov/daqiqa limiti.
- [ ] Session-level generation locking yoki idempotency; eski xabarlar provenance siyosati.
- [ ] Eski rate bucket’lar cleanup’i, bir vaqtdagi AI so‘rovlar chegarasi va xarajat kvotalari; login uchun brute-force himoyasi.
- [ ] To‘liq CSRF strategiyasi va salbiy testlar; production origin/cookie konfiguratsiyasi.
- [ ] Email verification va JWT/session revocation. Hozir logout cookie’ni o‘chiradi; nusxalangan JWT muddati tugaguncha yaroqli qolishi mumkin.
- [ ] Alembic migration, backup’dan tiklash va foydalanuvchi ma’lumotlarini to‘liq o‘chirish sinovi.
- [ ] Iqtibosni asl matnga solishtirish, hadis raqamlash standartlari, diniy ekspertiza va o‘zbekcha semantik retrieval.
- [ ] HTTPS, ishonchli reverse proxy IP’lari, `AUTH_COOKIE_SECURE=true`, monitoring va maxfiy ma’lumotlarsiz loglash.
- [ ] Suhbatda ulashilgan Neon/Groq credentials’larini almashtirish va production secrets boshqaruvi.

Manbalar: [PostgreSQL ON CONFLICT va RETURNING](https://www.postgresql.org/docs/current/sql-insert.html), [SQLAlchemy PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html), [OWASP API Security Top 10](https://devguide.owasp.org/en/07-training-education/07-api-top-ten/), [FastAPI HTTPS](https://fastapi.tiangolo.com/deployment/https/). Bu hujjat yakunlangan mustaqil security audit o‘rnini bosmaydi.
