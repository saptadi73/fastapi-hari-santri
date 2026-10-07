# Migrasi Payment Portal Hari Santri

## Kontrak aktif

Portal Event hanya memakai:

- `POST /api/v1/hari-santri/orders/{order_id}/checkout`
- `GET /api/v1/hari-santri/orders/{order_id}/payment-status`
- `POST /api/v1/admin/hari-santri/payments/{reference_id}/reconcile` (admin-triggered server-to-server reconciliation; order reference, not UUID)
- `POST /api/v1/integrations/payment-portal/callback`

Checkout dibuat server-to-server ke `fastapi-bayar`. Browser hanya menerima `payment_url`; browser tidak boleh memilih gateway, mengirim credential payment, atau mengubah status menjadi lunas.

## Legacy yang sudah tidak dipublikasikan

Router `app.modules.payments.routes` tidak lagi di-include pada `app.api.router`. Akibatnya endpoint direct berikut tidak tersedia melalui API utama:

- DOKU Direct VA/QRIS/Direct Debit/Checkout
- Midtrans Checkout dan webhook
- Manual transfer/static QRIS proof
- Offline payment dan konfirmasi manual
- Webhook DOKU/Midtrans lama
- Katalog payment channel legacy, laporan mutation/status provider, dan provider webhook; laporan read-only lama masih dipertahankan sementara untuk migrasi dan rekonsiliasi.

Client lama yang memanggil `POST /orders/{order_id}/continue-payment` menerima `410 Gone` dengan instruksi untuk memakai checkout Hari Santri/Payment Portal; endpoint tersebut tidak lagi memanggil provider apa pun.

Model `Order`, `OrderStatus`, dan helper payment lama masih ada untuk kompatibilitas data/internal code. Jangan menambahkan endpoint baru ke router legacy.

## Checklist rollout

- [ ] Deploy migration wallet/payment yang diperlukan.
- [ ] Daftarkan client dan service pada `fastapi-bayar`.
- [ ] Isi secret Payment Portal pada secret manager backend.
- [ ] Daftarkan HTTPS return URL dan callback URL.
- [ ] Uji checkout, callback signature, callback duplicate, amount/currency/reference mismatch, dan callback terlambat di sandbox.
- [ ] Pastikan `GET /openapi.json` tidak menampilkan route DOKU/Midtrans/manual legacy.
- [ ] Setelah data lama dan laporan dimigrasikan, hapus file adapter, config, scripts, dan dependency provider legacy.
- [x] Tambahkan server-side `GET /api/v1/client/payments/{payment_id}` dan jalur rekonsiliasi admin yang memvalidasi payment/service/reference/event/amount/currency lalu memakai transisi callback idempotent.
- [x] Kunci row order sepanjang checkout untuk serialisasi request lokal; transaksi paralel memakai payment attempt dan idempotency key yang sama, dan payment ID tersimpan tidak dapat ditimpa.
- [x] Tambahkan contract tests untuk OAuth scope/token, create, idempotent replay key, timeout, lookup, callback raw-body signature, replay, dan expiry/late-paid review.

## Preflight and current blockers

Sebelum deploy production, simpan `PAYMENT_PORTAL_BASE_URL`, client ID/secret, callback secret, return URL, `PUBLIC_BASE_URL`, dan `APP_ENV=production` melalui secret store/environment injection backend (bukan frontend atau git). Jalankan `python -m scripts.check_payment_portal_config`; pemeriksaan hanya menampilkan status configured/missing, tidak pernah mencetak nilai credential. Production checkout menolak base/return/public URL non-HTTPS, service code selain `HARI_SANTRI_2026`, dan konfigurasi tanpa callback secret.

Callback URL yang didaftarkan adalah `https://<domain-api>/api/v1/integrations/payment-portal/callback`. Return URL adalah URL Nuxt hasil pembayaran yang ditetapkan operator. Daftarkan client `fastapi-hari-santri`, service `HARI_SANTRI_2026`, scopes `payments:read payments:write`, dan kedua URL HTTPS pada operator `fastapi-bayar`; akses operator dan domain production dibutuhkan untuk menyelesaikan langkah itu. Rekonsiliasi hanya dipicu admin dan status remote tidak pernah dibaca dari query browser. Bila create-payment timeout sebelum ID Portal tersimpan, ulangi checkout dengan idempotency key order yang sama agar respons remote dipulihkan terlebih dahulu.

Cleanup source provider legacy masih ditahan: laporan historis `/admin/reports/payments*` dan `/admin/transactions` tetap dipublikasikan read-only, sementara `users`, `participants`, `tickets`, `email_notifications`, dan `iwbif` masih mengimpor model/order/payment lama. Migrasi data/report dan audit consumer tersebut belum dinyatakan selesai. Jangan hapus adapter, model, dependency, atau konfigurasi lama sampai pemilik sistem memindahkan consumer dan menyetujui verifikasi data/report.

Tahap cleanup awal sudah dilakukan: `.env.example` tidak lagi menawarkan secret/provider
DOKU atau Midtrans, dan `scripts/seed_payment_channels.py` sudah menjadi migration marker
yang berhenti aman. Field config serta adapter provider masih dipertahankan sementara untuk
read-only report dan kompatibilitas data historis; hapus setelah audit consumer selesai.
