from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceInvariants(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plastic_attribute = cls.env["product.attribute"].create(
            {"name": "Invariant Plastic Type"}
        )
        cls.bambu_value = cls.env["product.attribute.value"].create(
            {
                "name": "PLA Basic Bambu Green",
                "attribute_id": cls.plastic_attribute.id,
                "variant_code": "BMGR",
            }
        )
        cls.emotion_attribute = cls.env["product.attribute"].create(
            {"name": "Invariant Urban Emotion"}
        )
        cls.smile_value = cls.env["product.attribute.value"].create(
            {
                "name": "Smile",
                "attribute_id": cls.emotion_attribute.id,
                "variant_code": "SML",
            }
        )
        cls.allowed_template = cls.env["product.template"].create(
            {
                "name": "Invariant Urban Retail Unit",
                "type": "product",
                "tracking": "serial",
                "attribute_line_ids": [
                    Command.create(
                        {
                            "attribute_id": cls.plastic_attribute.id,
                            "value_ids": [Command.set(cls.bambu_value.ids)],
                        }
                    ),
                    Command.create(
                        {
                            "attribute_id": cls.emotion_attribute.id,
                            "value_ids": [Command.set(cls.smile_value.ids)],
                        }
                    ),
                ],
            }
        )
        cls.allowed_product = cls.allowed_template.product_variant_id
        cls.other_product = cls.env["product.product"].create(
            {
                "name": "Invariant Other Product",
                "type": "product",
                "tracking": "serial",
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Invariant Device Type",
                "code": "INVARIANT-DEVICE",
                "allowed_product_template_ids": [Command.set(cls.allowed_template.ids)],
                "attribute_line_ids": [
                    Command.create(
                        {
                            "attribute_id": cls.emotion_attribute.id,
                            "sequence": 10,
                            "use_variant_code": False,
                        }
                    ),
                    Command.create(
                        {
                            "attribute_id": cls.plastic_attribute.id,
                            "sequence": 20,
                            "use_variant_code": True,
                        }
                    ),
                ],
            }
        )

    def test_mac_can_be_reused_by_different_devices(self):
        devices = self.env["pinout.device"].create(
            [
                {
                    "device_uid": f"MAC-NON-UNIQUE-{number}",
                    "device_type_id": self.device_type.id,
                    "mac": "00:11:22:33:44:55",
                }
                for number in range(1, 3)
            ]
        )

        self.assertEqual(len(devices), 2)
        self.assertEqual(len(set(devices.mapped("mac"))), 1)

    def test_variant_summary_uses_configured_order_codes_and_names(self):
        device = self.env["pinout.device"].create(
            {
                "device_uid": "VARIANT-SUMMARY-001",
                "device_type_id": self.device_type.id,
                "current_product_id": self.allowed_product.id,
            }
        )

        self.assertEqual(device.variant_summary, "Smile / BMGR")

    def test_allowed_product_forms_reject_other_product(self):
        allowed_device = self.env["pinout.device"].create(
            {
                "device_uid": "ALLOWED-PRODUCT-001",
                "device_type_id": self.device_type.id,
                "current_product_id": self.allowed_product.id,
            }
        )
        self.assertEqual(allowed_device.current_product_id, self.allowed_product)

        with (
            self.assertRaisesRegex(ValidationError, "not allowed"),
            self.cr.savepoint(),
        ):
            self.env["pinout.device"].create(
                {
                    "device_uid": "DISALLOWED-PRODUCT-001",
                    "device_type_id": self.device_type.id,
                    "current_product_id": self.other_product.id,
                }
            )

    def test_final_lot_must_match_device_uid(self):
        lot = self.env["stock.lot"].create(
            {
                "name": "OTHER-FINAL-LOT-UID",
                "product_id": self.allowed_product.id,
                "company_id": self.env.company.id,
            }
        )

        with (
            self.assertRaisesRegex(ValidationError, "does not match Device UID"),
            self.cr.savepoint(),
        ):
            self.env["pinout.device"].create(
                {
                    "device_uid": "FINAL-LOT-UID-MISMATCH",
                    "device_type_id": self.device_type.id,
                    "current_product_id": self.allowed_product.id,
                    "final_lot_id": lot.id,
                }
            )

    def test_final_lot_must_match_current_product(self):
        lot = self.env["stock.lot"].create(
            {
                "name": "FINAL-LOT-PRODUCT-MISMATCH",
                "product_id": self.other_product.id,
                "company_id": self.env.company.id,
            }
        )

        with (
            self.assertRaisesRegex(ValidationError, "belongs to"),
            self.cr.savepoint(),
        ):
            self.env["pinout.device"].create(
                {
                    "device_uid": lot.name,
                    "device_type_id": self.device_type.id,
                    "current_product_id": self.allowed_product.id,
                    "final_lot_id": lot.id,
                }
            )
