# Hari Santri 2026 — TODO Implementasi Backend & Frontend

Dokumen ini adalah backlog peluncuran lintas repo. Acuan: dokumentasi teknis Hari Santri, naskah konten, dan kontrak Payment Portal. Harga, kuota, agenda, rute, pengisi acara, hadiah, dan voucher memerlukan keputusan panitia.

## Selesai pada iterasi ini

- [x] Buat database PostgreSQL lokal `hari_santri` pada `localhost:5432`, owner `openpg`, dan arahkan `.env` backend ke sana.
- [x] Seed event draft idempotent dengan slug `hari-santri-2026` dan hanya data nama/tanggal/lokasi yang terkonfirmasi.
- [x] Wajibkan checkbox terms pada checkout Hari Santri dan simpan waktu serta versi consent pada order.
- [x] Tambahkan model/migrasi order participants, shirt sizes, inventory, Payment Portal ledger/callback, per-participant tickets/check-ins, dan tenant bazar.
- [x] Tambahkan route peserta/ukuran kaos, admin inventory, checkout/status Portal Payment, callback HMAC, tiket, check-in, dan aplikasi bazar.
- [x] Validasi kuota paket dan kapasitas orang `capacity_people` dengan lock pada event saat order/roster dibuat.
- [x] Import master kode wilayah dan wajibkan pilihan provinsi hingga desa/kelurahan per peserta dengan validasi parent-child.
- [x] Buat adapter OAuth client credentials sesuai kontrak Payment Portal; simpan secrets server-side dan gunakan Idempotency-Key stabil per order.
- [x] Pastikan checkout event hanya memakai Payment Portal dan tidak menerima gateway/manual payment langsung.
- [x] Tetapkan locale aktif ke Indonesian/English (`id/en`) dengan Indonesia sebagai default.
- [x] Ganti home Nuxt dengan naskah konten CMS, halaman pendaftaran keluarga, halaman hasil pembayaran, daftar QR peserta, halaman inventory admin, scanner check-in, tenant bazar, dan dashboard Hari Santri.
- [x] Jalankan database lokal ke Alembic head `202609290052`; tambahkan tes dasar consent, callback signature, QR token, validasi wali, locale, dan guard gateway.
- [x] Tambahkan wallet peserta, kartu voucher QR terpisah dari QR peserta, wallet exhibitor approved, transfer atomik, dan idempotency `request_id`.
- [x] Tambahkan endpoint admin membuat voucher, peserta melihat saldo/QR voucher, exhibitor melakukan charge, dan exhibitor melihat saldo.
- [x] Tambahkan daftar seluruh voucher peserta dan riwayat kredit wallet exhibitor untuk kebutuhan dashboard/settlement.
- [x] Tolak penggunaan ulang `request_id` dengan nominal atau exhibitor yang berbeda.
- [x] Tambahkan endpoint admin untuk daftar/revoke voucher, riwayat pemakaian peserta, dan export CSV transaksi exhibitor.
- [x] Sediakan endpoint self-service exhibitor agar user tidak perlu memasukkan UUID lapak saat scan/lihat saldo.
- [x] Tambahkan migration `202610040055` untuk wallet, voucher, dan ledger transfer.
- [x] Jalankan regresi backend: 260 test existing lulus setelah fitur wallet ditambahkan.
- [x] Cabut router payment legacy dari API utama; checkout Hari Santri hanya memakai Payment Portal `fastapi-bayar` dan callback bertanda tangan.
- [x] Bersihkan `.env.example` dari secret/provider DOKU-Midtrans dan matikan seeder payment channel legacy.

## P0 — Voucher wallet sebelum sandbox/UAT

- [ ] Jalankan `alembic upgrade head` pada staging dan production setelah backup/approval migration.
- [x] Frontend voucher menampilkan QR voucher dan memisahkannya dari QR peserta check-in.
- [x] Lengkapi layar admin memilih peserta `PAID`, exhibitor approved, dan settlement pending dari dropdown berbasis nama; tidak ada input UUID operasional.
- [x] Buat layar exhibitor scanner: pilih nominal, scan QR voucher/input fallback, password peserta, saldo tenant, dan riwayat transaksi.
- [x] Lengkapi layar exhibitor dengan filter tanggal, receipt ringkas, dan total voucher diterima pada periode; pengajuan/konfirmasi settlement sudah tersedia.
- [x] Tetapkan masa berlaku voucher sampai 15 November 2026, transaksi tidak dapat dibatalkan, dan settlement mengurangi saldo setelah admin mengonfirmasi pembayaran.
- [x] Tetapkan penerbitan voucher secara selektif/manual oleh admin sebagai reward; tidak semua peserta wajib menerima voucher.
- [x] Tetapkan voucher boleh bernilai Rp0 dan panitia bebas menentukan nominal saldo awal; peserta dapat memiliki beberapa voucher.
- [x] Tetapkan saldo voucher hangus efektif setelah 15 November 2026 dan transaksi dapat memakai beberapa voucher melalui beberapa scan.
- [x] Tetapkan seluruh tenant yang terdaftar selain yang ditolak dapat menerima voucher; satu akun dapat memiliki beberapa lapak.
- [x] Tetapkan settlement diajukan untuk seluruh saldo tersedia dan konfirmasi pembayaran dilakukan pemilik tenant.
- [x] Implementasikan pemilihan beberapa lapak milik satu akun dan pengajuan/konfirmasi settlement dari UI exhibitor.
- [x] Tetapkan semua exhibitor approved dapat menerima voucher, nominal transaksi bebas, dan scan wajib dikonfirmasi dengan password akun peserta/pemesan pemilik voucher.
- [x] Tambahkan admin adjustment saldo voucher, expiry enforcement, settlement pending/confirmed, dan pengurangan saldo setelah pembayaran nyata dikonfirmasi.
- [x] Implementasikan frontend Nuxt `../nuxt-hari-santri`: dashboard peserta, QR voucher, scanner tenant, riwayat saldo, admin voucher, adjustment/revoke, dan settlement.
- [x] Tetapkan saldo sisa hangus setelah 15 November 2026; settlement dilakukan hari yang sama atau hari berikutnya.
- [x] Tambahkan audit log untuk penerbitan/revoke voucher, scan berhasil/gagal, koreksi manual admin, dan settlement; endpoint admin tersedia untuk pemeriksaan.
- [x] Buat halaman audit voucher di Nuxt dengan filter aktivitas dan detail pelaku; backend mendukung filter action serta nama actor.
- [ ] Tambahkan test race condition dua scan bersamaan, request retry, QR invalid, QR peserta pada scanner voucher, saldo kurang, exhibitor belum approved, dan akses lintas pemilik exhibitor.
- [ ] Finalisasi keamanan QR di UAT: HTTPS, masking data peserta, uji limit pada beberapa worker, dan rotasi secret dengan rencana migrasi token.
- [x] Terapkan rate limit bersama per akun pada tiga endpoint charge voucher; limiter mengembalikan HTTP 429 dengan `Retry-After` dan key tidak memuat token QR.
- [x] Ganti limiter proses dengan penghitung PostgreSQL atomik agar batas berlaku lintas worker; perubahan tabel melalui migration `202610040059`.
- [x] Tambahkan report rekonsiliasi per tenant yang membandingkan total kredit, settlement pending/confirmed, saldo wallet, serta export CSV; rekonsiliasi debit peserta detail masih perlu UAT.
- [x] Tambahkan unit contract test wallet untuk advisory lock, retry `request_id`, reuse nominal berbeda, QR invalid, voucher expired, dan ownership exhibitor.
- [x] Tambahkan unit contract test password peserta invalid dan saldo voucher tidak cukup.

## P0 — Sebelum sandbox UAT

- [ ] Operator mendaftarkan client `fastapi-hari-santri` pada `fastapi-bayar`, service `HARI_SANTRI_2026`, scopes `payments:read payments:write`, allowed return URL dan callback URL HTTPS.
- [ ] Hapus source adapter DOKU/Midtrans dan dependency/config secret lama setelah seluruh data/report legacy dimigrasikan dan tidak ada consumer internal yang tersisa.
- [ ] Isi `PAYMENT_PORTAL_BASE_URL`, client ID/secret, callback secret dan return URL pada secret store backend; jangan menaruh secret di `.env.example`, frontend, atau log.
- [ ] Tambahkan server-to-server lookup `GET /api/v1/client/payments/{payment_id}` sebagai jalur pemeriksaan/reconciliation callback terlambat; jangan mengubah status dari query browser.
- [ ] Uji OAuth token, create payment, idempotent replay, timeout UNKNOWN, callback signature raw-body, event replay, duplicate callback, amount mismatch, currency mismatch, wrong reference, invalid timestamp, dan service mismatch terhadap sandbox.
- [ ] Tambahkan lock/idempotency lokal checkout agar request simultan tidak mengirim dua create payment atau menimpa payment_id yang sudah terisi.
- [ ] Uji expiry dan keterlambatan bayar: release reservation sekali; PAID setelah release menjadi `paid_needs_review`, tanpa tiket otomatis.
- [x] Pastikan admin dapat membuat paket `hari_santri_package` dengan metadata activity/min/max/capacity; backend memvalidasi batas jumlah peserta dan kapasitas paket/orang saat reservasi.
- [x] Hilangkan ketergantungan alur Hari Santri pada `ParticipantProfile`; user memilih tepat satu paket kegiatan dan mengisi roster langsung di order.
- [ ] Tambahkan operasi expiry worker dan rekonsiliasi/outbox dengan retry, request ID, audit log, alert callback gagal, dan runbook.
- [ ] Buat role petugas check-in terbatas dan audit trail check-in; saat ini route menggunakan admin/organizer.

## P1 — Kesiapan operasional acara

- [ ] Implementasikan halaman/admin CMS untuk activity types, paket/harga/kuota, periode jual, syarat anak, refund, event timezone, dan audit perubahan.
- [ ] Implementasikan inventory report per ukuran serta transaksi perubahan ukuran pasca-lunas dengan stok atomik, deadline dan audit.
- [ ] Tambahkan edit roster sebelum cutoff; setelah lunas batasi perubahan ukuran dan data anak sesuai kebijakan panitia.
- [ ] Tambahkan snapshot nama wilayah pada tiket/laporan jika label master wilayah berubah setelah pendaftaran.
- [ ] Tambahkan report peserta/paket/aktivitas, total reservasi, pembayaran menurut callback, stok kaos, check-in, CSV dengan PII minimum.
- [ ] Lengkapi alur bazar: upload aman foto/logo/dokumen, kurasi, kuota/zonasi stan, fasilitas, biaya jika disahkan, tenant dashboard, dan audit keputusan.
- [ ] Implementasikan route GeoJSON/GPX dengan validasi geometri, start/finish/checkpoint, jarak, fallback daftar titik, aksesibilitas dan versi publik.
- [ ] Implementasikan CMS agenda, performer, prizes, sponsor, voucher, FAQ, konten sejarah, media license/alt text, draft-preview-publish, SEO dan sitemap.
- [ ] Konfigurasikan syarat acara, privasi, kebijakan refund, kontak resmi, retensi/hapus PII, consent wali, dan akses petugas.
- [ ] Lengkapi CMS dan selaraskan copy semua halaman auth/legal Hari Santri.

## UAT dan release gate

- [ ] Pendaftaran satu orang dan keluarga dengan kombinasi ukuran/aktivitas yang benar.
- [ ] Invalid activity, paket tidak aktif, jumlah peserta di bawah/di atas min/max, stok ukuran/paket habis, dan usaha menyentuh foreign event ditolak.
- [ ] Double-click checkout, timeout sebelum/sesudah remote commit, replay idempotency key, dan dua callback serentak hanya menghasilkan satu payment/order transition/tiket.
- [ ] Callback signature salah, body berubah satu byte, timestamp kedaluwarsa, event ID dipakai ulang dengan payload berbeda, amount/currency/reference/client/service salah tidak mengubah order.
- [ ] Redirect sukses palsu tetap PENDING; tiket hanya muncul setelah callback/status server terverifikasi PAID.
- [ ] QR duplikat, QR tidak valid, ticket revoked, check-in ganda, kamera ditolak, dan fallback input diuji perangkat mobile.
- [ ] QR voucher valid, QR peserta salah konteks, voucher tidak aktif, saldo kurang, scan ganda, retry `request_id`, dan dua scanner bersamaan diuji pada perangkat mobile.
- [ ] Tenant submit, edit/duplicate, keputusan needs_revision/approved/rejected, hak akses peserta/admin, upload invalid, dan email/pemberitahuan diuji.
- [ ] Uji responsive dan aksesibilitas desktop/mobile, screen reader, keyboard, `id` dan `en`, copy/metadata/placeholder, serta image/media load.
- [ ] Jalankan test backend penuh, `alembic current`, `npm run lint`, `npx vue-tsc --noEmit`, build Nuxt, UAT sandbox, backup/restore drill, dan verifikasi production secrets/callback allowlist.

## Keputusan panitia yang masih dibutuhkan

Harga/isi paket; paket keluarga dan min/max orang; kuota order/orang; aturan usia dan wali; stok serta size chart; batas bayar/ubah kaos; rute resmi dan jam; start/finish/medis; agenda/pengisi acara; hadiah/syarat undian; nilai awal voucher; saldo sisa setelah 15 November; waktu settlement hari yang sama atau berikutnya; biaya dan fasilitas stan; kontak, domain produksi, dan kebijakan data anak.
