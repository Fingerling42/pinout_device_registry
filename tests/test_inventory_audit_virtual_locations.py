import logging

from odoo import Command
from odoo.tests import tagged

from .test_inventory_audit_calculation import InventoryAuditCase

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestInventoryAuditVirtualLocations(InventoryAuditCase):
    """Standard virtual balances do not hide physical discrepancies."""

    def check_virtual_balance(self, device, virtual):
        quants = self.env["stock.quant"].search(
            [("lot_id", "=", device.final_lot_id.id)]
        )
        balances = {}
        for quant in quants:
            key = (quant.location_id, quant.company_id, quant.product_id)
            balances[key] = balances.get(key, 0) + quant.quantity
        self.assertEqual(
            {key: qty for key, qty in balances.items() if qty},
            {
                (device.location_id, self.env.company, device.current_product_id): 1,
                (virtual, self.env.company, device.current_product_id): -1,
            },
        )
        self.config.action_refresh()
        result = self.result(device)
        _logger.info(
            "M0 virtual balance: usage=%s; balances=%s; status=%s; details=%s",
            virtual.usage,
            [
                (loc.display_name, loc.usage, qty)
                for (loc, _, _), qty in balances.items()
            ],
            result.status,
            result.details,
        )
        self.assertEqual(result.status, "match")
        self.assertIn("Virtual operation balance", result.details)
        self.assertIn(virtual.display_name, result.details)
        physical = quants.filtered(lambda q: q.location_id != virtual)
        internal_negative = self.quant(
            -1, device.current_product_id, self.locations[2], device.final_lot_id
        )
        for qty in (-1, 1):
            internal_negative.quantity = qty
            self.config.action_refresh()
            self.assertEqual(self.result(device).status, "exact_mismatch")
            self.assertIn(self.locations[2].display_name, self.result(device).details)
        internal_negative.quantity = 0
        physical.location_id = self.locations[1]
        self.config.action_refresh()
        self.assertEqual(self.result(device).status, "exact_mismatch")

    def test_virtual_rule_boundaries_and_rounding(self):
        device, lot = self.serial("VIRTUAL-BOUNDARIES")
        self.quant(1, self.products[1], lot=lot)
        virtual = self.env["stock.location"].create(
            {"name": "Boundary virtual", "usage": "production"}
        )
        quant = self.quant(-1, self.products[1], virtual, lot)
        for usage, scrap, qty, status in [
            ("production", False, -1, "match"),
            ("inventory", False, -1, "match"),
            ("supplier", False, -1, "match"),
            ("customer", False, -1, "match"),
            ("customer", False, 1, "exact_mismatch"),
            ("internal", False, -1, "exact_mismatch"),
            ("transit", False, -1, "exact_mismatch"),
            ("inventory", True, -1, "exact_mismatch"),
            ("inventory", False, 0.001, "match"),
        ]:
            with self.subTest(usage=usage, scrap=scrap, qty=qty):
                virtual.write({"usage": usage, "scrap_location": scrap})
                quant.quantity = qty
                self.config.action_refresh()
                self.assertEqual(self.result(device).status, status)
        virtual.write({"usage": "production", "scrap_location": False})
        quant.quantity = -1
        # Corrupted source keys cannot be produced through normal serial ORM
        # constraints. Keep the real quant/search/access path for diagnosis.
        other = self.env["res.company"].create({"name": "Virtual foreign company"})
        for column, value in [
            ("product_id", self.products[0].id),
            ("company_id", other.id),
        ]:
            with self.subTest(column=column):
                original = quant[column].id
                self.env.flush_all()
                self.env.cr.execute(
                    f"UPDATE stock_quant SET {column} = %s WHERE id = %s",
                    (value, quant.id),
                )
                quant.invalidate_recordset([column])
                self.config.action_refresh()
                self.assertEqual(self.result(device).status, "exact_mismatch")
                self.env.cr.execute(
                    f"UPDATE stock_quant SET {column} = %s WHERE id = %s",
                    (original, quant.id),
                )
                quant.invalidate_recordset([column])

    def test_manufacturing_balance(self):
        product = self.products[1]
        device = self.device("VIRTUAL-MO")
        source_quant = (
            self.env["stock.quant"]
            .with_context(inventory_mode=True)
            .create(
                {
                    "product_id": self.products[0].id,
                    "location_id": self.locations[0].id,
                    "inventory_quantity": 1,
                }
            )
        )
        source_quant.action_apply_inventory()
        bom = self.env["mrp.bom"].create(
            {
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_id": product.id,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {"product_id": self.products[0].id, "product_qty": 1}
                    )
                ],
            }
        )
        production = self.env["mrp.production"].create(
            {
                "product_id": product.id,
                "product_qty": 1,
                "product_uom_id": product.uom_id.id,
                "bom_id": bom.id,
                "location_src_id": self.locations[0].id,
                "location_dest_id": self.locations[0].id,
                "pinout_device_ids": [Command.set(device.ids)],
            }
        )
        production.action_confirm()
        production.button_mark_done()
        self.assertEqual(production.state, "done")
        self.assertEqual(device.final_lot_id, production.lot_producing_id)
        self.assertEqual(device.current_product_id, product)
        finished = production.move_finished_ids.filtered(
            lambda m: m.product_id == product
        )
        self.assertEqual(finished.state, "done")
        self.assertEqual(finished.location_id.usage, "production")
        self.check_virtual_balance(device, finished.location_id)

    def test_receipt_balance(self):
        self.receive_serial("supplier")

    def test_customer_return_balance(self):
        self.receive_serial("customer")

    def receive_serial(self, usage):
        device, lot = self.serial("VIRTUAL-RECEIPT-" + usage)
        supplier = self.env["stock.location"].create(
            {"name": "Virtual receipt source", "usage": usage}
        )
        move = self.env["stock.move"].create(
            {
                "name": "Virtual serial receipt",
                "product_id": self.products[1].id,
                "product_uom_qty": 1,
                "product_uom": self.products[1].uom_id.id,
                "location_id": supplier.id,
                "location_dest_id": device.location_id.id,
            }
        )
        move._action_confirm()
        move.quantity = 1
        move.move_line_ids.lot_id = lot
        move.picked = True
        move._action_done()
        self.assertEqual(move.state, "done")
        self.check_virtual_balance(device, supplier)

    def test_inventory_adjustment_balance(self):
        device, lot = self.serial("VIRTUAL-ADJUSTMENT")
        quant = (
            self.env["stock.quant"]
            .with_context(inventory_mode=True)
            .create(
                {
                    "product_id": self.products[1].id,
                    "location_id": device.location_id.id,
                    "lot_id": lot.id,
                    "inventory_quantity": 1,
                }
            )
        )
        quant.action_apply_inventory()
        virtual = self.products[1].property_stock_inventory
        self.assertEqual(virtual.usage, "inventory")
        self.check_virtual_balance(device, virtual)
