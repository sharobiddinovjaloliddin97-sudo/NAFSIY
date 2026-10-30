# NAFSIY V0.9.3 — Backup va restore sinovi

Holat: 6/6 bajarildi. Test branch backup’i alohida bo‘sh restore database’ga xatosiz tiklandi. Jadvallar, yozuvlar soni, Alembic revision va sxema tekshirildi.

## Bajarilgan ishlar

- PostgreSQL 18.6 `pg_dump`, `pg_restore` va `psql` clientlari rasmiy EDB arxividan olindi. Lokal server o‘rnatilmadi yoki ishga tushirilmadi.
- Clientlar `%LOCALAPPDATA%\NAFSIY\tools\postgresql-18\pgsql\bin` ichida. Bu papka foydalanuvchi `PATH` ro‘yxatiga qo‘shildi; mavjud VS Code oynalarini qayta ochish kerak.
- `.gitignore` orqali `backups/`, `*.dump` va `*.backup` yashirildi. `.env` ham Git’dan yashirilgan.
- Backup manbasi `.env` ichidagi `TEST_DATABASE_URL`: direct connection, asosiy `DATABASE_URL` hostidan boshqa test branch.
- Test branch sxemasi SQLAlchemy modellari bilan solishtirildi: farq topilmadi. Shundan keyin faqat test branch’da mavjud sxema `69e1d999085f` baseline revision bilan stamp qilindi.
- `backups/nafsiy-test.dump` custom-format backup yaratildi: 21 262 bayt. `pg_dump` exit code `0`, `pg_restore --list` exit code `0`; barcha 9 ta jadval arxiv ro‘yxatida bor.
- `backups/nafsiy-test.manifest.json` faylida SHA-256, hajm, revision va yozuvlar soni saqlandi. Manifestda connection string yoki parol yo‘q.
- Restore manzili manba va asosiy bazaning host/database juftliklaridan boshqa ekanligi tekshirildi. PostgreSQL 18 target’da restore oldidan public relations, routines, types va qo‘shimcha user schemas bo‘sh edi.
- `pg_restore --no-owner --no-acl --exit-on-error --single-transaction` exit code `0` bilan tugadi. Mavjud obyektlarni o‘chirish ishlatilmadi.
- Tiklangan barcha 9 ta jadvaldagi yozuvlar soni va Alembic revision backup snapshot’iga mos chiqdi. `python -m backend.db.schema_check` sxema modellarga mosligini tasdiqladi; `psql` bilan jadvallar va revision qayta tekshirildi, exit code `0`.
- `backups/nafsiy-test.restore-check.json` natijalar fayli yaratildi, manifestda `restore_verified: true` qayd etildi. Connection ma’lumotlari child process environment orqali uzatildi; bu processlar yakunlandi.

Backup va yozuvlar soni bitta eksport qilingan repeatable-read snapshot’dan olindi. Manba keyinchalik o‘zgarsa ham restore natijasini manifestdagi backup holati bilan solishtirish mumkin. Asosiy `DATABASE_URL` va asosiy bazadagi ma’lumotlar o‘zgartirilmadi.

## Backup snapshot’i

| Jadval | Backup snapshot | Restore |
| --- | ---: | ---: |
| ai_rate_limits | 0 | 0 |
| alembic_version | 1 | 1 |
| auth_users | 1 | 1 |
| ayahs | 0 | 0 |
| chat_messages | 4 | 4 |
| chat_sessions | 3 | 3 |
| surahs | 0 | 0 |
| translations | 0 | 0 |
| user_bookmarks | 0 | 0 |

Alembic revision: `69e1d999085f`.

## Qayta sinov uchun restore muhitini tayyorlash

Joriy `NAFSIY_RESTORE_TARGET` endi tiklangan ma’lumotlarni saqlaydi va bo‘sh emas. Unga ushbu restore’ni takroran bajarmaslik kerak; yangi sinov uchun yana bo‘sh target tayyorlanadi.

Neon Console’da **New Project** orqali `nafsiy-restore-test` nomli alohida loyiha yaratiladi. PostgreSQL versiyasi 18 bo‘lsin. Yangi database bo‘sh qolishi kerak; unga ilova yoki migration ishga tushirilmaydi.

Yangi loyihada **Connect → Connection pooling OFF** tanlanib, direct URL lokal `.env` fayliga quyidagi nom bilan saqlanadi:

```ini
NAFSIY_RESTORE_TARGET=postgresql://USER:PASSWORD@NEW_RESTORE_HOST/neondb?sslmode=require&channel_binding=require
```

Bu namuna; haqiqiy qiymat yangi loyihadan olinadi. URL’ni Git yoki hujjatlarga qo‘shmaslik kerak. Project ID, branch va database nomi Neon Console’da manba bilan solishtiriladi. Faqat ikki URL satrining farq qilishi alohida database ekanini isbotlamaydi.

## Qayta sinov tartibi

1. Restore target manbadan va asosiy bazadan boshqa ekanini hamda database bo‘shligini tekshirish.
2. Dump SHA-256 qiymatini manifest bilan qayta solishtirish.
3. `pg_restore --no-owner --no-acl --exit-on-error --single-transaction` bilan yangi bo‘sh database’ga tiklash. Xatoda tranzaksiya rollback bo‘lishi kerak; `--clean` ishlatilmaydi.
4. 9 ta jadval, barcha jadvallardagi yozuvlar soni va Alembic revision’ni manifest bilan solishtirish.
5. Tiklangan sxemani SQLAlchemy modellari bilan solishtirish.
6. Faqat shu tekshiruvlar o‘tgach checklist va manifestdagi restore holatini yangilash; vaqtinchalik process connection o‘zgaruvchilarini tozalash.

## Checklist

- [x] `pg_dump`, `pg_restore` va `psql` ishladi.
- [x] Alohida bo‘sh restore database yaratildi va tekshirildi.
- [x] Backup fayli xatosiz olindi va arxiv ro‘yxati tekshirildi.
- [x] Restore xatosiz bajarildi.
- [x] 9 ta jadval qayta tiklandi.
- [x] Tiklangan ma’lumotlar soni va Alembic revision solishtirildi.

## Chegaralar

Restore’dan keyin manba test branch’ga `.env` ichidagi `TEST_DATABASE_URL` bilan qayta ulanish `password authentication failed` xatosini qaytardi. Shu sabab manbaning hozirgi sonlari qayta olinmadi. Tiklangan sonlar va revision backup bilan bir snapshot’da olingan manifestga solishtirildi va mos chiqdi. Manba test branch’dan yana foydalanish uchun uning amaldagi connection ma’lumotlari bilan `TEST_DATABASE_URL` yangilanishi kerak.

Bo‘sh baseline migration mavjud sxemani Alembic nazoratiga oladi; u bo‘sh database’ni noldan yaratmaydi. Fresh-install migration strategiyasi alohida ish bo‘lib qoladi.

Bu test branch backup’i; production’dagi keyingi o‘zgarishlarni qamrab olmaydi. Production uchun backup jadvali, saqlash muddati, tashqi himoyalangan nusxa va muntazam restore sinovlari hali belgilanmagan. Faqat dump yaratish muvaffaqiyatli restore isboti emas.

Client va server major versiyasi moslashtirildi: PostgreSQL 16 `pg_dump` PostgreSQL 18 serveridan backup ola olmaydi. [PostgreSQL pg_dump hujjati](https://www.postgresql.org/docs/18/app-pgdump.html). Windows clientlari [rasmiy PostgreSQL Windows sahifasi](https://www.postgresql.org/download/windows/) tavsiya qilgan [EDB binaries](https://www.enterprisedb.com/download-postgresql-binaries) manbasidan olindi.
