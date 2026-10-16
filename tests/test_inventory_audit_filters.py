from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .test_inventory_audit_calculation import InventoryAuditCase


@tagged("post_install", "-at_install")
class TestInventoryAuditFilters(InventoryAuditCase):
    def test_filters_match_displayed_values_and_combined_domains(self):
        reader = new_test_user(
            self.env,
            login="audit_filter_reader",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        devices = self.env["pinout.device"]
        for index, status in enumerate(
            ("match", "exact_mismatch", "group_shortage", "not_checked")
        ):
            device = self.device("FILTER-%s" % status)
            result = self.result(device)
            result._store_success(result.generation, {"status": status})
            if index % 2:
                result._mark_pending()
            devices |= device
        missing = self.device("FILTER-MISSING")
        self.result(missing).unlink()
        devices |= missing
        hidden = self.device("FILTER-HIDDEN", product=self.products[1])
        hidden_quant = self.quant(1, product=self.products[1])
        result = self.result(hidden)
        result._store_success(result.generation, {"status": "match"})
        devices |= hidden
        archived_source = self.device(
            "FILTER-ARCHIVED-SOURCE", product=self.products[2]
        )
        stale = self.device("FILTER-STALE", product=self.products[2])
        result = self.result(stale)
        result._store_success(result.generation, {"status": "match"})
        archived_source.write({"current_product_id": False, "active": False})
        devices |= stale
        other = self.env["res.company"].create({"name": "Filter other company"})
        location = self.locations[0].copy({"company_id": other.id})
        foreign = self.device("FILTER-FOREIGN", location=location)
        result = (
            self.env["pinout.inventory.audit.device"].sudo()._for_device(foreign, other)
        )
        result._store_success(result.generation, {"status": "match"})
        devices |= foreign
        rules = self.env["ir.rule"].create(
            [
                {
                    "name": "Filter hidden source " + model,
                    "model_id": self.env["ir.model"]._get_id(model),
                    "domain_force": repr([("id", "!=", source.id)]),
                }
                for model, source in (
                    ("stock.quant", hidden_quant),
                    ("pinout.device", archived_source),
                )
            ]
        )
        Device = self.env["pinout.device"].with_user(reader)
        visible_devices = Device.browse(devices.ids)
        self.assertEqual(hidden.with_user(reader).inventory_check_status, "not_checked")
        self.assertEqual(stale.with_user(reader).inventory_check_status, "not_checked")
        status_conditions = [
            (operator, value)
            for status in (
                "match",
                "exact_mismatch",
                "group_shortage",
                "not_checked",
                False,
            )
            for operator, value in (
                ("=", status),
                ("!=", status),
                ("in", [status]),
                ("not in", [status]),
            )
        ] + [
            ("in", []),
            ("not in", []),
            ("in", ["match", "not_checked"]),
            ("=?", False),
            ("=?", "match"),
            ("like", "match"),
            ("not like", "match"),
            ("ilike", "MATCH"),
            ("not ilike", "match"),
            ("=like", "%match"),
            ("=ilike", "%MATCH"),
            ("<", "match"),
            (">", "match"),
            ("<=", "match"),
            (">=", "match"),
        ]
        pending_conditions = [
            (operator, value)
            for pending in (False, True)
            for operator, value in (
                ("=", pending),
                ("!=", pending),
                ("in", [pending]),
                ("not in", [pending]),
                ("=?", pending),
            )
        ] + [("in", []), ("not in", [])]
        with patch.object(
            type(self.config),
            "_calculate",
            side_effect=AssertionError("filter calculates"),
        ):
            for field, conditions in (
                ("inventory_check_status", status_conditions),
                ("inventory_check_pending", pending_conditions),
            ):
                for operator, value in conditions:
                    domain = [(field, operator, value)]
                    with self.subTest(domain=domain):
                        expected = visible_devices.filtered_domain(domain)
                        found = Device.search([("id", "in", devices.ids), *domain])
                        self.assertEqual(set(found.ids), set(expected.ids))
            for domain in (
                [
                    "|",
                    ("inventory_check_status", "=", "match"),
                    ("inventory_check_pending", "=", True),
                ],
                [
                    ("inventory_check_status", "!=", "match"),
                    ("inventory_check_pending", "=", False),
                ],
                ["!", ("inventory_check_status", "in", ["match", "exact_mismatch"])],
                [
                    ("inventory_check_status", "=", "not_checked"),
                    ("device_uid", "ilike", "HIDDEN"),
                ],
                [
                    ("inventory_check_pending", "=", True),
                    ("location_id", "=", self.locations[0].id),
                ],
            ):
                self.assertEqual(
                    set(Device.search([("id", "in", devices.ids), *domain]).ids),
                    set(visible_devices.filtered_domain(domain).ids),
                )
        # Visibility must be recomputed after a rule changes in this same request.
        rules.unlink()
        self.assertIn(
            hidden.id, Device.search([("inventory_check_status", "=", "match")]).ids
        )
        self.assertIn(
            stale.id, Device.search([("inventory_check_status", "=", "match")]).ids
        )

    def test_result_candidates_rules_pagination_and_missing_acl(self):
        reader = new_test_user(
            self.env,
            login="audit_candidate_reader",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        devices = [self.device("CANDIDATE-%s" % i) for i in range(4)]
        results = (
            self.env["pinout.inventory.audit.device"]
            .sudo()
            .browse([self.result(device).id for device in devices])
        )
        for result in results:
            result._store_success(result.generation, {"status": "match"})
        self.env["ir.rule"].create(
            {
                "name": "Hide first result",
                "model_id": self.env["ir.model"]._get_id(results._name),
                "domain_force": repr([("id", "!=", results[0].id)]),
            }
        )
        Result = self.env[results._name].with_user(reader)
        domain = [("id", "in", results.ids)]
        self.assertEqual(
            Result.search(domain, order="id", offset=1, limit=1).ids, results[2].ids
        )
        self.assertEqual(Result.search_count(domain), 3)
        self.assertEqual(
            Result.read_group(domain, ["company_id"], ["company_id"])[0][
                "company_id_count"
            ],
            3,
        )
        with self.assertRaises(AccessError):
            results[0].with_user(reader).read(["status"])
        archived = self.device(
            "CANDIDATE-ARCHIVED", current_product_id=False, active=False
        )
        results[2].write({"source_device_ids": [(4, archived.id)]})
        self.env["ir.rule"].create(
            {
                "name": "Hide archived candidate source",
                "model_id": self.env["ir.model"]._get_id("pinout.device"),
                "domain_force": repr([("id", "!=", archived.id)]),
            }
        )
        self.assertEqual(
            Result.search(domain, order="id", offset=1, limit=1).ids, results[3].ids
        )
        self.assertEqual(Result.search_count(domain), 2)
        with patch.object(
            type(self.env["stock.quant"]), "check_access_rights", return_value=False
        ):
            self.assertFalse(Result.search(domain))
            with self.assertRaises(AccessError):
                results[1].with_user(reader).read(["status"])
        internal = new_test_user(
            self.env,
            login="audit_candidate_internal",
            groups="base.group_user",
            context={"no_reset_password": True},
        )
        with self.assertRaises(AccessError):
            self.env[results._name].with_user(internal).search(domain)
