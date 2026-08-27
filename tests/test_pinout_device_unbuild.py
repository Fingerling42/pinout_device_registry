from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceUnbuild(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.tracked_product = cls.env["product.product"].create(
            {
                "name": "Registry Unbuild Retail Unit",
                "type": "product",
                "tracking": "serial",
            }
        )
        cls.intermediate_product = cls.env["product.product"].create(
            {
                "name": "Registry Unbuild Intermediate Form",
                "type": "product",
            }
        )
        cls.unmanaged_product = cls.env["product.product"].create(
            {
                "name": "Unmanaged Unbuild Product",
                "type": "product",
            }
        )
        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.tracked_product.product_tmpl_id.id,
                "product_id": cls.tracked_product.id,
                "product_qty": 1.0,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": cls.intermediate_product.id,
                            "product_qty": 1.0,
                        }
                    )
                ],
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Registry Unbuild Device Type",
                "code": "REGISTRY-UNBUILD",
                "allowed_product_template_ids": [
                    Command.set(
                        (
                            cls.tracked_product.product_tmpl_id
                            | cls.intermediate_product.product_tmpl_id
                        ).ids
                    )
                ],
            }
        )
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "REGISTRY-UNBUILD-001",
                "product_id": cls.tracked_product.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.device = cls.env["pinout.device"].create(
            {
                "device_uid": cls.lot.name,
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.tracked_product.id,
                "final_lot_id": cls.lot.id,
            }
        )
        cls.intermediate_device = cls.env["pinout.device"].create(
            {
                "device_uid": "REGISTRY-UNBUILD-INTERMEDIATE-001",
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.intermediate_product.id,
            }
        )

    def _create_unbuild(self, product, **values):
        values.setdefault("product_id", product.id)
        values.setdefault("product_qty", 1.0)
        values.setdefault(
            "bom_id", self.bom.id if product == self.tracked_product else False
        )
        values.setdefault("location_id", self.stock_location.id)
        values.setdefault("location_dest_id", self.stock_location.id)
        return self.env["mrp.unbuild"].create(values)

    def test_managed_product_forms_are_detected_from_device_types(self):
        managed_unbuilds = self._create_unbuild(
            self.tracked_product
        ) | self._create_unbuild(self.intermediate_product)
        unmanaged_unbuild = self._create_unbuild(self.unmanaged_product)

        self.assertTrue(all(managed_unbuilds.mapped("pinout_registry_managed")))
        self.assertFalse(unmanaged_unbuild.pinout_registry_managed)
        self.assertFalse(unmanaged_unbuild._validate_pinout_registry_unbuild())

    def test_tracked_lot_onchange_detects_registry_device(self):
        unbuild = self.env["mrp.unbuild"].new(
            {
                "product_id": self.tracked_product.id,
                "lot_id": self.lot.id,
            }
        )

        unbuild._onchange_pinout_lot_id()

        self.assertEqual(unbuild.pinout_device_id, self.device)

    def test_successful_unbuild_persists_device_detected_from_lot(self):
        self.env["stock.quant"]._update_available_quantity(
            self.tracked_product,
            self.stock_location,
            1.0,
            lot_id=self.lot,
        )
        unbuild = self._create_unbuild(self.tracked_product, lot_id=self.lot.id)

        unbuild.action_unbuild()

        self.assertEqual(unbuild.state, "done")
        self.assertEqual(unbuild.pinout_device_id, self.device)

    def test_untracked_product_requires_manual_device(self):
        unbuild = self._create_unbuild(self.intermediate_product)

        with self.assertRaisesRegex(UserError, "Select the Registry Device"):
            unbuild.action_unbuild()

        unbuild.pinout_device_id = self.intermediate_device
        self.assertEqual(
            unbuild._validate_pinout_registry_unbuild(),
            self.intermediate_device,
        )

    def test_registry_unbuild_requires_one_device(self):
        unbuild = self._create_unbuild(
            self.intermediate_product,
            product_qty=2.0,
            pinout_device_id=self.intermediate_device.id,
        )

        with self.assertRaisesRegex(UserError, "exactly one physical Device"):
            unbuild.action_unbuild()

    def test_unbuild_product_must_match_device_current_form(self):
        unbuild = self._create_unbuild(
            self.intermediate_product,
            pinout_device_id=self.device.id,
        )

        with self.assertRaisesRegex(UserError, "does not match Current Product"):
            unbuild.action_unbuild()

    def test_tracked_lot_must_be_current_device_lot(self):
        self.device.final_lot_id = False
        unbuild = self._create_unbuild(self.tracked_product, lot_id=self.lot.id)

        with self.assertRaisesRegex(UserError, "is historical"):
            unbuild.action_unbuild()

    def test_bundled_device_must_be_unpaired_before_unbuild(self):
        bundle_type = self.env["pinout.device.bundle.type"].create(
            {
                "name": "Registry Unbuild Bundle Type",
                "code": "REGISTRY-UNBUILD-BUNDLE",
                "allow_any_product_form": True,
                "requires_kit_bom": False,
            }
        )
        bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "REGISTRY-UNBUILD-BUNDLE-001",
                "bundle_type_id": bundle_type.id,
            }
        )
        self.intermediate_device.bundle_id = bundle
        unbuild = self._create_unbuild(
            self.intermediate_product,
            pinout_device_id=self.intermediate_device.id,
        )

        with self.assertRaisesRegex(UserError, "Cancel and Unpair"):
            unbuild.action_unbuild()
