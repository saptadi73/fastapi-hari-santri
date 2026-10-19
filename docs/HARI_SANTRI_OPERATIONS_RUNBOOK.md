# Hari Santri 2026 Operations Runbook

## Deploy and schedule

1. Take a PostgreSQL backup and deploy the application revision containing Alembic migration `202610070060`.
2. Run `python -m alembic upgrade head` and confirm `python -m alembic current` reports `202610070060 (head)`.
3. Configure `HARI_SANTRI_OUTBOX_MAX_ATTEMPTS` (default 8) and `HARI_SANTRI_OUTBOX_RETRY_BASE_SECONDS` (default 30).
4. Jalankan worker persisten `python -m scripts.hari_santri_worker --watch --interval-seconds 5 --batch-size 100` sebagai unit systemd `hari-santri-worker.service`. Mode `--watch` membuat siklus callback/expiry setiap 5 detik dan berhenti rapi saat SIGTERM. Jangan menjalankan scheduler lama bersamaan dengan unit ini.
5. Retain stdout/stderr in the central log system. Worker mencetak satu ringkasan JSON per siklus dengan `request_id`, jumlah expiry, ID mismatch stok, dan jumlah retry callback. Target operasional: callback Payment Portal → Event diproses maksimal sekitar 5 detik setelah callback masuk, di luar waktu jaringan/DB.

Mode `--once` tetap tersedia untuk pemeriksaan manual dan scheduler lama. Menjalankan worker berulang aman: expiry hanya meninggalkan order yang eligible secara transaksional, sedangkan callback dideduplikasi berdasarkan event ID dan payload hash. Jangan menjalankan expiry dari startup setiap proses API.

## Expired reservations

The worker finds Hari Santri orders whose reservation expiry has passed and whose order status is still draft, pending, or partially paid. It locks each order and its inventory, releases reserved shirt counts, expires the participants/payment/order, then writes an audit entry in the same transaction.

If inventory cannot cover the expected reservation, the worker leaves that size count unchanged, expires the order, writes `order_reservation_expiry_inventory_mismatch`, and emits an ERROR log with `request_id` and order ID. Reconcile inventory before editing stock. A later PAID callback for an already expired order is set to `paid_needs_review`; it must not consume stock reserved by another order or issue tickets automatically.

## Callback retry and alert handling

The callback endpoint verifies HMAC and timestamp before processing. If verified callback processing fails, it stores a sanitized payload in `hari_santri_outbox_events`, records a request ID and audit entry, and acknowledges receipt only after the outbox transaction commits. Invalid signatures are rejected and never enter the retry queue.

The scheduled worker retries transient database/runtime errors with exponential delay, up to the configured maximum. Permanent application conflicts go directly to dead-letter. Replayed callbacks remain idempotent through callback event ID and payload hash. Outbox payload intentionally omits unrelated customer data, request secrets, and raw callback body.

For a callback suspected missing or delayed, use **Rekonsiliasi callback terlambat** in `/admin/hari-santri-operations` and enter the order reference (no UUID required). The backend obtains an OAuth token and calls `GET /api/v1/client/payments/{payment_id}` directly. It validates the returned payment ID, service, reference, event, amount, currency, and status. Pending/created results are recorded but do not transition the order; terminal results flow through the same idempotent callback transition. A late PAID result after reservation release becomes `paid_needs_review` and does not issue tickets. If a create request timed out before the Portal payment ID was saved, retry checkout first with the same order-derived idempotency key, then reconcile.

Configure the central log alert to notify operations on these ERROR messages:

- `Verified Hari Santri callback processing failed`
- `Hari Santri callback reached dead letter`
- `Hari Santri reservation expiry inventory mismatch`

Use the request ID to correlate API logs, audit rows, and the worker summary. The admin UI at `/admin/hari-santri-operations` lists callback state without exposing its payload. Before manually retrying a dead-letter event, compare the event ID, amount, currency, reference, and current Payment Portal status. Then use **Jadwalkan ulang**. Inspect the audit entry `payment_callback_manually_requeued` afterward. If the callback still fails, stop retrying and reconcile the order/payment with the Payment Portal operator.

## Check-in access

Create or update the staff account in Admin → User & role management and assign `checkin_staff`; keep the account active. This role can call only the check-in endpoint among protected Hari Santri operations. `admin` and `organizer` retain check-in access. Each accepted and rejected scan is audited with actor and request ID; raw QR token is never stored in the audit payload.

If a staff account is lost or no longer assigned to the checkpoint, set it inactive or change its role. Do not share organizer/admin credentials with scanners.

## Routine checks

- Confirm the worker has a successful JSON summary at least once per minute during active registration/payment periods.
- Review `/admin/hari-santri-operations` for pending entries older than the retry interval and any dead-letter entry.
- Review `/admin/audit-vouchers` for expiry inventory mismatch and check-in rejection patterns.
- Verify the callback ERROR alert reaches the operations channel during staging drills; do not send synthetic callbacks to production.
- Escalate unresolved `paid_needs_review` and inventory mismatch cases to the payment/event operator. Record the decision and supporting request IDs in the incident record.
