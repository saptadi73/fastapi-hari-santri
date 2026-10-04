# Migrasi Payment Portal Hari Santri

## Kontrak aktif

Portal Event hanya memakai:

- `POST /api/v1/hari-santri/orders/{order_id}/checkout`
- `GET /api/v1/hari-santri/orders/{order_id}/payment-status`
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

Tahap cleanup awal sudah dilakukan: `.env.example` tidak lagi menawarkan secret/provider
DOKU atau Midtrans, dan `scripts/seed_payment_channels.py` sudah menjadi migration marker
yang berhenti aman. Field config serta adapter provider masih dipertahankan sementara untuk
read-only report dan kompatibilitas data historis; hapus setelah audit consumer selesai.
