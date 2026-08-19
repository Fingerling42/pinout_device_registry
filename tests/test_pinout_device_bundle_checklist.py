from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBundleChecklist(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.urban_product = cls.env["product.product"].create(
            {"name": "Checklist Urban", "type": "product"}
        )
        cls.insight_product = cls.env["product.product"].create(
            {"name": "Checklist Insight", "type": "product"}
        )
        cls.kit_product = cls.env["product.product"].create(
            {"name": "Checklist Dual Kit", "type": "product"}
        )
        cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.kit_product.product_tmpl_id.id,
                "product_id": cls.kit_product.id,
                "product_qty": 1.0,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create(
                        {"product_id": cls.urban_product.id, "product_qty": 1.0}
                    ),
                    Command.create(
                        {
                            "product_id": cls.insight_product.id,
                            "product_qty": 1.0,
                        }
                    ),
                ],
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Checklist Device Type",
                "code": "CHECKLIST-DEVICE",
                "allowed_product_template_ids": [
                    Command.set(
                        (
                            cls.urban_product.product_tmpl_id
                            | cls.insight_product.product_tmpl_id
                        ).ids
                    )
                ],
            }
        )
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Checklist Bundle Type",
                "code": "CHECKLIST-BUNDLE",
                "allowed_product_template_ids": [
                    Command.set(cls.kit_product.product_tmpl_id.ids)
                ],
                "requires_kit_bom": True,
            }
        )
        cls.urban_device = cls.env["pinout.device"].create(
            {
                "device_uid": "CHECKLIST-URBAN-001",
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.urban_product.id,
            }
        )
        cls.insight_device = cls.env["pinout.device"].create(
            {
                "device_uid": "CHECKLIST-INSIGHT-001",
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.insight_product.id,
            }
        )
        cls.bundle = cls.env["pinout.device.bundle"].create(
            {
                "name": "CHECKLIST-DUAL-001",
                "bundle_type_id": cls.bundle_type.id,
                "bundle_product_id": cls.kit_product.id,
            }
        )

    def _rows_by_product(self):
        return {
            row["product"]: row for row in self.bundle._get_component_checklist_rows()
        }

    def test_checklist_tracks_missing_and_attached_devices(self):
        rows = self._rows_by_product()
        self.assertEqual(rows[self.urban_product]["status"], "missing")
        self.assertEqual(rows[self.insight_product]["status"], "missing")
        checklist = str(self.bundle.expected_component_checklist)
        self.assertEqual(checklist.count("Missing"), 2)

        self.urban_device.bundle_id = self.bundle
        rows = self._rows_by_product()
        self.assertEqual(rows[self.urban_product]["status"], "complete")
        self.assertEqual(rows[self.urban_product]["attached_quantity"], 1.0)
        self.assertEqual(rows[self.insight_product]["status"], "missing")
        checklist = str(self.bundle.expected_component_checklist)
        self.assertEqual(checklist.count("Complete"), 1)
        self.assertEqual(checklist.count("Missing"), 1)
        self.assertIn(self.urban_device.device_uid, checklist)

        self.insight_device.bundle_id = self.bundle
        rows = self._rows_by_product()
        self.assertEqual(rows[self.urban_product]["status"], "complete")
        self.assertEqual(rows[self.insight_product]["status"], "complete")

        checklist = str(self.bundle.expected_component_checklist)
        self.assertEqual(checklist.count("Complete"), 2)
        self.assertIn(self.urban_product.display_name, checklist)
        self.assertIn(self.insight_product.display_name, checklist)
        self.assertIn(self.urban_device.device_uid, checklist)
        self.assertIn(self.insight_device.device_uid, checklist)
