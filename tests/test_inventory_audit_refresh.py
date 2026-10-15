from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from psycopg2 import OperationalError

from .test_inventory_audit_calculation import InventoryAuditCase


@tagged("post_install", "-at_install")
class TestInventoryAuditRefresh(InventoryAuditCase):
    def _finish_pending_one_at_time(self):
        domain = [("company_id", "=", self.env.company.id), ("pending", "=", True)]
        self.assertFalse(self.env["pinout.inventory.audit.group"].search(domain))
        Result = self.env["pinout.inventory.audit.device"]
        for remaining in range(Result.search_count(domain), 0, -1):
            self.assertFalse(self.config.last_full_check_at)
            self.config._process_refresh(limit=1)
            self.assertEqual(Result.search_count(domain), remaining - 1)
            self.assertEqual(bool(self.config.last_full_check_at), remaining == 1)
        self.assertTrue(self.config.last_full_check_at)
        self.assertFalse(self.config.full_check_started_at)

    def test_full_refresh_does_not_repeat_groups_for_initially_ungrouped_devices(self):
        for i in range(4):
            self.device("FIRST-REFRESH-%s" % i, location=self.locations[i % 2])
        with patch.object(
            type(self.config),
            "_calculate",
            autospec=True,
            side_effect=type(self.config)._calculate,
        ) as calculate:
            self.config.action_refresh()
        group_calls = [
            call.args[1] for call in calculate.call_args_list if call.args[1]
        ]
        self.assertEqual(len(group_calls), 2)
        self.assertEqual(
            {group.location_id.id for group in group_calls}, set(self.locations[:2].ids)
        )
        for call in calculate.call_args_list:
            if not call.args[1]:
                self.assertFalse(call.args[2].location_id.company_id)
        self.assertFalse(
            self.env["pinout.inventory.audit.device"].search(
                [("company_id", "=", self.env.company.id), ("pending", "=", True)]
            )
        )

    def test_bounded_manual_processing_and_idempotence(self):
        self.device()
        self.quant(1)
        self.quant(3, location=self.locations[1])
        self.config._queue_full_check()
        self.config._process_refresh(limit=1)
        groups = (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)])
        )
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups.filtered("pending")), 1)
        self.assertTrue(self.config.full_check_started_at)
        self.assertFalse(self.config.last_full_check_at)
        self.config._process_refresh(limit=1)
        self.assertFalse(groups.filtered("pending"))
        self._finish_pending_one_at_time()
        self.config._process_refresh(limit=1)
        self.assertEqual(
            groups,
            self.env["pinout.inventory.audit.group"]
            .sudo()
            .search([("company_id", "=", self.env.company.id)]),
        )
        surplus = groups.filtered(lambda g: g.location_id == self.locations[1])
        self.assertEqual(surplus.surplus_quantity, 3)

    def test_legacy_cron_never_recalculates(self):
        device = self.device()
        quant = self.quant(1)
        self.config.action_refresh()
        result = self.result(device)
        checked_at = result.checked_at
        self.config.last_full_check_at = fields.Datetime.now() - timedelta(days=2)
        with (
            patch.object(
                type(self.config),
                "_calculate",
                side_effect=AssertionError("automatic calculation"),
            ),
            patch.object(
                type(self.config),
                "_queue_full_check",
                side_effect=AssertionError("automatic discovery"),
            ),
        ):
            # Old results do not trigger a daily recovery pass.
            self.config._cron_refresh()
            self.assertEqual(result.status, "match")
            self.assertFalse(result.pending)
            # ORM changes only mark the successful snapshot stale.
            quant.quantity = 0
            self.assertTrue(result.pending)
            self.config._cron_refresh()
            self.assertEqual(result.status, "match")
            self.assertEqual(result.checked_at, checked_at)
            # A changed scope does not schedule an automatic full pass either.
            self.config.scope_ids.filtered(
                lambda scope: scope.location_id == self.locations[0]
            ).unlink()
            self.assertTrue(self.config.full_check_pending)
            self.config._cron_refresh()
            self.assertTrue(self.config.full_check_pending)
        self.config.action_refresh()
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.pending)
        self.assertFalse(self.config.full_check_pending)
        self.assertTrue(self.config.last_full_check_at)

    def test_legacy_cron_is_inactive(self):
        cron = self.env.ref("pinout_device_registry.inventory_audit_refresh_cron")
        self.assertFalse(cron.active)

    def test_migration_disables_noupdate_cron_without_changing_snapshots(self):
        device = self.device()
        quant = self.quant(1)
        self.config.action_refresh()
        result = self.result(device)
        group = result.group_id
        quant.quantity = 0
        self.config.last_error = "Previous failure"
        self.config.full_check_started_at = fields.Datetime.now()
        cron = self.env.ref("pinout_device_registry.inventory_audit_refresh_cron")
        data = self.env["ir.model.data"].search(
            [
                ("module", "=", "pinout_device_registry"),
                ("name", "=", "inventory_audit_refresh_cron"),
            ]
        )
        self.assertTrue(data.noupdate)
        cron.active = True
        unrelated = cron.copy({"name": "Unrelated test cron", "active": True})
        records = [self.config, result, group, device, quant]
        snapshots = [record.read(load=None) for record in records]
        path = (
            Path(__file__).resolve().parents[1]
            / "migrations/17.0.1.16.0/post-disable-inventory-audit-cron.py"
        )
        spec = spec_from_file_location("disable_inventory_audit_cron", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        for _attempt in range(2):
            migration.migrate(self.env.cr, "17.0.1.15.0")
            self.assertFalse(cron.active)
            self.assertTrue(unrelated.active)
            self.assertTrue(data.noupdate)
            for record, before in zip(records, snapshots, strict=True):
                self.assertEqual(record.read(load=None), before)

    def test_device_refresh_updates_entire_group_only(self):
        first = self.device()
        second = self.device("CALC-2")
        other = self.device("CALC-3", location=self.locations[1])
        self.quant(2)
        first.action_refresh_inventory_audit()
        self.assertEqual(self.result(first).status, "match")
        self.assertEqual(self.result(second).status, "match")
        self.assertFalse(self.result(first).pending)
        self.assertTrue(self.result(other).pending)
        first.location_id = self.locations[1]
        first.action_refresh_inventory_audit()
        self.assertEqual(self.result(second).group_id.expected_quantity, 1)
        self.assertEqual(self.result(other).status, "group_shortage")
        first.write({"location_id": False})
        first.action_refresh_inventory_audit()
        self.assertEqual(self.result(first).status, "not_checked")
        self.assertFalse(self.result(first).pending)
        self.assertEqual(self.result(other).group_id.expected_quantity, 1)

    def test_error_preserves_snapshot_and_retries(self):
        device = self.device()
        quant = self.quant(1)
        self.config.action_refresh()
        result = self.result(device)
        group = result.group_id
        checked_at = group.checked_at
        quant.quantity = 0
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=ValueError("private source details"),
        ):
            self.config.action_refresh()
        self.assertEqual(group.status, "match")
        self.assertEqual(result.status, "match")
        self.assertEqual(group.checked_at, checked_at)
        self.assertTrue(group.pending)
        self.assertIn("ValueError", group.last_error)
        self.assertNotIn("private source", group.last_error)
        self.config.action_refresh()
        self.assertEqual(group.status, "group_shortage")
        self.assertFalse(group.last_error)
        self.assertFalse(result.pending)

    def test_partial_store_is_rolled_back_and_other_group_runs(self):
        device = self.device()
        other = self.device("CALC-2", location=self.locations[1])
        self.quant(1)
        self.config.action_refresh()
        last_full_check_at = self.config.last_full_check_at
        checked_at = self.result(device).checked_at
        self.quant(-1)
        self.quant(1, location=self.locations[1])
        Group = type(self.env["pinout.inventory.audit.group"])
        original = Group._store_success

        def fail_one(group, generation, values):
            if group.location_id == self.locations[0]:
                raise ValueError("store failed")
            return original(group, generation, values)

        with patch.object(Group, "_store_success", fail_one):
            self.config.action_refresh()
        self.assertEqual(self.result(device).status, "match")
        self.assertTrue(self.result(device).pending)
        self.assertEqual(self.result(device).checked_at, checked_at)
        self.assertEqual(self.config.last_full_check_at, last_full_check_at)
        self.assertTrue(self.config.last_error)
        self.assertFalse(self.result(other).pending)
        self.assertEqual(self.result(other).status, "match")
        self.config.action_refresh()
        self.assertEqual(self.result(device).status, "group_shortage")
        self.assertFalse(self.config.last_error)
        self.assertFalse(self.config.full_check_started_at)

    def test_selected_check_failure_rolls_back_the_request(self):
        first = self.device()
        second = self.device("SELECTED-2", location=self.locations[1])
        self.quant(1)
        self.quant(1, location=self.locations[1])
        self.config.action_refresh()
        self.quant(-1)
        self.quant(-1, location=self.locations[1])
        results = self.result(first) | self.result(second)
        groups = results.group_id
        before_results = results.read(load=None)
        before_groups = groups.read(load=None)
        Group = type(groups)
        original = Group._store_success
        calls = []

        def fail_second(group, generation, values):
            calls.append(group.id)
            if len(calls) == 2:
                raise ValueError("selected store failed")
            return original(group, generation, values)

        with patch.object(Group, "_store_success", fail_second):
            with self.assertRaisesRegex(ValueError, "selected store failed"):
                # Model the RPC rollback without committing test data.
                with self.env.cr.savepoint():
                    (first | second).action_refresh_inventory_audit()
        self.assertEqual(len(calls), 2)
        self.assertEqual(results.read(load=None), before_results)
        self.assertEqual(groups.read(load=None), before_groups)

    def test_generation_captured_before_calculation(self):
        device = self.device()
        self.quant(1)
        self.config.action_refresh()
        group = self.result(device).group_id
        original = type(self.config)._calculate

        def changed(config, *args):
            snapshot = original(config, *args)
            group._mark_pending()
            return snapshot

        with patch.object(type(self.config), "_calculate", changed):
            with self.assertRaisesRegex(ValueError, "changed during calculation"):
                self.config._recalculate()
        self.assertTrue(group.pending)
        self.assertEqual(group.status, "match")
        self.config.action_refresh()
        self.assertFalse(group.pending)

    def test_database_conflicts_propagate(self):
        self.device()
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=OperationalError("serialization conflict"),
        ):
            with self.assertRaises(OperationalError):
                self.config.action_refresh()

    def test_refresh_access_and_no_calculation_on_read(self):
        device = self.device()
        operator = new_test_user(
            self.env,
            login="refresh_operator",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        internal = new_test_user(
            self.env,
            login="refresh_internal",
            groups="base.group_user",
            context={"no_reset_password": True},
        )
        device.with_user(operator).action_refresh_inventory_audit()
        with self.assertRaises(AccessError):
            device.with_user(internal).action_refresh_inventory_audit()
        with self.assertRaises(AccessError):
            self.config.with_user(internal).action_refresh()
        foreign = self.env["res.company"].create({"name": "Refresh foreign"})
        config = (
            self.env["pinout.inventory.audit.config"]
            .sudo()
            .create({"company_id": foreign.id})
        )
        with self.assertRaises(AccessError):
            config.with_user(operator).action_refresh()
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("read calculated"),
        ):
            device.read(["device_uid", "current_product_id"])
            self.result(device).read(["status", "pending"])

    def test_concurrent_manual_checks_share_lock(self):
        # Advisory locks are visible across real connections without committing
        # test Devices or other fixture data.
        with self.registry.cursor() as other_cr:
            other_cr.execute(
                "SELECT pg_advisory_xact_lock(734921, %s)", [self.env.company.id]
            )
            self.assertFalse(self.config._try_refresh_lock())
            with patch.object(
                type(self.config),
                "_calculate",
                side_effect=AssertionError("concurrent calculation"),
            ):
                self.config._cron_refresh()
            with self.assertRaisesRegex(UserError, "already running"):
                self.config.action_refresh()
            device = self.device()
            with self.assertRaisesRegex(UserError, "already running"):
                device.action_refresh_inventory_audit()
        self.assertTrue(self.config._try_refresh_lock())

    def test_discovery_failure_is_pending_and_retryable(self):
        device = self.device()
        self.quant(1)
        self.config.action_refresh()
        result = self.result(device)
        before = result.read(load=None)
        last_full_check_at = self.config.last_full_check_at
        original = type(self.config)._queue_full_check

        def fail_after_discovery(config):
            original(config)
            raise ValueError("private discovery details")

        with patch.object(
            type(self.config),
            "_queue_full_check",
            fail_after_discovery,
        ):
            self.config.action_refresh()
        self.assertTrue(self.config.full_check_pending)
        self.assertEqual(self.config.last_full_check_at, last_full_check_at)
        self.assertEqual(result.read(load=None), before)
        self.assertIn("discovery failed (ValueError)", self.config.last_error)
        self.assertNotIn("private discovery", self.config.last_error)
        self.config.action_refresh()
        self.assertFalse(self.config.full_check_pending)
        self.assertTrue(self.config.last_full_check_at)
        self.assertFalse(self.config.last_error)

    def test_pending_incomplete_devices_are_bounded_and_finish(self):
        devices = self.device(location_id=False) | self.device(
            "CALC-2", current_product_id=False
        )
        self.config._queue_full_check()
        self.assertTrue(self.config.full_check_started_at)
        self.assertEqual(
            len(
                self.env["pinout.inventory.audit.device"]
                .sudo()
                .search([("device_id", "in", devices.ids), ("pending", "=", True)])
            ),
            2,
        )
        self._finish_pending_one_at_time()
        for device in devices:
            self.assertEqual(self.result(device).status, "not_checked")
            self.assertFalse(self.result(device).pending)

    def test_pending_aggregation_is_restricted_to_the_selected_key(self):
        self.device()
        self.quant(1)
        self.quant(100, location=self.locations[1])
        self.config._queue_full_check()
        Group = self.env["pinout.inventory.audit.group"].sudo()
        first = Group.search([("location_id", "=", self.locations[0].id)])
        snapshot = self.config._calculate(first, self.env["pinout.device"])
        self.assertEqual(len(snapshot["groups"]), 1)
        self.assertEqual(snapshot["groups"][0]["location"], self.locations[0])
        self.assertEqual(snapshot["groups"][0]["values"]["actual_quantity"], 1)
        self.assertTrue(
            Group.search([("location_id", "=", self.locations[1].id)]).pending
        )

    def test_committed_source_change_cannot_publish_old_match(self):
        # Commit only isolated source/result fixtures, never the test transaction.
        # Scope is supplied directly. A bare company isolates row locks without
        # invoking Odoo's company setup or changing shared users/stock defaults.
        from odoo import SUPERUSER_ID, api, models
        from odoo.tools import mute_logger
        from psycopg2.errors import SerializationFailure

        cleanup_ids = None
        try:
            # These sources have no committed audit configuration. Their global
            # scope hooks are irrelevant to the calculation race and would lock
            # unrelated configurations held by the main test transaction.
            # Keep Quant dirty markers enabled in the actual source transaction.
            with (
                self.registry.cursor() as setup_cr,
                patch.object(type(self.config), "_invalidate_scope", return_value=None),
            ):
                setup_cr.execute("SET LOCAL lock_timeout = '5s'")
                env = api.Environment(setup_cr, SUPERUSER_ID, {})
                partner = env["res.partner"].create({"name": "Concurrency fixture"})
                company = models.Model.create(
                    env["res.company"],
                    [
                        {
                            "name": "Concurrency fixture",
                            "partner_id": partner.id,
                            "currency_id": env.company.currency_id.id,
                        }
                    ],
                )
                company_id, partner_id = company.id, partner.id
                location = env["stock.location"].create(
                    {
                        "name": "Concurrency",
                        "usage": "internal",
                        "company_id": company.id,
                    }
                )
                product = env["product.product"].create(
                    {"name": "Concurrency", "type": "product"}
                )
                device_type = env["pinout.device.type"].create(
                    {
                        "name": "Concurrency",
                        "code": "AUDIT-CONCURRENCY",
                        "allowed_product_template_ids": [
                            fields.Command.set(product.product_tmpl_id.ids)
                        ],
                    }
                )
                device = env["pinout.device"].create(
                    {
                        "device_uid": "AUDIT-CONCURRENCY-1",
                        "device_type_id": device_type.id,
                        "current_product_id": product.id,
                        "location_id": location.id,
                    }
                )
                quant = env["stock.quant"].create(
                    {
                        "product_id": product.id,
                        "location_id": location.id,
                        "quantity": 1,
                    }
                )
                group = env["pinout.inventory.audit.group"]._get_or_create(
                    company, product, location
                )
                result = env["pinout.inventory.audit.device"]._for_device(
                    device, company, group
                )
                group._store_success(
                    group.generation,
                    {"status": "match", "actual_quantity": 1, "expected_quantity": 1},
                )
                result._store_success(result.generation, {"status": "match"})
                group_id, quant_id, product_id, location_id = (
                    group.id,
                    quant.id,
                    product.id,
                    location.id,
                )
                cleanup_ids = (device.id, product.product_tmpl_id.id, device_type.id)
                setup_cr.commit()
            original = type(self.config)._calculate

            def change_after_read(config, *args):
                snapshot = original(config, *args)
                with self.registry.cursor() as source_cr:
                    env = api.Environment(source_cr, SUPERUSER_ID, {})
                    env["stock.quant"].browse(quant_id).quantity = 0
                    # Same dirty marker used by the source hooks, in the source
                    # transaction. There is deliberately no committed config.
                    env["pinout.inventory.audit.group"].browse(group_id)._mark_pending()
                    source_cr.commit()
                return snapshot

            Config = type(self.config)
            with (
                patch.object(
                    Config,
                    "_get_products",
                    lambda config: config.env["product.product"].browse(product_id),
                ),
                patch.object(
                    Config,
                    "_get_locations",
                    lambda config: config.env["stock.location"].browse(location_id),
                ),
            ):
                with self.registry.cursor() as worker_cr:
                    env = api.Environment(worker_cr, SUPERUSER_ID, {})
                    worker = env["pinout.inventory.audit.config"].new(
                        {"company_id": company_id}
                    )
                    self.assertTrue(worker._try_refresh_lock())
                    with (
                        patch.object(Config, "_calculate", change_after_read),
                        mute_logger("odoo.sql_db"),
                    ):
                        with self.assertRaises(SerializationFailure):
                            worker._recalculate()
                    worker_cr.rollback()
                with self.registry.cursor() as retry_cr:
                    env = api.Environment(retry_cr, SUPERUSER_ID, {})
                    group = env["pinout.inventory.audit.group"].browse(group_id)
                    self.assertTrue(group.pending)
                    self.assertEqual(group.status, "match")
                    config = env["pinout.inventory.audit.config"].new(
                        {"company_id": company_id}
                    )
                    config._recalculate()
                    self.assertFalse(group.pending)
                    self.assertEqual(group.status, "group_shortage")
                    retry_cr.commit()
        finally:
            if cleanup_ids:
                with (
                    self.registry.cursor() as cleanup_cr,
                    patch.object(
                        type(self.config), "_invalidate_scope", return_value=None
                    ),
                ):
                    env = api.Environment(cleanup_cr, SUPERUSER_ID, {})
                    device_id, template_id, type_id = cleanup_ids
                    env["pinout.inventory.audit.group"].browse(group_id).unlink()
                    env["pinout.device"].browse(device_id).unlink()
                    env["stock.quant"].browse(quant_id).unlink()
                    env["pinout.device.type"].browse(type_id).unlink()
                    env["product.template"].browse(template_id).unlink()
                    env["stock.location"].browse(location_id).unlink()
                    env["res.company"].browse(company_id).unlink()
                    env["res.partner"].browse(partner_id).unlink()
                    cleanup_cr.commit()
