from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBundleIdentifier(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Identifier Test Type",
                "allow_any_product_form": True,
                "requires_kit_bom": False,
            }
        )

    def test_bundle_type_code_is_generated_and_normalized(self):
        self.assertEqual(self.bundle_type.code, "IDENTIFIER-TEST-TYPE")

        manual_type = self.env["pinout.device.bundle.type"].create(
            {
                "name": "Manual Code Type",
                "code": " custom code ",
                "allow_any_product_form": True,
                "requires_kit_bom": False,
            }
        )

        self.assertEqual(manual_type.code, "CUSTOM-CODE")

    def test_bundle_id_is_generated_from_type_code(self):
        first_bundle, second_bundle = self.env["pinout.device.bundle"].create(
            [
                {"bundle_type_id": self.bundle_type.id},
                {"bundle_type_id": self.bundle_type.id},
            ]
        )

        self.assertRegex(first_bundle.name, r"^IDENTIFIER-TEST-TYPE-\d{6}$")
        self.assertRegex(second_bundle.name, r"^IDENTIFIER-TEST-TYPE-\d{6}$")
        self.assertNotEqual(first_bundle.name, second_bundle.name)

    def test_manual_bundle_id_is_preserved_and_unique(self):
        bundle_model = self.env["pinout.device.bundle"]
        bundle = bundle_model.create(
            {
                "name": "MANUAL-BUNDLE-ID",
                "bundle_type_id": self.bundle_type.id,
            }
        )

        self.assertEqual(bundle.name, "MANUAL-BUNDLE-ID")
        with self.assertRaisesRegex(ValidationError, "globally unique"):
            bundle_model.create(
                {
                    "name": "MANUAL-BUNDLE-ID",
                    "bundle_type_id": self.bundle_type.id,
                }
            )

    def test_used_bundle_type_code_cannot_be_changed(self):
        editable_type = self.env["pinout.device.bundle.type"].create(
            {
                "name": "Editable Type",
                "code": "EDITABLE",
                "allow_any_product_form": True,
                "requires_kit_bom": False,
            }
        )
        editable_type.code = "CHANGED-BEFORE-USE"
        bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "ARCHIVED-BUNDLE",
                "bundle_type_id": editable_type.id,
            }
        )
        bundle.active = False

        with self.assertRaisesRegex(ValidationError, "cannot be changed"):
            editable_type.code = "CHANGED-AFTER-USE"

    def test_bundle_id_can_only_be_changed_in_draft(self):
        bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "DRAFT-BUNDLE-ID",
                "bundle_type_id": self.bundle_type.id,
            }
        )
        bundle.name = "UPDATED-DRAFT-BUNDLE-ID"
        self.assertEqual(bundle.name, "UPDATED-DRAFT-BUNDLE-ID")

        bundle.with_context(pinout_bundle_state_transition=True).write(
            {"state": "ready_for_sale"}
        )
        with self.assertRaisesRegex(ValidationError, "only be changed"):
            bundle.name = "FORBIDDEN-RESERVED-ID"
