# Audit Implementasi Portal Hari Santri 2026

Status audit: 4 Oktober 2026. Dokumen teknis dan naskah konten menjadi sumber operasional. Nilai harga, kuota, waktu, rute, pengisi acara, hadiah, voucher, dan biaya bazar tidak boleh dikarang; panitia mengaturnya di backend/CMS setelah disahkan.

## Batas sistem

- `fastapi-hari-santri` adalah Portal Event: akun pemesan, event, paket, order, roster peserta/keluarga, ukuran dan stok kaos, tiket, check-in, konten, serta laporan operasional.
- Alur Hari Santri dimulai dari akun pemesan lalu pemilihan tepat satu paket kegiatan. `ParticipantProfile` dan endpoint registrasi peserta legacy bukan prasyarat untuk membuat order Hari Santri; roster peserta diisi langsung pada order.
- `fastapi-bayar` adalah Payment Portal: OAuth client credentials, hosted checkout, callback provider, ledger, settlement, dan rekonsiliasi. Metode pembayaran dipilih dan diproses sepenuhnya di sana.
- Browser hanya menerima `payment_url`. Tidak ada key Portal Payment/gateway di Nuxt. Redirect browser tidak mengubah status order menjadi lunas.
- Tidak ada endpoint pembayaran gateway langsung untuk Portal Event. Kontrak aktif hanya checkout dan callback Payment Portal.
- Router payment legacy sudah dicabut dari API utama. Endpoint DOKU Direct, Midtrans checkout, manual/offline payment, dan webhook provider lama tidak lagi dipublikasikan. Laporan read-only lama dipertahankan sementara untuk migrasi dan rekonsiliasi. Modul/model lama belum dihapus dari source agar data order lama dan helper internal tidak rusak; penghapusan source/dependency fisik menjadi pekerjaan cleanup terpisah setelah migrasi data selesai.

## Implementasi yang tersedia

| Area | Implementasi |
|---|---|
| Event & paket | Slug event `hari-santri-2026`; hanya satu produk `hari_santri_package` untuk satu kegiatan (`CYCLING` atau `FAMILY_WALK`) boleh berada di cart/order. Checkout mengunci event serta menahan kuota paket. |
| Peserta | `order_participants` menyimpan satu baris per anggota keluarga. Anak di bawah 18 tahun membutuhkan nama/kontak wali. Roster mengunci event dan memeriksa kapasitas orang aktif. |
| Wilayah tinggal | Master `administrative_regions` berisi kode resmi dari workbook wilayah. Setiap peserta order wajib memilih dan mengirim provinsi, kabupaten/kota, kecamatan, dan desa/kelurahan; backend memvalidasi parent-child sebelum reservasi. |
| Kaos | `shirt_sizes` dan `shirt_inventory`; kapasitas, reserved, allocated, dan stok tersedia per event/ukuran. Pemilihan roster mengunci inventory dan menolak stok tidak cukup. |
| Payment | Adapter server-to-server mengikuti `docs/EVENT_CLIENT_INTEGRATION.md` pada repo `fastapi-bayar`: OAuth2 client credentials, `POST /api/v1/client/payments`, idempotency key, dan callback HMAC Base64-SHA256 atas `timestamp + "." + raw_body`. |
| Callback | Event ID unik, pemeriksaan signature/timestamp/event type/status/reference/payment ID/event/amount/currency, pembaruan order idempotent, pelepasan/reservasi stok dan penerbitan tiket setelah status `PAID`. Konflik pembayaran terlambat menjadi `paid_needs_review`. |
| Tiket | Token acak bertanda tangan per peserta; database menyimpan hash. Pemilik dapat melihat QR; check-in hanya sekali. |
| Bazar | Pengajuan tenant terpisah dari order peserta; status diajukan/ditinjau/disetujui/ditolak/perlu revisi. Tidak ada biaya checkout stan di portal Event. |
| Wallet voucher | Wallet peserta multi-kartu, wallet tenant terdaftar selain rejected, dan ledger transfer tersedia. Transfer memakai lock database, validasi saldo, ownership exhibitor, dan `request_id` idempotency. |
| Proteksi scan voucher | Tiga endpoint charge memakai penghitung PostgreSQL atomik yang berlaku lintas worker; default 30 request per akun per 60 detik, dikonfigurasi lewat environment. |
| Audit voucher | Admin dapat meninjau audit dari halaman Nuxt, memfilter jenis aktivitas, dan melihat nama actor serta payload operasional tanpa token QR/password. |
| Locale | Locale aktif API/UI `id` dan `en`; Indonesia adalah default. |
| Database lokal | Database `hari_santri` tersedia di localhost:5432, owner `openpg`, dan `.env` backend lokal diarahkan ke database itu. Migration voucher wallet menambah head `202610040055`; penerapan di environment target tetap harus diverifikasi. |
| Terms | Checkout Hari Santri mewajibkan persetujuan syarat; order menyimpan `terms_accepted_at` dan `terms_version=hari-santri-2026-v1`. |

## API Event Hari Santri

Semua path memakai prefix `/api/v1`.

- `GET /events/{event_id}/shirt-sizes`: ukuran aktif dan ketersediaan publik.
- `GET /admin/events/{event_id}/shirt-sizes`: semua ukuran dan inventory untuk admin.
- `POST /admin/events/{event_id}/shirt-sizes`, `PUT /admin/shirt-sizes/{size_id}`: kelola master dan kapasitas ukuran.
- `PUT /orders/{order_id}/participants`, `GET /orders/{order_id}/participants`: roster milik pemesan dan reservasi kaos.
- `GET /regions?level=province`: daftar provinsi.
- `GET /regions?level=regency&parent_code={province_code}`: kabupaten/kota dari provinsi terpilih.
- `GET /regions?level=district&parent_code={regency_code}`: kecamatan dari kabupaten/kota terpilih.
- `GET /regions?level=village&parent_code={district_code}`: desa/kelurahan dari kecamatan terpilih.
- `POST /hari-santri/orders/{order_id}/checkout`: membuat/melanjutkan hosted checkout Portal Payment.
- `GET /hari-santri/orders/{order_id}/payment-status`: status order lokal; status PAID hanya dari callback terverifikasi.
- `POST /integrations/payment-portal/callback`: callback server-to-server bertanda tangan.
- `GET /hari-santri/me/tickets`: tiket QR peserta.
- `POST /admin/hari-santri/vouchers`: admin membuat kartu voucher dan saldo awal peserta.
- `GET /admin/hari-santri/voucher-participants`: admin mengambil peserta dari order `PAID` yang belum memiliki voucher untuk picker penerbitan reward.
- `GET /admin/hari-santri/exhibitors/approved`, `GET /admin/hari-santri/settlements`: data pilihan admin berbasis nama untuk settlement; UUID hanya disimpan dan dikirim oleh frontend secara internal setelah pilihan dibuat.
- `GET /admin/hari-santri/audit-logs?limit=200&action=voucher_scanned`: audit trail dapat difilter per aksi; response memuat nama actor untuk kebutuhan pemeriksaan admin.
- `GET /admin/hari-santri/vouchers`, `POST /admin/hari-santri/vouchers/{id}/revoke`: admin melihat dan menonaktifkan voucher bersaldo nol.
- `POST /admin/hari-santri/vouchers/{id}/adjust`: admin menambah/mengurangi saldo voucher dengan alasan.
- `GET /hari-santri/me/wallet`: peserta melihat saldo dan QR voucher.
- `GET /hari-santri/me/wallets`: peserta melihat seluruh kartu voucher pada akun.
- `GET /hari-santri/me/wallet/transfers`: peserta melihat riwayat pemakaian voucher.
- `POST /hari-santri/exhibitors/{exhibitor_id}/wallet/charge`: exhibitor/admin melakukan charge dari QR voucher.
- `POST /hari-santri/me/exhibitor/wallet/charge`, `GET /hari-santri/me/exhibitor/wallet`, `GET /hari-santri/me/exhibitor/wallet/transfers`: pengguna exhibitor memakai lapak approved milik akun aktif tanpa mengirim UUID.
- `GET /hari-santri/exhibitors/{exhibitor_id}/wallet`: melihat saldo exhibitor.
- `GET /hari-santri/exhibitors/{exhibitor_id}/wallet/transfers`: riwayat kredit exhibitor.
- `GET /hari-santri/exhibitors/{exhibitor_id}/wallet/transfers.csv`: export CSV transaksi exhibitor.
- `POST /admin/hari-santri/exhibitors/{exhibitor_id}/settlements`, `POST /admin/hari-santri/settlements/{id}/confirm`: proses settlement dan pengurangan saldo setelah pembayaran dikonfirmasi.
- `POST /hari-santri/staff/checkins`: check-in token satu kali; implementasi saat ini memakai role `admin`/`organizer`.
- `POST /bazaar/applications`, `GET /bazaar/me/applications`: pengajuan/riwayat tenant.
- `GET /admin/events/{event_id}/bazaar/applications`, `PATCH /admin/bazaar/applications/{id}`: daftar dan keputusan admin.

## Integrasi Payment Portal

Konfigurasi server: `PAYMENT_PORTAL_BASE_URL`, `PAYMENT_PORTAL_CLIENT_ID`, `PAYMENT_PORTAL_CLIENT_SECRET`, `PAYMENT_PORTAL_CALLBACK_SECRET`, `PAYMENT_PORTAL_SERVICE_CODE`, `PAYMENT_PORTAL_RETURN_URL`, timeout, dan toleransi timestamp. Credential client dibuat oleh operator `fastapi-bayar`; jangan simpan di frontend, git, atau log.

Payload Event menyertakan event ID stabil, nama event, order reference, nominal integer IDR, customer name/email, return URL, dan metadata order/paket. `Idempotency-Key` diturunkan dari UUID order. Jika create timeout, state lokal menjadi UNKNOWN dan retry memakai reference/key yang sama; jangan membuat order pembayaran baru. Callback sukses adalah sumber utama status. Status lookup server-to-server untuk rekonsiliasi callback yang hilang tetap harus disambungkan sebelum produksi.

## Hasil audit / gap sebelum launch

1. Event `hari-santri-2026` sudah dibuat sebagai draft lokal dengan nama/tanggal/lokasi terkonfirmasi. Dua placeholder paket `CYCLING` dan `FAMILY_WALK` sudah dibuat nonaktif; admin/panitia harus mengisi harga, mengaktifkan paket, kuota orang/paket, kebijakan anak, dan inventory size dengan data yang disahkan.
2. Produk wajib memuat metadata kegiatan dan batas peserta. Layar admin paket legacy belum menyediakan editor metadata Hari Santri; edit seed/manual admin diperlukan sampai UI khusus selesai.
3. Callback sudah tervalidasi, tetapi polling status backend belum memanggil `GET /client/payments/{payment_id}` di Payment Portal. Callback retry/reconciliation worker/outbox belum berjalan pada app Event.
4. Idempotency unik di Payment Portal, tetapi penguncian request checkout Event dan penyimpanan response ketika timeout masih perlu dites dengan sandbox dan race concurrent.
5. Callback `PAID` menerbitkan tiket individual, tetapi pengujian belum mencakup seluruh race antara expiry, pembayaran terlambat, alokasi stok, refund, dan check-in.
6. Check-in sementara memakai admin/organizer; buat role petugas terbatas, checkpoint, audit per checkpoint, dan perangkat scanner UAT.
7. Aplikasi bazar belum menyediakan lampiran foto/logo/dokumen, kuota/zonasi stan, fasilitas final, penjadwalan seleksi, atau aturan biaya. Status moderasi saja belum menyelesaikan operasional tenant.
8. CMS rute/GeoJSON, agenda, performer, prizes, aturan voucher, halaman syarat/privasi, media berizin, export CSV aman, outbox email, retention PII/anak, dan dashboard KPI Hari Santri belum seluruhnya dipetakan ke modul khusus. Wallet voucher, laporan settlement, rekonsiliasi, dan UI audit tersedia. Pembatalan/refund voucher tidak didukung sesuai keputusan transaksi final. Konfirmasi scan memakai password akun peserta/pemesan pemilik voucher.
9. Beberapa route/admin/dashboard Nuxt legacy IWBIF masih ada secara langsung dan kontennya belum semuanya dialihbahasakan; navigasi publik tidak menampilkannya. Selesaikan/tutup route tersebut pada fase follow-up.
10. Locale lama `zh-CN` pada data konten tidak boleh diganti label menjadi `id` otomatis karena isinya Mandarin. Admin perlu mengisi terjemahan Indonesia yang sah untuk resource yang masih memakai fallback.
11. Teks keputusan panitia yang kosong wajib tetap berupa status “akan diumumkan”; jangan mempublikasikan contoh harga/rute/jam/hadiah.

## Migrasi dan operasi lokal

Database baru telah dibuat sebagai `hari_santri`; `.env` lokal menggunakan `postgresql+asyncpg://openpg:***@localhost:5432/hari_santri`. Jalankan migrasi dengan `python -m alembic upgrade head`, lalu import master wilayah dengan `python scripts/import_administrative_regions.py`. Workbook saat ini memiliki satu baris desa orphan tanpa parent kecamatan; importer melewatinya agar foreign key dan pilihan hierarkis tetap valid. Sebelum upgrade database lama/produksi, backup dan uji restore serta verifikasi urutan revisi pada snapshot produksi.

Integrasi client Payment Portal belum dapat dianggap production-ready sampai operator mendaftarkan client/service, mengizinkan return/callback URL HTTPS yang benar, menaruh secrets di secret store, menjalankan sandbox end-to-end, dan mengaktifkan callback worker/reconciliation.
