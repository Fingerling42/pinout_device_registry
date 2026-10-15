from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


class InventoryAuditCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["pinout.device.type"].search([]).write({"active": False})
        cls.config = cls.env["pinout.inventory.audit.config"].sudo().create({})
        cls.locations = cls.env["stock.location"].create(
            [
                {"name": name, "usage": "internal", "company_id": cls.env.company.id}
                for name in ("Calculation A", "Calculation B", "Outside")
            ]
        )
        cls.config.scope_ids = [
            fields.Command.create({"location_id": loc.id}) for loc in cls.locations[:2]
        ]
        cls.products = cls.env["product.product"].create(
            [
                {
                    "name": "Calculation " + tracking,
                    "type": "product",
                    "tracking": tracking,
                }
                for tracking in ("none", "serial", "lot")
            ]
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Calculation",
                "code": "CALCULATION",
                "allowed_product_template_ids": [
                    fields.Command.set(cls.products.product_tmpl_id.ids)
                ],
            }
        )

    def device(self, name="CALC-1", product=None, location=None, **values):
        return self.env["pinout.device"].create(
            {
                "device_uid": name,
                "device_type_id": self.device_type.id,
                "current_product_id": (product or self.products[0]).id,
                "location_id": (location or self.locations[0]).id,
                **values,
            }
        )

    def quant(self, qty, product=None, location=None, lot=None, **values):
        return self.env["stock.quant"].create(
            {
                "product_id": (product or self.products[0]).id,
                "location_id": (location or self.locations[0]).id,
                "quantity": qty,
                "lot_id": lot.id if lot else False,
                **values,
            }
        )

    def serial(self, name="SERIAL-1"):
        device = self.device(name, self.products[1])
        lot = self.env["stock.lot"].create(
            {
                "name": name,
                "product_id": self.products[1].id,
                "company_id": self.env.company.id,
                "pinout_device_id": device.id,
            }
        )
        device.final_lot_id = lot
        return device, lot

    def run_audit(self):
        self.config._recalculate()
        return (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)])
        )

    def result(self, device):
        return (
            self.env["pinout.inventory.audit.device"]
            .sudo()
            .search([("device_id", "=", device.id)])
        )


@tagged("post_install", "-at_install")
class TestInventoryAuditCalculation(InventoryAuditCase):
    def test_untracked_shortage_reserved_and_surplus(self):
        devices = self.device() | self.device("CALC-2", state="reserved")
        quant = self.quant(1, reserved_quantity=1)
        self.run_audit()
        self.assertEqual(self.result(devices[0]).status, "group_shortage")
        self.assertEqual(self.result(devices[1]).status, "group_shortage")
        quant.quantity = 3
        group = self.run_audit()
        self.assertEqual(group.expected_quantity, 2)
        self.assertEqual(group.actual_quantity, 3)
        self.assertEqual(group.surplus_quantity, 1)
        self.assertEqual(self.result(devices[0]).status, "match")
        self.assertIn("does not prove", self.result(devices[0]).details)

    def test_exact_locations_negative_and_zero_groups(self):
        device = self.device()
        self.quant(10, location=self.locations[1])
        quant = self.quant(-2)
        groups = self.run_audit()
        group = groups.filtered(lambda g: g.location_id == self.locations[0])
        self.assertEqual(group.shortage_quantity, 3)
        self.assertEqual(group.actual_quantity, -2)
        quant.unlink()
        device.unlink()
        groups = self.run_audit()
        self.assertEqual(group.actual_quantity, 0)
        self.assertEqual(group.expected_quantity, 0)
        self.assertEqual(group.status, "match")
        self.assertEqual(
            groups.filtered(
                lambda g: g.location_id == self.locations[1]
            ).surplus_quantity,
            10,
        )

    def test_packages_owners_rounding_and_company(self):
        uom = self.products[0].uom_id.copy(
            {
                "name": "Calculation tenths",
                "rounding": 0.1,
                "uom_type": "bigger",
                "factor_inv": 1,
            }
        )
        self.products[0].write({"uom_id": uom.id, "uom_po_id": uom.id})
        self.device()
        package = self.env["stock.quant.package"].create({})
        owner = self.env["res.partner"].create({"name": "Quant Owner"})
        self.quant(0.4, package_id=package.id)
        self.quant(0.59, owner_id=owner.id)
        other = self.env["res.company"].create({"name": "Calculation Other"})
        foreign = self.env["stock.location"].create(
            {"name": "Foreign", "usage": "internal", "company_id": other.id}
        )
        self.quant(100, location=foreign)
        group = self.run_audit()
        self.assertAlmostEqual(group.actual_quantity, 0.99)
        self.assertEqual(group.status, "match")
        self.assertFalse(group.shortage_quantity)

    def test_serial_exact_match_and_other_location_equal_totals(self):
        device, lot = self.serial()
        quant = self.quant(1, self.products[1], lot=lot)
        self.run_audit()
        self.assertEqual(self.result(device).status, "match")
        quant.location_id = self.locations[2]
        other_lot = lot.copy({"name": "UNREGISTERED", "pinout_device_id": False})
        self.quant(1, self.products[1], lot=other_lot)
        group = self.run_audit()
        self.assertEqual(group.expected_quantity, group.actual_quantity)
        self.assertEqual(group.exact_mismatch_count, 1)
        self.assertEqual(group.status, "exact_mismatch")
        self.assertIn("Outside", self.result(device).details)
        self.assertEqual(group.surplus_quantity, 0)

    def test_serial_missing_duplicate_quantity_and_history(self):
        device, lot = self.serial()
        self.run_audit()
        self.assertEqual(self.result(device).status, "exact_mismatch")
        quant = self.quant(2, self.products[1], lot=lot)
        self.run_audit()
        self.assertEqual(self.result(device).status, "exact_mismatch")
        quant.quantity = 1
        self.quant(1, self.products[1], location=self.locations[2], lot=lot)
        self.run_audit()
        self.assertEqual(self.result(device).status, "exact_mismatch")
        device.final_lot_id = False
        group = self.run_audit()
        self.assertEqual(self.result(device).status, "not_checked")
        self.assertFalse(group.surplus_quantity)

    def test_excluded_incomplete_and_lot_tracking(self):
        devices = [
            self.device("EX-" + str(i), **values)
            for i, values in enumerate(
                [
                    {"state": "sold"},
                    {"state": "scrapped"},
                    {"active": False},
                    {"current_product_id": False},
                    {"location_id": False},
                ]
            )
        ]
        devices.append(self.device("OUTSIDE", location=self.locations[2]))
        devices.append(self.device("LOT", product=self.products[2]))
        devices.append(self.device("NO-SERIAL", product=self.products[1]))
        self.run_audit()
        for device in devices:
            result = self.result(device)
            self.assertEqual(result.status, "not_checked")
            self.assertTrue(result.details)
        self.assertFalse(self.result(devices[0]).group_id)

    def test_identity_conflicts_are_visible(self):
        device, lot = self.serial()
        self.quant(1, self.products[1], lot=lot)
        # Legacy corruption cannot be produced through today's identity guards.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE stock_lot SET product_id = %s WHERE id = %s",
            [self.products[0].id, lot.id],
        )
        lot.invalidate_recordset()
        self.run_audit()
        self.assertEqual(self.result(device).status, "exact_mismatch")
        self.assertIn("Product differs", self.result(device).details)
        other = self.env["res.company"].create({"name": "Conflict"})
        self.env.cr.execute(
            "UPDATE stock_lot SET company_id = %s WHERE id = %s", [other.id, lot.id]
        )
        lot.invalidate_recordset()
        self.run_audit()
        self.assertEqual(self.result(device).status, "not_checked")
        self.assertIn("company conflicts", self.result(device).details)

    def test_read_only_idempotent_atomic_and_access(self):
        device = self.device()
        quant = self.quant(1)
        before = (device.read(), quant.read())
        count = self.env["pinout.inventory.audit.group"].sudo().search_count([])
        self.config._calculate()
        self.assertEqual(
            self.env["pinout.inventory.audit.group"].sudo().search_count([]), count
        )
        group = self.run_audit()
        self.run_audit()
        self.assertEqual(self.run_audit(), group)
        self.assertEqual((device.read(), quant.read()), before)
        quant.quantity = 0
        model = type(self.env["pinout.inventory.audit.group"])
        with patch.object(
            model, "_store_success", side_effect=ValueError("store failed")
        ):
            with self.assertRaisesRegex(ValueError, "store failed"):
                self.run_audit()
        self.assertEqual(group.status, "match")
        self.assertEqual(self.result(device).status, "match")
        with self.assertRaises(AccessError):
            self.config.with_user(self.env.ref("base.user_admin"))._calculate()

    def test_serial_stock_without_devices_and_uncertain_stock(self):
        lot = self.env["stock.lot"].create(
            {
                "name": "NEW-SERIAL",
                "product_id": self.products[1].id,
                "company_id": self.env.company.id,
            }
        )
        quant = self.quant(1, self.products[1], lot=lot)
        group = self.run_audit()
        self.assertEqual(group.expected_quantity, 0)
        self.assertEqual(group.surplus_quantity, 1)
        quant.quantity = 2
        self.run_audit()
        self.assertEqual(group.status, "not_checked")
        self.assertFalse(group.surplus_quantity)
        quant.unlink()
        self.quant(1, self.products[1])
        self.run_audit()
        self.assertEqual(group.status, "not_checked")
        self.assertFalse(group.surplus_quantity)

    def test_historical_serial_is_not_current_or_unregistered(self):
        device, lot = self.serial()
        self.quant(1, self.products[1], lot=lot)
        device.write({"final_lot_id": False, "current_product_id": self.products[0].id})
        groups = self.run_audit()
        serial_group = groups.filtered(lambda g: g.product_id == self.products[1])
        self.assertEqual(serial_group.expected_quantity, 0)
        self.assertEqual(serial_group.actual_quantity, 1)
        self.assertFalse(serial_group.surplus_quantity)
        self.assertEqual(self.result(device).status, "group_shortage")

    def test_moving_and_excluding_device_clears_previous_result(self):
        device = self.device()
        self.quant(1)
        old_group = self.run_audit()
        device.location_id = self.locations[1]
        self.run_audit()
        self.assertEqual(old_group.expected_quantity, 0)
        self.assertEqual(self.result(device).location_id, self.locations[1])
        self.assertEqual(self.result(device).status, "group_shortage")
        device.active = False
        self.run_audit()
        self.assertFalse(self.result(device).group_id)
        self.assertEqual(self.result(device).status, "not_checked")
        shared = self.env["stock.location"].create(
            {"name": "Uncertain Company", "usage": "internal", "company_id": False}
        )
        device.write({"active": True, "location_id": shared.id})
        self.run_audit()
        self.assertIn("no company", self.result(device).details)

    def test_serial_diagnostics_protected_by_source_access(self):
        device, lot = self.serial()
        quant = self.quant(1, self.products[1], location=self.locations[2], lot=lot)
        group = self.run_audit()
        result = self.result(device)
        self.assertIn(quant, result.source_quant_ids)
        self.assertIn(self.locations[2], result.source_location_ids)
        operator = new_test_user(
            self.env,
            login="calculation_operator",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        self.assertEqual(result.with_user(operator).status, "exact_mismatch")
        self.env["ir.rule"].create(
            {
                "name": "Hide serial outside scope",
                "model_id": self.env["ir.model"]._get_id("stock.quant"),
                "domain_force": repr([("id", "!=", quant.id)]),
            }
        )
        for record in (group, result):
            self.assertNotIn(
                record.id, self.env[record._name].with_user(operator).search([]).ids
            )
            with self.assertRaises(AccessError):
                record.with_user(operator).read(["details"])
