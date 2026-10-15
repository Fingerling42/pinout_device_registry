from odoo import fields
from odoo.tests import tagged

from . import test_inventory_audit_background as background
from . import test_inventory_audit_refresh as refresh
from .test_inventory_audit_calculation import InventoryAuditCase


@tagged("post_install", "-at_install")
class TestInventoryAuditExistingConfig(InventoryAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Make another company's configuration older than our own, as on an
        # installation where audit was configured before these tests started.
        cls.config.unlink()
        cls.other_company = cls.env["res.company"].create(
            {"name": "Existing audit configuration"}
        )
        cls.other_config = cls.env["pinout.inventory.audit.config"].create(
            {"company_id": cls.other_company.id}
        )
        cls.other_location = cls.env["stock.location"].create(
            {
                "name": "Existing audit location",
                "usage": "internal",
                "company_id": cls.other_company.id,
            }
        )
        cls.other_config.scope_ids = [
            fields.Command.create({"location_id": cls.other_location.id})
        ]
        cls.other_group = cls.env["pinout.inventory.audit.group"]._get_or_create(
            cls.other_company, cls.products[0], cls.other_location
        )
        cls.config = cls.env["pinout.inventory.audit.config"].create(
            {
                "scope_ids": [
                    fields.Command.create({"location_id": location.id})
                    for location in cls.locations[:2]
                ]
            }
        )
        cls.background_device = cls.device(
            cls, "EXISTING-CONFIG-NO-LOCATION", location_id=False
        )

    _finish_pending_one_at_time = (
        refresh.TestInventoryAuditRefresh._finish_pending_one_at_time
    )
    test_incomplete_devices = refresh.TestInventoryAuditRefresh.test_pending_incomplete_devices_are_bounded_and_finish
    test_selected_device = (
        refresh.TestInventoryAuditRefresh.test_device_refresh_updates_entire_group_only
    )
    test_committed_source_change = refresh.TestInventoryAuditRefresh.test_committed_source_change_cannot_publish_old_match
    test_dashboard = background.TestInventoryAuditBackground.test_dashboard

    def test_unlocated_selected_check_keeps_its_audit_company(self):
        device = self.device("SELECTED-OWNER", location_id=False)
        before = self.other_group.read(load=None)
        device.action_refresh_inventory_audit()
        result = self.result(device)
        self.assertEqual(result.company_id, self.config.company_id)
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.pending)
        self.assertIn("Location is missing", result.details)
        self.assertEqual(self.other_group.read(load=None), before)
