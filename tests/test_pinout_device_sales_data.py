from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPinoutDeviceSalesData(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Device Sales Metadata Customer"}
        )
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.normal_product = cls.env["product.product"].create(
            {
                "name": "Device Sales Metadata Retail Unit",
                "type": "product",
                "tracking": "serial",
            }
        )
        cls.kit_product = cls.env["product.product"].create(
            {"name": "Device Sales Metadata Kit", "type": "product"}
        )
        cls.kit_components = cls.env["product.product"].create(
            [
                {
                    "name": "Device Sales Metadata Kit Urban",
                    "type": "product",
                    "tracking": "serial",
                },
                {
                    "name": "Device Sales Metadata Kit Insight",
                    "type": "product",
                    "tracking": "serial",
                },
            ]
        )
        cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.kit_product.product_tmpl_id.id,
                "product_id": cls.kit_product.id,
                "product_qty": 1.0,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create({"product_id": product.id, "product_qty": 1.0})
                    for product in cls.kit_components
                ],
            }
        )
        all_device_products = cls.normal_product | cls.kit_components
        cls.device_type = cls.env["pinout.device.type"].create(
            {
                "name": "Device Sales Metadata Type",
                "code": "SALES-METADATA-DEVICE",
                "allowed_product_template_ids": [
                    Command.set(all_device_products.product_tmpl_id.ids)
                ],
            }
        )
        cls.bundle_type = cls.env["pinout.device.bundle.type"].create(
            {
                "name": "Device Sales Metadata Bundle Type",
                "code": "SALES-METADATA-BUNDLE",
                "requires_kit_bom": True,
                "allowed_product_template_ids": [
                    Command.set(cls.kit_product.product_tmpl_id.ids)
                ],
            }
        )
        cls.normal_lot = cls._create_lot(cls.normal_product, "SALES-NORMAL-001")
        cls.normal_device = cls._create_device(cls.normal_lot)
        cls.kit_lots = cls.env["stock.lot"]
        cls.kit_devices = cls.env["pinout.device"]
        for index, product in enumerate(cls.kit_components, start=1):
            lot = cls._create_lot(product, f"SALES-KIT-{index:03d}")
            cls.kit_lots |= lot
            cls.kit_devices |= cls._create_device(lot)
        cls.bundle = cls.env["pinout.device.bundle"].create(
            {
                "name": "SALES-METADATA-KIT-BUNDLE",
                "bundle_type_id": cls.bundle_type.id,
                "bundle_product_id": cls.kit_product.id,
            }
        )
        cls.kit_devices.write({"bundle_id": cls.bundle.id})

        for lot in cls.normal_lot | cls.kit_lots:
            cls.env["stock.quant"]._update_available_quantity(
                lot.product_id,
                cls.stock_location,
                1,
                lot_id=lot,
            )

    @classmethod
    def _create_lot(cls, product, name):
        return cls.env["stock.lot"].create(
            {
                "name": name,
                "product_id": product.id,
                "company_id": cls.env.company.id,
            }
        )

    @classmethod
    def _create_device(cls, lot):
        return cls.env["pinout.device"].create(
            {
                "device_uid": lot.name,
                "device_type_id": cls.device_type.id,
                "current_product_id": lot.product_id.id,
                "final_lot_id": lot.id,
                "state": "ready_for_sale",
            }
        )

    def _create_sale_order(self, product, reference=False):
        return self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": reference,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "product_uom_qty": 1.0,
                            "product_uom": product.uom_id.id,
                            "price_unit": 100.0,
                        }
                    )
                ],
            }
        )

    def _complete_sale_delivery(self, sale_order, devices):
        sale_order.action_confirm()
        picking = sale_order.picking_ids.filtered(lambda item: item.state != "cancel")
        self.assertEqual(len(picking), 1)
        devices_by_product = {device.current_product_id: device for device in devices}
        moves = picking.move_ids.filtered(lambda move: move.state != "cancel")
        self.assertEqual(set(moves.product_id), set(devices_by_product))

        for move in moves:
            device = devices_by_product[move.product_id]
            move.move_line_ids.unlink()
            move.write(
                {
                    "picked": True,
                    "move_line_ids": [
                        Command.create(
                            {
                                "product_id": move.product_id.id,
                                "product_uom_id": move.product_uom.id,
                                "quantity": 1.0,
                                "picking_id": picking.id,
                                "location_id": picking.location_id.id,
                                "location_dest_id": picking.location_dest_id.id,
                                "lot_id": device.final_lot_id.id,
                                "picked": True,
                            }
                        )
                    ],
                }
            )
        picking._action_done()
        return picking

    def test_standard_sale_populates_device_sales_metadata(self):
        sale_order = self._create_sale_order(
            self.normal_product,
            reference="CUSTOMER-NORMAL-REFERENCE",
        )
        delivery = self._complete_sale_delivery(sale_order, self.normal_device)
        device = self.normal_device

        self.assertEqual(device.state, "sold")
        self.assertEqual(device.last_customer_id, self.partner)
        self.assertEqual(device.last_sale_order_id, sale_order)
        self.assertEqual(device.last_delivery_id, delivery)
        self.assertEqual(device.last_order_reference, "CUSTOMER-NORMAL-REFERENCE")

        device.final_lot_id = False

        self.assertEqual(device.final_lot_history_ids, self.normal_lot)
        self.assertEqual(device.last_customer_id, self.partner)
        self.assertEqual(device.last_sale_order_id, sale_order)
        self.assertEqual(device.last_delivery_id, delivery)
        self.assertEqual(device.last_order_reference, "CUSTOMER-NORMAL-REFERENCE")

    def test_last_customer_grouping_and_expansion(self):
        sale_order = self._create_sale_order(self.normal_product)
        self._complete_sale_delivery(sale_order, self.normal_device)
        devices = self.normal_device | self.kit_devices
        result = devices.web_read_group(
            [("id", "in", devices.ids)],
            ["last_customer_id"],
            ["last_customer_id"],
        )
        self.assertEqual(result["length"], 2)
        groups = {
            group["last_customer_id"][0] if group["last_customer_id"] else False: group
            for group in result["groups"]
        }
        self.assertEqual(groups[self.partner.id]["last_customer_id_count"], 1)
        self.assertEqual(groups[False]["last_customer_id_count"], 2)
        self.assertEqual(
            devices.search(groups[self.partner.id]["__domain"]), self.normal_device
        )
        self.assertEqual(devices.search(groups[False]["__domain"]), self.kit_devices)

        second_partner = self.env["res.partner"].create({"name": "Another Customer"})
        kit_order = self._create_sale_order(self.kit_product)
        kit_order.partner_id = second_partner
        self._complete_sale_delivery(kit_order, self.kit_devices)
        groups = devices.read_group(
            [("id", "in", devices.ids), ("state", "=", "sold")],
            ["last_customer_id"],
            ["last_customer_id", "device_type_id"],
            lazy=False,
        )
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {group["last_customer_id"][0]: group["__count"] for group in groups},
            {self.partner.id: 1, second_partner.id: 2},
        )
        page = devices.web_read_group(
            [("id", "in", devices.ids)],
            ["last_customer_id"],
            ["last_customer_id"],
            limit=1,
            orderby="last_customer_id asc",
        )
        self.assertEqual(page["length"], 2)
        self.assertEqual(page["groups"][0]["last_customer_id"][0], second_partner.id)
        self.assertEqual(
            devices.search(page["groups"][0]["__domain"]), self.kit_devices
        )
        for operator, value, expected in (
            ("=", False, devices.browse()),
            ("!=", False, devices),
            ("!=", self.partner.id, self.kit_devices),
            ("in", [False, self.partner.id], self.normal_device),
            ("not in", [False, self.partner.id], self.kit_devices),
            ("in", [], devices.browse()),
            ("not in", [], devices),
        ):
            with self.subTest(operator=operator, value=value):
                self.assertEqual(
                    devices.search(
                        [
                            ("id", "in", devices.ids),
                            ("last_customer_id", operator, value),
                        ]
                    ),
                    expected,
                )

    def test_customer_grouping_reads_delivery_changes_without_cached_metadata(self):
        order = self._create_sale_order(self.normal_product)
        delivery = self._complete_sale_delivery(order, self.normal_device)
        self.normal_device.final_lot_id = False
        another_partner = self.env["res.partner"].create({"name": "Corrected Customer"})
        delivery.partner_id = another_partner
        # Observe writes in this transaction without relying on the computed cache.
        domain = [("id", "=", self.normal_device.id)]
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(groups[0]["last_customer_id"][0], another_partner.id)
        self.assertEqual(
            self.normal_device.search(groups[0]["__domain"]), self.normal_device
        )
        delivery.move_line_ids.location_dest_id = self.stock_location
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertFalse(groups[0]["last_customer_id"])
        delivery.move_line_ids.location_dest_id = self.env.ref(
            "stock.stock_location_customers"
        )
        delivery.move_ids.write({"state": "cancel"})
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertFalse(groups[0]["last_customer_id"])

    def test_customer_grouping_respects_device_and_stock_record_rules(self):
        order = self._create_sale_order(self.normal_product)
        self._complete_sale_delivery(order, self.normal_device)
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Customer Grouping User",
                    "login": "customer-grouping-test",
                    "company_id": self.env.company.id,
                    "company_ids": [Command.set(self.env.company.ids)],
                    "groups_id": [
                        Command.set(
                            [
                                self.env.ref("stock.group_stock_manager").id,
                                self.env.ref("sales_team.group_sale_manager").id,
                            ]
                        )
                    ],
                }
            )
        )
        devices = (self.normal_device | self.kit_devices).with_user(user)
        domain = [("id", "in", devices.ids)]
        groups = devices.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(len(groups), 2)
        for model, rule_domain in (
            ("stock.move.line", [("lot_id", "!=", self.normal_lot.id)]),
            ("stock.lot", [("id", "!=", self.normal_lot.id)]),
            ("stock.picking", [("id", "not in", order.picking_ids.ids)]),
        ):
            with self.subTest(model=model):
                stock_rule = self.env["ir.rule"].create(
                    {
                        "name": "Customer grouping hidden stock data",
                        "model_id": self.env["ir.model"]._get_id(model),
                        "domain_force": repr(rule_domain),
                    }
                )
                groups = devices.read_group(domain, [], ["last_customer_id"])
                self.assertEqual(len(groups), 1)
                self.assertFalse(groups[0]["last_customer_id"])
                self.assertEqual(devices.search(groups[0]["__domain"]), devices)
                stock_rule.unlink()

        other_company = self.env["res.company"].create(
            {"name": "Grouping Other Company"}
        )
        foreign_partner = self.env["res.partner"].create({"name": "Foreign Customer"})
        foreign_lot = self.env["stock.lot"].create(
            {
                "name": self.normal_device.device_uid,
                "product_id": self.normal_product.id,
                "company_id": other_company.id,
                "pinout_device_id": self.normal_device.id,
            }
        )
        source = self.env["stock.location"].create(
            {
                "name": "Foreign Historical Source",
                "usage": "inventory",
                "company_id": other_company.id,
            }
        )
        destination = self.env.ref("stock.stock_location_customers")
        operation = self.env["stock.picking.type"].create(
            {
                "name": "Foreign Historical Delivery",
                "sequence_code": "FOREIGN-GROUP",
                "code": "outgoing",
                "company_id": other_company.id,
            }
        )
        foreign_delivery = self.env["stock.picking"].create(
            {
                "picking_type_id": operation.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "partner_id": foreign_partner.id,
                "company_id": other_company.id,
                "date_done": "2099-01-01 00:00:00",
            }
        )
        # A later historical delivery in an inaccessible company must not win.
        self.env["stock.move"].create(
            {
                "name": "Foreign Historical Move",
                "product_id": self.normal_product.id,
                "product_uom": self.normal_product.uom_id.id,
                "state": "done",
                "company_id": other_company.id,
                "picking_id": foreign_delivery.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "move_line_ids": [
                    Command.create(
                        {
                            "product_id": self.normal_product.id,
                            "product_uom_id": self.normal_product.uom_id.id,
                            "lot_id": foreign_lot.id,
                            "quantity": 1,
                            "company_id": other_company.id,
                            "picking_id": foreign_delivery.id,
                            "location_id": source.id,
                            "location_dest_id": destination.id,
                        }
                    )
                ],
            }
        )
        admin_groups = self.normal_device.read_group(
            [("id", "=", self.normal_device.id)], [], ["last_customer_id"]
        )
        self.assertEqual(admin_groups[0]["last_customer_id"][0], foreign_partner.id)
        groups = devices.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(
            {
                group["last_customer_id"][0] if group["last_customer_id"] else False
                for group in groups
            },
            {self.partner.id, False},
        )
        self.assertFalse(
            devices.search([*domain, ("last_customer_id", "=", foreign_partner.id)])
        )
        self.env["ir.rule"].create(
            {
                "name": "Customer grouping hidden device",
                "model_id": self.env["ir.model"]._get_id("pinout.device"),
                "domain_force": repr([("id", "!=", self.normal_device.id)]),
            }
        )
        groups = devices.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["last_customer_id"])
        self.assertEqual(
            devices.search(groups[0]["__domain"]).ids, self.kit_devices.ids
        )

    def test_customer_grouping_return_and_resale(self):
        order = self._create_sale_order(self.normal_product)
        delivery = self._complete_sale_delivery(order, self.normal_device)
        wizard = (
            self.env["stock.return.picking"]
            .with_context(
                active_model="stock.picking",
                active_id=delivery.id,
                active_ids=delivery.ids,
            )
            .create({})
        )
        return_id, _ = wizard._create_returns()
        returned = self.env["stock.picking"].browse(return_id)
        returned.move_ids.write({"picked": True})
        returned.move_line_ids.write(
            {"lot_id": self.normal_lot.id, "quantity": 1, "picked": True}
        )
        returned._action_done()
        domain = [("id", "=", self.normal_device.id)]
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(groups[0]["last_customer_id"][0], self.partner.id)
        second_partner = self.env["res.partner"].create({"name": "Resale Customer"})
        resale = self._create_sale_order(self.normal_product)
        resale.partner_id = second_partner
        self._complete_sale_delivery(resale, self.normal_device)
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(groups[0]["last_customer_id"][0], second_partner.id)
        self.assertEqual(self.normal_device.last_customer_id, second_partner)

    def test_latest_sales_metadata_uses_all_historical_lots(self):
        first_sale_order = self._create_sale_order(self.normal_product)
        self._complete_sale_delivery(first_sale_order, self.normal_device)

        replacement_product = self.env["product.product"].create(
            {
                "name": "Device Sales Metadata Replacement Retail Unit",
                "type": "product",
                "tracking": "serial",
            }
        )
        self.device_type.allowed_product_template_ids = [
            Command.link(replacement_product.product_tmpl_id.id)
        ]
        replacement_lot = self.env["stock.lot"].create(
            {
                "name": self.normal_device.device_uid,
                "product_id": replacement_product.id,
                "company_id": self.env.company.id,
                "pinout_device_id": self.normal_device.id,
            }
        )
        self.env["stock.quant"]._update_available_quantity(
            replacement_product,
            self.stock_location,
            1,
            lot_id=replacement_lot,
        )
        self.normal_device.write(
            {
                "current_product_id": replacement_product.id,
                "final_lot_id": replacement_lot.id,
            }
        )

        latest_sale_order = self._create_sale_order(
            replacement_product,
            reference="LATEST-HISTORICAL-REFERENCE",
        )
        latest_delivery = self._complete_sale_delivery(
            latest_sale_order, self.normal_device
        )
        self.normal_device.final_lot_id = False

        self.assertEqual(
            set(self.normal_device.final_lot_history_ids.ids),
            {self.normal_lot.id, replacement_lot.id},
        )
        self.assertEqual(self.normal_device.last_sale_order_id, latest_sale_order)
        self.assertEqual(self.normal_device.last_delivery_id, latest_delivery)
        self.assertEqual(
            self.normal_device.last_order_reference,
            "LATEST-HISTORICAL-REFERENCE",
        )

        another_partner = self.env["res.partner"].create({"name": "Latest Customer"})
        latest_delivery.partner_id = another_partner
        domain = [("id", "=", self.normal_device.id)]
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(groups[0]["last_customer_id"][0], another_partner.id)
        latest_delivery.date_done = fields.Datetime.to_datetime("2000-01-01 00:00:00")
        groups = self.normal_device.read_group(domain, [], ["last_customer_id"])
        self.assertEqual(groups[0]["last_customer_id"][0], self.partner.id)

    def test_kit_sale_populates_component_devices_and_bundle_metadata(self):
        sale_order = self._create_sale_order(self.kit_product)
        delivery = self._complete_sale_delivery(sale_order, self.kit_devices)

        for device in self.kit_devices:
            self.assertEqual(device.state, "sold")
            self.assertEqual(device.last_customer_id, self.partner)
            self.assertEqual(device.last_sale_order_id, sale_order)
            self.assertEqual(device.last_delivery_id, delivery)
            self.assertEqual(device.last_order_reference, sale_order.name)
        self.assertEqual(self.bundle.state, "sold")
        self.assertEqual(self.bundle.customer_id, self.partner)
        self.assertEqual(self.bundle.sale_order_id, sale_order)
        self.assertEqual(self.bundle.delivery_id, delivery)
