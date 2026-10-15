from unittest.mock import patch
from uuid import uuid4

from odoo import SUPERUSER_ID, api, fields
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

    def test_concurrent_check_ignores_locked_unrelated_result(self):
        token = uuid4().hex
        with (
            self.registry.cursor() as setup_cr,
            patch.object(type(self.config), "_invalidate_scope", return_value=None),
        ):
            setup_cr.execute("SET LOCAL lock_timeout = '5s'")
            env = api.Environment(setup_cr, SUPERUSER_ID, {})
            device_type = env["pinout.device.type"].create(
                {"name": "Unrelated lock fixture", "code": f"UNRELATED-{token}"}
            )
            device = env["pinout.device"].create(
                {"device_uid": f"UNRELATED-{token}", "device_type_id": device_type.id}
            )
            result = env["pinout.inventory.audit.device"]._for_device(
                device, env.company
            )
            result._store_success(
                result.generation,
                {"status": "not_checked", "details": "Unrelated successful snapshot"},
            )
            result_id, device_id, type_id = result.id, device.id, device_type.id
            names = [
                "company_id",
                "status",
                "details",
                "pending",
                "generation",
                "checked_at",
                "write_date",
            ]
            before = result.read(names, load=None)
            setup_cr.commit()
        try:
            with self.registry.cursor() as holder_cr:
                holder_cr.execute(
                    "SELECT id FROM pinout_inventory_audit_device WHERE id = %s FOR UPDATE",
                    [result_id],
                )
                refresh.TestInventoryAuditRefresh.test_committed_source_change_cannot_publish_old_match(
                    self
                )
            with self.registry.cursor() as check_cr:
                env = api.Environment(check_cr, SUPERUSER_ID, {})
                self.assertEqual(
                    env["pinout.inventory.audit.device"]
                    .browse(result_id)
                    .read(names, load=None),
                    before,
                )
        finally:
            with (
                self.registry.cursor() as cleanup_cr,
                patch.object(type(self.config), "_invalidate_scope", return_value=None),
            ):
                cleanup_cr.execute("SET LOCAL lock_timeout = '5s'")
                env = api.Environment(cleanup_cr, SUPERUSER_ID, {})
                env["pinout.inventory.audit.device"].browse(result_id).unlink()
                env["pinout.device"].browse(device_id).unlink()
                env["pinout.device.type"].browse(type_id).unlink()
                cleanup_cr.commit()

    def test_concurrency_fixture_coexists_with_legacy_names(self):
        # Existing records are only read. Create missing legacy-name blockers
        # in this test transaction, without committing or cleaning other data.
        Company = self.env["res.company"].with_context(active_test=False)
        company = Company.search([("name", "=", "Concurrency fixture")])
        if not company:
            company = Company.create({"name": "Concurrency fixture"})
        Type = self.env["pinout.device.type"].with_context(active_test=False)
        device_type = Type.search([("code", "=", "AUDIT-CONCURRENCY")])
        if not device_type:
            device_type = Type.create(
                {"name": "Legacy concurrency type", "code": "AUDIT-CONCURRENCY"}
            )
        Device = self.env["pinout.device"].with_context(active_test=False)
        device = Device.search([("device_uid", "=", "AUDIT-CONCURRENCY-1")])
        if not device:
            device = self.device("AUDIT-CONCURRENCY-1")
        sources = [
            (company, ["name", "partner_id", "currency_id", "write_date"]),
            (
                device_type,
                ["code", "active", "allowed_product_template_ids", "write_date"],
            ),
            (device, ["device_uid", "device_type_id", "location_id", "write_date"]),
        ]
        before = [record.read(names, load=None) for record, names in sources]
        token = uuid4()
        with patch.object(refresh, "uuid4", return_value=token):
            refresh.TestInventoryAuditRefresh.test_committed_source_change_cannot_publish_old_match(
                self
            )
        for (record, names), snapshot in zip(sources, before, strict=True):
            record.invalidate_recordset(names)
            self.assertEqual(record.read(names, load=None), snapshot)

        # A new cursor sees committed leftovers that the main test snapshot
        # cannot see. Successful cleanup must remove every newly owned source.
        with self.registry.cursor() as check_cr:
            env = api.Environment(check_cr, SUPERUSER_ID, {})
            for model, field, value in (
                ("res.company", "name", f"Concurrency fixture {token.hex}"),
                ("res.partner", "name", f"Concurrency fixture {token.hex}"),
                ("stock.location", "name", f"Concurrency fixture {token.hex}"),
                ("product.template", "name", f"Concurrency fixture {token.hex}"),
                ("pinout.device.type", "code", f"AUDIT-CONCURRENCY-{token.hex}"),
                ("pinout.device", "device_uid", f"AUDIT-CONCURRENCY-1-{token.hex}"),
            ):
                self.assertFalse(
                    env[model]
                    .with_context(active_test=False)
                    .search_count([(field, "=", value)]),
                    model,
                )
