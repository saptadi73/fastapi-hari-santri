import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.hari_santri.service import HariSantriService


class HariSantriAuditLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_rows_include_actor_name_and_keep_payload(self):
        actor_id = uuid4()
        log = SimpleNamespace(
            id=uuid4(),
            actor_user_id=actor_id,
            action="voucher_scanned",
            entity_type="wallet_transfer",
            entity_id=uuid4(),
            payload={"amount": 25000, "error_code": None},
            created_at=datetime.now(timezone.utc),
        )
        result = SimpleNamespace(all=lambda: [(log, "Tenant Owner")])
        db = SimpleNamespace(execute=AsyncMock(return_value=result))

        rows = await HariSantriService.list_audit_logs(db, 50, "voucher_scanned")

        self.assertEqual("Tenant Owner", rows[0]["actor_name"])
        self.assertEqual(log.payload, rows[0]["payload"])
        self.assertEqual(log.id, rows[0]["id"])
        statement = str(db.execute.call_args.args[0])
        self.assertIn("hari_santri_audit_logs.action =", statement)


if __name__ == "__main__":
    unittest.main()
