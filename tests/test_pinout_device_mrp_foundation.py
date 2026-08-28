from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceMrpFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.finished_location = cls.env["stock.location"].create(
            {
                "name": "Registry MO Finished Forms",
                "location_id": cls.stock_location.id,
                "usage": "internal",
            }
        )
        cls.source_product = cls.env["product.product"].create(
            {
                "name": "Registry MO Source Form",
                "type": "product",
            }
        )
        cls.other_source_product = cls.env["product.product"].create(
            {
                "name": "Registry MO Other Source Form",
                "type": "product",
            }
        )
        cls.target_template = cls.env["product.template"].create(
            {
                "name": "Registry MO Target Form",
                "type": "product",
                "pinout_device_state_after_manufacturing": "ready_for_packaging",
            }
        )
        cls.target_product = cls.target_template.product_variant_id
        cls.unmanaged_product = cls.env["product.product"].create(
            {
                "name": "Unmanaged MO Product",
                "type": "product",
            }
        )
        cls.component_product = cls.env["product.product"].create(
            {
                "name": "Unmanaged MO Component",
                "type": "product",
            }
        )
        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.target_template.id,
                "product_id": cls.target_product.id,
                "product_qty": 1.0,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": cls.source_product.id,
                            "product_qty": 1.0,
                        }
                    )
                ],
            }
        )
        cls.unmanaged_bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.unmanaged_product.product_tmpl_id.id,
                "product_id": cls.unmanaged_product.id,
                "product_qty": 1.0,
                "type": "normal",
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": cls.component_product.id,
                            "product_qty": 1.0,
                        }
                    )
                ],
            }
        )
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Registry MO Device Type",
                "code": "REGISTRY-MO",
                "allowed_product_template_ids": [
                    Command.set(
                        (
                            cls.source_product.product_tmpl_id
                            | cls.other_source_product.product_tmpl_id
                            | cls.target_template
                        ).ids
                    )
                ],
            }
        )
        cls.other_device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Registry MO Other Device Type",
                "code": "REGISTRY-MO-OTHER",
                "allowed_product_template_ids": [
                    Command.set(
                        (cls.source_product.product_tmpl_id | cls.target_template).ids
                    )
                ],
            }
        )
        cls.device_sequence = 0

    def _create_device(self, **values):
        self.__class__.device_sequence += 1
        values.setdefault(
            "device_uid",
            f"REGISTRY-MO-{self.device_sequence:03d}",
        )
        values.setdefault("device_type_id", self.device_type.id)
        values.setdefault("current_product_id", self.source_product.id)
        return self.env["pinout.device"].create(values)

    def _create_production(self, quantity=1.0, product=None, bom=None):
        product = product or self.target_product
        bom = bom or self.bom
        return self.env["mrp.production"].create(
            {
                "product_id": product.id,
                "product_qty": quantity,
                "product_uom_id": product.uom_id.id,
                "bom_id": bom.id,
                "location_src_id": self.stock_location.id,
                "location_dest_id": self.finished_location.id,
            }
        )

    def test_product_form_configures_device_state_after_manufacturing(self):
        default_template = self.env["product.template"].create(
            {"name": "Registry MO Default State Product"}
        )

        self.assertEqual(
            self.target_template.pinout_device_state_after_manufacturing,
            "ready_for_packaging",
        )
        self.assertEqual(
            default_template.pinout_device_state_after_manufacturing,
            "no_change",
        )

    def test_matching_batch_can_be_linked_and_confirmed_without_sync(self):
        devices = self._create_device() | self._create_device()
        production = self._create_production(quantity=2.0)

        production.pinout_device_ids = [Command.set(devices.ids)]

        self.assertTrue(production.pinout_registry_available)
        self.assertIn(self.device_type, production.pinout_allowed_device_type_ids)
        self.assertEqual(production.pinout_source_product_id, self.source_product)
        self.assertEqual(production.pinout_target_device_state, "ready_for_packaging")
        self.assertEqual(production.pinout_device_count, 2)
        self.assertEqual(devices.manufacturing_order_ids, production)

        production.action_confirm()

        self.assertEqual(production.state, "confirmed")
        self.assertEqual(devices.current_product_id, self.source_product)
        self.assertEqual(set(devices.mapped("state")), {"wip"})

    def test_untracked_batch_synchronizes_devices_after_completion(self):
        devices = self._create_device(
            quality_status="needs_test"
        ) | self._create_device(quality_status="needs_test")
        production = self._create_production(quantity=2.0)
        production.pinout_device_ids = [Command.set(devices.ids)]
        production.action_confirm()

        production.button_mark_done()

        self.assertEqual(production.state, "done")
        self.assertEqual(
            set(devices.mapped("current_product_id")), {self.target_product}
        )
        self.assertEqual(set(devices.mapped("state")), {"ready_for_packaging"})
        self.assertEqual(set(devices.mapped("quality_status")), {"needs_test"})
        self.assertEqual(set(devices.mapped("location_id")), {self.finished_location})
        for device in devices:
            self.assertTrue(
                device.message_ids.filtered(
                    lambda message: "was synchronized after" in message.body
                )
            )
        self.assertTrue(
            production.message_ids.filtered(
                lambda message: "were synchronized to Product Form" in message.body
            )
        )

    def test_do_not_change_preserves_device_state_and_quality(self):
        self.target_template.pinout_device_state_after_manufacturing = "no_change"
        device = self._create_device(state="rework", quality_status="ok")
        production = self._create_production()
        production.pinout_device_ids = [Command.set(device.ids)]
        production.action_confirm()

        production.button_mark_done()

        self.assertEqual(device.current_product_id, self.target_product)
        self.assertEqual(device.state, "rework")
        self.assertEqual(device.quality_status, "ok")
        self.assertEqual(device.location_id, self.finished_location)

    def test_partial_untracked_batch_is_rejected_before_backorder(self):
        devices = self._create_device() | self._create_device()
        production = self._create_production(quantity=2.0)
        production.pinout_device_ids = [Command.set(devices.ids)]
        production.action_confirm()
        production.qty_producing = 1.0
        state_before_completion = production.state

        with (
            self.assertRaisesRegex(ValidationError, "Partial production"),
            self.cr.savepoint(),
        ):
            production.button_mark_done()

        self.assertEqual(production.state, state_before_completion)
        self.assertEqual(
            set(devices.mapped("current_product_id")), {self.source_product}
        )

    def test_active_final_lot_blocks_untracked_synchronization(self):
        device = self._create_device()
        lot = self.env["stock.lot"].create(
            {
                "name": device.device_uid,
                "product_id": self.source_product.id,
                "company_id": self.env.company.id,
            }
        )
        device.final_lot_id = lot
        production = self._create_production()
        production.pinout_device_ids = [Command.set(device.ids)]
        production.action_confirm()

        with (
            self.assertRaisesRegex(ValidationError, "active Final Lot"),
            self.cr.savepoint(),
        ):
            production.button_mark_done()

        self.assertEqual(production.state, "confirmed")
        self.assertEqual(device.current_product_id, self.source_product)
        self.assertEqual(device.final_lot_id, lot)

    def test_unlinked_standard_manufacturing_order_is_unchanged(self):
        production = self._create_production(
            product=self.unmanaged_product,
            bom=self.unmanaged_bom,
        )

        production.action_confirm()

        self.assertEqual(production.state, "confirmed")
        self.assertFalse(production.pinout_registry_available)
        self.assertFalse(production.pinout_device_ids)

    def test_device_count_must_match_manufacturing_quantity(self):
        production = self._create_production(quantity=2.0)

        with (
            self.assertRaisesRegex(ValidationError, "must match the number"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(self._create_device().ids)]

    def test_serial_tracked_output_requires_one_device_per_order(self):
        self.target_template.tracking = "serial"
        devices = self._create_device() | self._create_device()
        production = self._create_production(quantity=2.0)

        with (
            self.assertRaisesRegex(ValidationError, "serial-tracked output"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(devices.ids)]

    def test_current_product_must_match_unique_bom_source_form(self):
        production = self._create_production()
        device = self._create_device(current_product_id=self.other_source_product.id)

        with (
            self.assertRaisesRegex(ValidationError, "does not match the source"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(device.ids)]

    def test_bom_must_have_one_managed_source_form_per_device(self):
        ambiguous_bom = self.bom.copy(
            {
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": self.source_product.id,
                            "product_qty": 1.0,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.other_source_product.id,
                            "product_qty": 1.0,
                        }
                    ),
                ]
            }
        )
        production = self._create_production(bom=ambiguous_bom)

        with (
            self.assertRaisesRegex(ValidationError, "exactly one source Product Form"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(self._create_device().ids)]

    def test_mixed_device_types_are_rejected(self):
        devices = self._create_device() | self._create_device(
            device_type_id=self.other_device_type.id
        )
        production = self._create_production(quantity=2.0)

        with (
            self.assertRaisesRegex(ValidationError, "exactly one Device Type"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(devices.ids)]

    def test_bundled_and_unavailable_devices_are_rejected(self):
        bundle = self.env["pinout.device.bundle"].create(
            {
                "name": "REGISTRY-MO-BUNDLE",
                "bundle_type_id": self.env.ref(
                    "pinout_device_registry.bundle_type_other"
                ).id,
            }
        )
        bundled_device = self._create_device(bundle_id=bundle.id)
        production = self._create_production()

        with (
            self.assertRaisesRegex(ValidationError, "Cancel and Unpair"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(bundled_device.ids)]

        sold_device = self._create_device(state="sold")
        with (
            self.assertRaisesRegex(ValidationError, "Reserved, Sold, or Scrapped"),
            self.cr.savepoint(),
        ):
            production.pinout_device_ids = [Command.set(sold_device.ids)]

    def test_device_cannot_be_linked_to_two_active_manufacturing_orders(self):
        device = self._create_device()
        first_production = self._create_production()
        first_production.pinout_device_ids = [Command.set(device.ids)]
        second_production = self._create_production()

        with (
            self.assertRaisesRegex(ValidationError, "already assigned to active"),
            self.cr.savepoint(),
        ):
            second_production.pinout_device_ids = [Command.set(device.ids)]

        first_production.action_cancel()
        second_production.pinout_device_ids = [Command.set(device.ids)]
        self.assertEqual(second_production.pinout_device_ids, device)
