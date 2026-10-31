# Dokumentasi Teknis Portal Event Harlah NU ke-101 (harlahnu.com)

**Versi 1.0 · 29 Oktober 2026 · Untuk developer backend (FastAPI) dan frontend (Nuxt 3)**
Pasangan dokumen: `Konten_Web_Portal_Harlah_NU_ke-101.md` (naskah konten).

## 1. Ringkasan

Portal Harlah NU ke-101 adalah **migrasi dan adaptasi** portal Hari Santri 2026 (`nuxt-hari-santri` + `fastapi-hari-santri`). Alur inti **tidak diubah**: akun pemesan, paket per kegiatan, roster keluarga, ukuran kaos, checkout ke Portal Payment, tiket QR, check-in, bazar, voucher, donasi, rekonsiliasi.

Yang berubah:
1. Identitas event, tanggal, domain, dan konfigurasi (bagian 3).
2. Konten: sejarah NU dan Resolusi Jihad menggantikan sejarah Hari Santri saja (bagian 5).
3. Fitur baru bernilai tinggi: hitung mundur, linimasa sejarah, halaman Mitra, Keluarga Favorit, media kit, sertifikat (bagian 6).

Prinsip yang tetap berlaku dari dokumen Hari Santri:
- Status bayar hanya dari server. Return URL dan query browser bukan bukti pembayaran.
- Harga, kuota, rute, jam, pengisi acara, hadiah, voucher, biaya tenant, dan titik kumpul tidak boleh dikarang dari contoh. Belum disahkan = tampil "diumumkan panitia".
- Bahasa portal hanya Indonesia (`id`).
- Nama/foto tokoh dan foto anak hanya tampil dengan persetujuan publikasi.
- Browser tidak pernah memegang secret Portal Payment.

## 2. Peran

| Peran | Kewenangan |
|---|---|
| Pengunjung | Baca konten, lihat paket, mulai pendaftaran, donasi, kirim pengajuan mitra |
| Pemesan | Kelola akun, pesanan, roster keluarga, ukuran kaos, bayar, lihat tiket, ikut Keluarga Favorit |
| `checkin_staff` | Pindai QR tiket (hak terbatas, bukan admin) |
| Admin konten | Sunting halaman, agenda, pengisi acara, hadiah, sejarah, galeri, FAQ, sponsor, pengumuman |
| Admin registrasi | Paket, kuota, ukuran kaos, koreksi roster, ekspor |
| Admin keuangan | Status pembayaran, rekonsiliasi, settlement voucher |
| `organizer` | Operasional hari-H, lihat laporan |
| Super admin | Pengguna, peran, konfigurasi, audit |

## 3. Migrasi dari Hari Santri ke Harlah NU

### 3.1 Konfigurasi frontend

```env
NUXT_PUBLIC_API_BASE_URL=https://api.harlahnu.com      # wajib eksplisit, bukan localhost
NUXT_PUBLIC_API_BASE_PATH=/api/v1
NUXT_PUBLIC_EVENT_SLUG=harlah-nu-101
NUXT_PUBLIC_SITE_URL=https://harlahnu.com
NUXT_PUBLIC_APP_NAME=Harlah NU ke-101
NUXT_PUBLIC_EVENT_STARTS_AT=2027-01-31T05:30:00+07:00   # contoh; isi jam resmi panitia
```

Build statis tetap menolak localhost/loopback pada `API_BASE_URL` dan `SITE_URL`.

### 3.2 Daftar perubahan (checklist)

**Backend**
- [x] Laporan peserta menampilkan tanggal order/pendaftaran pertama dan daerah asal `desa/kelurahan, kecamatan, kabupaten/kota`; CSV memakai kontrak yang sama dan tidak memakai negara sebagai fallback.
- [ ] Buat event baru `harlah-nu-101` (jangan menimpa `hari-santri-2026`). Atribut: tanggal 2027-01-31, lokasi, zona waktu `Asia/Jakarta`.
- [x] Seed dua jenis kegiatan aktif: `CYCLING` (Sepeda Santai, biaya pendaftaran Rp75.000) dan `FAMILY_WALK` (Jalan Sehat Keluarga, biaya pendaftaran Rp25.000). Nominal tetap bersumber dari produk server-side.
- [ ] Paket `hari_santri_package` → pertimbangkan ganti nama tipe menjadi generik `event_package` (migrasi nama tipe; jaga kompatibilitas data lama).
- [ ] Voucher: ubah masa berlaku default dari 15 November 2026 menjadi tanggal baru (konfigurasi per event, bukan konstanta kode).
- [ ] DNS: arahkan `harlahnu.com` dan `api.harlahnu.com` ke `103.127.136.22` sebelum sertifikat diterbitkan.
- [ ] CORS: tambah `https://harlahnu.com` (dan `www` bila DNS/redirect www disiapkan). Pertahankan origin `https://harisantri2026.com` selama masa transisi.
- [ ] Payment Portal: daftarkan client/service code, **return URL** (`https://harlahnu.com/pembayaran/hasil`), dan **callback URL** untuk domain baru. Uji di sandbox dulu.
- [ ] Email transaksional: nama pengirim, subjek, dan templat berisi "Harlah NU ke-101".
- [ ] Putuskan nasib data lama: pesanan/donasi Hari Santri 15 Nov 2026 dipindahkan ke event baru atau di-refund. **Keputusan panitia, bukan otomatis.**
- [ ] Referensi wilayah: pembaruan ke 38 provinsi (Papua 93–96) masih TODO dari rilis sebelumnya.

**Terminologi dan biaya pendaftaran**
- [x] Gunakan istilah publik **Jenis Kegiatan** untuk pilihan Sepeda Santai dan Jalan Sehat Keluarga; istilah internal `hari_santri_package` dan field API `price` dipertahankan untuk kompatibilitas.
- [x] Gunakan istilah publik **Biaya Pendaftaran**, bukan Harga, pada beranda, formulir, dashboard pembayaran, dan administrasi kegiatan.
- [x] Checkout mengambil total order dari harga produk backend; perubahan biaya hanya berlaku untuk order baru, sedangkan riwayat order tetap memakai snapshot nominal saat dibuat.

**Frontend**
- [ ] Ganti semua string "Hari Santri 2026", "15 November 2026", "HeroFunWalk.com" (cari dengan `grep -rn`). Sumber string sebaiknya satu berkas konten, bukan tersebar di komponen.
- [ ] Menu: tambah grup **Sejarah NU** dan halaman **Mitra** (bagian 1 naskah konten).
- [ ] Metadata, OG image, sitemap, `robots.txt`, JSON-LD `Event` untuk domain baru.
- [ ] Logo dan palet baru (token warna di bagian 8).
- [ ] Halaman legal (privasi, ketentuan, tata tertib, pembatalan) versi Harlah NU yang disahkan.
- [ ] **Redirect 301** dari `harisantri2026.com` (semua path) ke `harlahnu.com` yang sepadan, agar tautan lama dan tiket lama tidak mati. Jangan redirect `/api` jika masih dipakai callback lama.
- [ ] Hapus atau sembunyikan komponen legacy IWBIF yang masih ada (44 berkas, lihat TODO frontend).

### 3.3 Urutan rilis yang disarankan

1. Staging di subdomain (`staging.harlahnu.com`) dengan event `harlah-nu-101`.
2. UAT sandbox Payment Portal (bagian 10).
3. Isi konten final dari daftar bagian 15 naskah.
4. Rilis produksi dengan nominal uji yang disetujui.
5. Aktifkan redirect domain lama, monitoring, dan backup.

### 3.4 Celah migrasi yang mudah terlewat

**Isolasi antar-event (wajib, risiko tertinggi)**
- [ ] Check-in harus menolak tiket yang `event_id`-nya bukan `harlah-nu-101`. Tiket Hari Santri tidak boleh lolos di scanner Harlah NU, dan sebaliknya.
- [ ] Setiap query pesanan, voucher, tenant, donasi, dan laporan harus terfilter `event_id`. Cek terutama `GET /orders?event_id=`, dashboard voucher, dan laporan stok kaos.
- [ ] Kunci penandatangan token QR: putuskan apakah dipakai bersama atau dipisah per event. Jika dipakai bersama, validasi `event_id` di dalam token.
- [ ] Voucher Hari Santri yang masih berlaku: hanguskan, refund, atau tidak berlaku di acara baru. Tulis kebijakannya.

**Laporan peserta**
- Endpoint aktif: `GET /api/v1/admin/reports/participants` dan `GET /api/v1/admin/reports/participants.csv`.
- `registered_at` berasal dari order pertama; bila belum ada order, gunakan `account_created_at`.
- `origin_region` berurutan desa/kelurahan, kecamatan, kabupaten/kota dari `administrative_regions`.
- Frontend menampilkan **Belum tersedia** bila wilayah kosong, bukan `Indonesia`. Kontrak lengkap ada di [Kontrak Laporan Peserta](PARTICIPANT_REPORTING.md).

**Akun dan sesi**
- [ ] Akun pemesan lama: apakah database dipakai bersama (pengguna lama bisa masuk dan langsung mendaftar) atau dipisah? Putuskan, lalu sesuaikan alur "akun tanpa order aktif → `/daftar`".
- [ ] Cookie dan sesi terikat domain, jadi pengguna harus masuk ulang di `harlahnu.com`. Sesuaikan `SameSite`, domain cookie, dan tautan reset kata sandi/verifikasi email yang memakai domain lama.
- [ ] `sessionStorage` referensi donasi tidak terbawa lintas domain. Donasi yang belum selesai di domain lama tetap harus bisa dilacak lewat backend.

**Pembayaran dan transaksi berjalan**
- [ ] Pembayaran yang masih tertunda di Portal Payment dengan return URL domain lama: pertahankan route `/pembayaran/hasil` di domain lama sampai semua order selesai atau kedaluwarsa, atau redirect 301 yang membawa path dan query.
- [ ] Callback terlambat untuk order lama tetap harus diterima backend. Jangan mencabut URL callback lama sebelum antrean outbox kosong.
- [ ] Beri tahu peserta Hari Santri yang sudah mendaftar atau membayar lewat email: acara berubah, apa yang terjadi pada pesanan mereka, dan tenggatnya.

**Penamaan teknis (agar developer tidak bingung)**
- [ ] Peta rename: `hari-santri-2026` → `harlah-nu-101` (slug), `hari_santri_package` → `event_package`, `/hari-santri/...` → alias netral `/events/...` (pertahankan path lama sementara dengan deprecation), `useHariSantriRegistration` → `useEventRegistration`, skrip uji `test-hari-santri-*` → `test-event-*`, nama folder rilis `/var/www/nuxt-hari-santri` → folder baru.
- [ ] Jalankan `grep -rniE "hari.?santri|15 november|herofunwalk|iwbif"` pada source dan hasil build. Pencarian itu ikut menjaring konten sejarah Hari Santri yang memang sengaja dipertahankan di halaman Sejarah NU, jadi periksa tiap hasilnya.
- [ ] Master wilayah: pembaruan ke 38 provinsi masih tertunda dari rilis lama. Tentukan dikerjakan sebelum pendaftaran dibuka atau tidak.

**SEO dan tautan**
- [ ] Redirect 301 per path (bukan hanya ke beranda), pertahankan `sitemap` lama sementara, dan gunakan fitur perpindahan domain di Search Console.
- [ ] Perbarui tautan di media sosial, poster, QR cetak, dan materi sponsor yang masih memuat domain lama.

## 4. Arsitektur singkat

```
Browser (Nuxt 3, statis)
   │  Accept-Language: id
   ▼
FastAPI  /api/v1  ──► PostgreSQL (data, rate limit bersama, outbox)
   │                └─► Worker terjadwal (expiry reservasi, outbox, pengumuman)
   └─► Payment Portal (server-to-server; callback HMAC)
Tidak ada secret Portal di browser.
```

## 5. Model konten (CMS)

Naskah konten dimuat dari CMS backend, bukan ditanam di komponen. Entitas baru atau yang diperluas:

| Entitas | Field utama | Catatan |
|---|---|---|
| `Event` | `id, slug, name, tagline, starts_at, ends_at, timezone, venue_name, venue_address, organizer, status(draft/published)` | `starts_at` memberi daya hitung mundur |
| `ContentBlock` | `id, event_id, key, title, body_md, status, updated_by, updated_at` | key mis. `home.hero`, `about`, `terms` |
| `AgendaItem` | `id, event_id, order, title, description, starts_at?, time_label, is_confirmed` | `is_confirmed=false` → tampil "Waktu diumumkan" |
| `Performer` | `id, name, role, bio, photo_media_id?, is_confirmed, publish_consent` | Tampil hanya jika `is_confirmed` dan `publish_consent` |
| `Prize` | `id, name, description, sponsor_id?, is_confirmed` | Tampil hanya jika `is_confirmed` |
| `HistoryEntry` | `id, slug, date_label, date_sort, title, summary, body_md, source_ids[], status, order` | Sumber linimasa sejarah (F-02) |
| `HistorySource` | `id, title, publisher, url, accessed_at` | Rujukan wajib per entri |
| `Sponsor` | `id, name, tier(platinum/gold/silver/supported_by), logo_media_id, url, order, status(prospect/confirmed)` | Hanya `confirmed` tampil publik |
| `SponsorLead` | `id, company, contact_name, email, phone, tier_interest, message, status, created_at` | Dari formulir Mitra (F-03) |
| `MediaAsset` | `id, kind(photo/illustration/poster), url, alt_text, caption, is_illustration, consent_status, credit` | `is_illustration=true` → label wajib |
| `FaqItem` | `id, order, question, answer_md, status` | |
| `Announcement` | `id, title, body_md, starts_at, ends_at, pinned` | Banner di beranda |
| `FamilyEntry` | `id, order_id, family_name, photo_media_id, caption, consent_guardian, status(pending/approved/rejected), submitted_at` | Keluarga Favorit (F-04) |
| `Certificate` | `id, ticket_id, serial, issued_at, pdf_media_id` | Sertifikat (F-07) |

Semua entitas CMS: `created_at, updated_at, updated_by`, log audit perubahan, dan status `draft/published`. Perubahan ke `published` hanya oleh admin konten.

## 6. Fitur dan prioritas

Legenda: **MVP** wajib sebelum pendaftaran dibuka · **P1** sebelum hari-H · **P2** setelah rilis.

### 6.1 Fitur yang diwarisi (tetap, hanya konfigurasi)

| ID | Fitur | Status | Catatan |
|---|---|---|---|
| W-01 | Akun pemesan dan masuk | MVP | `app/pages/auth/register.vue` → `/daftar` |
| W-02 | Paket dinamis per activity | MVP | Data dari API; admin mengisi harga/kuota |
| W-03 | Roster keluarga + ukuran kaos | MVP | `PUT /orders/{order_id}/participants` |
| W-04 | Checkout dan pemulihan pesanan | MVP | Lihat `CHECKOUT_RESUME.md`; idempotency per order |
| W-05 | Hasil pembayaran dan status | MVP | `GET /hari-santri/orders/{order_id}/payment-status` (pertimbangkan alias netral `/events/orders/...`) |
| W-06 | Tiket QR per peserta | MVP | Token opaque bertanda tangan, tanpa PII |
| W-07 | Check-in staf (ZXing) | MVP | Role `checkin_staff` |
| W-08 | Bazar dan pengajuan tenant | P1 | Terpisah dari order tiket |
| W-09 | Voucher peserta dan scanner tenant | P1 | Rate limit lewat PostgreSQL; 429 + `Retry-After` |
| W-10 | Donasi | P1 | `POST /donations`, status dari ledger backend |
| W-11 | Rekonsiliasi admin | MVP | `/admin/hari-santri-operations` |
| W-12 | Audit voucher dan settlement | P1 | |

### 6.2 Fitur baru

#### F-01 Konten dari CMS (MVP)
- Backend: `GET /events/{slug}/content` mengembalikan blok konten terbit; `GET /events/{slug}/agenda`, `/performers`, `/prizes`, `/faq`, `/announcements`, `/sponsors`.
- Frontend: composable `useEventContent(key)` dengan fallback teks "diumumkan panitia" saat data kosong atau `is_confirmed=false`.
- Admin: CRUD + pratinjau + riwayat versi.
- **Selesai bila:** mengganti judul hero dari admin tampil di beranda tanpa deploy ulang; item belum terkonfirmasi tidak pernah tampil sebagai janji.

#### F-02 Linimasa Sejarah NU (MVP)
- Backend: `GET /history/entries?event=harlah-nu-101` (urut `date_sort`), `GET /history/entries/{slug}`; setiap entri mengembalikan `sources[]`.
- Frontend: halaman `/sejarah-nu` dengan komponen `TimelineEntry` (tanggal, judul, ringkas, tombol "Selengkapnya"), kartu "Fakta Cepat", daftar rujukan, label **"Ilustrasi, bukan foto dokumentasi"** bila `is_illustration`.
- Konten: dari bagian 8–9 naskah konten; verifikasi editor sebelum `published`.
- Tahun yang belum pasti (mis. Tashwirul Afkar) disimpan sebagai `date_label` teks, bukan tanggal.
- **Selesai bila:** entri tanpa minimal satu sumber tidak bisa dipublikasikan; halaman lolos pemeriksaan aksesibilitas (bagian 9).

#### F-03 Halaman Mitra dan Sponsor (MVP)
- Backend: `GET /sponsors` (hanya `confirmed`), `POST /sponsor-leads` (rate limit per IP, honeypot atau captcha ringan, validasi email), notifikasi email ke panitia.
- Frontend: `/mitra` (paket Platinum/Gold/Silver, strip logo per tingkat, formulir, tombol "Unduh Proposal" menuju berkas di media).
- Keputusan panitia: tampilkan nominal atau tidak (default: tidak).
- Admin: kelola lead (status baru/dihubungi/setuju/tolak), kelola logo sponsor.
- **Selesai bila:** logo hanya muncul jika `status=confirmed`; urutan tampilan mengikuti tingkat paket.

#### F-04 Keluarga Favorit (P1)
- Latar: syarat menyebut tiga Keluarga Paling Favorit. Mekanisme pemilihan **belum diputuskan panitia**. Dua opsi: (a) juri panitia memilih, (b) voting publik. Default aman: (a).
- Backend: `POST /family-entries` (hanya pemesan dengan order lunas), `GET /family-entries?status=approved`, admin moderasi + penetapan 3 pemenang. Wajib `consent_guardian=true` bila ada anak di foto. Unggah foto lewat media endpoint dengan validasi tipe/ukuran dan pemindaian.
- Frontend: formulir unggah di dashboard, galeri hasil moderasi.
- Privasi: tidak menampilkan nama anak lengkap; mudah dicabut (pemilik dapat menarik entri).
- **Selesai bila:** entri baru selalu `pending` sampai disetujui admin; entri ditarik hilang dari publik.

#### F-05 Media kit dan Twibbon (P1)
- Backend: `GET /media-kit` daftar aset (poster, banner, frame foto) dengan versi dan tautan unduh.
- Frontend: halaman `/media-kit`; komponen twibbon sisi klien (kanvas) yang menempel frame ke foto pengguna **tanpa mengunggah ke server**.
- **Selesai bila:** foto pengguna tidak meninggalkan perangkat.

#### F-06 Pengumuman dan banner (P1)
- `GET /announcements?active=1`; banner beranda dengan tombol tutup (simpan penutupan di `localStorage`, berupa kenyamanan saja).

#### F-07 Sertifikat digital (P2)
- Syarat: data check-in dan checkpoint sudah ada.
- Backend: terbitkan setelah acara bagi tiket berstatus checked-in (dan checkpoint selesai bila itu aturan panitia). `GET /tickets/{id}/certificate` mengembalikan PDF atau tautan bertanda tangan. Nomor seri unik.
- Frontend: tombol "Unduh Sertifikat" di `/dashboard/tiket` setelah acara selesai.
- **Selesai bila:** sertifikat hanya untuk tiket yang benar-benar check-in; reissue terkontrol.

#### F-08 Dinding ucapan (P2, opsional)
- `POST /wishes` (moderasi wajib sebelum tampil), `GET /wishes?status=approved`; filter kata terlarang dan pelaporan.

#### F-09 SEO dan Open Graph (MVP)
- Metadata per route (tabel SEO di naskah bagian 13), `sitemap.xml` dari route statis + `HistoryEntry`, JSON-LD `Event`, gambar OG 1200×630.

#### F-10 Hitung mundur (MVP)
- Sumber waktu: `Event.starts_at` dari API (jangan ditanam di klien).
- Komponen `CountdownBlock`: hitung dengan selisih terhadap `starts_at`; koreksi jam perangkat dengan header `Date` dari respons API; tampil nol-hari, "sedang berlangsung", "selesai".
- Aksesibilitas: `aria-live="off"` pada angka (jangan membacakan tiap detik); sediakan teks tanggal statis; hormati `prefers-reduced-motion`.

#### F-11 Peta dan rute (P1, menunggu GPX/GeoJSON)
- Backend: `GET /routes` (GeoJSON per activity, `version`), validasi GeoJSON.
- Frontend: peta statis dahulu (gambar), peta interaktif jika rute disahkan. Tampilkan titik start/finish, pos medis, aksesibilitas, versi rute. **Jangan menampilkan rute sebelum disetujui pengelola kawasan.**

#### F-12 Konten dua kalender (P2)
- Tampilkan tanggal Masehi dan keterangan 16 Rajab di halaman sejarah sebagai informasi, tanpa konversi otomatis di klien.

## 7. Kontrak API: tambahan

Semua di bawah `/api/v1`. Respons JSON, UTF-8, `Accept-Language: id`. Waktu dalam ISO 8601 dengan offset.

| Metode | Path | Auth | Fungsi |
|---|---|---|---|
| GET | `/events/{slug}` | publik | Info event, `starts_at`, status |
| GET | `/events/{slug}/content` | publik | Blok konten terbit |
| GET | `/events/{slug}/agenda` | publik | Agenda (dengan `is_confirmed`) |
| GET | `/events/{slug}/performers` | publik | Hanya `confirmed` + `consent` |
| GET | `/events/{slug}/prizes` | publik | Hanya `confirmed` |
| GET | `/events/{slug}/faq` | publik | |
| GET | `/history/entries` | publik | Linimasa sejarah |
| GET | `/history/entries/{slug}` | publik | Detail + sumber |
| GET | `/sponsors` | publik | Hanya `confirmed` |
| POST | `/sponsor-leads` | publik, rate limit | Minat sponsor |
| GET | `/media-kit` | publik | Aset unduh |
| GET | `/announcements` | publik | Pengumuman aktif |
| POST | `/family-entries` | pemesan (order lunas) | Kirim entri Keluarga Favorit |
| GET | `/family-entries` | publik | Hanya `approved` |
| GET | `/tickets/{id}/certificate` | pemilik tiket | Sertifikat (P2) |
| GET | `/routes` | publik | GeoJSON rute (P1) |
| `*` | `/admin/cms/...` | admin konten | CRUD entitas CMS |

Endpoint yang sudah ada (tidak diubah): `GET /events`, `GET /orders?event_id=`, `PUT /orders/{order_id}/participants`, `POST /hari-santri/orders/{order_id}/checkout`, `GET /hari-santri/orders/{order_id}/payment-status`, `POST /hari-santri/staff/checkins`, `POST /bazaar/applications`, `GET /bazaar/me/applications`, `POST /donations`, `GET /donations/{reference_id}/status`.

**Contoh respons `GET /events/harlah-nu-101`:**

```json
{
  "slug": "harlah-nu-101",
  "name": "Harlah NU ke-101: Sepeda Santai & Jalan Sehat Keluarga",
  "tagline": "Dari Pesantren untuk Negeri",
  "starts_at": "2027-01-31T05:30:00+07:00",
  "timezone": "Asia/Jakarta",
  "venue_name": "Summarecon Crown Gading",
  "status": "published"
}
```

**Contoh respons `GET /history/entries`:**

```json
{
  "items": [
    {
      "slug": "resolusi-jihad",
      "date_label": "22 Oktober 1945",
      "date_sort": "1945-10-22",
      "title": "Resolusi Jihad",
      "summary": "Fatwa membela tanah air dirumuskan para ulama NU di Surabaya.",
      "is_illustration": false,
      "sources": [
        { "title": "Sejarah Hari Santri", "publisher": "NU Online", "url": "https://nu.or.id/fragmen/sejarah-hari-santri-XE9hw" }
      ]
    }
  ]
}
```

**Kode galat standar:** `400` validasi, `401/403` auth, `404`, `409` konflik (mis. kuota habis), `422` skema, `429` rate limit dengan header `Retry-After`. Tubuh galat: `{"code":"string","message":"id-ID text","fields":{...}}`.

## 8. Frontend: halaman, komponen, token

### 8.1 Rute

| Route | Konten | Prioritas |
|---|---|---|
| `/` | Beranda (hero, hitung mundur, dua kegiatan, nilai, agenda, bazar, galeri, FAQ) | MVP |
| `/sejarah-nu` | Linimasa, pesantren, Resolusi Jihad | MVP |
| `/mitra` | Paket sponsor dan formulir | MVP |
| `/media-kit` | Aset dan twibbon | P1 |
| `/daftar`, `/auth/*`, `/dashboard/*` | Pendaftaran, akun, tiket, voucher | MVP |
| `/donasi`, `/pembayaran/hasil` | Donasi dan hasil bayar | P1 / MVP |
| `/daftar-tenant`, `/exhibitor/scanner` | Bazar | P1 |
| `/staff/scan` | Check-in | MVP |
| `/admin/*` | CMS, voucher, audit, operasi, sponsor, moderasi | MVP |
| `/privacy`, `/terms`, `/code-of-conduct`, `/refund-policy` | Legal Harlah NU | MVP |

### 8.2 Komponen

`CountdownBlock`, `TimelineEntry`, `FactCard`, `SourceList`, `IllustrationLabel`, `SponsorTierStrip`, `SponsorLeadForm`, `PackageCard`, `AgendaList`, `GalleryGrid`, `FamilyEntryForm`, `TwibbonCanvas`, `AnnouncementBanner`, `StatusBadge` (pemetaan status pesanan), `RegistrationCta` (tombol dinamis, lihat HARI_SANTRI_FRONTEND).

### 8.3 Token desain (dari mockup tema)

```css
:root{
  --nu-deep:#0A4A28;    /* hero, judul, footer */
  --nu-green:#13803E;   /* ikon, hover tautan */
  --nu-gold:#F2B01E;    /* tombol utama di atas hijau tua */
  --nu-sunset:#B04F08;  /* label kecil di atas putih (kontras lolos) */
  --nu-leaf:#7DBF45;    /* garis tepi di atas hijau tua */
  --nu-mist:#F1F7EE;    /* latar kartu */
  --nu-sprout:#D6EBD0;  /* teks sekunder di atas hijau tua */
  --nu-ink:#12261A;     /* teks isi */
}
/* Oranye terang #F27B21 hanya untuk dekorasi besar, bukan teks kecil. */
```
Font: Lora (judul), Plus Jakarta Sans (isi).

### 8.4 Aturan UI

- Status akun/pesanan dibaca ulang saat login, navigasi, fokus tab, dan setelah penyimpanan peserta/tenant (tetap seperti Hari Santri).
- Kegagalan API = peringatan + tombol coba lagi, **bukan** "belum mendaftar".
- Tombol bayar tidak menerima klik paralel; tidak membuat order baru otomatis.
- Gambar ilustrasi wajib label "Ilustrasi, bukan foto dokumentasi".

## 9. Keamanan, privasi, aksesibilitas

**Keamanan**
- Secret Portal Payment hanya di backend. Callback diverifikasi HMAC; outbox dengan dead-letter dan jadwal ulang admin.
- Idempotency key per order untuk checkout; `request_id` untuk donasi.
- Rate limit scan dan formulir publik di PostgreSQL (bersama lintas worker).
- Validasi unggahan (tipe MIME, ukuran, pemindaian), penyimpanan di bucket privat, tautan unduh bertanda tangan.
- QR hanya memuat token opaque bertanda tangan; scanner tetap online dan menolak pemakaian ulang.
- Log audit untuk perubahan konten, moderasi, voucher, dan rekonsiliasi.

**Privasi**
- Minimalkan data: email, HP, nama peserta, tanggal lahir bila diperlukan, wali anak.
- Foto anak hanya dengan `consent_guardian`; opsi tarik entri dan hapus.
- Halaman privasi Harlah NU disahkan panitia sebelum rilis (backlog warisan).
- Retensi: tetapkan masa simpan data peserta dan unggahan setelah acara; jadwalkan penghapusan.

**Aksesibilitas (WCAG 2.2 AA sebagai target)**
- Kontras teks ≥ 4,5:1 (token di 8.3 sudah dipilih untuk itu), target sentuh ≥ 44 px.
- Semua gambar punya `alt`; ilustrasi diberi keterangan.
- Navigasi keyboard penuh (menu dropdown, dialog, formulir); fokus terlihat.
- `prefers-reduced-motion` mematikan animasi linimasa dan hitung mundur.
- Uji dengan pembaca layar pada beranda, `/sejarah-nu`, `/daftar`, dan `/pembayaran/hasil`.

## 10. Rencana pengujian

**Skrip otomatis (yang sudah ada)**
`node scripts/test-hari-santri-registration.mjs`, `node scripts/test-checkout-recovery.mjs`, `node scripts/test-donation-payment.mjs`, `npx vue-tsc --noEmit`, `npm run build`. Ganti nama skrip dan fixture ke penamaan netral saat migrasi (mis. `test-event-registration.mjs`).

**Tambahan**

| Area | Uji |
|---|---|
| CMS | Item `is_confirmed=false` tidak tampil sebagai janji; draft tidak bocor ke publik |
| Sejarah | Entri tanpa sumber tidak bisa terbit; `date_label` teks tampil benar |
| Hitung mundur | Zona waktu, koreksi jam perangkat, state sebelum/saat/sesudah acara |
| Mitra | Honeypot/rate limit, email ke panitia, hanya `confirmed` tampil |
| Keluarga Favorit | Hanya order lunas; consent wajib; moderasi; tarik entri |
| Twibbon | Foto tidak terkirim ke jaringan (cek tab Network) |
| Redirect domain | 301 dari domain lama, tiket lama tetap terbuka, callback lama tidak rusak |
| Pembayaran sandbox | PAID, pending/expired, UNKNOWN lalu resume, callback terlambat/duplikat, `paid_needs_review`, refresh halaman hasil |
| Beban | Lonjakan pendaftaran saat pengumuman; stok ukuran kaos tidak oversell; double-click |
| Mobile | Viewport 360–430 px; layar kecil untuk scanner; koneksi lambat |

## 11. Peta jalan

| Tahap | Isi | Prasyarat |
|---|---|---|
| 0. Keputusan panitia | Bagian 15 naskah konten, paket, kebijakan data lama | Rapat panitia |
| 1. Migrasi (MVP) | Bagian 3, F-01, F-02, F-03, F-09, F-10, legal | Domain, Payment Portal sandbox |
| 2. UAT | Bagian 10, perbaikan, konten final | Fixture harga/kuota disetujui |
| 3. Rilis pendaftaran | Produksi, redirect, monitoring | Lulus UAT |
| 4. Pra-acara (P1) | F-04, F-05, F-06, F-11, bazar, voucher | Rute disahkan, tenant terseleksi |
| 5. Hari-H | Check-in, scanner voucher, tim rekonsiliasi siaga | Latihan lapangan, kuota tiket |
| 6. Pasca-acara (P2) | F-07 sertifikat, laporan, galeri foto asli, penghapusan data sesuai retensi | Data check-in |

## 12. Risiko dan keputusan terbuka

| Risiko | Mitigasi |
|---|---|
| Waktu persiapan pendek (target 31 Januari 2027) | Kunci MVP dulu; tunda F-07/F-08 |
| Data lama Hari Santri tercampur | Event baru terpisah; keputusan migrasi/refund tertulis |
| Isi sejarah salah atau diperdebatkan | Verifikasi pengurus/lembaga sejarah NU; sumber wajib per entri |
| Foto/nama tokoh tanpa izin | Gerbang `consent` di model data |
| Logo NU resmi belum ada | Pakai placeholder berlabel; ganti sebelum produksi cetak/tayang |
| Callback pembayaran terlambat | Rekonsiliasi admin + `paid_needs_review` |
| Lonjakan trafik | Cache konten CMS (CDN), halaman statis, rate limit |

**Keputusan panitia yang masih terbuka:** harga dan isi paket; paket jalan sehat per orang atau keluarga; aturan anak; kuota dan stok kaos; tenggat bayar dan ubah ukuran; rute; jam dan pengisi acara; hadiah dan mekanisme Keluarga Favorit; kebijakan voucher; biaya tenant; refund; nasib data lama; nominal sponsor tampil publik atau tidak.

## 13. Definisi selesai (rilis MVP)

- Harga di server sesuai konfigurasi; satu order tidak menghasilkan pembayaran atau tiket ganda.
- Tiket terbit hanya setelah status pembayaran valid dari server.
- Ukuran kaos per peserta tercatat; laporan stok cocok dengan pesanan.
- Admin dapat memutakhirkan agenda, hadiah, sejarah, FAQ, sponsor tanpa deploy.
- Tidak ada string "Hari Santri 2026", "15 November 2026", atau "HeroFunWalk" tersisa di build (periksa dengan `grep` pada hasil `dist`).
- `vue-tsc` dan build lulus; smoke test desktop/mobile lulus.
- Domain lama mengalihkan dengan 301; callback dan tiket lama diuji.

## 14. Lampiran: peta pekerjaan ke repo

| Repo | Pekerjaan |
|---|---|
| `fastapi-hari-santri` (atau fork `fastapi-harlah-nu`) | Event baru, entitas CMS (bagian 5), endpoint bagian 7, migrasi Alembic, worker, konfigurasi CORS/Portal |
| `nuxt-hari-santri` (atau fork `nuxt-harlah-nu`) | Rute dan komponen bagian 8, string/konten, token desain, SEO, redirect |
| Payment Portal | Client/service code baru, return dan callback URL domain baru |
| Infrastruktur | DNS `harlahnu.com`, TLS, redirect 301, CDN, backup, monitoring |

Disarankan **fork** untuk repo kedua agar rilis Hari Santri lama tidak terganggu, dengan modul bersama (pembayaran, tiket, voucher) dipertahankan sinkron lewat paket atau cherry-pick yang terdokumentasi.
