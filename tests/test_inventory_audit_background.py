from odoo.tests import tagged

from . import test_inventory_audit_refresh as refresh
from . import test_inventory_audit_views as views
from .test_inventory_audit_calculation import InventoryAuditCase


@tagged("post_install", "-at_install")
class TestInventoryAuditBackground(InventoryAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        background = cls.device(cls, "BACKGROUND-NO-LOCATION", location_id=False)
        names = [
            "device_uid",
            "current_product_id",
            "location_id",
            "state",
            "write_date",
        ]
        cls.background_device = background
        before = background.read(names, load=None)

        def check_background():
            background.invalidate_recordset(names)
            if background.exists().read(names, load=None) != before:
                raise AssertionError("Unlocated background Device was changed")

        cls.addClassCleanup(check_background)

    # Exercise the same contracts with an additional ambiguous-company Device.
    _finish_pending_one_at_time = (
        refresh.TestInventoryAuditRefresh._finish_pending_one_at_time
    )
    test_bounded_cron = (
        refresh.TestInventoryAuditRefresh.test_bounded_cron_discovery_and_idempotence
    )
    test_full_refresh = refresh.TestInventoryAuditRefresh.test_full_refresh_does_not_repeat_groups_for_initially_ungrouped_devices
    test_incomplete_devices = refresh.TestInventoryAuditRefresh.test_pending_incomplete_devices_are_bounded_and_finish

    def test_dashboard(self):
        views.TestInventoryAuditViews.test_dashboard_counts_and_navigation(self)
        result = self.result(self.background_device)
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.pending)
        self.assertIn("Location is missing", result.details)
