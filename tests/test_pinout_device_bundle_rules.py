from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBundleRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.variant_attribute = cls.env["product.attribute"].create(
            {"name": "Bundle Rules Color"}
        )
        cls.green_value, cls.magenta_value = cls.env["product.attribute.value"].create(
            [
                {
                    "name": "Bundle Rules Green",
                    "attribute_id": cls.variant_attribute.id,
                },
                {
                    "name": "Bundle Rules Magenta",
                    "attribute_id": cls.variant_attribute.id,
                },
            ]
        )
        cls.kit_template = cls.env["product.template"].create(
            {
                "name": "Bundle Rules Kit",
                "type": "product",
                "attribute_line_ids": [
                    Command.create(
                        {
                            "attribute_id": cls.variant_attribute.id,
                            "value_ids": [
                                Command.set((cls.green_value | cls.magenta_value).ids)
                            ],
                        }
                    )
                ],
            }
        )
        cls.green_kit = cls.kit_template.product_variant_ids.filtered(
            lambda product: (
                cls.green_value
                in product.product_template_variant_value_ids.product_attribute_value_id
            )
        )
        cls.magenta_kit = cls.kit_template.product_variant_ids.filtered(
            lambda product: (
                cls.magenta_value
                in product.product_template_variant_value_ids.product_attribute_value_id
            )
        )
        (
            cls.green_urban,
            cls.green_insight,
            cls.magenta_urban,
            cls.magenta_insight,
            cls.unexpected_product,
        ) = cls.env["product.product"].create(
            [
                {
                    "name": name,
                    "type": "product",
                    "tracking": "serial",
                }
                for name in (
                    "Bundle Rules Green Urban",
                    "Bundle Rules Green Insight",
                    "Bundle Rules Magenta Urban",
                    "Bundle Rules Magenta Insight",
                    "Bundle Rules Unexpected Device",
                )
            ]
        )
        cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.kit_template.id,
                "product_qty": 2.0,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": cls.green_urban.id,
                            "product_qty": 4.0,
                            "bom_product_template_attribute_value_ids": [
                                Command.set(
                                    cls.green_kit.product_template_variant_value_ids.ids
                                )
                            ],
                        }
                    ),
                    Command.create(
                        {
                            "product_id": cls.green_insight.id,
                            "product_qty": 2.0,
                            "bom_product_template_attribute_value_ids": [
                                Command.set(
                                    cls.green_kit.product_template_variant_value_ids.ids
                                )
                            ],
                        }
                    ),
                    Command.create(
                        {
                            "product_id": cls.magenta_urban.id,
                            "product_qty": 2.0,
                            "bom_product_template_attribute_value_ids": [
                                Command.set(
                                    cls.magenta_kit.product_template_variant_value_ids.ids
                                )
                            ],
                        }
                    ),
                    Command.create(
                        {
                            "product_id": cls.magenta_insight.id,
                            "product_qty": 2.0,
                            "bom_product_template_attribute_value_ids": [
                                Command.set(
                                    cls.magenta_kit.product_template_variant_value_ids.ids
                                )
                            ],
                        }
                    ),
                ],
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Bundle Rules Device Type",
                "code": "BUNDLE-RULES-DEVICE",
                "allowed_product_template_ids": [
                    Command.set(
                        (
                            cls.green_urban
                            | cls.green_insight
                            | cls.magenta_urban
                            | cls.magenta_insight
                            | cls.unexpected_product
                        ).product_tmpl_id.ids
                    )
                ],
            }
        )
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Bundle Rules Type",
                "code": "BUNDLE-RULES",
                "requires_kit_bom": True,
                "allowed_product_template_ids": [Command.set(cls.kit_template.ids)],
            }
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

    def _create_bundle(self, name, product=None):
        return self.env["pinout.device.bundle"].create(
            {
                "name": name,
                "bundle_type_id": self.bundle_type.id,
                "bundle_product_id": (product or self.green_kit).id,
            }
        )

    def _create_device(self, uid, product, **values):
        return self.env["pinout.device"].create(
            {
                "device_uid": uid,
                "device_type_id": self.device_type.id,
                "current_product_id": product.id,
                **values,
            }
        )

    def _deliver_device(self, device):
        self.env["stock.quant"]._update_available_quantity(
            device.current_product_id,
            self.stock_location,
            1,
            lot_id=device.final_lot_id,
        )
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.outgoing_type.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
            }
        )
        product = device.current_product_id
        self.env["stock.move"].create(
            {
                "name": product.display_name,
                "product_id": product.id,
                "product_uom_qty": 1,
                "product_uom": product.uom_id.id,
                "picking_id": picking.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
                "picked": True,
                "move_line_ids": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_id": product.uom_id.id,
                            "quantity": 1,
                            "picking_id": picking.id,
                            "location_id": self.stock_location.id,
                            "location_dest_id": self.customer_location.id,
                            "lot_id": device.final_lot_id.id,
                            "picked": True,
                        }
                    )
                ],
            }
        )
        picking._action_done()
        return picking

    def test_variant_specific_bom_lines_and_normalized_quantities(self):
        green_bundle = self._create_bundle("BUNDLE-RULES-GREEN")
        magenta_bundle = self._create_bundle("BUNDLE-RULES-MAGENTA", self.magenta_kit)

        self.assertEqual(
            green_bundle._get_expected_component_quantities(),
            {self.green_urban: 2.0, self.green_insight: 1.0},
        )
        self.assertEqual(
            magenta_bundle._get_expected_component_quantities(),
            {self.magenta_urban: 1.0, self.magenta_insight: 1.0},
        )

        devices = (
            self._create_device("BUNDLE-GREEN-URBAN-001", self.green_urban)
            | self._create_device("BUNDLE-GREEN-URBAN-002", self.green_urban)
            | self._create_device("BUNDLE-GREEN-INSIGHT-001", self.green_insight)
        )
        devices.write({"bundle_id": green_bundle.id})
        rows = {
            row["product"]: row for row in green_bundle._get_component_checklist_rows()
        }

        self.assertEqual(rows[self.green_urban]["expected_quantity"], 2.0)
        self.assertEqual(rows[self.green_urban]["attached_quantity"], 2.0)
        self.assertEqual(rows[self.green_urban]["status"], "complete")
        self.assertEqual(rows[self.green_insight]["status"], "complete")

    def test_unexpected_component_is_rejected(self):
        bundle = self._create_bundle("BUNDLE-RULES-UNEXPECTED")
        device = self._create_device("BUNDLE-UNEXPECTED-001", self.unexpected_product)

        with (
            self.assertRaisesRegex(ValidationError, "not expected by the Kit BoM"),
            self.cr.savepoint(),
        ):
            device.bundle_id = bundle

    def test_excess_component_quantity_is_rejected(self):
        bundle = self._create_bundle("BUNDLE-RULES-EXCESS")
        allowed_devices = self._create_device(
            "BUNDLE-EXCESS-001", self.green_urban
        ) | self._create_device("BUNDLE-EXCESS-002", self.green_urban)
        excess_device = self._create_device("BUNDLE-EXCESS-003", self.green_urban)
        allowed_devices.write({"bundle_id": bundle.id})

        with (
            self.assertRaisesRegex(ValidationError, "can contain at most 2"),
            self.cr.savepoint(),
        ):
            excess_device.bundle_id = bundle

    def test_sold_device_is_rejected(self):
        bundle = self._create_bundle("BUNDLE-RULES-SOLD")
        device = self._create_device("BUNDLE-SOLD-001", self.green_urban, state="sold")

        with (
            self.assertRaisesRegex(ValidationError, "because it is Sold"),
            self.cr.savepoint(),
        ):
            device.bundle_id = bundle

    def test_delivered_device_is_rejected_after_manual_state_change(self):
        bundle = self._create_bundle("BUNDLE-RULES-DELIVERED")
        lot = self.env["stock.lot"].create(
            {
                "name": "BUNDLE-DELIVERED-001",
                "product_id": self.green_urban.id,
                "company_id": self.env.company.id,
            }
        )
        device = self._create_device(
            lot.name,
            self.green_urban,
            final_lot_id=lot.id,
            state="ready_for_sale",
        )
        delivery = self._deliver_device(device)
        self.assertEqual(device.state, "sold")

        device.state = "ready_for_sale"
        self.assertEqual(device.last_delivery_id, delivery)
        with (
            self.assertRaisesRegex(ValidationError, "already delivered"),
            self.cr.savepoint(),
        ):
            device.bundle_id = bundle
