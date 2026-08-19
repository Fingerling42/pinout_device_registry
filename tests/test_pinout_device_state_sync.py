from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceStateSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.customer_location = cls.env.ref("stock.stock_location_customers")
        cls.internal_location = cls.env["stock.location"].create(
            {
                "name": "Device State Internal Location",
                "usage": "internal",
                "location_id": cls.stock_location.location_id.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.scrap_location = cls.env["stock.location"].search(
            [("scrap_location", "=", True), ("company_id", "=", cls.env.company.id)],
            limit=1,
        )
        cls.outgoing_type = cls.env["stock.picking.type"].search(
            [
                ("code", "=", "outgoing"),
                ("warehouse_id.company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        cls.incoming_type = cls.outgoing_type.return_picking_type_id
        cls.internal_type = cls.env["stock.picking.type"].search(
            [
                ("code", "=", "internal"),
                ("warehouse_id.company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        cls.products = cls.env["product.product"].create(
            [
                {
                    "name": "Device State Product A",
                    "type": "product",
                    "tracking": "serial",
                },
                {
                    "name": "Device State Product B",
                    "type": "product",
                    "tracking": "serial",
                },
            ]
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Device State Test Type",
                "code": "DEVICE-STATE-TEST",
                "allowed_product_template_ids": [
                    Command.set(cls.products.product_tmpl_id.ids)
                ],
            }
        )
        cls.lots = cls.env["stock.lot"].create(
            [
                {
                    "name": f"DEVICE-STATE-{index}",
                    "product_id": product.id,
                    "company_id": cls.env.company.id,
                }
                for index, product in enumerate(cls.products, start=1)
            ]
        )
        for lot in cls.lots:
            cls.env["stock.quant"]._update_available_quantity(
                lot.product_id,
                cls.stock_location,
                1,
                lot_id=lot,
            )
        cls.bundle = cls.env["pinout.device.bundle"].create(
            {
                "name": "DEVICE-STATE-BUNDLE",
                "bundle_type_id": cls.env.ref(
                    "pinout_device_registry.bundle_type_other"
                ).id,
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
                    "bundle_id": cls.bundle.id,
                }
                for lot in cls.lots
            ]
        )

    def _complete_picking(self, picking_type, source, destination, devices=None):
        devices = devices or self.devices
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
            }
        )
        for device in devices:
            product = device.current_product_id
            self.env["stock.move"].create(
                {
                    "name": product.display_name,
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "product_uom": product.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": source.id,
                    "location_dest_id": destination.id,
                    "picked": True,
                    "move_line_ids": [
                        Command.create(
                            {
                                "product_id": product.id,
                                "product_uom_id": product.uom_id.id,
                                "quantity": 1,
                                "picking_id": picking.id,
                                "location_id": source.id,
                                "location_dest_id": destination.id,
                                "lot_id": device.final_lot_id.id,
                                "picked": True,
                            }
                        )
                    ],
                }
            )
        picking._action_done()
        return picking

    def test_delivery_return_resale_and_scrap_sync_device_state(self):
        self.devices.mapped("last_delivery_id")
        self.bundle.mapped("delivery_id")

        delivery = self._complete_picking(
            self.outgoing_type, self.stock_location, self.customer_location
        )

        self.assertEqual(delivery.state, "done")
        self.assertEqual(
            set(delivery.move_line_ids.lot_id.ids),
            set(self.lots.ids),
        )
        self.assertEqual(set(self.devices.mapped("state")), {"sold"})
        self.assertEqual(self.bundle.state, "sold")
        self.assertEqual(self.bundle.delivery_id, delivery)

        returned_device = self.devices[0]
        self._complete_picking(
            self.incoming_type,
            self.customer_location,
            self.stock_location,
            returned_device,
        )
        self.assertEqual(returned_device.state, "returned")

        second_delivery = self._complete_picking(
            self.outgoing_type,
            self.stock_location,
            self.customer_location,
            returned_device,
        )
        self.assertEqual(returned_device.state, "sold")
        self.assertEqual(returned_device.last_delivery_id, second_delivery)

        self._complete_picking(
            self.incoming_type,
            self.customer_location,
            self.stock_location,
            returned_device,
        )
        scrap = self.env["stock.scrap"].create(
            {
                "product_id": returned_device.current_product_id.id,
                "product_uom_id": returned_device.current_product_id.uom_id.id,
                "scrap_qty": 1,
                "location_id": self.stock_location.id,
                "scrap_location_id": self.scrap_location.id,
                "lot_id": returned_device.final_lot_id.id,
            }
        )
        scrap.do_scrap()
        self.assertEqual(returned_device.state, "scrapped")

    def test_repeated_sync_does_not_post_duplicate_message(self):
        self._complete_picking(
            self.outgoing_type,
            self.stock_location,
            self.customer_location,
            self.devices[0],
        )
        device = self.devices[0]
        message_count = len(device.message_ids)

        device._sync_state_from_stock_moves()

        self.assertEqual(len(device.message_ids), message_count)

    def test_internal_move_does_not_change_device_state(self):
        device = self.devices[0]

        self._complete_picking(
            self.internal_type,
            self.stock_location,
            self.internal_location,
            device,
        )

        self.assertEqual(device.state, "ready_for_sale")

    def test_separate_deliveries_do_not_sell_bundle(self):
        first_delivery = self._complete_picking(
            self.outgoing_type,
            self.stock_location,
            self.customer_location,
            self.devices[0],
        )
        second_delivery = self._complete_picking(
            self.outgoing_type,
            self.stock_location,
            self.customer_location,
            self.devices[1],
        )

        self.assertNotEqual(first_delivery, second_delivery)
        self.assertEqual(set(self.devices.mapped("state")), {"sold"})
        self.assertEqual(self.bundle.state, "draft")
