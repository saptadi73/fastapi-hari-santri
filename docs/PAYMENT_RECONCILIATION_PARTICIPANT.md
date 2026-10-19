# Pemeriksaan pembayaran oleh peserta

Pada halaman hasil pembayaran, peserta dapat menekan **Periksa ke Payment
Portal** jika callback belum diterima backend event. Backend memverifikasi bahwa
order milik akun tersebut, membatasi inquiry tiga kali per menit, lalu melakukan
lookup server-to-server ke Payment Portal. Browser tidak mengirim status PAID
dan tidak dapat mengubah status melalui query URL.

Jika lookup tervalidasi `PAID`, transisi callback idempotent dijalankan dan tiket
QR diterbitkan. Jika masih `PENDING`, order tetap menunggu callback/payment;
peserta tidak membuat order baru.
