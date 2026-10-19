# Integrasi donasi dengan Portal Payment

Backend membuat referensi donasi terpisah dari order tiket dan mengirim nominal
IDR, identitas donatur, metadata tujuan donasi, service terkonfigurasi, serta
return URL ke Portal Payment. Request ID dipakai untuk lock/idempotency lokal
dan idempotency key portal. Respons diverifikasi terhadap referensi, nominal,
mata uang, event, dan service sebelum URL dikembalikan ke frontend.

Frontend mengalihkan pengguna ke URL tersebut; pilihan publik MIDTRANS dan
DOKU ditangani Portal Payment, dengan kode MIDTRANS_SNAP/DOKU_CHECKOUT tetap.
Callback donasi memperbarui donasi tanpa menerbitkan tiket. Query browser tidak
boleh dipakai untuk menandai donasi lunas.

Verifikasi 19 Oktober 2026: `python -m pytest tests/test_hari_santri.py -k donation -q`
berhasil (4 tes). Ini pengujian otomatis, bukan pembayaran donasi live.
Pembayaran donasi sampai lunas/callback sandbox masih perlu pengujian terpisah.
