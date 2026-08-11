from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceFinalLot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_a = cls.env["product.product"].create(
            {
                "name": "Final Lot Product A",
                "tracking": "serial",
            }
        )
        cls.product_b = cls.env["product.product"].create(
            {
                "name": "Final Lot Product B",
                "tracking": "serial",
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Final Lot Test Type",
                "code": "FINAL-LOT-TEST",
                "allowed_product_template_ids": [
                    (
                        6,
                        0,
                        [
                            cls.product_a.product_tmpl_id.id,
                            cls.product_b.product_tmpl_id.id,
                        ],
                    )
                ],
            }
        )
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "FINAL-LOT-DEVICE-001",
                "product_id": cls.product_a.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.device = cls.env["pinout.device"].create(
            {
                "device_uid": cls.lot.name,
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.product_a.id,
                "final_lot_id": cls.lot.id,
            }
        )

    def test_product_onchange_keeps_existing_final_lot(self):
        device_form = Form(self.device)
        device_form.current_product_id = self.product_b

        self.assertEqual(device_form.final_lot_id, self.lot)
        with (
            self.assertRaisesRegex(
                ValidationError, "manually clear Final Lot / Serial"
            ),
            self.cr.savepoint(),
        ):
            device_form.save()

    def test_final_lot_can_be_cleared_explicitly(self):
        device_form = Form(self.device)
        device_form.current_product_id = self.product_b
        device_form.final_lot_id = self.env["stock.lot"]
        updated_device = device_form.save()

        self.assertEqual(updated_device.current_product_id, self.product_b)
        self.assertFalse(updated_device.final_lot_id)

    def test_device_type_onchange_keeps_product_and_final_lot(self):
        other_type = self.env["pinout.device.type"].create(
            {
                "name": "Other Final Lot Test Type",
                "code": "OTHER-FINAL-LOT-TEST",
                "allowed_product_template_ids": [
                    (6, 0, [self.product_b.product_tmpl_id.id])
                ],
            }
        )

        device_form = Form(self.device)
        device_form.device_type_id = other_type

        self.assertEqual(device_form.current_product_id, self.product_a)
        self.assertEqual(device_form.final_lot_id, self.lot)
        with (
            self.assertRaisesRegex(ValidationError, "not allowed for this Device Type"),
            self.cr.savepoint(),
        ):
            device_form.save()
