from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from .test_inventory_audit_calculation import InventoryAuditCase


@tagged("post_install", "-at_install")
class TestInventoryAuditViews(InventoryAuditCase):
    def test_device_fields_filters_and_freshness(self):
        device = self.device()
        self.quant(1)
        self.config.action_refresh()
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("read must not calculate"),
        ):
            self.assertEqual(device.inventory_check_status, "match")
            self.assertFalse(device.inventory_check_pending)
            self.assertTrue(device.last_inventory_check)
            for op, value, found in [
                ("=", "match", True),
                ("!=", "match", False),
                ("in", ["match"], True),
                ("not in", ["match"], False),
            ]:
                self.assertEqual(
                    device
                    in self.env["pinout.device"].search(
                        [("inventory_check_status", op, value)]
                    ),
                    found,
                )
        self.quant(1)
        self.assertTrue(device.inventory_check_pending)
        self.assertEqual(device.inventory_check_status, "match")
        self.assertIn(
            device,
            self.env["pinout.device"].search([("inventory_check_pending", "=", True)]),
        )
        device.action_refresh_inventory_audit()
        self.assertFalse(device.inventory_check_pending)
        self.assertIn("does not prove", device.inventory_check_details)

    def test_missing_and_hidden_results_are_not_checked(self):
        device = self.device()
        self.assertEqual(device.inventory_check_status, "not_checked")
        self.assertTrue(device.inventory_check_pending)
        self.assertIn(
            device,
            self.env["pinout.device"].search(
                [("inventory_check_status", "=", "not_checked")]
            ),
        )
        self.quant(1)
        self.config.action_refresh()
        user = new_test_user(
            self.env,
            login="audit_view_reader",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        self.env["ir.rule"].create(
            {
                "name": "Hide audit stock",
                "model_id": self.env["ir.model"]._get_id("stock.quant"),
                "domain_force": "[('id', '=', 0)]",
                "groups": [
                    fields.Command.link(self.env.ref("stock.group_stock_user").id)
                ],
            }
        )
        reader = device.with_user(user)
        self.assertEqual(reader.inventory_check_status, "not_checked")
        self.assertFalse(reader.last_inventory_check)
        self.assertFalse(reader.inventory_check_error)
        self.assertIn(
            device.id,
            self.env["pinout.device"]
            .with_user(user)
            .search([("inventory_check_status", "!=", "match")])
            .ids,
        )
        self.assertNotIn(
            device.id,
            self.env["pinout.device"]
            .with_user(user)
            .search([("inventory_check_status", "=", "match")])
            .ids,
        )
        config = self.config.with_user(user)
        self.assertFalse(config.surplus_group_count)
        self.assertFalse(config.mismatch_group_ids)
        with self.assertRaises(AccessError):
            self.result(device).group_id.with_user(user).action_open_quants()
        internal = new_test_user(
            self.env,
            login="audit_view_internal",
            groups="base.group_user",
            context={"no_reset_password": True},
        )
        with self.assertRaises(AccessError):
            device.with_user(internal).read(["inventory_check_status"])

    def test_error_keeps_last_success_visible_as_stale(self):
        device = self.device()
        self.quant(1)
        self.config.action_refresh()
        result = self.result(device)
        checked_at = device.last_inventory_check
        result._mark_pending()
        result._store_error(result.generation, "Retry <audit>")
        self.assertEqual(device.inventory_check_status, "match")
        self.assertTrue(device.inventory_check_pending)
        self.assertEqual(device.last_inventory_check, checked_at)
        self.assertEqual(device.inventory_check_error, "Retry <audit>")
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("dashboard must not calculate"),
        ):
            self.config.read(
                [
                    "mismatch_group_count",
                    "surplus_group_count",
                    "not_checked_device_count",
                    "mismatch_group_ids",
                ]
            )
        device.action_refresh_inventory_audit()
        self.assertFalse(device.inventory_check_pending)
        self.assertFalse(device.inventory_check_error)

    def test_allowed_companies_do_not_share_cached_device_status(self):
        other = self.env["res.company"].create({"name": "Audit UI other company"})
        location = self.env["stock.location"].create(
            {"name": "Other stock", "usage": "internal", "company_id": other.id}
        )
        device = self.device(location=location)
        result = (
            self.env["pinout.inventory.audit.device"].sudo()._for_device(device, other)
        )
        result._store_success(
            result.generation, {"status": "match", "details": "Other company snapshot"}
        )
        user = new_test_user(
            self.env,
            login="audit_view_companies",
            groups="stock.group_stock_user",
            company_ids=[fields.Command.set([self.env.company.id, other.id])],
            context={"no_reset_password": True},
        )
        reader = device.with_user(user).with_context(
            allowed_company_ids=[self.env.company.id, other.id]
        )
        self.assertEqual(reader.inventory_check_status, "match")
        limited = reader.with_context(allowed_company_ids=[self.env.company.id])
        self.assertEqual(limited.inventory_check_status, "not_checked")
        self.assertFalse(limited.last_inventory_check)
        self.assertNotIn(
            device.id,
            self.env["pinout.device"]
            .with_user(user)
            .with_context(allowed_company_ids=[self.env.company.id])
            .search([("inventory_check_status", "=", "match")])
            .ids,
        )

    def test_dashboard_counts_and_navigation(self):
        device, _lot = self.serial()
        self.quant(1, product=self.products[1])  # Same total, wrong serial.
        self.quant(3, location=self.locations[1])
        self.device("INCOMPLETE", current_product_id=False)
        self.config.action_refresh()
        self.assertEqual(self.config.mismatch_group_count, 1)
        self.assertEqual(self.config.surplus_group_count, 1)
        self.assertEqual(self.config.not_checked_device_count, 1)
        group = self.config.mismatch_group_ids
        self.assertEqual(group.exact_mismatch_count, 1)
        self.assertFalse(group.shortage_quantity)
        self.assertFalse(group.surplus_quantity)
        quant_action = group.action_open_quants()
        quants = self.env["stock.quant"].search(quant_action["domain"])
        self.assertEqual(quants.product_id, self.products[1])
        self.assertEqual(quants.location_id, self.locations[0])
        self.assertEqual(quants.company_id, self.env.company)
        self.assertEqual(
            self.env["pinout.device"].search(group.action_open_devices()["domain"]),
            device,
        )
        self.assertEqual(
            self.config.action_open_surplus()["context"], {"search_default_surplus": 1}
        )
        self.assertEqual(self.config.action_open_dashboard()["res_id"], self.config.id)
        self.assertEqual(
            self.config.action_open_configuration()["res_id"], self.config.id
        )
        self.assertEqual(
            self.env["pinout.inventory.audit.device"].search_count(
                self.config.action_open_not_checked()["domain"]
            ),
            1,
        )


@tagged("post_install", "-at_install")
class TestInventoryAuditUI(InventoryAuditCase, HttpCase):
    def test_dashboard_refresh_surplus_and_stock_navigation(self):
        self.device("UI-AUDIT-SHORTAGE")
        self.quant(3, location=self.locations[1])
        self.config.action_refresh()
        action = self.env.ref("pinout_device_registry.inventory_audit_dashboard_action")
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
                const click = async selector => {
                    const el = await wait(() => document.querySelector(selector), selector);
                    el.click();
                };
                await wait(() => document.querySelector('[name="surplus_group_count"]')?.textContent.includes('1'), 'surplus count');
                await wait(() => document.querySelector('[name="mismatch_group_ids"]')?.textContent.includes('Group Shortage'), 'default discrepancies');
                await click('button[name="action_refresh"]');
                await wait(() => document.querySelector('button[name="action_refresh"]:not(:disabled)'), 'refresh done');
                await click('button[name="action_open_surplus"]');
                await wait(() => document.querySelector('.o_list_view')?.textContent.includes('Calculation B'), 'surplus location');
                if (document.querySelector('.o_list_view').textContent.includes('Calculation A')) throw new Error('Surplus filter includes shortage');
                await click('.o_data_row .o_data_cell');
                await wait(() => document.querySelector('button[name="action_open_quants"]'), 'group form');
                await click('button[name="action_open_quants"]');
                await wait(() => document.querySelector('.o_list_view')?.textContent.includes('Calculation B'), 'stock navigation');
                console.log('test successful');
            })().catch(error => console.error(error));
            """,
            login="admin",
            timeout=120,
        )

    def test_device_stale_refresh_and_configuration(self):
        device = self.device("UI-AUDIT-DEVICE")
        self.config.action_refresh()
        self.quant(1)
        self.assertTrue(device.inventory_check_pending)
        self.browser_js(
            f"/web#model=pinout.device&id={device.id}&view_type=form",
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
                const tab = await wait(() => [...document.querySelectorAll('.o_notebook .nav-link')]
                    .find(node => node.textContent.includes('Inventory Audit')), 'audit tab');
                tab.click();
                await wait(() => document.querySelector('[name="inventory_check_status"]')?.textContent.includes('Group Shortage'), 'last successful shortage');
                const pending = await wait(() => document.querySelector('[name="inventory_check_pending"] input'), 'pending checkbox');
                if (!pending.checked) throw new Error('Stale status is not marked pending');
                document.querySelector('button[name="action_refresh_inventory_audit"]').click();
                await wait(() => document.querySelector('[name="inventory_check_status"]')?.textContent.includes('Match'), 'refreshed Match');
                if (document.querySelector('[name="inventory_check_pending"] input').checked) throw new Error('Refresh did not clear pending');
                if (!document.querySelector('[name="inventory_check_details"]').textContent.includes('does not prove')) throw new Error('Missing untracked semantics');
                console.log('test successful');
            })().catch(error => console.error(error));
            """,
            login="admin",
            timeout=120,
        )
        dashboard = self.env.ref(
            "pinout_device_registry.inventory_audit_dashboard_action"
        )
        self.browser_js(
            f"/web#action={dashboard.id}",
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
                const configure = await wait(() => document.querySelector('button[name="action_open_configuration"]'), 'configure scope');
                configure.click();
                await wait(() => document.querySelector('[name="scope_ids"]')?.textContent.includes('Calculation A'), 'configured location A');
                if (!document.querySelector('[name="scope_ids"]').textContent.includes('Calculation B')) throw new Error('Missing second scope');
                await wait(() => document.querySelector('[name="include_children"] input')?.checked, 'include children');
                console.log('test successful');
            })().catch(error => console.error(error));
            """,
            login="admin",
            timeout=120,
        )
