from lxml import etree
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


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

    def test_only_checked_fields_are_updated_and_reported_in_chatter(self):
        devices = self.devices[:2]
        wizard = self.env["pinout.device.batch.update.wizard"].create(
            {
                "device_ids": [Command.set(devices.ids)],
                "update_state": True,
                "state": "ready_for_packaging",
                "quality_status": "ok",
                "update_physical_location_note": True,
                "physical_location_note": "Batch shelf A",
            }
        )

        wizard.action_apply()

        self.assertEqual(
            devices.mapped("state"),
            ["ready_for_packaging", "ready_for_packaging"],
        )
        self.assertEqual(devices.mapped("quality_status"), ["unknown", "unknown"])
        self.assertEqual(
            devices.mapped("physical_location_note"),
            ["Batch shelf A", "Batch shelf A"],
        )
        for device in devices:
            batch_messages = device.message_ids.filtered(
                lambda message: "Batch update applied" in (message.body or "")
            )
            self.assertEqual(len(batch_messages), 1)
            message_body = batch_messages.body
            self.assertIn("State", message_body)
            self.assertIn("Ready for Packaging", message_body)
            self.assertIn("Physical Location Note", message_body)
            self.assertNotIn("Quality Status", message_body)

    def test_batch_update_can_start_testing(self):
        devices = self.devices[:2]
        wizard = self.env["pinout.device.batch.update.wizard"].create(
            {
                "device_ids": [Command.set(devices.ids)],
                "update_quality_status": True,
                "quality_status": "testing",
            }
        )

        wizard.action_apply()

        self.assertEqual(devices.mapped("quality_status"), ["testing", "testing"])
        self.assertEqual(devices.mapped("state"), ["wip", "wip"])
        self.assertEqual(self.devices[2].quality_status, "unknown")
        for device in devices:
            batch_messages = device.message_ids.filtered(
                lambda message: "Batch update applied" in (message.body or "")
            )
            self.assertIn("Quality Status", batch_messages.body)
            self.assertIn("Testing", batch_messages.body)

    def test_testing_filter_and_quality_grouping(self):
        self.devices[:2].write({"quality_status": "testing"})
        search_view = self.env.ref("pinout_device_registry.pinout_device_view_search")
        arch = etree.fromstring(search_view.arch_db)
        testing_filter = arch.xpath("//filter[@name='quality_testing']")[0]
        domain = [
            *safe_eval(testing_filter.get("domain")),
            ("id", "in", self.devices.ids),
        ]

        self.assertEqual(self.env["pinout.device"].search(domain), self.devices[:2])
        groups = self.env["pinout.device"].read_group(
            [("id", "in", self.devices.ids)], ["quality_status"], ["quality_status"]
        )
        self.assertEqual(
            {
                group["quality_status"]: group["quality_status_count"]
                for group in groups
            },
            {"testing": 2, "unknown": 1},
        )

    def test_apply_requires_checked_field_and_selected_device(self):
        no_field_wizard = self.env["pinout.device.batch.update.wizard"].create(
            {"device_ids": [Command.set(self.devices[:1].ids)]}
        )
        with self.assertRaisesRegex(UserError, "Select at least one field"):
            no_field_wizard.action_apply()

        no_device_wizard = self.env["pinout.device.batch.update.wizard"].create(
            {
                "device_ids": [Command.clear()],
                "update_state": True,
                "state": "ready_for_packaging",
            }
        )
        with self.assertRaisesRegex(UserError, "Select at least one device"):
            no_device_wizard.action_apply()
