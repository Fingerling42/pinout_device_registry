from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBundleType(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.allowed_kit = cls.env["product.product"].create(
            {"name": "Allowed Configurable Kit", "type": "product"}
        )
        cls.other_product = cls.env["product.product"].create(
            {"name": "Other Configurable Product", "type": "product"}
        )
        cls.component = cls.env["product.product"].create(
            {"name": "Configurable Kit Component", "type": "product"}
        )
        cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.allowed_kit.product_tmpl_id.id,
                "product_id": cls.allowed_kit.id,
                "product_qty": 1.0,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create({"product_id": cls.component.id, "product_qty": 1.0})
                ],
            }
        )
        cls.kit_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Configurable Kit Type",
                "code": "CONFIG-KIT",
                "requires_kit_bom": True,
                "allowed_product_template_ids": [
                    Command.set([cls.allowed_kit.product_tmpl_id.id])
                ],
            }
        )
        cls.generic_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Configurable Generic Type",
                "code": "CONFIG-GENERIC",
                "requires_kit_bom": False,
            }
        )

    def test_kit_type_requires_allowed_product_with_kit_bom(self):
        with self.assertRaisesRegex(ValidationError, "requires a Bundle Product"):
            self.env["pinout.device.bundle"].create(
                {
                    "name": "MISSING-KIT-PRODUCT",
                    "bundle_type_id": self.kit_type.id,
                }
            )

        with self.assertRaisesRegex(ValidationError, "is not allowed"):
            self.env["pinout.device.bundle"].create(
                {
                    "name": "FORBIDDEN-KIT-PRODUCT",
                    "bundle_type_id": self.kit_type.id,
                    "bundle_product_id": self.other_product.id,
                }
            )

        bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "VALID-KIT-PRODUCT",
                "bundle_type_id": self.kit_type.id,
                "bundle_product_id": self.allowed_kit.id,
            }
        )
        self.assertEqual(bundle.bundle_product_id, self.allowed_kit)

    def test_kit_type_rejects_allowed_product_without_kit_bom(self):
        self.kit_type.allowed_product_template_ids = [
            Command.link(self.other_product.product_tmpl_id.id)
        ]

        with self.assertRaisesRegex(ValidationError, "active Kit BoM"):
            self.env["pinout.device.bundle"].create(
                {
                    "name": "PRODUCT-WITHOUT-KIT-BOM",
                    "bundle_type_id": self.kit_type.id,
                    "bundle_product_id": self.other_product.id,
                }
            )

    def test_generic_type_allows_empty_or_non_kit_product(self):
        empty_bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "GENERIC-WITHOUT-PRODUCT",
                "bundle_type_id": self.generic_type.id,
            }
        )
        product_bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "GENERIC-WITH-PRODUCT",
                "bundle_type_id": self.generic_type.id,
                "bundle_product_id": self.other_product.id,
            }
        )

        self.assertFalse(empty_bundle.bundle_product_id)
        self.assertEqual(product_bundle.bundle_product_id, self.other_product)

    def test_type_change_keeps_incompatible_product_visible(self):
        restricted_type = self.env["pinout.device.bundle.type"].create(
            {
                "name": "Restricted Configurable Type",
                "code": "CONFIG-RESTRICTED",
                "requires_kit_bom": False,
                "allowed_product_template_ids": [
                    Command.set([self.allowed_kit.product_tmpl_id.id])
                ],
            }
        )
        bundle = self.env["pinout.device.bundle"].new(
            {
                "name": "TYPE-CHANGE",
                "bundle_type_id": restricted_type.id,
                "bundle_product_id": self.other_product.id,
            }
        )

        result = bundle._onchange_bundle_type_id()

        self.assertEqual(bundle.bundle_product_id, self.other_product)
        self.assertIn("warning", result)
        self.assertEqual(
            result["domain"]["bundle_product_id"],
            [("product_tmpl_id", "in", [self.allowed_kit.product_tmpl_id.id])],
        )
