# Wallet Peserta dan Voucher QR Hari Santri 2026

Status: backend dan frontend Nuxt inti selesai; UAT perangkat, auth integration, dan deployment masih perlu diselesaikan. Operasional tidak meminta admin/user mengetik UUID.

Frontend tersedia di sibling project `../nuxt-hari-santri/` dengan tiga area: peserta (QR/saldo/riwayat), scanner tenant (kamera/input manual, nominal, password peserta, saldo/riwayat), dan admin (voucher, adjustment, revoke, settlement).

Frontend memakai token `hs_access_token` dari local storage dan `VITE_API_BASE_URL` (default `/api/v1`). Pada production, gunakan HTTPS dan kebijakan auth portal yang berlaku.

## Konsep bisnis

Setiap peserta pada order `PAID` dapat memiliki beberapa kartu voucher belanja. Kartu ini berbeda dari tiket QR peserta: QR peserta dipakai untuk identitas/check-in, sedangkan QR voucher dipakai exhibitor untuk memindahkan saldo belanja. Voucher boleh bernilai Rp0 saat diterbitkan.

Saldo peserta disimpan pada `participant_wallets`. Kartu voucher menyimpan hash token QR pada `participant_vouchers`; token mentah tidak disimpan di database. Saldo exhibitor disimpan pada `exhibitor_wallets` dan terhubung ke `bazaar_applications` berstatus `approved`.

## Alur operasional

1. Peserta menyelesaikan pembayaran dan order berstatus `PAID`.
2. Admin membuat kartu voucher untuk setiap peserta dan mengisi saldo awal.
3. Peserta melihat QR peserta dan QR voucher pada dashboard.
4. Exhibitor login; petugas memilih salah satu lapak milik akun, memasukkan nominal, lalu memindai QR voucher peserta. Semua tenant yang tidak berstatus `rejected` dapat menerima voucher.
5. Backend mengunci wallet peserta dan wallet exhibitor dalam transaksi database.
6. Jika saldo cukup, saldo peserta dikurangi dan saldo exhibitor ditambah. Pembelian dapat memakai beberapa voucher dengan beberapa transaksi scan.
7. Transfer dicatat di `wallet_transfers`.

Nominal menggunakan integer rupiah. Saldo tidak boleh negatif. `request_id` wajib unik agar retry scan tidak membuat debit ganda.

## Endpoint aktif

Semua endpoint menggunakan prefix `/api/v1`.

| Endpoint | Akses | Fungsi |
|---|---|---|
| `POST /admin/hari-santri/vouchers` | admin/organizer | Membuat kartu voucher dan saldo awal peserta |
| `GET /admin/hari-santri/voucher-participants` | admin/organizer | Peserta order `PAID` yang belum memiliki voucher untuk dropdown penerbitan |
| `GET /admin/hari-santri/vouchers` | admin/organizer | Melihat voucher aktif/revoked |
| `POST /admin/hari-santri/vouchers/{voucher_id}/revoke` | admin/organizer | Menonaktifkan voucher dengan saldo nol |
| `POST /admin/hari-santri/vouchers/{voucher_id}/adjust` | admin/organizer | Menambah atau mengurangi saldo voucher dengan alasan |
| `GET /hari-santri/me/wallet` | peserta/pemesan | Melihat saldo, data kartu, dan QR voucher |
| `GET /hari-santri/me/wallets` | peserta/pemesan | Melihat semua kartu voucher peserta dalam akun/order |
| `GET /hari-santri/me/wallet/transfers` | peserta/pemesan | Melihat riwayat pemakaian voucher |
| `POST /hari-santri/exhibitors/{exhibitor_id}/wallet/charge` | pemilik exhibitor/admin | Memindahkan saldo dari peserta ke exhibitor |
| `POST /hari-santri/me/exhibitor/wallet/charge` | pemilik exhibitor | Memindahkan saldo menggunakan lapak approved akun aktif |
| `GET /hari-santri/me/exhibitor/wallet` | pemilik exhibitor | Melihat saldo lapak akun aktif |
| `GET /hari-santri/me/exhibitor/wallet/transfers` | pemilik exhibitor | Melihat riwayat lapak akun aktif |
| `GET /hari-santri/me/exhibitors` | pemilik exhibitor | Memilih salah satu dari beberapa lapak akun |
| `POST /hari-santri/me/exhibitors/{exhibitor_id}/wallet/charge` | pemilik exhibitor | Charge pada lapak yang dipilih dari daftar akun |
| `GET /hari-santri/me/exhibitors/{exhibitor_id}/wallet/transfers?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` | pemilik exhibitor | Riwayat transaksi dengan filter periode |
| `POST /hari-santri/me/exhibitors/{exhibitor_id}/settlements` | pemilik exhibitor | Mengajukan settlement seluruh saldo tersedia |
| `POST /hari-santri/me/exhibitor/settlements/{settlement_id}/confirm` | pemilik exhibitor | Mengonfirmasi pembayaran settlement yang diterima |
| `GET /hari-santri/exhibitors/{exhibitor_id}/wallet` | pemilik exhibitor/admin | Melihat saldo exhibitor |
| `GET /hari-santri/exhibitors/{exhibitor_id}/wallet/transfers` | pemilik exhibitor/admin | Melihat riwayat kredit exhibitor |
| `GET /hari-santri/exhibitors/{exhibitor_id}/wallet/transfers.csv?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` | pemilik exhibitor/admin | Export CSV transaksi dengan filter periode |
| `GET /admin/hari-santri/exhibitors/approved` | admin/organizer | Daftar exhibitor approved untuk dropdown settlement |
| `GET /admin/hari-santri/settlements` | admin/organizer | Daftar settlement dengan nama exhibitor untuk dropdown konfirmasi |
| `GET /admin/hari-santri/settlements/reconciliation` | admin/organizer | Rekonsiliasi kredit voucher, settlement, saldo wallet, dan status mismatch |
| `GET /admin/hari-santri/settlements/reconciliation.csv` | admin/organizer | Export CSV rekonsiliasi settlement |
| `GET /admin/hari-santri/audit-logs?limit=200&action=voucher_scanned` | admin/organizer | Melihat dan memfilter audit trail dengan nama actor |

### Membuat voucher

```json
{"participant_id": "<uuid>", "initial_balance": 100000}
```

Response mengandung `voucher_id`, nama peserta, saldo, dan `qr_token`. Kartu voucher kedua untuk peserta yang sama ditolak.

### Scan dan charge

```json
{"qr_token": "<token-dari-QR-voucher>", "amount": 25000, "request_id": "scan-device-01-20261004-000001", "participant_password": "<password-peserta>"}
```

Response mengandung `participant_balance`, `exhibitor_balance`, dan `transfer_id`. `request_id` harus dipertahankan ketika request diulang.

## Aturan keamanan dan konsistensi

- QR tidak memuat saldo atau PII; backend selalu membaca saldo terbaru.
- QR peserta tidak dapat digunakan sebagai QR voucher.
- Semua tenant yang terdaftar dan tidak berstatus `rejected` dapat menerima saldo; satu akun dapat memiliki beberapa lapak.
- Pemilik exhibitor hanya dapat melakukan charge untuk exhibitor miliknya; admin/organizer dapat membantu operasional.
- Saldo peserta harus cukup dan nominal harus positif.
- Voucher tidak aktif ditolak.
- Wallet peserta dan exhibitor dikunci saat transfer.
- `wallet_transfers.request_id` unik dan memakai advisory transaction lock untuk retry bersamaan.
- Test contract backend memverifikasi advisory lock, retry idempotent, QR invalid, voucher expired, ownership exhibitor, password peserta, dan saldo tidak cukup; test dua request database bersamaan tetap menjadi gate UAT.
- Jangan mencetak QR voucher ke log.
- Semua endpoint charge membatasi percobaan per akun, default 30 request per 60 detik; batas dapat diatur melalui `VOUCHER_SCAN_RATE_LIMIT_PER_MINUTE` dan `VOUCHER_SCAN_RATE_LIMIT_WINDOW_SECONDS`. Jika limit tercapai API mengembalikan HTTP 429 dan `Retry-After`.
- Counter rate limit disimpan di PostgreSQL melalui migration `202610040059` dan operasi upsert atomik, sehingga batas berlaku lintas worker tanpa layanan tambahan.
- Transaksi charge wajib memakai password akun peserta/pemesan pemilik voucher; exhibitor tetap menjadi pihak yang melakukan scan. Transaksi yang sudah berhasil tidak memiliki endpoint pembatalan.
- Settlement diajukan oleh pemilik tenant untuk seluruh saldo tersedia, dapat dilakukan hari yang sama atau hari berikutnya. Saldo exhibitor baru berkurang setelah pemilik tenant mengonfirmasi pembayaran uang nyata.
- Saldo peserta yang tersisa efektif menjadi hangus setelah 15 November 2026 pukul 23:59 WIB.

## Database dan deployment

Migration: `202610040055`–`202610070060` (`alembic/versions/20261004_055_voucher_wallets.py` sampai migration operasional `20261007_060_hari_santri_operations.py`). Tabel audit `hari_santri_audit_logs` mencatat actor, action, entity, payload non-QR, dan waktu operasi, termasuk kode kegagalan scan tanpa token QR. Token QR mentah tidak pernah masuk audit payload.

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Sebelum produksi, lakukan backup, migration pada staging, dan uji restore.
