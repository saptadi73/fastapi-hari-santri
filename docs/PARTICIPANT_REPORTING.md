# Kontrak Laporan Peserta Harlah NU

Dokumen ini menjelaskan kontrak laporan peserta yang dipakai backend `fastapi-hari-santri` dan halaman frontend `/admin/participants-report`. Semua endpoint menggunakan prefix `/api/v1` dan hanya dapat diakses admin.

## Endpoint

| Metode | Endpoint | Fungsi |
| --- | --- | --- |
| `GET` | `/admin/reports/participants` | Daftar peserta dengan pagination dan filter. |
| `GET` | `/admin/reports/participants.csv` | Ekspor data peserta memakai filter yang sama. |

Parameter daftar: `event_id`, `package_id`, `payment_status`, `search`, `profile_status`, `page`, dan `size`. Ekspor CSV menerima filter yang sama kecuali pagination.

## Tanggal masuk

Field resmi yang dikirim API:

- `registered_at`: waktu order/pendaftaran pertama milik akun.
- `created_at`: alias kompatibilitas untuk `registered_at`.
- `account_created_at`: waktu akun pengguna dibuat.

Jika akun belum memiliki order, `registered_at` dan `created_at` menggunakan `account_created_at`. Frontend harus membaca `registered_at` lebih dahulu dan menampilkan tanggal sesuai zona/locale Indonesia.

## Daerah asal

Wilayah bersumber dari kode profil akun pemesan dan tabel master `administrative_regions`. Backend mengembalikan:

- `village_code`, `village_name` untuk desa/kelurahan;
- `district_code`, `district_name` untuk kecamatan;
- `regency_code`, `regency_name` untuk kabupaten/kota;
- `province_code`, `province_name` untuk kebutuhan data lengkap;
- `origin_region` sebagai label siap tampil dengan urutan `desa/kelurahan, kecamatan, kabupaten/kota`.

Contoh `origin_region`: `Setia Asih, Tarumajaya, KAB. BEKASI`.

Nilai `country` seperti `Indonesia` bukan daerah asal dan tidak boleh dipakai sebagai fallback UI. Bila ketiga nama wilayah belum tersedia, frontend menampilkan **Belum tersedia**. Kode wilayah hanya menjadi fallback diagnostik untuk klien lama; UI utama memakai nama dari backend.

## CSV

Ekspor CSV menyertakan field tanggal dan wilayah yang sama: `created_at`, `registered_at`, `account_created_at`, `origin_region`, serta pasangan kode/nama desa, kecamatan, kabupaten/kota, dan provinsi. Setiap paket/order peserta tetap diekspor sebagai baris terpisah.

## Aturan implementasi

- Resolusi nama wilayah dilakukan secara batch agar tidak menimbulkan query per peserta.
- Order diurutkan dari `created_at` paling awal; order pertama menjadi tanggal masuk.
- Data pembayaran tetap berasal dari callback atau rekonsiliasi server, bukan return URL browser.
- Perubahan label master wilayah saat ini akan terlihat pada laporan. Snapshot nama wilayah historis tetap menjadi backlog terpisah.

## Verifikasi

```powershell
python -m pytest -q tests/test_participant_reporting.py tests/test_hari_santri_reporting.py
```

Uji penerimaan frontend:

1. Kolom **Tanggal masuk** tidak menampilkan “Belum tersedia” jika akun atau order memiliki tanggal.
2. Kolom **Daerah asal** berurutan desa/kelurahan, kecamatan, kabupaten/kota.
3. Negara tidak tampil sebagai daerah asal.
4. CSV memuat tanggal dan wilayah yang sama dengan tabel.
5. Pagination dan pencarian tidak mengubah definisi field.
