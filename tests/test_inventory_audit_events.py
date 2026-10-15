from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from . import test_inventory_audit_calculation as calculation
from .common_inventory_audit import InventoryAuditCompanyCase


@tagged("post_install", "-at_install")
class TestInventoryAuditEvents(InventoryAuditCompanyCase):
    device = calculation.TestInventoryAuditCalculation.device
    quant = calculation.TestInventoryAuditCalculation.quant
    serial = calculation.TestInventoryAuditCalculation.serial
    run_audit = calculation.TestInventoryAuditCalculation.run_audit
    result = calculation.TestInventoryAuditCalculation.result

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pinout.inventory.audit.config"].sudo().create({})
        cls.locations = cls.env["stock.location"].create(
            [
                {"name": name, "usage": "internal", "company_id": cls.env.company.id}
                for name in ("Events A", "Events B", "Events Outside")
            ]
        )
        cls.config.scope_ids = [
            fields.Command.create({"location_id": loc.id}) for loc in cls.locations[:2]
        ]
        cls.products = cls.env["product.product"].create(
            [
                {"name": "Events " + tracking, "type": "product", "tracking": tracking}
                for tracking in ("none", "serial", "lot")
            ]
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Events",
                "code": "EVENTS",
                "allowed_product_template_ids": [
                    fields.Command.set(cls.products.product_tmpl_id.ids)
                ],
            }
        )

    def test_quant_old_new_and_last_unlink(self):
        device = self.device()
        quant = self.quant(1)
        group = self.run_audit()
        checked_at = group.checked_at
        quant.location_id = self.locations[1]
        groups = (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)])
        )
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(groups.mapped("pending")))
        self.assertTrue(self.result(device).pending)
        self.assertEqual(group.checked_at, checked_at)
        self.assertEqual(group.actual_quantity, 1)
        self.run_audit()
        quant.unlink()
        self.assertFalse(group.pending)
        self.assertTrue(
            groups.filtered(lambda g: g.location_id == self.locations[1]).pending
        )
        self.run_audit()
        self.assertFalse(any(groups.mapped("pending")))

    def test_reservation_and_notes_do_not_invalidate(self):
        device = self.device()
        quant = self.quant(2)
        group = self.run_audit()
        generation = group.generation
        quant.reserved_quantity = 1
        device.quality_status = "testing"
        device.message_post(body="Audit unrelated note")
        self.assertFalse(group.pending)
        self.assertFalse(self.result(device).pending)
        self.assertEqual(group.generation, generation)

    def test_device_move_form_archive_state_delete_and_first_registration(self):
        device = self.device()
        group = self.run_audit()
        for values in (
            {"location_id": self.locations[1].id},
            {"current_product_id": self.products[2].id},
            {"state": "sold"},
            {"state": "returned"},
            {"state": "scrapped"},
            {"active": False},
            {"active": True},
        ):
            old = self.result(device).group_id
            device.write(values)
            self.assertTrue(self.result(device).pending)
            if old:
                self.assertTrue(old.pending)
            self.run_audit()
        result = self.result(device)
        device.unlink()
        self.assertFalse(result.exists())
        new = self.device("NEW")
        self.assertTrue(self.result(new).pending)
        self.assertTrue(group.pending)

    def test_serial_outside_scope_and_historical_surplus(self):
        device, lot = self.serial()
        self.quant(1, self.products[1], lot=lot)
        group = self.run_audit()
        outside = self.quant(1, self.products[1], self.locations[2], lot)
        self.assertTrue(group.pending)
        self.assertTrue(self.result(device).pending)
        self.run_audit()
        outside.quantity = 2
        self.assertTrue(group.pending)
        self.run_audit()
        outside.unlink()
        self.assertTrue(group.pending)
        self.run_audit()
        device.final_lot_id = False
        self.assertTrue(group.pending)
        self.run_audit()
        lot.pinout_device_id = False
        self.assertTrue(group.pending)

    def test_tracking_uom_and_lot_metadata(self):
        self.quant(1)
        group = self.run_audit()
        self.products[0].tracking = "lot"
        self.assertTrue(group.pending)
        self.run_audit()
        self.products[0].uom_id.rounding = 0.1
        self.assertTrue(group.pending)
        self.run_audit()
        lot = self.env["stock.lot"].create(
            {
                "name": "METADATA",
                "product_id": self.products[0].id,
                "company_id": self.env.company.id,
            }
        )
        quant = self.quant(1, lot=lot)
        self.run_audit()
        lot.name = "RENAMED"
        self.assertTrue(group.pending)
        self.run_audit()
        quant.unlink()
        self.run_audit()
        lot.unlink()
        self.assertTrue(group.pending)

    def test_rollback_and_generation_rejects_stale_result(self):
        self.quant(1)
        group = self.run_audit()
        generation = group.generation
        with self.assertRaisesRegex(ValueError, "rollback"):
            with self.env.cr.savepoint():
                self.quant(2)
                self.assertTrue(group.pending)
                raise ValueError("rollback")
        self.assertFalse(group.pending)
        self.assertEqual(group.generation, generation)
        self.quant(2)
        self.assertGreater(group.generation, generation)
        self.assertFalse(group._store_success(generation, {"actual_quantity": 100}))
        self.assertTrue(group.pending)
        self.assertEqual(group.actual_quantity, 1)

    def test_batch_coalesces_and_never_calculates(self):
        model = type(self.config)
        with patch.object(
            model, "_calculate", side_effect=AssertionError("calculation in hook")
        ):
            devices = self.env["pinout.device"].create(
                [
                    {
                        "device_uid": "BATCH-%s" % i,
                        "device_type_id": self.device_type.id,
                        "current_product_id": self.products[0].id,
                        "location_id": self.locations[0].id,
                    }
                    for i in range(50)
                ]
            )
        groups = (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)])
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups.generation, 2)
        self.assertEqual(
            self.env["pinout.inventory.audit.device"]
            .sudo()
            .search_count([("company_id", "=", self.env.company.id)]),
            len(devices),
        )
        self.assertFalse(groups.checked_at)

    def test_stock_operations_invalidate(self):
        device = self.device()
        product = self.products[0]
        supplier = self.env.ref("stock.stock_location_suppliers")
        customer = self.env.ref("stock.stock_location_customers")
        production = self.env["stock.location"].create(
            {"name": "Events Production", "usage": "production"}
        )
        scrap = self.env["stock.location"].create(
            {"name": "Events Scrap", "usage": "inventory", "scrap_location": True}
        )
        group = self.run_audit()
        # Receipt, internal transfer, delivery, return, scrap, manufacturing output,
        # and unbuild consumption all reach the same standard stock.move path.
        for source, dest in (
            (supplier, self.locations[0]),
            (self.locations[0], self.locations[1]),
            (self.locations[1], self.locations[0]),
            (self.locations[0], customer),
            (customer, self.locations[0]),
            (self.locations[0], scrap),
            (production, self.locations[0]),
            (self.locations[0], production),
        ):
            move = self.env["stock.move"].create(
                {
                    "name": "Audit event",
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "product_uom": product.uom_id.id,
                    "location_id": source.id,
                    "location_dest_id": dest.id,
                }
            )
            move._action_confirm()
            move.quantity = 1
            move.picked = True
            move._action_done()
            self.assertTrue(group.pending)
            self.assertTrue(self.result(device).pending)
            self.run_audit()
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", self.locations[0].id),
            ],
            limit=1,
        )
        quant.with_context(inventory_mode=True).inventory_quantity = 5
        quant.with_context(inventory_mode=True).action_apply_inventory()
        self.assertTrue(group.pending)

    def test_quant_merge_preserves_successful_quantity(self):
        self.quant(1)
        self.quant(2)
        group = self.run_audit()
        self.env.flush_all()
        self.env["stock.quant"]._merge_quants()
        self.assertFalse(group.pending)
        self.assertEqual(self.run_audit().actual_quantity, 3)

    def test_quant_product_and_lot_replacement(self):
        device, lot = self.serial()
        quant = self.quant(1, self.products[1], lot=lot)
        old = self.run_audit()
        replacement = lot.copy({"name": "REPLACEMENT", "pinout_device_id": False})
        quant.lot_id = replacement
        self.assertTrue(old.pending)
        self.assertTrue(self.result(device).pending)
        self.run_audit()
        quant.write({"product_id": self.products[0].id, "lot_id": False})
        self.assertTrue(old.pending)
        new = (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search(
                [
                    ("product_id", "=", self.products[0].id),
                    ("location_id", "=", self.locations[0].id),
                ]
            )
        )
        self.assertTrue(new)
        self.assertTrue(new.pending)
        self.run_audit()
        self.assertEqual(old.actual_quantity, 0)
        self.assertEqual(new.actual_quantity, 1)

    def test_outside_untracked_and_foreign_stock_do_not_queue_groups(self):
        self.quant(2, location=self.locations[2])
        other = self.env["res.company"].create({"name": "Events Other"})
        foreign = self.env["stock.location"].create(
            {
                "name": "Events Foreign",
                "usage": "internal",
                "company_id": other.id,
            }
        )
        self.quant(2, location=foreign)
        self.assertFalse(
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)])
        )
        device = self.device(location=self.locations[2])
        self.run_audit()
        self.assertEqual(self.result(device).status, "not_checked")
        device.location_id = False
        self.assertTrue(self.result(device).pending)
        self.run_audit()
        self.assertIn("Location is missing", self.result(device).details)
