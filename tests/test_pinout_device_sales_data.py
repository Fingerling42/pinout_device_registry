from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceSalesData(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Device Sales Metadata Customer"}
        )
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.normal_product = cls.env["product.product"].create(
            {
                "name": "Device Sales Metadata Retail Unit",
                "type": "product",
                "tracking": "serial",
            }
        )
        cls.kit_product = cls.env["product.product"].create(
            {"name": "Device Sales Metadata Kit", "type": "product"}
        )
        cls.kit_components = cls.env["product.product"].create(
            [
                {
                    "name": "Device Sales Metadata Kit Urban",
                    "type": "product",
                    "tracking": "serial",
                },
                {
                    "name": "Device Sales Metadata Kit Insight",
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
                    for product in cls.kit_components
                ],
            }
        )
        all_device_products = cls.normal_product | cls.kit_components
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Device Sales Metadata Type",
                "code": "SALES-METADATA-DEVICE",
                "allowed_product_template_ids": [
                    Command.set(all_device_products.product_tmpl_id.ids)
                ],
            }
        )
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Device Sales Metadata Bundle Type",
                "code": "SALES-METADATA-BUNDLE",
                "requires_kit_bom": True,
                "allowed_product_template_ids": [
                    Command.set(cls.kit_product.product_tmpl_id.ids)
                ],
            }
        )
        cls.normal_lot = cls._create_lot(cls.normal_product, "SALES-NORMAL-001")
        cls.normal_device = cls._create_device(cls.normal_lot)
        cls.kit_lots = cls.env["stock.lot"]
        cls.kit_devices = cls.env["pinout.device"]
        for index, product in enumerate(cls.kit_components, start=1):
            lot = cls._create_lot(product, f"SALES-KIT-{index:03d}")
            cls.kit_lots |= lot
            cls.kit_devices |= cls._create_device(lot)
        cls.bundle = cls.env["pinout.device.bundle"].create(
            {
                "name": "SALES-METADATA-KIT-BUNDLE",
                "bundle_type_id": cls.bundle_type.id,
                "bundle_product_id": cls.kit_product.id,
            }
        )
        cls.kit_devices.write({"bundle_id": cls.bundle.id})

        for lot in cls.normal_lot | cls.kit_lots:
            cls.env["stock.quant"]._update_available_quantity(
                lot.product_id,
                cls.stock_location,
                1,
                lot_id=lot,
            )

    @classmethod
    def _create_lot(cls, product, name):
        return cls.env["stock.lot"].create(
            {
                "name": name,
                "product_id": product.id,
                "company_id": cls.env.company.id,
            }
        )

    @classmethod
    def _create_device(cls, lot):
        return cls.env["pinout.device"].create(
            {
                "device_uid": lot.name,
                "device_type_id": cls.device_type.id,
                "current_product_id": lot.product_id.id,
                "final_lot_id": lot.id,
                "state": "ready_for_sale",
            }
        )

    def _create_sale_order(self, product, reference=False):
        return self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": reference,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": 1.0,
                            "product_uom": product.uom_id.id,
                            "price_unit": 100.0,
                        }
                    )
                ],
            }
        )

    def _complete_sale_delivery(self, sale_order, devices):
        sale_order.action_confirm()
        picking = sale_order.picking_ids.filtered(lambda item: item.state != "cancel")
        self.assertEqual(len(picking), 1)
        devices_by_product = {device.current_product_id: device for device in devices}
        moves = picking.move_ids.filtered(lambda move: move.state != "cancel")
        self.assertEqual(set(moves.product_id), set(devices_by_product))

        for move in moves:
            device = devices_by_product[move.product_id]
            move.move_line_ids.unlink()
            move.write(
                {
                    "picked": True,
                    "move_line_ids": [
                        Command.create(
                            {
                                "product_id": move.product_id.id,
                                "product_uom_id": move.product_uom.id,
                                "quantity": 1.0,
                                "picking_id": picking.id,
                                "location_id": picking.location_id.id,
                                "location_dest_id": picking.location_dest_id.id,
                                "lot_id": device.final_lot_id.id,
                                "picked": True,
                            }
                        )
                    ],
                }
            )
        picking._action_done()
        return picking

    def test_standard_sale_populates_device_sales_metadata(self):
        sale_order = self._create_sale_order(
            self.normal_product,
            reference="CUSTOMER-NORMAL-REFERENCE",
        )
        delivery = self._complete_sale_delivery(sale_order, self.normal_device)
        device = self.normal_device

        self.assertEqual(device.state, "sold")
        self.assertEqual(device.last_customer_id, self.partner)
        self.assertEqual(device.last_sale_order_id, sale_order)
        self.assertEqual(device.last_delivery_id, delivery)
        self.assertEqual(device.last_order_reference, "CUSTOMER-NORMAL-REFERENCE")

    def test_kit_sale_populates_component_devices_and_bundle_metadata(self):
        sale_order = self._create_sale_order(self.kit_product)
        delivery = self._complete_sale_delivery(sale_order, self.kit_devices)

        for device in self.kit_devices:
            self.assertEqual(device.state, "sold")
            self.assertEqual(device.last_customer_id, self.partner)
            self.assertEqual(device.last_sale_order_id, sale_order)
            self.assertEqual(device.last_delivery_id, delivery)
            self.assertEqual(device.last_order_reference, sale_order.name)
        self.assertEqual(self.bundle.state, "sold")
        self.assertEqual(self.bundle.customer_id, self.partner)
        self.assertEqual(self.bundle.sale_order_id, sale_order)
        self.assertEqual(self.bundle.delivery_id, delivery)
