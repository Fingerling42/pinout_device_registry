from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceBatchUpdate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Batch Update Device Type",
                "code": "BATCH-UPDATE-TEST",
            }
        )
        cls.devices = cls.env["pinout.device"].create(
            [
                {
                    "device_uid": f"BATCH-UPDATE-{number:03d}",
                    "device_type_id": cls.device_type.id,
                }
                for number in range(1, 4)
            ]
        )

    def test_device_selection_can_change_before_apply(self):
        wizard = (
            self.env["pinout.device.batch.update.wizard"]
            .with_context(
                active_model="pinout.device",
                active_ids=self.devices[:2].ids,
            )
            .create({})
        )

        self.assertEqual(wizard.device_count, 2)
        self.assertEqual(wizard.device_ids, self.devices[:2])

        wizard.device_ids = [Command.set(self.devices[1:].ids)]
        self.assertEqual(wizard.device_count, 2)

        wizard.update_state = True
        wizard.state = "ready_for_packaging"
        wizard.action_apply()

        self.assertEqual(self.devices[0].state, "wip")
        self.assertEqual(
            self.devices[1:].mapped("state"),
            ["ready_for_packaging", "ready_for_packaging"],
        )
