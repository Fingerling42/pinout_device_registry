from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


@tagged("post_install", "-at_install")
class TestInventoryAuditStorage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "Audit Other Company"}
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Audit Form", "type": "product"}
        )
        cls.location = cls.env["stock.location"].create(
            {
                "name": "Audit Location",
                "usage": "internal",
                "company_id": cls.company.id,
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Audit Type",
                "code": "AUDIT-STORAGE",
                "allowed_product_template_ids": [
                    fields.Command.set(cls.product.product_tmpl_id.ids)
                ],
            }
        )
        cls.device = cls.env["pinout.device"].create(
            {
                "device_uid": "AUDIT-001",
                "device_type_id": cls.device_type.id,
                "current_product_id": cls.product.id,
                "location_id": cls.location.id,
            }
        )
        cls.operator = new_test_user(
            cls.env,
            login="audit_storage_operator",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        cls.internal = new_test_user(
            cls.env,
            login="audit_storage_internal",
            groups="base.group_user",
            context={"no_reset_password": True},
        )
        cls.Group = cls.env["pinout.inventory.audit.group"].sudo()
        cls.Result = cls.env["pinout.inventory.audit.device"].sudo()

    def group(self, location=None):
        return self.Group._get_or_create(
            self.company, self.product, location or self.location
        )

    def test_unique_keys_and_idempotent_creation(self):
        group = self.group()
        self.assertEqual(group, self.group())
        self.assertTrue(group.pending)
        self.assertEqual(group.status, "not_checked")
        self.assertFalse(group.checked_at)
        result = self.Result._for_device(self.device, self.company, group)
        self.assertEqual(
            result, self.Result._for_device(self.device, self.company, group)
        )
        self.assertEqual(result.group_id, group)
        for model, values in (
            (
                self.Group,
                {
                    "company_id": self.company.id,
                    "product_id": self.product.id,
                    "location_id": self.location.id,
                },
            ),
            (self.Result, {"company_id": self.company.id, "device_id": self.device.id}),
        ):
            with (
                mute_logger("odoo.sql_db"),
                self.assertRaises(IntegrityError),
                self.cr.savepoint(),
            ):
                model.create(values)
        config = self.env["pinout.inventory.audit.config"].create(
            {"company_id": self.company.id}
        )
        self.assertTrue(config.full_check_pending)
        self.assertFalse(config.scope_ids)
        with (
            mute_logger("odoo.sql_db"),
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
        ):
            config.copy()
        scope = self.env["pinout.inventory.audit.scope"].create(
            {
                "config_id": config.id,
                "location_id": self.location.id,
            }
        )
        with (
            mute_logger("odoo.sql_db"),
            self.assertRaises(IntegrityError),
            self.cr.savepoint(),
        ):
            scope.copy()
        other_location = self.location.copy({"company_id": self.other_company.id})
        with (
            self.assertRaisesRegex(UserError, "Incompatible companies"),
            self.cr.savepoint(),
        ):
            scope.location_id = other_location

    def test_success_error_pending_and_generation(self):
        group = self.group()
        values = {"status": "match", "expected_quantity": 1, "actual_quantity": 1}
        self.assertTrue(group._store_success(group.generation, values))
        checked_at = group.checked_at
        generation = group.generation
        group._mark_pending()
        self.assertEqual(group.status, "match")
        self.assertTrue(group.pending)
        self.assertFalse(group._store_success(generation, {"status": "group_shortage"}))
        group._store_error(group.generation, "Retry this group")
        self.assertEqual(group.checked_at, checked_at)
        self.assertEqual(group.actual_quantity, 1)
        self.assertEqual(group.last_error, "Retry this group")
        self.assertTrue(group._store_success(group.generation, values))
        self.assertFalse(group.pending)
        self.assertFalse(group.last_error)
        self.assertEqual(len(self.Group.search([])), 1)

    def test_changed_keys_and_discard_clear_old_match(self):
        group = self.group()
        result = self.Result._for_device(self.device, self.company, group)
        result._store_success(result.generation, {"status": "match"})
        location = self.location.copy({"name": "Audit New Location"})
        self.device.location_id = location
        result = self.Result._for_device(
            self.device, self.company, self.group(location)
        )
        self.assertEqual(result.status, "not_checked")
        self.assertTrue(result.pending)
        self.assertFalse(result.checked_at)
        self.assertEqual(result.location_id, location)
        result._store_success(result.generation, {"status": "match"})
        result.group_id._discard()
        self.assertFalse(result.group_id)
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.checked_at)
        self.device.unlink()
        self.assertFalse(result.exists())

    def test_snapshot_can_describe_incomplete_and_conflicting_inputs(self):
        self.device.write({"current_product_id": False, "location_id": False})
        result = self.Result._for_device(self.device, self.company)
        self.assertFalse(result.product_id)
        self.assertFalse(result.location_id)
        self.assertEqual(result.status, "not_checked")
        # Registry has no company_id: diagnostic snapshots must allow conflicts.
        other_location = self.location.copy({"company_id": self.other_company.id})
        self.device.location_id = other_location
        result = self.Result._for_device(self.device, self.company)
        self.assertEqual(result.location_id, other_location)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            result.write({"group_id": self.group().id})

    def test_read_only_acl_and_company_rules(self):
        group = self.group()
        result = self.Result._for_device(self.device, self.company, group)
        for record in (group, result):
            operator_record = record.with_user(self.operator)
            self.assertTrue(operator_record.read(["status"]))
            with self.assertRaises(AccessError):
                operator_record.write({"status": "match"})
            with self.assertRaises(AccessError):
                operator_record.unlink()
            with self.assertRaises(AccessError):
                record.with_user(self.internal).read(["status"])
            with self.assertRaises(AccessError):
                operator_record._mark_pending()
        other_location = self.location.copy({"company_id": self.other_company.id})
        other_group = self.Group._get_or_create(
            self.other_company, self.product, other_location
        )
        self.assertNotIn(
            other_group.id, self.Group.with_user(self.operator).search([]).ids
        )
        with self.assertRaises(AccessError):
            other_group.with_user(self.operator).read(["actual_quantity"])

    def test_hidden_source_blocks_search_read_and_grouping(self):
        group = self.group()
        result = self.Result._for_device(self.device, self.company, group)
        quant = (
            self.env["stock.quant"]
            .sudo()
            .create(
                {
                    "product_id": self.product.id,
                    "location_id": self.location.id,
                    "quantity": 1,
                }
            )
        )
        lot = self.env["stock.lot"].create(
            {
                "name": "AUDIT-LOT",
                "product_id": self.product.id,
                "company_id": self.company.id,
            }
        )
        for model_name, hidden_id in (
            ("stock.lot", lot.id),
            ("stock.quant", quant.id),
            ("pinout.device", self.device.id),
            ("product.product", self.product.id),
            ("stock.location", self.location.id),
        ):
            rule = self.env["ir.rule"].create(
                {
                    "name": "Audit hidden source",
                    "model_id": self.env["ir.model"]._get_id(model_name),
                    "domain_force": repr([("id", "!=", hidden_id)]),
                }
            )
            for record in (group, result):
                model = self.env[record._name].with_user(self.operator)
                self.assertNotIn(record.id, model.search([]).ids, model_name)
                self.assertFalse(
                    model.read_group([], ["company_id"], ["company_id"]), model_name
                )
                with self.assertRaises(AccessError):
                    record.with_user(self.operator).read(["status"])
            rule.unlink()
            self.assertIn(group.id, self.Group.with_user(self.operator).search([]).ids)

    def test_stale_snapshot_retains_access_to_its_original_sources(self):
        group = self.group()
        quant = (
            self.env["stock.quant"]
            .sudo()
            .create(
                {
                    "product_id": self.product.id,
                    "location_id": self.location.id,
                    "quantity": 1,
                }
            )
        )
        group._store_success(
            group.generation, {"status": "match", "actual_quantity": 1}
        )
        self.assertEqual(group.source_device_ids, self.device)
        self.assertEqual(group.source_quant_ids, quant)
        self.device.current_product_id = False
        group._mark_pending()
        rule = self.env["ir.rule"].create(
            {
                "name": "Hide former snapshot Device",
                "model_id": self.env["ir.model"]._get_id("pinout.device"),
                "domain_force": repr([("id", "!=", self.device.id)]),
            }
        )
        operator_group = group.with_user(self.operator)
        self.assertFalse(operator_group.search([("id", "=", group.id)]))
        with self.assertRaises(AccessError):
            operator_group.read(["expected_quantity"])
        rule.unlink()
        other_product = self.env["product.product"].create(
            {"name": "Other Audit Form", "type": "product"}
        )
        quant.product_id = other_product
        rule = self.env["ir.rule"].create(
            {
                "name": "Hide former snapshot Quant",
                "model_id": self.env["ir.model"]._get_id("stock.quant"),
                "domain_force": repr([("id", "!=", quant.id)]),
            }
        )
        self.assertFalse(operator_group.search([("id", "=", group.id)]))
        with self.assertRaises(AccessError):
            operator_group.read(["actual_quantity"])
        # A successful replacement forgets old sources instead of accumulating history.
        group._store_success(
            group.generation, {"status": "match", "actual_quantity": 0}
        )
        self.assertFalse(group.source_device_ids)
        self.assertFalse(group.source_quant_ids)
        self.assertTrue(operator_group.search([("id", "=", group.id)]))

    def test_storage_does_not_modify_sources_and_rollback_is_atomic(self):
        before = self.device.read()[0]
        stock_models = ("stock.quant", "stock.move", "stock.lot", "mrp.production")
        counts = {model: self.env[model].search_count([]) for model in stock_models}
        group = self.group()
        result = self.Result._for_device(self.device, self.company, group)
        group._store_success(
            group.generation, {"status": "match", "actual_quantity": 3}
        )
        result._store_success(result.generation, {"status": "match"})
        with self.assertRaisesRegex(ValueError, "rollback"), self.cr.savepoint():
            group._mark_pending()
            result._reset("Excluded")
            raise ValueError("rollback")
        self.assertFalse(group.pending)
        self.assertFalse(result.pending)
        self.assertEqual(result.status, "match")
        self.assertEqual(self.device.read()[0], before)
        self.assertEqual(
            counts, {model: self.env[model].search_count([]) for model in stock_models}
        )
