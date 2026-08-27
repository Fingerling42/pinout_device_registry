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

    def test_current_final_lot_is_linked_to_history(self):
        self.assertEqual(self.lot.pinout_device_id, self.device)
        self.assertEqual(self.device.final_lot_history_ids, self.lot)
        self.assertTrue(self.lot.pinout_is_current_device_lot)

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
        self.assertEqual(updated_device.final_lot_history_ids, self.lot)
        self.assertEqual(self.lot.pinout_device_id, updated_device)
        self.assertFalse(self.lot.pinout_is_current_device_lot)

    def test_same_uid_can_link_lots_for_different_product_variants(self):
        second_lot = self.env["stock.lot"].create(
            {
                "name": self.device.device_uid,
                "product_id": self.product_b.id,
                "company_id": self.env.company.id,
                "pinout_device_id": self.device.id,
            }
        )

        self.assertEqual(
            set(self.device.final_lot_history_ids.ids),
            {self.lot.id, second_lot.id},
        )
        self.assertNotEqual(self.lot.product_id, second_lot.product_id)

    def test_historical_lot_must_match_device_uid(self):
        mismatched_lot = self.env["stock.lot"].create(
            {
                "name": "OTHER-FINAL-LOT-DEVICE",
                "product_id": self.product_b.id,
                "company_id": self.env.company.id,
            }
        )

        with (
            self.assertRaisesRegex(ValidationError, "identifiers do not match"),
            self.cr.savepoint(),
        ):
            mismatched_lot.pinout_device_id = self.device

    def test_device_uid_cannot_diverge_from_historical_lots(self):
        self.device.final_lot_id = False

        with (
            self.assertRaisesRegex(
                ValidationError, "Historical serial links cannot be silently changed"
            ),
            self.cr.savepoint(),
        ):
            self.device.device_uid = "CHANGED-FINAL-LOT-DEVICE"

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
