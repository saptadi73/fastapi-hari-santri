# Melanjutkan pesanan Hari Santri

- Pembuatan order tetap dikunci per akun; order aktif tidak boleh diduplikasi.
- Frontend membaca pesanan dari server setelah login dan sebelum submit pendaftaran.
- Order dengan peserta tersimpan dilanjutkan melalui halaman Pesanan & Pembayaran.
- Order tanpa peserta dilengkapi di formulir yang memakai ID order lama.
- Jika status server gagal dimuat, frontend tidak membuat order baru.
- Checkout dengan payment ID yang sudah ada memanggil renew checkout, bukan create
  payment. Token sesi lama tidak dipakai ulang karena bisa kedaluwarsa lebih dahulu.
- Retry tanpa payment ID tetap memakai idempotency key berbasis ID order.
- Pesanan lunas menuju tiket; paid_needs_review menuju status pemeriksaan panitia.

Verifikasi regresi: `tests/test_checkout_resume.py` dan test kontrak Hari Santri.
Tidak ada migrasi database dan tidak ada pembayaran provider yang dibuat saat test.
