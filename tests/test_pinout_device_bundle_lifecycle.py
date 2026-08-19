from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBundleLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Bundle Lifecycle Customer"}
        )
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.customer_location = cls.env.ref("stock.stock_location_customers")
        cls.outgoing_type = cls.env["stock.picking.type"].search(
            [
                ("code", "=", "outgoing"),
                ("warehouse_id.company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        cls.incoming_type = cls.outgoing_type.return_picking_type_id
        cls.kit_product = cls.env["product.product"].create(
            {"name": "Bundle Lifecycle Kit", "type": "product"}
        )
        cls.components = cls.env["product.product"].create(
            [
                {
                    "name": "Bundle Lifecycle Urban",
                    "type": "product",
                    "tracking": "serial",
                },
                {
                    "name": "Bundle Lifecycle Insight",
                    "type": "product",
                    "tracking": "serial",
                },
            ]
        )
        cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.kit_product.product_tmpl_id.id,
                "product_id": cls.kit_product.id,
                "product_qty": 1.0,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create({"product_id": product.id, "product_qty": 1.0})
                    for product in cls.components
                ],
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Bundle Lifecycle Device Type",
                "code": "BUNDLE-LIFECYCLE-DEVICE",
                "allowed_product_template_ids": [
                    Command.set(cls.components.product_tmpl_id.ids)
                ],
            }
        )
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Bundle Lifecycle Type",
                "code": "BUNDLE-LIFECYCLE",
                "requires_kit_bom": True,
                "allowed_product_template_ids": [
                    Command.set(cls.kit_product.product_tmpl_id.ids)
                ],
            }
        )
        cls.lots = cls.env["stock.lot"].create(
            [
                {
                    "name": f"BUNDLE-LIFECYCLE-{index:03d}",
                    "product_id": product.id,
                    "company_id": cls.env.company.id,
                }
                for index, product in enumerate(cls.components, start=1)
            ]
        )
        for lot in cls.lots:
            cls.env["stock.quant"]._update_available_quantity(
                lot.product_id,
                cls.stock_location,
                1.0,
                lot_id=lot,
            )
        cls.bundle = cls.env["pinout.device.bundle"].create(
            {
                "name": "BUNDLE-LIFECYCLE-001",
                "bundle_type_id": cls.bundle_type.id,
                "bundle_product_id": cls.kit_product.id,
            }
        )
        cls.devices = cls.env["pinout.device"].create(
            [
                {
                    "device_uid": lot.name,
                    "device_type_id": cls.device_type.id,
                    "current_product_id": lot.product_id.id,
                    "final_lot_id": lot.id,
                    "state": "ready_for_sale",
                    "quality_status": "ok",
                    "bundle_id": cls.bundle.id,
                }
                for lot in cls.lots
            ]
        )

    def _create_sale_delivery(self):
        sale_order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.kit_product.id,
                            "product_uom_qty": 1.0,
                            "product_uom": self.kit_product.uom_id.id,
                            "price_unit": 100.0,
                        }
                    )
                ],
            }
        )
        sale_order.action_confirm()
        picking = sale_order.picking_ids.filtered(lambda item: item.state != "cancel")
        self.assertEqual(len(picking), 1)
        return sale_order, picking

    def _assign_bundle_lots(self, picking):
        picking.move_line_ids.unlink()
        devices_by_product = {
            device.current_product_id: device for device in self.devices
        }
        move_line_values = []
        for move in picking.move_ids.filtered(lambda item: item.state != "cancel"):
            device = devices_by_product[move.product_id]
            move_line_values.append(
                {
                    "move_id": move.id,
                    "picking_id": picking.id,
                    "product_id": move.product_id.id,
                    "product_uom_id": move.product_uom.id,
                    "quantity": 1.0,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "lot_id": device.final_lot_id.id,
                }
            )
        return self.env["stock.move.line"].create(move_line_values)

    def _complete_return(self, devices):
        picking = self.env["stock.picking"].create(
            {
                "partner_id": self.partner.id,
                "picking_type_id": self.incoming_type.id,
                "location_id": self.customer_location.id,
                "location_dest_id": self.stock_location.id,
            }
        )
        for device in devices:
            product = device.current_product_id
            self.env["stock.move"].create(
                {
                    "name": product.display_name,
                    "product_id": product.id,
                    "product_uom_qty": 1.0,
                    "product_uom": product.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": self.customer_location.id,
                    "location_dest_id": self.stock_location.id,
                    "picked": True,
                    "move_line_ids": [
                        Command.create(
                            {
                                "product_id": product.id,
                                "product_uom_id": product.uom_id.id,
                                "quantity": 1.0,
                                "picking_id": picking.id,
                                "location_id": self.customer_location.id,
                                "location_dest_id": self.stock_location.id,
                                "lot_id": device.final_lot_id.id,
                                "picked": True,
                            }
                        )
                    ],
                }
            )
        picking._action_done()
        return picking

    def test_ready_for_sale_requires_complete_qualified_devices(self):
        detached_device = self.devices[0]
        detached_device.bundle_id = False
        with self.assertRaisesRegex(ValidationError, "Expected Components"):
            self.bundle.action_mark_ready_for_sale()
        detached_device.bundle_id = self.bundle

        detached_device.final_lot_id = False
        with self.assertRaisesRegex(ValidationError, "Final Lot"):
            self.bundle.action_mark_ready_for_sale()
        detached_device.final_lot_id = self.lots[0]

        detached_device.quality_status = "unknown"
        with self.assertRaisesRegex(ValidationError, "Quality Status OK"):
            self.bundle.action_mark_ready_for_sale()
        detached_device.quality_status = "ok"

        detached_device.state = "wip"
        with self.assertRaisesRegex(ValidationError, "must be Ready for Sale"):
            self.bundle.action_mark_ready_for_sale()

    def test_ready_pairing_is_locked_until_reset_to_draft(self):
        self.bundle.action_mark_ready_for_sale()
        self.assertEqual(self.bundle.state, "ready_for_sale")

        with self.assertRaisesRegex(ValidationError, "only be changed while"):
            self.bundle.bundle_product_id = False
        with self.assertRaisesRegex(ValidationError, "lifecycle actions"):
            self.bundle.state = "sold"
        with self.assertRaisesRegex(ValidationError, "current bundle is in Draft"):
            self.devices[0].bundle_id = False

        self.bundle.action_reset_to_draft()
        self.assertEqual(self.bundle.state, "draft")
        self.devices[0].bundle_id = False
        self.assertFalse(self.devices[0].bundle_id)

    def test_manage_devices_shows_attached_and_compatible_available_devices(self):
        available = self.env["pinout.device"].create(
            {
                "device_uid": "BUNDLE-LIFECYCLE-AVAILABLE",
                "device_type_id": self.device_type.id,
                "current_product_id": self.components[0].id,
                "state": "ready_for_sale",
            }
        )
        blocked = self.env["pinout.device"].create(
            {
                "device_uid": "BUNDLE-LIFECYCLE-BLOCKED",
                "device_type_id": self.device_type.id,
                "current_product_id": self.components[0].id,
                "state": "sold",
            }
        )

        action = self.bundle.action_manage_devices()
        visible_devices = self.env["pinout.device"].search(action["domain"])

        self.assertEqual(action["res_model"], "pinout.device")
        self.assertTrue(self.devices <= visible_devices)
        self.assertIn(available, visible_devices)
        self.assertNotIn(blocked, visible_devices)

    def test_serial_assignment_reserves_and_unassign_restores_ready_state(self):
        self.bundle.action_mark_ready_for_sale()
        sale_order, picking = self._create_sale_delivery()
        move_lines = self._assign_bundle_lots(picking)

        self.assertEqual(self.bundle.state, "reserved")
        self.assertEqual(set(self.devices.mapped("state")), {"reserved"})
        self.assertEqual(self.bundle.reservation_delivery_id, picking)
        self.assertEqual(self.bundle.customer_id, self.partner)
        self.assertEqual(self.bundle.sale_order_id, sale_order)
        self.assertEqual(self.bundle.delivery_id, picking)

        move_lines.unlink()
        self.assertEqual(self.bundle.state, "ready_for_sale")
        self.assertEqual(set(self.devices.mapped("state")), {"ready_for_sale"})
        self.assertFalse(self.bundle.reservation_delivery_id)

        self._assign_bundle_lots(picking)
        self.assertEqual(self.bundle.state, "reserved")
        picking.action_cancel()
        self.assertEqual(self.bundle.state, "ready_for_sale")
        self.assertEqual(set(self.devices.mapped("state")), {"ready_for_sale"})

    def test_sale_partial_return_full_return_and_prepare_for_resale(self):
        self.bundle.action_mark_ready_for_sale()
        _sale_order, picking = self._create_sale_delivery()
        move_lines = self._assign_bundle_lots(picking)
        move_lines.write({"picked": True})
        picking.move_ids.write({"picked": True})
        picking._action_done()

        self.assertEqual(self.bundle.state, "sold")
        self.assertEqual(set(self.devices.mapped("state")), {"sold"})

        self._complete_return(self.devices[0])
        self.assertEqual(self.bundle.state, "partially_returned")

        self._complete_return(self.devices[1])
        self.assertEqual(self.bundle.state, "returned")
        self.assertEqual(set(self.devices.mapped("state")), {"returned"})

        self.devices.write({"state": "ready_for_sale", "quality_status": "ok"})
        self.bundle.action_prepare_for_resale()
        self.assertEqual(self.bundle.state, "ready_for_sale")
        self.assertEqual(self.bundle.device_ids, self.devices)

    def test_cancel_and_unpair_releases_devices_and_records_uids(self):
        self.bundle.action_mark_ready_for_sale()
        self.bundle.action_cancel_and_unpair()

        self.assertEqual(self.bundle.state, "cancelled")
        self.assertFalse(self.bundle.device_ids)
        self.assertFalse(self.devices.bundle_id)
        message_body = " ".join(self.bundle.message_ids.mapped("body"))
        for device in self.devices:
            self.assertIn(device.device_uid, message_body)
