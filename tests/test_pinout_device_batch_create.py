from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, HttpCase, TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestPinoutDeviceBatchCreate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {"name": "Batch Form", "type": "product"}
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Batch Type",
                "code": "BATCH-CREATE",
                "allowed_product_template_ids": [
                    fields.Command.set(cls.product.product_tmpl_id.ids)
                ],
            }
        )
        cls.user = new_test_user(
            cls.env, login="batch_operator", groups="stock.group_stock_user"
        )
        cls.other_user = new_test_user(
            cls.env, login="batch_other", groups="stock.group_stock_user"
        )

    def wizard(self, uids=None, **values):
        return self.env["pinout.device.batch.create.wizard"].create(
            {
                "device_type_id": self.device_type.id,
                "line_ids": [
                    fields.Command.create({"device_uid": uid}) for uid in (uids or [])
                ],
                **values,
            }
        )

    def test_fifty_rows_form_and_result(self):
        with Form(
            self.env["pinout.device.batch.create.wizard"].with_user(self.user)
        ) as form:
            form.device_type_id = self.device_type
            form.current_product_id = self.product
            form.quality_status = "testing"
            form.location_id = self.env.ref("stock.stock_location_stock")
            form.physical_location_note = "Tray 5"
            form.pasted_text = "UID\tMAC\tAddress\n" + "\n".join(
                f"BATCH-{i:03}\tshared-mac\taddress-{i}" for i in range(50)
            )
        wizard = form.record
        wizard.action_parse()
        self.assertEqual(wizard.device_count, 50)
        wizard.action_validate()
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "like", "BATCH-%")])
        )
        stock_models = ("stock.quant", "stock.move", "stock.lot", "mrp.production")
        before = {model: self.env[model].search_count([]) for model in stock_models}
        action = wizard.action_create()
        self.assertEqual(
            before, {model: self.env[model].search_count([]) for model in stock_models}
        )
        devices = self.env["pinout.device"].search(action["domain"])
        self.assertEqual(len(devices), 50)
        self.assertEqual(action["context"], {})
        for device in devices:
            self.assertEqual(device.device_type_id, self.device_type)
            self.assertEqual(device.current_product_id, self.product)
            self.assertEqual(device.state, "wip")
            self.assertEqual(device.quality_status, "testing")
            self.assertEqual(device.physical_location_note, "Tray 5")
            self.assertEqual(device.location_id, wizard.location_id)
            self.assertEqual(device.mac, "shared-mac")
            self.assertFalse(device.final_lot_id | device.final_lot_history_ids)
            self.assertFalse(device.bundle_id)
            self.assertFalse(device.manufacturing_order_ids)

    def test_minimal_rows_defaults_and_cancel(self):
        wizard = self.wizard(pasted_text="MINIMAL-1\nMINIMAL-2")
        wizard.action_parse()
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "=", "MINIMAL-1")])
        )
        # Cancelling a saved transient does not register devices.
        wizard.unlink()
        wizard = self.wizard(["MINIMAL-1"])
        devices = self.env["pinout.device"].search(wizard.action_create()["domain"])
        self.assertEqual(devices.state, "wip")
        self.assertEqual(devices.quality_status, "unknown")
        self.assertFalse(devices.current_product_id)
        self.assertFalse(devices.location_id)
        self.assertFalse(devices.mac)
        self.assertFalse(devices.robonomics_device_address)

    def test_parse_empty_cells_crlf_and_replace(self):
        wizard = self.wizard(
            ["OLD"],
            pasted_text="Device UID\tMAC\tRobonomics Device Address\r\nA\t\taddr\r\n\r\nB\tmac\t\r\n\tmac\taddr\r\nC",
        )
        wizard.action_parse()
        self.assertEqual(wizard.device_count, 4)
        self.assertEqual(wizard.line_ids[0].robonomics_device_address, "addr")
        self.assertFalse(wizard.line_ids[0].mac)
        self.assertEqual(wizard.line_ids[1].mac, "mac")
        self.assertFalse(wizard.line_ids[1].robonomics_device_address)
        with self.assertRaisesRegex(UserError, "Row 3"):
            wizard.action_create()
        wizard.line_ids[2].unlink()
        wizard.line_ids[0].device_uid = "CORRECTED"
        wizard.action_validate()
        self.assertEqual(
            len(self.env["pinout.device"].search(wizard.action_create()["domain"])), 3
        )

    def test_malformed_paste_preserves_rows(self):
        wizard = self.wizard(["KEEP"], pasted_text="A\nB\tmac\taddr\textra")
        with self.assertRaisesRegex(UserError, "Line 2"):
            wizard.action_parse()
        self.assertEqual(wizard.line_ids.device_uid, "KEEP")
        for text in ("", "UID\tMAC\tAddress", "\n\n"):
            wizard.pasted_text = text
            with self.assertRaises(UserError):
                wizard.action_parse()
            self.assertEqual(wizard.line_ids.device_uid, "KEEP")

    def test_duplicate_and_blank_rows(self):
        wizard = self.wizard(["NEW", "NEW", "   "])
        with self.assertRaises(UserError) as error:
            wizard.action_create()
        self.assertIn("Row 2", str(error.exception))
        self.assertIn("Row 3", str(error.exception))
        self.assertEqual(wizard.device_count, 3)
        self.assertFalse(self.env["pinout.device"].search([("device_uid", "=", "NEW")]))
        with self.assertRaises(UserError):
            self.wizard().action_create()

    def test_archived_and_hidden_conflicts(self):
        self.env["pinout.device"].create(
            {
                "device_uid": "TAKEN",
                "device_type_id": self.device_type.id,
                "active": False,
                "notes": "PRIVATE DETAILS",
            }
        )
        self.env["ir.rule"].create(
            {
                "name": "Hide devices for batch test",
                "model_id": self.env["ir.model"]._get_id("pinout.device"),
                "domain_force": "[('device_uid', '!=', 'TAKEN')]",
            }
        )
        wizard = (
            self.env["pinout.device.batch.create.wizard"]
            .with_user(self.user)
            .create(
                {
                    "device_type_id": self.device_type.id,
                    "line_ids": [
                        fields.Command.create({"device_uid": value})
                        for value in ("FREE", "TAKEN")
                    ],
                }
            )
        )
        self.assertFalse(
            self.env["pinout.device"]
            .with_user(self.user)
            .with_context(active_test=False)
            .search([("device_uid", "=", "TAKEN")])
        )
        with self.assertRaisesRegex(
            UserError, "Row 2: Device UID is already in use"
        ) as error:
            wizard.action_validate()
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "=", "FREE")])
        )

    def test_incompatible_form_is_retained(self):
        other_product = self.env["product.product"].create({"name": "Other Form"})
        wizard = self.wizard(["BAD-FORM"], current_product_id=other_product.id)
        with self.assertRaisesRegex(UserError, "not allowed"):
            wizard.action_create()
        self.assertEqual(wizard.current_product_id, other_product)
        self.assertEqual(wizard.line_ids.device_uid, "BAD-FORM")

    def test_late_model_error_rolls_back_whole_batch(self):
        wizard = self.wizard(["ROLLBACK-1", "ROLLBACK-2"])
        model_class = type(self.env["pinout.device"])
        original = model_class.create

        def fail_after_create(model, values):
            original(model, values)
            raise ValidationError("Late model failure")

        with (
            patch.object(model_class, "create", fail_after_create),
            self.assertRaisesRegex(ValidationError, "Late model failure"),
        ):
            wizard.action_create()
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "like", "ROLLBACK-%")])
        )
        self.assertEqual(wizard.device_count, 2)

    def test_uid_race_uses_sql_guard_and_rolls_back(self):
        wizard = self.wizard(["RACE-FREE", "RACE-TAKEN"])
        self.env["pinout.device"].create(
            {"device_uid": "RACE-TAKEN", "device_type_id": self.device_type.id}
        )
        # Simulate another transaction winning after availability was checked.
        with (
            patch.object(type(wizard), "_validate_lines", return_value=None),
            mute_logger("odoo.sql_db"),
            self.assertRaisesRegex(UserError, "no devices were created"),
        ):
            wizard.action_create()
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "=", "RACE-FREE")])
        )
        self.assertEqual(wizard.device_count, 2)

    def test_transient_ownership_and_device_create_rules(self):
        wizard = (
            self.env["pinout.device.batch.create.wizard"]
            .with_user(self.user)
            .create(
                {
                    "device_type_id": self.device_type.id,
                    "line_ids": [fields.Command.create({"device_uid": "ACL-UID"})],
                }
            )
        )
        with self.assertRaises(AccessError):
            wizard.with_user(self.other_user).action_create()
        self.env["ir.rule"].create(
            {
                "name": "Reject new device",
                "model_id": self.env["ir.model"]._get_id("pinout.device"),
                "domain_force": "[('device_uid', '!=', 'ACL-UID')]",
            }
        )
        with self.assertRaises(AccessError):
            wizard.action_create()
        self.assertFalse(
            self.env["pinout.device"].search([("device_uid", "=", "ACL-UID")])
        )

    def test_context_does_not_copy_identity_links(self):
        existing = self.env["pinout.device"].create(
            {"device_uid": "EXISTING", "device_type_id": self.device_type.id}
        )
        wizard = self.wizard(["CLEAN"]).with_context(
            default_active=False,
            default_notes="Unrelated default",
            default_final_lot_id=999999,
            default_bundle_id=999999,
            default_manufacturing_order_ids=[fields.Command.link(999999)],
        )
        devices = self.env["pinout.device"].search(wizard.action_create()["domain"])
        self.assertTrue(devices.active)
        self.assertFalse(devices.notes)
        self.assertFalse(devices.final_lot_id)
        self.assertFalse(devices.bundle_id)
        self.assertFalse(devices.manufacturing_order_ids)
        self.assertEqual(existing.device_uid, "EXISTING")


@tagged("post_install", "-at_install")
class TestPinoutDeviceBatchCreateUI(HttpCase):
    def test_paste_correct_cancel_and_create_fifty(self):
        self.env["pinout.device.type"].create(
            {"name": "Batch UI Type", "code": "BATCH-UI"}
        )
        action = self.env.ref("pinout_device_registry.pinout_device_action")
        self.browser_js(
            f"/web#action={action.id}",
            r"""
            (async () => {
                const wait = async (predicate, label) => {
                    const deadline = Date.now() + 20000;
                    while (Date.now() < deadline) {
                        const value = predicate();
                        if (value) return value;
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                    throw new Error('Timed out: ' + label);
                };
                const element = selector => wait(() => document.querySelector(selector), selector);
                const click = async selector => {
                    const button = await wait(() => {
                        const node = document.querySelector(selector);
                        return node && !node.disabled ? node : null;
                    }, selector);
                    button.click();
                };
                const fill = async (selector, text) => {
                    const input = await element(selector);
                    input.focus();
                    input.value = text;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    if (input.tagName === 'TEXTAREA') {
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.blur();
                    }
                };
                const open = async () => {
                    const button = await wait(() => [...document.querySelectorAll('.o_list_view button')]
                        .find(node => node.textContent.trim() === 'Batch Create'), 'Batch Create button');
                    button.click();
                    await fill('.modal [name="device_type_id"] input', 'Batch UI Type');
                    const option = await wait(() => [...document.querySelectorAll('.o-autocomplete--dropdown-item a')]
                        .find(node => node.textContent.trim() === 'Batch UI Type'), 'Device Type option');
                    option.click();
                };
                const paste = async text => {
                    await fill('.modal [name="pasted_text"] textarea', text);
                    await click('.modal button[name="action_parse"]');
                };
                await open();
                await paste('UI-CANCEL');
                await wait(() => document.querySelectorAll('.modal .o_data_row').length === 1, 'cancel row');
                await click('.modal button[special="cancel"]');
                await wait(() => !document.querySelector('.modal'), 'cancel closes wizard');
                await open();
                await paste('UI-DUPLICATE\nUI-DUPLICATE');
                await wait(() => document.querySelectorAll('.modal .o_data_row').length === 2, 'duplicate rows');
                await click('.modal button[name="action_validate"]');
                const error = await element('.o_error_dialog');
                if (!error.textContent.includes('Row 2')) throw new Error('Missing row diagnostic');
                error.querySelector('button').click();
                await wait(() => !document.querySelector('.o_error_dialog'), 'close error');
                await paste('UID\tMAC\tAddress\n' + Array.from({length: 50}, (_, i) =>
                    `UI-BATCH-${String(i).padStart(3, '0')}\t\taddress-${i}`).join('\n'));
                await wait(() => document.querySelectorAll('.modal .o_data_row').length === 50, '50 parsed rows');
                await click('.modal button[name="action_validate"]');
                await wait(() => document.querySelector('.o_notification')?.textContent.includes('50 devices'), 'checked notification');
                await click('.modal button[name="action_create"]');
                await wait(() => !document.querySelector('.modal') &&
                    document.querySelectorAll('.o_list_view .o_data_row').length === 50, '50 created devices');
                if (!document.querySelector('.o_list_view').textContent.includes('UI-BATCH-049')) {
                    throw new Error('Missing last created device');
                }
                console.log('test successful');
            })().catch(error => console.error(error));
            """,
            login="admin",
            timeout=120,
        )
        devices = self.env["pinout.device"].search(
            [("device_uid", "like", "UI-BATCH-%")]
        )
        self.assertEqual(len(devices), 50)
        self.assertFalse(
            self.env["pinout.device"].search(
                [("device_uid", "in", ["UI-CANCEL", "UI-DUPLICATE"])]
            )
        )
