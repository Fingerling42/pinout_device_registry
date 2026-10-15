from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common_inventory_audit import InventoryAuditCompanyCase


@tagged("post_install", "-at_install")
class TestInventoryAuditScope(InventoryAuditCompanyCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["pinout.inventory.audit.config"]
        cls.Scope = cls.env["pinout.inventory.audit.scope"]
        cls.Location = cls.env["stock.location"]
        cls.config = cls.Config.create({})
        cls.existing_products = cls.config._get_products()
        cls.warehouses = cls.env["stock.warehouse"].create(
            [
                {"name": "Audit Warehouse A", "code": "AWA"},
                {"name": "Audit Warehouse B", "code": "AWB"},
            ]
        )
        cls.root = cls.warehouses[0].view_location_id
        cls.stock = cls.warehouses[0].lot_stock_id
        cls.child = cls.Location.create(
            {
                "name": "Audit Shelf",
                "usage": "internal",
                "location_id": cls.stock.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {"name": "Audit PCB", "type": "product"}
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Scope Test",
                "code": "SCOPE-TEST",
                "allowed_product_template_ids": [
                    fields.Command.set(cls.product.product_tmpl_id.ids)
                ],
            }
        )

    def add_scope(self, location=None, children=True):
        return self.Scope.create(
            {
                "config_id": self.config.id,
                "location_id": (location or self.root).id,
                "include_children": children,
            }
        )

    def snapshot(self, location=None):
        location = location or self.stock
        device = self.env["pinout.device"].create(
            {
                "device_uid": "SCOPE-DEVICE",
                "device_type_id": self.device_type.id,
                "current_product_id": self.product.id,
                "location_id": location.id,
            }
        )
        group = (
            self.env["pinout.inventory.audit.group"]
            .sudo()
            ._get_or_create(self.env.company, self.product, location)
        )
        result = (
            self.env["pinout.inventory.audit.device"]
            .sudo()
            ._for_device(device, self.env.company, group)
        )
        group._store_success(
            group.generation, {"status": "match", "actual_quantity": 1}
        )
        result._store_success(result.generation, {"status": "match"})
        self.config.full_check_pending = False
        return group, result

    def test_empty_multiwarehouse_and_overlap(self):
        self.assertFalse(self.config._get_locations())
        self.assertIn("not configured", self.config.scope_message)
        self.add_scope()
        self.add_scope(self.stock)
        self.add_scope(self.warehouses[1].view_location_id)
        locations = self.config._get_locations()
        self.assertEqual(
            set(locations.ids),
            {self.stock.id, self.child.id, self.warehouses[1].lot_stock_id.id},
        )
        self.assertNotIn(self.root, locations)
        self.assertEqual(len(locations), len(set(locations.ids)))

    def test_exact_location_and_excluded_types(self):
        scope = self.add_scope(self.stock, False)
        self.assertEqual(self.config._get_locations(), self.stock)
        scope.include_children = True
        for usage in (
            "customer",
            "supplier",
            "production",
            "inventory",
            "transit",
            "view",
        ):
            self.Location.create(
                {
                    "name": "Excluded " + usage,
                    "usage": usage,
                    "location_id": self.stock.id,
                    "company_id": self.env.company.id,
                }
            )
        self.Location.create(
            {
                "name": "Scrap",
                "usage": "internal",
                "scrap_location": True,
                "location_id": self.stock.id,
                "company_id": self.env.company.id,
            }
        )
        self.assertEqual(
            set(self.config._get_locations().ids), {self.stock.id, self.child.id}
        )
        for values in (
            {"location_id": self.root.id, "include_children": False},
            {"location_id": self.env.ref("stock.stock_location_customers").id},
        ):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                scope.write(values)

    def test_company_shared_view_and_invalid_roots(self):
        other = self.env["res.company"].create({"name": "Scope Other Company"})
        foreign = self.Location.create(
            {"name": "Other Internal", "usage": "internal", "company_id": other.id}
        )
        with self.assertRaises(UserError), self.cr.savepoint():
            self.add_scope(foreign)
        shared = self.Location.create(
            {"name": "Shared View", "usage": "view", "company_id": False}
        )
        self.stock.location_id = shared
        foreign.location_id = shared
        self.add_scope(shared)
        self.assertEqual(
            set(self.config._get_locations().ids), {self.stock.id, self.child.id}
        )
        for values in (
            {"usage": "internal", "company_id": False},
            {"usage": "internal", "scrap_location": True},
            {"usage": "internal", "active": False},
        ):
            location = self.Location.create({"name": "Invalid Root", **values})
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.add_scope(location)

    def test_scope_change_retires_results_and_is_transactional(self):
        scope = self.add_scope()
        group, result = self.snapshot(self.child)
        with self.assertRaisesRegex(ValueError, "rollback"), self.cr.savepoint():
            scope.location_id = self.stock
            scope.include_children = False
            raise ValueError("rollback")
        self.assertTrue(group.exists())
        self.assertEqual(result.status, "match")
        scope.location_id = self.stock
        self.assertTrue(group.pending)
        self.assertTrue(result.pending)
        self.assertTrue(self.config.full_check_pending)
        scope.include_children = False
        self.assertFalse(group.exists())
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.group_id)
        self.assertFalse(result.checked_at)
        scope.unlink()
        self.assertFalse(self.config._get_locations())

    def test_location_tree_type_active_and_scrap_changes(self):
        self.add_scope()
        group, result = self.snapshot(self.child)
        self.child.location_id = self.warehouses[1].lot_stock_id
        self.assertFalse(group.exists())
        self.assertEqual(result.status, "not_checked")
        self.child.location_id = self.stock
        self.assertIn(self.child, self.config._get_locations())
        self.child.scrap_location = True
        self.assertNotIn(self.child, self.config._get_locations())
        self.child.scrap_location = False
        self.child.usage = "transit"
        self.assertNotIn(self.child, self.config._get_locations())
        self.child.usage = "internal"
        self.child.active = False
        self.assertNotIn(self.child, self.config._get_locations())
        self.child.active = True
        self.assertIn(self.child, self.config._get_locations())
        new = self.child.copy({"name": "New shelf"})
        self.assertIn(new, self.config._get_locations())
        self.config.full_check_pending = False
        new.unlink()
        self.assertTrue(self.config.full_check_pending)

    def test_product_forms_without_devices_and_changes(self):
        self.add_scope()
        self.assertEqual(
            self.config._get_products(), self.existing_products | self.product
        )
        group, result = self.snapshot()
        self.device_type.allowed_product_template_ids = [fields.Command.clear()]
        self.assertFalse(group.exists())
        self.assertEqual(result.status, "not_checked")
        self.assertEqual(self.config._get_products(), self.existing_products)
        self.device_type.allowed_product_template_ids = [
            fields.Command.set(self.product.product_tmpl_id.ids)
        ]
        self.device_type.active = False
        self.assertEqual(self.config._get_products(), self.existing_products)
        self.device_type.active = True
        self.product.active = False
        self.assertEqual(self.config._get_products(), self.existing_products)
        self.product.active = True
        self.product.product_tmpl_id.type = "consu"
        self.assertEqual(self.config._get_products(), self.existing_products)
        self.product.product_tmpl_id.type = "product"
        self.assertEqual(
            self.config._get_products(), self.existing_products | self.product
        )

    def test_config_removal_clears_matches(self):
        self.add_scope()
        group, result = self.snapshot()
        self.config.unlink()
        self.assertFalse(group.exists())
        self.assertEqual(result.status, "not_checked")
        self.assertFalse(result.checked_at)

    def test_product_variants_and_company_filter(self):
        attribute = self.env["product.attribute"].create({"name": "Audit Colour"})
        values = self.env["product.attribute.value"].create(
            [
                {"name": "Light", "attribute_id": attribute.id},
                {"name": "Dark", "attribute_id": attribute.id},
            ]
        )
        template = self.env["product.template"].create(
            {
                "name": "Audit Variants",
                "type": "product",
                "attribute_line_ids": [
                    fields.Command.create(
                        {
                            "attribute_id": attribute.id,
                            "value_ids": [fields.Command.set(values.ids)],
                        }
                    )
                ],
            }
        )
        self.device_type.allowed_product_template_ids = [
            fields.Command.link(template.id)
        ]
        variants = template.product_variant_ids
        self.assertEqual(len(variants), 2)
        self.assertEqual(
            self.config._get_products(),
            self.existing_products | self.product | variants,
        )
        variants[0].active = False
        self.assertEqual(
            self.config._get_products(),
            self.existing_products | self.product | variants[1],
        )
        other = self.env["res.company"].create({"name": "Product Other Company"})
        template.company_id = other
        self.assertEqual(
            self.config._get_products(), self.existing_products | self.product
        )
        template.company_id = False
        template.active = False
        self.assertEqual(
            self.config._get_products(), self.existing_products | self.product
        )
        self.device_type.active = False
        self.assertEqual(
            self.config.with_context(active_test=False)._get_products(),
            self.existing_products,
        )

    def test_company_change_and_source_preservation(self):
        scope = self.add_scope()
        group, result = self.snapshot()
        device = result.device_id
        before = device.read(
            [
                "current_product_id",
                "location_id",
                "state",
                "quality_status",
                "final_lot_id",
            ]
        )
        quant_count = self.env["stock.quant"].search_count([])
        other = self.env["res.company"].create({"name": "New Scope Company"})
        with self.assertRaises(UserError), self.cr.savepoint():
            self.config.company_id = other
        self.assertTrue(group.exists())
        self.assertEqual(result.status, "match")
        scope.unlink()
        self.config.company_id = other
        self.assertTrue(self.config.full_check_pending)
        self.assertFalse(self.config._get_locations())
        self.assertEqual(
            before,
            device.read(
                [
                    "current_product_id",
                    "location_id",
                    "state",
                    "quality_status",
                    "final_lot_id",
                ]
            ),
        )
        self.assertEqual(quant_count, self.env["stock.quant"].search_count([]))
