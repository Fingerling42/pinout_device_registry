from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


@tagged("post_install", "-at_install")
class TestPinoutDeviceUid(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device_type_a = cls.env["pinout.device.type"].create(
            {"name": "Device UID Type A", "code": "UID-TYPE-A"}
        )
        cls.device_type_b = cls.env["pinout.device.type"].create(
            {"name": "Device UID Type B", "code": "UID-TYPE-B"}
        )

    def test_device_uid_is_unique_across_device_types(self):
        self.env["pinout.device"].create(
            {
                "device_uid": "GLOBAL-DEVICE-UID-001",
                "device_type_id": self.device_type_a.id,
            }
        )

        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.cr.savepoint(),
        ):
            self.env["pinout.device"].create(
                {
                    "device_uid": "GLOBAL-DEVICE-UID-001",
                    "device_type_id": self.device_type_b.id,
                }
            )

    def test_device_uid_explains_global_uniqueness(self):
        device_model = self.env["pinout.device"]
        constraint = next(
            item
            for item in device_model._sql_constraints
            if item[0] == "device_uid_unique"
        )

        self.assertIn(
            "across all Device Types", device_model._fields["device_uid"].help
        )
        self.assertEqual(
            constraint[2],
            "Device UID must be globally unique across all Device Types.",
        )
