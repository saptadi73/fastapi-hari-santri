# FastAPI Hari Santri 2026

Backend Portal Event untuk pendaftaran Sepeda Sehat dan Jalan Sehat Keluarga MWC NU Tarumajaya. Backend memiliki akun pemesan, paket/order, anggota keluarga, ukuran dan stok kaos per peserta, tiket QR, check-in, pengajuan bazar, konten, dan laporan operasional.

## Batas pembayaran

Gateway dan pembayaran manual tidak dipanggil oleh Portal Event. Semua checkout Hari Santri diarahkan ke Payment Portal.

## Quick start Windows

Buat/aktifkan virtual environment, install requirements, lalu atur `.env` dari `.env.example`. Untuk setup lokal yang diminta, pastikan database `hari_santri` tersedia di `localhost:5432` dan `DATABASE_URL` menunjuk ke database tersebut.

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe scripts\seed_hari_santri_2026.py
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

API memakai prefix `/api/v1`; Swagger tersedia di `/docs`. Migration head saat ini `202609290052`. Seed membuat event draft dengan data nama/tanggal/lokasi yang telah disahkan; harga, paket, kuota dan rute tetap wajib diisi admin/panitia.

## Referensi implementasi

- [Audit implementasi dan batas integrasi](docs/HARI_SANTRI_IMPLEMENTATION_AUDIT.md)
- [TODO backend/frontend dan release gate](docs/HARI_SANTRI_TODO.md)
- [Dokumentasi teknis portal](docs/Dokumentasi_Teknis_Portal_Event_Hari_Santri_2026.md)
- [Naskah konten website](docs/Konten_Web_Portal_Hari_Santri_2026.md)
- Kontrak API aktif tersedia melalui OpenAPI di `/openapi.json` dan Swagger di `/docs`.

## Environment inti

- `DATABASE_URL`: PostgreSQL Event Portal (`hari_santri` untuk lokal)
- `PAYMENT_PORTAL_BASE_URL`, `PAYMENT_PORTAL_CLIENT_ID`, `PAYMENT_PORTAL_CLIENT_SECRET`, `PAYMENT_PORTAL_CALLBACK_SECRET`, `PAYMENT_PORTAL_SERVICE_CODE`, `PAYMENT_PORTAL_RETURN_URL`: server-only; credential didaftarkan operator Payment Portal.
- `FRONTEND_URL`, `PUBLIC_BASE_URL`, `PROJECT_TIMEZONE`: URL dan timezone event.

Jangan commit `.env`, payment secrets, atau private key. Payment Portal client registration, callback allowlist, sandbox verification, status lookup/reconciliation worker, dan production secret provisioning masih merupakan release gate.

Bahasa aktif API: Indonesia (`id`, default) dan English (`en`).